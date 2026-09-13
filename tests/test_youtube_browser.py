"""Offline browser tests: real Chromium, routed HTML, synthetic ffmpeg MP3.

No fixture is a captured response or evidence of live provider compatibility.
"""
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from playwright.sync_api import Browser

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'youtube_browser.py'
SOURCE = 'https://www.youtube.com/watch?v=jNQXAC9IVRw&feature=shared'
START = 'https://tuberipper.cc/74/'
SIGNED = 'https://w2.tuberipper.cc/download/0?_k=synthetic-fixture-only'


def load():
    spec = importlib.util.spec_from_file_location('youtube_browser', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class BrowserWorkerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.audio_tmp = tempfile.TemporaryDirectory()
        audio = Path(cls.audio_tmp.name) / 'synthetic.mp3'
        subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
                        'sine=frequency=440:duration=1', '-codec:a', 'libmp3lame',
                        str(audio)], check=True)
        cls.audio = audio.read_bytes()

    @classmethod
    def tearDownClass(cls):
        cls.audio_tmp.cleanup()

    def fixture_html(self, href=SIGNED, video_id='jNQXAC9IVRw', duration='1'):
        return '''<input id="videoUrl"><button id="videoBtn" onclick="
        document.querySelector('#submitted').textContent=document.querySelector('#videoUrl').value;
        document.querySelector('#output').hidden=false">Convert</button>
        <span id="submitted"></span><div id="output" hidden>
        <h2>Synthetic audio title</h2><span class="duration" data-val="%s">00:01</span>
        <a class="list-image" href="https://www.youtube.com/watch?v=%s">Preview</a>
        <a class="js-download" href="%s">Extract Audio</a></div>''' % (duration, video_id, href)

    def routed(self, html=None, audio=None, filename='fixture.mp3', status=200):
        """Install offline responses underneath the worker's navigation guard."""
        html = self.fixture_html() if html is None else html
        audio = self.audio if audio is None else audio
        self.requests = []
        self.submitted = []
        original = Browser.new_context
        owner = self

        def new_context(browser, *args, **kwargs):
            context = original(browser, *args, **kwargs)

            def respond(route):
                url = route.request.url
                owner.requests.append(url)
                if '/download/' in url:
                    owner.submitted.extend(p.locator('#submitted').inner_text()
                                           for p in context.pages if p.url == START)
                    route.fulfill(status=200, body=audio, headers={
                        'Content-Type': 'audio/mpeg',
                        'Content-Disposition': 'attachment; filename="' + filename + '"'})
                elif url == START or url.startswith('https://en.onlymp3.to/'):
                    if status == 0:
                        route.abort('connectionfailed')
                    else:
                        route.fulfill(status=status, content_type='text/html', body=html)
                else:
                    route.abort()
            context.route('**/*', respond)
            return context
        return patch.object(Browser, 'new_context', new_context)

    def test_tuberipper_success_preserves_source_and_metadata(self):
        self.assertTrue(SCRIPT.exists(), 'Missing browser download worker')
        worker = load()
        with tempfile.TemporaryDirectory() as temp, self.routed():
            output = Path(temp) / 'saved.mp3'
            result = worker.download_audio(SOURCE, output, 'tuberipper', timeout_seconds=10)
            self.assertEqual(result['title'], 'Synthetic audio title')
            self.assertEqual(result['provider'], 'tuberipper')
            self.assertEqual(result['suggested_filename'], 'fixture.mp3')
            self.assertAlmostEqual(result['duration_seconds'], 1.0449, delta=.08)
            self.assertEqual(output.read_bytes(), self.audio)
            self.assertIn(SIGNED, self.requests)
            self.assertEqual(self.submitted, [SOURCE])

    def test_untrusted_download_links_never_navigate(self):
        worker = load()
        for href in ['https://evil.example/download/file.mp3',
                     'https://tuberipper.cc.evil.example/download/a',
                     'http://w2.tuberipper.cc/download/a',
                     'https://w2.tuberipper.cc/installer.exe',
                     'https://user:pass@w2.tuberipper.cc/download/a']:
            with self.subTest(href=href), tempfile.TemporaryDirectory() as temp, self.routed(self.fixture_html(href=href)):
                with self.assertRaises(worker.DownloadError) as caught:
                    worker.download_audio(SOURCE, Path(temp) / 'a.mp3', 'tuberipper', 5)
                self.assertEqual(caught.exception.kind, 'unsafe_download')
                self.assertNotIn(href, self.requests)

    def test_youtube_input_and_result_identity_validation(self):
        worker = load()
        invalid = ['https://evil.example/watch?v=jNQXAC9IVRw',
                   'https://youtube.com.evil.example/watch?v=jNQXAC9IVRw',
                   'https://www.youtube.com/watch?v=short',
                   'https://www.youtube.com/watch?v=jNQXAC9IVRw&v=abcdefghijk',
                   'https://user:pw@www.youtube.com/watch?v=jNQXAC9IVRw',
                   'https://www.youtube.com/watch?v=jNQXAC9IVR%77']
        for url in invalid:
            with self.subTest(url=url), tempfile.TemporaryDirectory() as tmp, self.routed():
                with self.assertRaises(worker.DownloadError) as caught:
                    worker.download_audio(url, Path(tmp) / 'a.mp3', 'tuberipper', 5)
                self.assertEqual(caught.exception.kind, 'invalid_url')
                self.assertEqual(self.requests, [])
        with tempfile.TemporaryDirectory() as tmp, self.routed(self.fixture_html(video_id='abcdefghijk')):
            with self.assertRaises(worker.DownloadError) as caught:
                worker.download_audio(SOURCE, Path(tmp) / 'a.mp3', 'tuberipper', 5)
            self.assertEqual(caught.exception.kind, 'video_mismatch')
            self.assertNotIn(SIGNED, self.requests)

    def test_invalid_media_and_duration_never_replace_destination(self):
        worker = load()
        cases = [(b'<html>login required</html>', 'fixture.mp3', '1', 'invalid_media'),
                 (self.audio, 'installer.exe', '1', 'unsafe_download'),
                 (self.audio, 'unknown.bin', '1', 'unsafe_download'),
                 (self.audio, 'fixture.mp3', '100', 'duration_mismatch')]
        for body, name, duration, kind in cases:
            with self.subTest(name=name, kind=kind), tempfile.TemporaryDirectory() as tmp, self.routed(self.fixture_html(duration=duration), body, name):
                output = Path(tmp) / 'a.mp3'
                output.write_bytes(b'existing file')
                with self.assertRaises(worker.DownloadError) as caught:
                    worker.download_audio(SOURCE, output, 'tuberipper', 5)
                self.assertEqual(caught.exception.kind, kind)
                self.assertEqual(output.read_bytes(), b'existing file')
                self.assertEqual(list(Path(tmp).iterdir()), [output])

    def test_challenge_and_visible_provider_error_stop_without_conversion(self):
        worker = load()
        cases = [('onlymp3', '<title>Just a moment...</title><p>Performing security verification</p>', 403, 'challenge'),
                 ('tuberipper', '<title>Just a moment...</title><p>Verify you are human</p>', 200, 'challenge'),
                 ('tuberipper', '<input id="videoUrl"><button id="videoBtn" onclick="document.querySelector(\'p\').hidden=false">Convert</button><p hidden>Unable extract files. Error getting video info.</p>', 200, 'provider_error')]
        for site, html, status, kind in cases:
            with self.subTest(site=site, kind=kind), tempfile.TemporaryDirectory() as tmp, self.routed(html, status=status):
                import time
                started = time.monotonic()
                with self.assertRaises(worker.DownloadError) as caught:
                    worker.download_audio(SOURCE, Path(tmp) / 'a.mp3', site, 5)
                self.assertEqual(caught.exception.kind, kind)
                self.assertLess(time.monotonic() - started, 4)
                self.assertFalse(any('/download/' in u for u in self.requests))
                if site == 'onlymp3':
                    self.assertEqual(self.requests[0], 'https://en.onlymp3.to/converter-v2?utm_source=convert-more')

    def test_ad_document_navigation_is_blocked(self):
        worker = load()
        html = self.fixture_html().replace('document.querySelector(\'#submitted\')',
            'window.open(\'https://advertising.example/popup\');document.querySelector(\'#submitted\')', 1)
        html += '<iframe src="https://advertising.example/frame"></iframe>'
        with tempfile.TemporaryDirectory() as tmp, self.routed(html):
            worker.download_audio(SOURCE, Path(tmp) / 'a.mp3', 'tuberipper', 5)
            self.assertFalse(any('advertising.example' in u for u in self.requests), self.requests)

    def test_onlymp3_semantic_fixture_is_guarded_and_unverified(self):
        worker = load()
        self.assertIn('UNVERIFIED', worker.__doc__)
        html = '''<label>YouTube URL<input type="url"></label>
        <button onclick="document.querySelector('a').hidden=false">Convert</button>
        <a hidden href="https://en.onlymp3.to/download/synthetic">Download MP3</a>'''
        with tempfile.TemporaryDirectory() as tmp, self.routed(html):
            result = worker.download_audio(SOURCE, Path(tmp) / 'a.mp3', 'onlymp3', 5)
            self.assertEqual(result['provider'], 'onlymp3')
            self.assertIsNone(result['title'])
        layouts = ['<input><button>Start now</button>',
                   html.replace('<button', '<label>YouTube URL<input type="url"></label><button', 1),
                   html.replace('</a>', '</a><a href="https://en.onlymp3.to/download/other">Download MP3</a>')]
        for layout in layouts:
            with self.subTest(layout=layout), tempfile.TemporaryDirectory() as tmp, self.routed(layout):
                with self.assertRaises(worker.DownloadError) as caught:
                    worker.download_audio(SOURCE, Path(tmp) / 'a.mp3', 'onlymp3', 3)
                self.assertEqual(caught.exception.kind, 'unsupported_layout')
                self.assertFalse(any('/download/' in u for u in self.requests))

    def test_cli_outputs_one_final_json_and_structured_failures(self):
        worker = load()
        self.assertTrue(hasattr(worker, 'main'), 'Missing JSON worker CLI')
        import contextlib
        import io
        with tempfile.TemporaryDirectory() as tmp, self.routed():
            stdout, stderr = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                code = worker.main(['--url', SOURCE, '--output', str(Path(tmp) / 'a.mp3'),
                                    '--site', 'tuberipper', '--timeout', '5'])
            self.assertEqual(code, 0)
            self.assertEqual(len(stdout.getvalue().splitlines()), 1)
            self.assertEqual(json.loads(stdout.getvalue())['provider'], 'tuberipper')
            self.assertEqual(stderr.getvalue(), '')
        import sys
        for extra in [['--url', 'https://evil.example/', '--output', '/tmp/not-created.mp3', '--site', 'tuberipper'], []]:
            result = subprocess.run([sys.executable, str(SCRIPT), *extra], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(result.stdout, '')
            self.assertIn('kind', json.loads(result.stderr))
            self.assertIn('error', json.loads(result.stderr))

    def test_invalid_options_and_unknown_tuberipper_layout_fail_safely(self):
        worker = load()
        for site, timeout in [('unknown', 3), ('tuberipper', 0), ('tuberipper', float('nan'))]:
            with self.subTest(site=site, timeout=timeout), tempfile.TemporaryDirectory() as tmp, self.routed():
                with self.assertRaises(worker.DownloadError) as caught:
                    worker.download_audio(SOURCE, Path(tmp) / 'a.mp3', site, timeout)
                self.assertEqual(caught.exception.kind, 'invalid_arguments')
                self.assertEqual(self.requests, [])
        for html in ['<p>Unknown layout</p>', self.fixture_html().replace('</div>',
                    '<a class="js-download" href="' + SIGNED + '">Extract Audio</a></div>')]:
            with self.subTest(html=html), tempfile.TemporaryDirectory() as tmp, self.routed(html):
                with self.assertRaises(worker.DownloadError) as caught:
                    worker.download_audio(SOURCE, Path(tmp) / 'a.mp3', 'tuberipper', 2)
                self.assertEqual(caught.exception.kind, 'unsupported_layout')

    def test_timeout_and_http_failure_have_structured_api_errors(self):
        worker = load()
        html = '<input id="videoUrl"><button id="videoBtn">Convert</button>'
        with tempfile.TemporaryDirectory() as tmp, self.routed(html):
            with self.assertRaises(worker.DownloadError) as caught:
                worker.download_audio(SOURCE, Path(tmp) / 'a.mp3', 'tuberipper', 1.5)
            self.assertEqual(caught.exception.kind, 'timeout')
        with tempfile.TemporaryDirectory() as tmp, self.routed('<p>Service unavailable</p>', status=503):
            with self.assertRaises(worker.DownloadError) as caught:
                worker.download_audio(SOURCE, Path(tmp) / 'a.mp3', 'tuberipper', 2)
            self.assertEqual(caught.exception.kind, 'provider_error')

    def test_browser_network_failure_is_structured_and_redacts_tokens(self):
        worker = load()
        html = self.fixture_html().replace("document.querySelector('#submitted')", "location.href='https://tuberipper.cc/broken?token=secret';document.querySelector('#submitted')", 1)
        with tempfile.TemporaryDirectory() as tmp, self.routed(html):
            with self.assertRaises(worker.DownloadError) as caught:
                worker.download_audio(SOURCE, Path(tmp) / 'a.mp3', 'tuberipper', 2)
            self.assertIn(caught.exception.kind, ('download_failed', 'timeout'))
            self.assertNotIn('secret', str(caught.exception))
        with tempfile.TemporaryDirectory() as tmp, self.routed(status=0):
            with self.assertRaises(worker.DownloadError) as caught:
                worker.download_audio(SOURCE, Path(tmp) / 'a.mp3', 'tuberipper', 3)
            self.assertEqual(caught.exception.kind, 'download_failed')


if __name__ == '__main__':
    unittest.main()
