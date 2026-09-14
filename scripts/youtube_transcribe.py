#!/usr/bin/env python3
"""One YouTube URL -> Playwright converter -> verified audio -> local FunASR TXT."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
from urllib.parse import quote, urlsplit
from urllib.request import urlopen

import media_transcribe as core

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / 'config.local.json'


def youtube_id(url):
    if not isinstance(url, str) or any(c.isspace() or ord(c) < 32 for c in url):
        raise ValueError('Expected an unmodified HTTPS YouTube video URL')
    parsed = urlsplit(url)
    if parsed.scheme != 'https' or parsed.netloc not in ('youtube.com', 'www.youtube.com', 'm.youtube.com', 'youtu.be'):
        raise ValueError('Only HTTPS YouTube video URLs are accepted')
    if parsed.netloc == 'youtu.be':
        value = parsed.path[1:]
    elif parsed.path == '/watch':
        values = [item[2:] for item in parsed.query.split('&') if item.startswith('v=')]
        if len(values) != 1:
            raise ValueError('Expected exactly one YouTube video ID')
        value = values[0]
    else:
        match = re.fullmatch(r'/(?:shorts|live|embed)/([^/]+)', parsed.path)
        value = match[1] if match else ''
    if not re.fullmatch(r'[A-Za-z0-9_-]{11}', value):
        raise ValueError('Invalid YouTube video ID; malformed IDs are not repaired')
    return value


def title_filename(title):
    if not isinstance(title, str) or not title.strip():
        raise ValueError('Missing video title/filename')
    title = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', title).strip().rstrip('.')
    if not title or title in ('.', '..'):
        raise ValueError('Unsafe empty title')
    if len(title.encode('utf-8')) > 230:
        raise ValueError('Video title too long for a filename; supply --title')
    return title + '.mp3'


def combined_title(video_title, uploader=None):
    if not isinstance(video_title, str) or not video_title.strip():
        raise ValueError('Missing video title/filename')
    title = video_title.strip()
    if not isinstance(uploader, str) or not uploader.strip():
        return title
    owner = uploader.strip()
    norm_title = re.sub(r'\s+', ' ', title).casefold()
    norm_owner = re.sub(r'\s+', ' ', owner).casefold()
    if norm_title == norm_owner or norm_title.startswith(f'[{norm_owner}] '):
        return title
    return f'[{owner}] {title}'


def youtube_metadata(url, timeout=30):
    endpoint = 'https://www.youtube.com/oembed?url=' + quote(url, safe='') + '&format=json'
    with urlopen(endpoint, timeout=timeout) as response:
        data = json.load(response)
    if not isinstance(data, dict):
        raise ValueError('Unexpected YouTube metadata response')
    title = data.get('title')
    uploader = data.get('author_name')
    if title is not None and not isinstance(title, str):
        raise ValueError('Unexpected YouTube title metadata')
    if uploader is not None and not isinstance(uploader, str):
        raise ValueError('Unexpected YouTube uploader metadata')
    return {'title': title, 'uploader': uploader}


def load_settings(path=None, overrides=None):
    existing_python = Path.home() / 'venvs/funasr-cpu/bin/python'
    settings = {'asr_python': str(existing_python) if existing_python.is_file() else sys.executable,
                'model_path': None, 'vad_path': None, 'punc_path': None,
                'jobs_root': str(Path.home() / 'transcription_jobs'), 'threads': 2,
                'chunk_seconds': 60, 'browser_timeout': 180, 'asr_timeout': 21600,
                'max_audio_bytes': 512 * 1024 * 1024, 'site': 'auto'}
    config = Path(path or os.environ.get('TRANSCRIPTION_CONFIG', DEFAULT_CONFIG)).expanduser()
    if config.exists():
        values = json.loads(config.read_text(encoding='utf-8'))
        if not isinstance(values, dict) or set(values) - set(settings):
            raise ValueError('Unknown configuration keys or non-object configuration')
        settings.update(values)
    settings.update({k: v for k, v in (overrides or {}).items() if v is not None})
    for key in ('asr_python', 'model_path', 'vad_path', 'punc_path', 'jobs_root'):
        if settings[key]:
            expanded = Path(settings[key]).expanduser()
            settings[key] = os.path.abspath(expanded) if key == 'asr_python' else str(expanded.resolve())
    for key, low, high in [('threads', 1, 64), ('chunk_seconds', 1, 300),
                           ('browser_timeout', 10, 600), ('asr_timeout', 30, 172800),
                           ('max_audio_bytes', 1024, 10 * 1024**3)]:
        if type(settings[key]) is not int or not low <= settings[key] <= high:
            raise ValueError(f'Invalid {key}: expected integer {low}..{high}')
    if settings['site'] not in ('auto', 'tuberipper', 'onlymp3'):
        raise ValueError('Unknown converter site')
    return settings


def preflight_asr(settings):
    for key in ('model_path', 'vad_path', 'punc_path'):
        path = settings.get(key)
        if key == 'model_path' and not path:
            raise ValueError('Missing model_path: configure a trusted local FunASR snapshot first; no automatic model download')
        if path and (not (Path(path) / 'model.pt').is_file() or (Path(path) / 'model.pt').stat().st_size == 0):
            raise ValueError(f'{key} has no complete model.pt: {path}')
    python = settings['asr_python']
    if not Path(python).is_file():
        raise ValueError(f'ASR Python not found: {python}')
    code = 'from funasr import AutoModel'
    if settings.get('punc_path'):
        code += '; from modelscope.pipelines import pipeline'
    checked = subprocess.run([python, '-c', code], capture_output=True, text=True, timeout=60)
    if checked.returncode:
        raise ValueError('ASR imports failed in selected environment: ' + checked.stderr[-2000:])


@contextmanager
def pipeline_lock(job):
    with (job / '.pipeline.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('Another YouTube pipeline is using this job') from None
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def run_pipeline(url, job, settings, download_only=False, title=None, downloader=None, infer=None,
                 metadata_fetcher=None):
    video_id = youtube_id(url)
    downloader = downloader or worker_download
    infer = infer or run_asr
    metadata_fetcher = metadata_fetcher or youtube_metadata
    metadata = {}
    if not title:
        try:
            metadata = metadata_fetcher(url) or {}
            if not isinstance(metadata, dict):
                raise ValueError('Unexpected YouTube metadata payload')
        except Exception:
            metadata = {}
    job = Path(job).expanduser().resolve()
    if not job.exists():
        core.create_job(job, core.make_entries([(video_id, video_id + '.mp3')]), 'youtube:' + url)
    with pipeline_lock(job):
        with core.job_lock(job):
            state = core.read_job(job)
            if (not state['source'].startswith('youtube:') or youtube_id(state['source'][8:]) != video_id
                    or len(state['entries']) != 1 or state['entries'][0]['id'] != video_id):
                raise ValueError('Job belongs to a different source; choose another job directory')
            state['source_type'] = 'youtube'
            entry = state['entries'][0]
            media = core.job_path(job, 'media', entry['name'])
            ready = False
            if media.exists():
                if core.sha256(media) != entry.get('media_sha256'):
                    raise ValueError('Existing audio changed or is untracked; refusing to overwrite')
                core.probe(media)
                if title and title_filename(title) != entry['name']:
                    raise ValueError('Title changed for existing job; choose another job directory')
                entry['download'] = 'done'
                entry.pop('download_error', None)
                ready = True
            if not ready:
                sites = ['tuberipper', 'onlymp3'] if settings['site'] == 'auto' else [settings['site']]
                attempts = state.setdefault('browser_attempts', [])
                for site in sites:
                    try:
                        with tempfile.TemporaryDirectory(prefix='.youtube-', dir=job) as tmp:
                            partial = Path(tmp) / 'audio.mp3'
                            meta = downloader(url, partial, site, settings)
                            if not partial.is_file() or partial.stat().st_size > settings['max_audio_bytes']:
                                raise ValueError('Audio missing or exceeds configured size limit')
                            duration = core.probe(partial)
                            digest = core.sha256(partial)
                            if entry.get('media_sha256') and digest != entry['media_sha256']:
                                raise ValueError('Reconverted audio bytes changed; use a new job rather than reusing old TXT')
                            suggested = meta.get('suggested_filename', '')
                            if Path(suggested).suffix.lower() != '.mp3' or meta.get('provider') != site:
                                raise ValueError('Converter returned unexpected filename or provider')
                            video_title = title or metadata.get('title') or meta.get('title') or Path(suggested).stem
                            uploader = None if title else metadata.get('uploader')
                            filename = title_filename(combined_title(video_title, uploader))
                            if entry.get('media_sha256'):
                                filename = entry['name']
                            target = core.job_path(job, 'media', filename)
                            target.parent.mkdir(parents=True, exist_ok=True)
                            if target.exists():
                                raise ValueError('Refusing to replace an untracked media file')
                            os.replace(partial, target)
                            entry.update(name=filename, original_title=video_title,
                                         uploader=uploader, suggested_filename=suggested,
                                         provider=site, download='done', media_sha256=digest,
                                         duration_seconds=duration)
                            entry.pop('download_error', None)
                            attempts.append({'provider': site, 'status': 'downloaded'})
                            ready = True
                            core.atomic_json(job / 'manifest.json', state)
                            break
                    except Exception as exc:
                        message = str(exc)[:2000]
                        attempts.append({'provider': site, 'status': 'error', 'error': message})
                        entry['download'] = 'error'
                        entry['download_error'] = message
                        core.atomic_json(job / 'manifest.json', state)
                if not ready:
                    raise RuntimeError(f'All selected converters failed; inspect {job / "manifest.json"}')
            core.atomic_json(job / 'manifest.json', state)
        if not download_only:
            try:
                code = infer(job, settings)
                if code:
                    raise RuntimeError(f'FunASR failed (exit {code}); inspect {job / "asr.log"}')
            except Exception as exc:
                with core.job_lock(job):
                    latest = core.read_job(job)
                    latest['entries'][0]['transcription'] = 'error'
                    latest['entries'][0]['transcription_error'] = str(exc)[:2000]
                    core.atomic_json(job / 'manifest.json', latest)
                raise
        report = core.status(job)
        report['download_only'] = download_only
        return report


def run_child(command, timeout, log_path=None):
    log = None
    if log_path:
        log_path = Path(log_path)
        if log_path.is_symlink():
            raise ValueError('Refusing to follow a subprocess log symlink')
        log = log_path.open('a', encoding='utf-8')
    process = None
    try:
        process = subprocess.Popen(command, stdout=log or subprocess.PIPE,
                                   stderr=subprocess.STDOUT if log else subprocess.PIPE,
                                   text=True, start_new_session=True)
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except BaseException as exc:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                pass
            finally:
                # The direct child may exit while descendants ignore SIGTERM.
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.communicate()
            if isinstance(exc, subprocess.TimeoutExpired):
                raise TimeoutError(f'Child process exceeded {timeout}s; process group stopped') from None
            raise
        return subprocess.CompletedProcess(command, process.returncode, stdout or '', stderr or '')
    finally:
        if log:
            log.close()


def worker_download(url, output, site, settings):
    command = [sys.executable, str(ROOT / 'scripts/youtube_browser.py'), '--url', url,
               '--output', str(output), '--site', site, '--timeout', str(settings['browser_timeout'])]
    result = run_child(command, settings['browser_timeout'] + 15)
    if result.returncode:
        # Worker exceptions can contain temporary signed download URLs. Do not persist query tokens.
        error = re.sub(r'https?://[^\s\"\']+', '<website-url>', result.stderr.strip()[-2000:])
        raise RuntimeError(error or f'{site} browser worker exited {result.returncode}')
    meta = json.loads(result.stdout)
    if not isinstance(meta, dict):
        raise ValueError('Browser worker returned invalid metadata')
    return meta


def run_asr(job, settings):
    command = [settings['asr_python'], str(ROOT / 'scripts/media_transcribe.py'), 'transcribe',
               '--job', str(job), '--model-path', settings['model_path'],
               '--threads', str(settings['threads']), '--chunk-seconds', str(settings['chunk_seconds'])]
    for key, flag in [('vad_path', '--vad-path'), ('punc_path', '--punc-path')]:
        if settings.get(key):
            command.extend([flag, settings[key]])
    return run_child(command, settings['asr_timeout'], Path(job) / 'asr.log').returncode


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('url', nargs='?', help='YouTube watch, short-link, Shorts, or live URL')
    parser.add_argument('--config', default=None, help='Local JSON config; default config.local.json')
    parser.add_argument('--configure', action='store_true', help='Validate and save one-time local settings')
    parser.add_argument('--job', help='Default: configured jobs_root/youtube-VIDEO_ID; reused on rerun')
    parser.add_argument('--title', help='Override filename title; original title normally comes from converter')
    parser.add_argument('--site', choices=['auto', 'tuberipper', 'onlymp3'])
    parser.add_argument('--download-only', action='store_true', help='Save verified MP3 without invoking ASR')
    for name in ('asr-python', 'model-path', 'vad-path', 'punc-path', 'jobs-root'):
        parser.add_argument('--' + name)
    for name in ('threads', 'chunk-seconds', 'browser-timeout', 'asr-timeout'):
        parser.add_argument('--' + name, type=int)
    args = parser.parse_args(argv)
    try:
        keys = ('asr_python', 'model_path', 'vad_path', 'punc_path', 'jobs_root', 'threads',
                'chunk_seconds', 'browser_timeout', 'asr_timeout', 'site')
        settings = load_settings(args.config, {k: getattr(args, k) for k in keys})
        if args.configure:
            if args.url or args.download_only:
                raise ValueError('--configure cannot be combined with a URL or --download-only')
            preflight_asr(settings)
            config = Path(args.config or os.environ.get('TRANSCRIPTION_CONFIG', DEFAULT_CONFIG)).expanduser()
            core.atomic_json(config, settings)
            print(json.dumps({'configured': str(config.resolve()), 'note': 'Imports verified; real inference still needs a media test.'}))
            return 0
        if not args.url:
            parser.error('Provide a YouTube URL or --configure')
        video_id = youtube_id(args.url)
        if not args.download_only:
            preflight_asr(settings)
        job = Path(args.job).expanduser() if args.job else Path(settings['jobs_root']) / ('youtube-' + video_id)
        report = run_pipeline(args.url, job, settings, args.download_only, args.title)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report['complete'] or (args.download_only and report['verified_media'] == 1) else 1
    except Exception as exc:
        print(json.dumps({'error': str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
