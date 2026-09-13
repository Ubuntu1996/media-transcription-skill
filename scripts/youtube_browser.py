"""Isolated Playwright MP3 worker.

TubeRipper uses observed /74/ markup and same-context direct download navigation.
OnlyMP3's semantic adapter is UNVERIFIED on a live accessible converter: the
observed exact source URL currently presents a security challenge. Never solve
or bypass challenges. Parent owns process-group termination/overall timeout.
"""
import argparse
import sys
import json
import math
import re
from pathlib import Path
import subprocess
import tempfile
import time
from urllib.parse import urlsplit


def trusted_url(url, domain):
    try:
        parts = urlsplit(url)
        return (parts.scheme == 'https' and not parts.username and not parts.password
                and parts.port in (None, 443) and bool(parts.hostname)
                and (parts.hostname == domain or parts.hostname.endswith('.' + domain)))
    except (ValueError, TypeError):
        return False

from playwright.sync_api import Error as PlaywrightError, TimeoutError as PlaywrightTimeoutError, sync_playwright

TUBERIPPER_URL = 'https://tuberipper.cc/74/'
ONLYMP3_URL = 'https://en.onlymp3.to/converter-v2?utm_source=convert-more'


class DownloadError(Exception):
    def __init__(self, message, kind='download_failed'):
        super().__init__(message)
        self.kind = kind


def youtube_video_id(url):
    """Validate the original token without normalizing or decoding the ID."""
    try:
        if not isinstance(url, str) or re.search(r'[\s\\\x00-\x1f\x7f]', url):
            raise ValueError()
        p = urlsplit(url)
        if (p.scheme != 'https' or p.username or p.password or p.port not in (None, 443)
                or p.hostname not in {'youtube.com', 'www.youtube.com', 'm.youtube.com',
                                      'music.youtube.com', 'youtu.be'}):
            raise ValueError()
        if p.hostname == 'youtu.be':
            video_id = p.path[1:]
        elif p.path == '/watch':
            ids = [item[2:] for item in p.query.split('&') if item.startswith('v=')]
            if len(ids) != 1:
                raise ValueError()
            video_id = ids[0]
        else:
            match = re.fullmatch(r'/(?:shorts|embed|live)/([A-Za-z0-9_-]{11})', p.path)
            video_id = match.group(1) if match else ''
        if not re.fullmatch(r'[A-Za-z0-9_-]{11}', video_id):
            raise ValueError()
        return video_id
    except (TypeError, ValueError):
        raise DownloadError('Expected an HTTPS YouTube URL with one valid 11-character video ID', 'invalid_url') from None


def check_blocked(page):
    text = (page.title() + '\n' + page.locator('body').inner_text(timeout=2000)).lower()
    if any(marker in text for marker in ('just a moment', 'performing security verification',
                                         'verify you are human', 'checking your browser',
                                         'confirm you are human', 'complete the captcha')):
        raise DownloadError('Provider requires a human security challenge; stopped without interaction', 'challenge')
    if 'unable extract files. error getting video info.' in text:
        raise DownloadError('Unable extract files. Error getting video info.', 'provider_error')


def wait_result(page, locator, remaining):
    while True:
        check_blocked(page)
        count = locator.count()
        if count > 1:
            raise DownloadError('Ambiguous provider result links', 'unsupported_layout')
        if count == 1 and locator.is_visible():
            return
        page.wait_for_timeout(min(200, remaining()))


def onlymp3_result(page, url, remaining):
    """Conservative semantic fallback, NOT a verified live-site adapter."""
    inputs = []
    for box in page.get_by_role('textbox').all():
        if not box.is_visible() or not box.is_editable():
            continue
        description = box.evaluate('''el => [el.getAttribute('aria-label'),
            el.getAttribute('placeholder'), ...Array.from(el.labels || [], x => x.innerText)].join(' ')''')
        if re.search(r'youtube', description, re.I) and re.search(r'url|link', description, re.I):
            inputs.append(box)
    buttons = [b for b in page.get_by_role('button', name=re.compile(r'^\s*Convert(?:\s+(?:to\s+)?MP3)?\s*$', re.I)).all()
               if b.is_visible() and b.is_enabled()]
    if len(inputs) != 1 or len(buttons) != 1:
        raise DownloadError('Cannot safely identify unique OnlyMP3 conversion controls', 'unsupported_layout')
    inputs[0].fill(url, timeout=remaining())
    buttons[0].click(timeout=remaining())
    links = page.get_by_role('link', name=re.compile(r'^\s*Download(?:\s+(?:MP3|Audio))?\s*$', re.I))
    # Unknown accessible layouts are not guessed or followed through ad pages.
    layout_deadline = time.monotonic() + min(10, remaining() / 1000)
    while True:
        check_blocked(page)
        if links.count() > 1:
            raise DownloadError('Ambiguous OnlyMP3 download links', 'unsupported_layout')
        if links.count() == 1 and links.is_visible():
            href = links.get_attribute('href')
            if not trusted_url(href, 'onlymp3.to'):
                raise DownloadError('Untrusted OnlyMP3 download link', 'unsafe_download')
            return None, None, href
        if time.monotonic() >= layout_deadline:
            raise DownloadError('No safely identifiable OnlyMP3 download link', 'unsupported_layout')
        page.wait_for_timeout(min(200, remaining()))


def tuberipper_result(page, url, video_id, remaining):
    for selector in ('#videoUrl', '#videoBtn'):
        control = page.locator(selector)
        if control.count() != 1 or not control.is_visible():
            raise DownloadError('TubeRipper conversion controls are missing or ambiguous', 'unsupported_layout')
    page.locator('#videoUrl').fill(url, timeout=remaining())
    page.locator('#videoBtn').click(timeout=remaining())
    link = page.locator('#output a.js-download').filter(has_text=re.compile(r'^\s*Extract Audio\s*$'))
    wait_result(page, link, remaining)
    title = page.locator('#output h2').inner_text().strip() or None
    try:
        expected_duration = float(page.locator('#output .duration[data-val]').get_attribute('data-val'))
        if not math.isfinite(expected_duration) or expected_duration <= 0:
            raise ValueError()
    except (ValueError, TypeError):
        raise DownloadError('Missing or invalid provider duration', 'unsupported_layout') from None
    preview = page.locator('#output a.list-image')
    if preview.count() != 1 or youtube_video_id(preview.get_attribute('href')) != video_id:
        raise DownloadError('Provider result does not match the requested video', 'video_mismatch')
    href = link.get_attribute('href')
    if not trusted_url(href, 'tuberipper.cc') or not urlsplit(href).path.startswith('/download/'):
        raise DownloadError('Untrusted provider download link', 'unsafe_download')
    return title, expected_duration, href


def download_audio(url, destination, site, timeout_seconds=180):
    """Save validated audio atomically, returning JSON-compatible metadata."""
    try:
        return _download_audio(url, destination, site, timeout_seconds)
    except PlaywrightTimeoutError:
        raise DownloadError('Browser download timed out', 'timeout') from None
    except PlaywrightError:
        # Playwright call logs can contain signed URLs and tokens.
        raise DownloadError('Browser download failed', 'download_failed') from None


def _download_audio(url, destination, site, timeout_seconds):
    video_id = youtube_video_id(url)
    if (site not in ('tuberipper', 'onlymp3') or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(timeout_seconds) or timeout_seconds <= 0):
        raise DownloadError('Expected a supported site and a positive finite timeout', 'invalid_arguments')
    destination = Path(destination)
    deadline = time.monotonic() + timeout_seconds

    def remaining():
        milliseconds = int((deadline - time.monotonic()) * 1000)
        if milliseconds <= 0:
            raise DownloadError('Browser download timed out', 'timeout')
        return milliseconds

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, timeout=remaining())
        try:
            context = browser.new_context(accept_downloads=True, service_workers='block')
            domain = 'tuberipper.cc' if site == 'tuberipper' else 'onlymp3.to'

            def guard_navigation(route):
                request = route.request
                if request.resource_type == 'document' and not trusted_url(request.url, domain):
                    route.abort('blockedbyclient')
                else:
                    route.fallback()

            context.route('**/*', guard_navigation)

            def close_popup(popup):
                if popup.opener() is not None:
                    popup.close()

            context.on('page', close_popup)
            page = context.new_page()
            page.set_default_timeout(remaining())
            response = page.goto(TUBERIPPER_URL if site == 'tuberipper' else ONLYMP3_URL,
                                 wait_until='domcontentloaded', timeout=remaining())
            check_blocked(page)
            if response and response.status >= 400:
                raise DownloadError('Provider returned HTTP ' + str(response.status), 'provider_error')
            if site == 'tuberipper':
                title, expected_duration, href = tuberipper_result(page, url, video_id, remaining)
            else:
                title, expected_duration, href = onlymp3_result(page, url, remaining)
            download_page = context.new_page()
            with download_page.expect_download(timeout=remaining()) as event:
                try:
                    download_page.goto(href, timeout=remaining())
                except PlaywrightError as exc:
                    if 'Download is starting' not in str(exc):
                        raise
            download = event.value
            if download.failure():
                raise DownloadError('Browser download failed')
            if Path(download.suggested_filename).suffix.lower() != '.mp3':
                raise DownloadError('Provider did not offer an MP3 filename', 'unsafe_download')
            destination.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix='.youtube-', dir=destination.parent) as tmp:
                staged = Path(tmp) / 'audio.mp3'
                download.save_as(staged)
                try:
                    probe = subprocess.run(['ffprobe', '-v', 'error', '-show_format',
                                            '-show_streams', '-of', 'json', str(staged)],
                                           capture_output=True, text=True,
                                           timeout=max(0.1, remaining() / 1000), check=True)
                    data = json.loads(probe.stdout)
                    duration = float(data['format']['duration'])
                    audio = [s for s in data['streams'] if s.get('codec_type') == 'audio']
                    if (data['format']['format_name'] != 'mp3' or len(audio) != 1
                            or audio[0].get('codec_name') != 'mp3'
                            or not math.isfinite(duration) or duration <= 0):
                        raise ValueError('Not MP3 audio')
                except (subprocess.CalledProcessError, ValueError, KeyError, TypeError):
                    raise DownloadError('Downloaded file is not valid MP3 audio', 'invalid_media') from None
                if expected_duration is not None and abs(duration - expected_duration) > max(2.0, expected_duration * 0.05):
                    raise DownloadError('Audio duration does not match provider result', 'duration_mismatch')
                staged.replace(destination)
            return {'title': title, 'suggested_filename': download.suggested_filename,
                    'duration_seconds': duration, 'provider': site}
        finally:
            browser.close()


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        raise DownloadError(message, 'invalid_arguments')


def main(argv=None):
    parser = JsonArgumentParser(description=__doc__)
    parser.add_argument('--url', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--site', required=True, choices=['tuberipper', 'onlymp3'])
    parser.add_argument('--timeout', type=float, default=180)
    try:
        args = parser.parse_args(argv)
        result = download_audio(args.url, args.output, args.site, args.timeout)
    except DownloadError as exc:
        print(json.dumps({'error': str(exc), 'kind': exc.kind}), file=sys.stderr)
        return 1
    except Exception as exc:
        # Avoid signed URL/token leakage from Playwright's verbose call log.
        print(json.dumps({'error': 'Browser worker failed: ' + type(exc).__name__,
                          'kind': 'download_failed'}), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
