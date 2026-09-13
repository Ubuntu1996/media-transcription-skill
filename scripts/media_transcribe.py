#!/usr/bin/env python3
"""Public Drive media to filename-preserving TXT; no implicit uploads."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tempfile
from urllib.parse import parse_qs, urlparse

MEDIA = {'.mp3', '.mp4', '.m4a', '.wav', '.webm', '.mkv', '.mov', '.ogg', '.flac', '.aac', '.opus', '.wma'}


def safe_name(name):
    if not isinstance(name, str) or not name or '\\' in name or ':' in name:
        raise ValueError(f'Unsafe filename: {name!r}')
    if any(ord(c) < 32 for c in name) or any(p in ('', '.', '..') for p in name.split('/')) or name.startswith('/'):
        raise ValueError(f'Unsafe filename: {name!r}')
    return PurePosixPath(name)


def make_entries(items):
    entries, outputs, ids = [], set(), set()
    sources = set()
    for file_id, name in items:
        p = safe_name(name)
        if p.suffix.lower() not in MEDIA:
            continue
        output_key = str(p.with_suffix('.txt')).casefold()
        if output_key in outputs or any(output_key.startswith(key + '/') or key.startswith(output_key + '/')
                                        for key in outputs):
            raise ValueError(f'TXT filename collision: {name}; split into separate jobs')
        source_key = str(p).casefold()
        if any(source_key.startswith(key + '/') or key.startswith(source_key + '/') for key in sources):
            raise ValueError(f'Source filename collision: {name}; split into separate jobs')
        if file_id in ids:
            raise ValueError(f'Duplicate source ID: {file_id}')
        outputs.add(output_key)
        sources.add(source_key)
        ids.add(file_id)
        entries.append({'id': file_id, 'name': str(p), 'download': 'pending', 'transcription': 'pending'})
    if not entries:
        raise ValueError('No supported media found; do not assume an inaccessible folder is empty')
    return entries


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='.progress-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(value, f, ensure_ascii=False, indent=2)
            f.write('\n')
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def create_job(job, entries, source):
    job = Path(job)
    job.mkdir(parents=True, exist_ok=False)
    atomic_json(job / 'manifest.json', {'schema': 1, 'source': source, 'entries': entries})


def read_job(job):
    data = json.loads((Path(job) / 'manifest.json').read_text(encoding='utf-8'))
    if data.get('schema') != 1:
        raise ValueError('Unsupported manifest schema')
    make_entries([(e['id'], e['name']) for e in data['entries']])
    return data


@contextmanager
def job_lock(job):
    job = Path(job)
    if not (job / 'manifest.json').is_file():
        raise ValueError('Missing manifest.json; create an inventory first')
    with (job / '.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('Another process is using this job') from None
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def job_path(job, area, name):
    safe_name(name)
    root = Path(job).resolve()
    path = root / area / name
    for node in (path, *path.parents):
        if node == root:
            break
        if node.is_symlink():
            raise ValueError(f'Symlink not allowed inside job: {node}')
    if not path.resolve().is_relative_to(root):
        raise ValueError('Path escapes job')
    return path


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def probe(path):
    result = subprocess.run(['ffprobe', '-v', 'error', '-show_streams', '-show_format',
                             '-of', 'json', str(path)], capture_output=True, text=True, timeout=60)
    if result.returncode:
        raise ValueError('ffprobe rejected input: not a readable media file')
    info = json.loads(result.stdout)
    if not any(s.get('codec_type') == 'audio' for s in info.get('streams', [])):
        raise ValueError('Media has no audio stream')
    duration = float(info.get('format', {}).get('duration', 0))
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError('Media duration must be finite and positive')
    return duration


def parse_drive_url(url):
    parsed = urlparse(url)
    if parsed.scheme != 'https' or parsed.netloc != 'drive.google.com':
        raise ValueError('Expected an HTTPS drive.google.com URL')
    folder = re.fullmatch(r'/drive/(?:u/\d+/)?folders/([A-Za-z0-9_-]+)/?', parsed.path)
    file = re.fullmatch(r'/file/d/([A-Za-z0-9_-]+)(?:/view|/preview)?/?', parsed.path)
    if folder:
        return 'folder', folder[1]
    if file:
        return 'file', file[1]
    if parsed.path in ('/open', '/uc'):
        ids = parse_qs(parsed.query).get('id', [])
        if len(ids) == 1 and re.fullmatch(r'[A-Za-z0-9_-]+', ids[0]):
            return 'file', ids[0]
    raise ValueError('Unrecognized Drive URL; do not repair malformed IDs')


def inventory(url, job, name=None, expected_count=None):
    kind, file_id = parse_drive_url(url)
    if kind == 'file':
        if not name:
            raise ValueError('Single-file links require --name with the verified original filename')
        items = [(file_id, name)]
    else:
        if name:
            raise ValueError('--name is only for single-file links')
        import gdown
        files = gdown.download_folder(id=file_id, output='inventory', skip_download=True,
                                     use_cookies=False, remaining_ok=False, quiet=True)
        if not files:
            raise ValueError('Folder inaccessible or empty; no job created')
        items = [(f.id, f.path) for f in files]
    entries = make_entries(items)
    if expected_count is not None and len(entries) != expected_count:
        raise ValueError(f'Expected {expected_count} media files, found {len(entries)}')
    create_job(job, entries, url)
    return {'media_count': len(entries), 'ignored_count': len(items) - len(entries),
            'names': [e['name'] for e in entries]}


def drive_download(file_id, output):
    if not re.fullmatch(r'[A-Za-z0-9_-]+', file_id):
        raise ValueError('Invalid Drive file ID in manifest')
    import gdown
    return gdown.download(id=file_id, output=str(output), use_cookies=False,
                          quiet=False, resume=False, verify=True)


def download_job(job, downloader=drive_download):
    job = Path(job)
    with job_lock(job):
        state = read_job(job)
        failures = 0
        for entry in state['entries']:
            try:
                target = job_path(job, 'media', entry['name'])
                target.parent.mkdir(parents=True, exist_ok=True)
                if target.exists():
                    if entry.get('media_sha256') != sha256(target):
                        raise ValueError('Existing media changed or is untracked; use a new job, do not overwrite')
                    probe(target)
                else:
                    with tempfile.TemporaryDirectory(prefix='.download-', dir=target.parent) as tmp:
                        partial = Path(tmp) / 'source.part'
                        if not downloader(entry['id'], partial) or not partial.is_file():
                            raise ValueError('Download did not produce a file')
                        duration = probe(partial)
                        entry['media_sha256'] = sha256(partial)
                        entry['duration_seconds'] = duration
                        os.replace(partial, target)
                entry['download'] = 'done'
                entry.pop('download_error', None)
            except Exception as exc:
                entry['download'] = 'error'
                entry['download_error'] = str(exc)
                failures += 1
            atomic_json(job / 'manifest.json', state)
        return 1 if failures else 0


def import_local(source, job):
    source = Path(source).expanduser().resolve()
    if not source.exists():
        raise ValueError('Local source does not exist')
    files = [source] if source.is_file() else sorted(p for p in source.rglob('*') if p.is_file() and not p.is_symlink())
    root = source.parent if source.is_file() else source
    entries = make_entries([(str(p), p.relative_to(root).as_posix()) for p in files])
    create_job(job, entries, 'local:' + str(source))
    def copy_file(file_id, output):
        shutil.copyfile(file_id, output)
        return str(output)
    return download_job(job, copy_file)


def result_text(result):
    if isinstance(result, list):
        if not result:
            return ''
        return '\n'.join(result_text(item) for item in result).strip()
    if not isinstance(result, dict):
        raise ValueError('Unexpected model result; expected dict or list of dicts')
    text = result.get('text', result.get('output'))
    if not isinstance(text, str):
        raise ValueError('Model did not return a text string')
    return text.strip()


class FunASRBackend:
    def __init__(self, config):
        paths = {}
        for key in ('model_path', 'vad_path', 'punc_path'):
            value = config.get(key)
            if value:
                path = Path(value).expanduser().resolve()
                weights = path / 'model.pt'
                if not weights.is_file() or weights.stat().st_size == 0:
                    raise ValueError(f'{key} requires a complete local snapshot with model.pt: {path}')
                paths[key] = str(path)
        if 'model_path' not in paths:
            raise ValueError('A local ASR model is required')
        import torch
        from funasr import AutoModel
        from funasr.utils.postprocess_utils import rich_transcription_postprocess
        torch.set_num_threads(config['threads'])
        kwargs = {'model': paths['model_path'], 'device': 'cpu', 'disable_update': True,
                  'trust_remote_code': False, 'ncpu': config['threads']}
        if paths.get('vad_path'):
            kwargs['vad_model'] = paths['vad_path']
        self.model = AutoModel(**kwargs)
        self.normalize = rich_transcription_postprocess
        self.punc_path = paths.get('punc_path')
        self.punc = None

    def recognize(self, path):
        text = result_text(self.model.generate(input=str(path), batch_size_s=60))
        return self.normalize(text).strip() if text else ''

    def punctuate(self, text):
        if self.punc is None:
            from modelscope.pipelines import pipeline
            from modelscope.utils.constant import Tasks
            self.punc = pipeline(task=Tasks.punctuation, model=self.punc_path, device='cpu')
        parts = []
        for start in range(0, len(text), 360):
            chunk = result_text(self.punc(text[start:start + 360]))
            if not chunk:
                raise ValueError('Punctuation model returned empty text')
            parts.append(chunk)
        return ''.join(parts)


def atomic_text(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='.transcript-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(text.strip() + '\n')
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def transcribe_job(job, config, backend_factory=FunASRBackend):
    if not 1 <= config['chunk_seconds'] <= 300 or not 1 <= config['threads'] <= 64:
        raise ValueError('chunk_seconds must be 1..300 and threads must be 1..64')
    job = Path(job)
    backend = None
    with job_lock(job):
        state = read_job(job)
        failures = 0
        for entry in state['entries']:
            try:
                source = job_path(job, 'media', entry['name'])
                stem = str(PurePosixPath(entry['name']).with_suffix('.txt'))
                output = job_path(job, 'txt', stem)
                raw = job_path(job, 'raw', stem)
                if entry['download'] != 'done' or not source.is_file():
                    raise ValueError('Download/import must complete first')
                if sha256(source) != entry.get('media_sha256'):
                    raise ValueError('Source media changed; use a new job')
                if output.exists():
                    if (entry.get('txt_sha256') == sha256(output) and entry.get('config') == config
                            and entry.get('transcribed_media_sha256') == entry.get('media_sha256')):
                        entry['transcription'] = 'done'
                        entry.pop('transcription_error', None)
                        continue
                    raise ValueError('Existing TXT edited, untracked, or model settings changed; use a new job')
                if raw.exists() and (entry.get('raw_sha256') != sha256(raw) or entry.get('config') != config
                                     or entry.get('raw_media_sha256') != entry.get('media_sha256')):
                    raise ValueError('Existing raw TXT changed or uses other settings; use a new job')
                duration = probe(source)
                entry['transcription'] = 'running'
                entry['config'] = config
                atomic_json(job / 'manifest.json', state)
                if backend is None:
                    backend = backend_factory(config)
                if raw.exists():
                    text = raw.read_text(encoding='utf-8').strip()
                else:
                    parts = []
                    chunk_seconds = config['chunk_seconds']
                    total = math.ceil(duration / chunk_seconds)
                    with tempfile.TemporaryDirectory(prefix='.audio-', dir=job) as tmp:
                        wav = Path(tmp) / 'chunk.wav'
                        for index in range(total):
                            cmd = ['ffmpeg', '-nostdin', '-v', 'error', '-y', '-ss', str(index * chunk_seconds),
                                   '-i', str(source), '-t', str(chunk_seconds), '-map', '0:a:0',
                                   '-vn', '-ac', '1', '-ar', '16000', '-c:a', 'pcm_s16le', str(wav)]
                            subprocess.run(cmd, check=True, capture_output=True, timeout=300)
                            part = backend.recognize(wav)
                            if not isinstance(part, str):
                                raise ValueError('ASR returned a non-string')
                            if part.strip():
                                parts.append(part.strip())
                            entry['chunks_done'] = index + 1
                            entry['chunks_total'] = total
                            atomic_json(job / 'manifest.json', state)
                    text = '\n'.join(parts).strip()
                    if not text:
                        raise ValueError('No speech text recognized; empty output is not success')
                    atomic_text(raw, text)
                    entry['raw_sha256'] = sha256(raw)
                    entry['raw_media_sha256'] = entry['media_sha256']
                    atomic_json(job / 'manifest.json', state)
                final = backend.punctuate(text) if config.get('punc_path') else text
                if not isinstance(final, str) or not final.strip():
                    raise ValueError('Final transcript is empty or invalid')
                atomic_text(output, final)
                entry['txt_sha256'] = sha256(output)
                entry['transcribed_media_sha256'] = entry['media_sha256']
                entry['transcription'] = 'done'
                entry['punctuation'] = 'model' if config.get('punc_path') else 'not_requested'
                entry['characters'] = len(final.strip())
                entry.pop('transcription_error', None)
            except Exception as exc:
                entry['transcription'] = 'error'
                entry['transcription_error'] = str(exc)
                failures += 1
            finally:
                atomic_json(job / 'manifest.json', state)
        return 1 if failures else 0


def status(job):
    state = read_job(job)
    rows = []
    for entry in state['entries']:
        row = {'name': entry['name'], 'download': entry['download'], 'transcription': entry['transcription'],
               'media_verified': False, 'txt_verified': False}
        try:
            media = job_path(job, 'media', entry['name'])
            txt = job_path(job, 'txt', str(PurePosixPath(entry['name']).with_suffix('.txt')))
            row['media_verified'] = media.is_file() and sha256(media) == entry.get('media_sha256')
            row['txt_verified'] = (txt.is_file() and txt.stat().st_size > 0
                                   and sha256(txt) == entry.get('txt_sha256')
                                   and entry.get('transcribed_media_sha256') == entry.get('media_sha256'))
        except (ValueError, OSError) as exc:
            row['verification_error'] = str(exc)
        for key in ('download_error', 'transcription_error', 'chunks_done', 'chunks_total', 'punctuation'):
            if key in entry:
                row[key] = entry[key]
        rows.append(row)
    complete = all(r['media_verified'] and r['txt_verified'] and r['download'] == 'done' and r['transcription'] == 'done' for r in rows)
    return {'job': str(Path(job).resolve()), 'total': len(rows),
            'verified_media': sum(r['media_verified'] for r in rows),
            'verified_transcripts': sum(r['txt_verified'] for r in rows), 'complete': complete, 'files': rows}


def doctor():
    import importlib.util
    return {'python': sys.version.split()[0], 'platform': sys.platform,
            'ffmpeg': shutil.which('ffmpeg'), 'ffprobe': shutil.which('ffprobe'),
            'packages': {p: importlib.util.find_spec(p) is not None for p in ('gdown', 'funasr', 'modelscope', 'torch')},
            'note': 'Package presence is not an import or real-model inference test. Local model.pt snapshots are required.'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('doctor', help='Report tools and package presence; no downloads')
    inv = sub.add_parser('inventory', help='Inventory a public Drive URL without downloading media')
    inv.add_argument('--url', required=True)
    inv.add_argument('--name', help='Verified original filename, required for single-file URLs')
    inv.add_argument('--expected-count', type=int)
    local = sub.add_parser('local', help='Copy and verify local media in a NEW job')
    local.add_argument('--input', required=True)
    dl = sub.add_parser('download', help='Download/retry pending Drive files in an existing job')
    tr = sub.add_parser('transcribe', help='Transcribe with local CPU models; resume completed files')
    tr.add_argument('--model-path', required=True)
    tr.add_argument('--vad-path')
    tr.add_argument('--punc-path')
    tr.add_argument('--chunk-seconds', type=int, default=60)
    tr.add_argument('--threads', type=int, default=2)
    st = sub.add_parser('status', help='Verify media/TXT hashes; nonzero exit until complete')
    for command in (inv, local, dl, tr, st):
        command.add_argument('--job', required=True)
    args = parser.parse_args(argv)
    try:
        code = 0
        if args.command == 'doctor':
            report = doctor()
            code = 0 if report['ffmpeg'] and report['ffprobe'] else 1
        else:
            job = Path(args.job).expanduser().resolve()
            if args.command == 'inventory':
                report = inventory(args.url, job, args.name, args.expected_count)
            elif args.command == 'local':
                code = import_local(args.input, job)
                report = status(job)
            elif args.command == 'download':
                code = download_job(job)
                report = status(job)
            elif args.command == 'transcribe':
                config = {k: str(Path(getattr(args, k)).expanduser().resolve()) if getattr(args, k) else None
                          for k in ('model_path', 'vad_path', 'punc_path')}
                config.update(chunk_seconds=args.chunk_seconds, threads=args.threads)
                code = transcribe_job(job, config)
                report = status(job)
            else:
                report = status(job)
                code = 0 if report['complete'] else 1
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return code
    except Exception as exc:
        print(json.dumps({'error': str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
