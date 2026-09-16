"""Offline orchestration tests; downloader/ASR are explicit test doubles."""
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
SCRIPT = SCRIPTS / 'youtube_transcribe.py'
sys.path.insert(0, str(SCRIPTS))


def load():
    spec = importlib.util.spec_from_file_location('youtube_runner', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ConfigurationTests(unittest.TestCase):
    def test_url_validation_and_portable_config(self):
        self.assertTrue(SCRIPT.exists(), 'YouTube runner does not exist')
        w = load()
        for url in ['https://youtu.be/jNQXAC9IVRw?t=2', 'https://www.youtube.com/watch?v=jNQXAC9IVRw',
                    'https://youtube.com/shorts/jNQXAC9IVRw']:
            self.assertEqual(w.youtube_id(url), 'jNQXAC9IVRw')
        for url in ['https://youtube.com.evil.test/watch?v=jNQXAC9IVRw', 'file:///etc/passwd',
                    'https://youtube.com/watch?v=bad', 'https://youtube.com/watch?v=jNQXAC9IVRw&v=jNQXAC9IVRw',
                    'https://youtube.com/watch?v=%6ANQXAC9IVRw']:
            with self.subTest(url=url), self.assertRaises(ValueError):
                w.youtube_id(url)
        self.assertEqual(w.title_filename('English / Talk: Lesson 1'), 'English _ Talk_ Lesson 1.mp3')
        self.assertEqual(w.combined_title('Lesson 1', 'Teacher A'), '[Teacher A] Lesson 1')
        self.assertEqual(w.combined_title('[Teacher A] Lesson 1', 'Teacher A'), '[Teacher A] Lesson 1')
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / 'config.json'
            config.write_text(json.dumps({'asr_python': sys.executable, 'model_path': None, 'threads': 2}))
            options = w.load_settings(config)
            self.assertEqual(options['asr_python'], sys.executable)
            with self.assertRaisesRegex(ValueError, 'model'):
                w.preflight_asr(options)

    def test_titles_include_optional_dates_without_duplicate_prefixes(self):
        w = load()
        cases = [
            ('Lesson 1', 'Teacher A', '2024-02-29', '[Teacher A] [2024-02-29] Lesson 1'),
            ('Lesson 1', None, '2024-02-29', '[2024-02-29] Lesson 1'),
            ('Lesson 1', '  ', '2024-02-29', '[2024-02-29] Lesson 1'),
            ('Teacher A', 'Teacher A', '2024-02-29', '[2024-02-29] Teacher A'),
            ('[Teacher A] Lesson 1', 'Teacher A', '2024-02-29', '[Teacher A] [2024-02-29] Lesson 1'),
            ('[teacher   a]  Lesson 1', 'Teacher A', '2024-02-29', '[teacher   a]  [2024-02-29] Lesson 1'),
            ('[Team ] A] Lesson 1', 'Team ] A', '2024-02-29', '[Team ] A] [2024-02-29] Lesson 1'),
            ('[Teacher A] [2024-02-29] Lesson 1', 'Teacher A', '2024-02-29', '[Teacher A] [2024-02-29] Lesson 1'),
            ('Lesson 1', 'Teacher A', None, '[Teacher A] Lesson 1'),
            ('Lesson 1', 'Teacher A', '2024-02-30', '[Teacher A] Lesson 1'),
            ('Lesson 1', None, 'unknown', 'Lesson 1'),
        ]
        for title, uploader, published, expected in cases:
            with self.subTest(title=title, uploader=uploader, published=published):
                self.assertEqual(w.combined_title(title, uploader, published), expected)


class MetadataTests(unittest.TestCase):
    def test_publish_date_normalization(self):
        w = load()
        for value in ['2024-02-29', ' 2024-02-29 ', '2024-02-29T12:30:00Z',
                      '2024-02-29T23:30:00-08:00', '2024-02-29T00:30:00+08:00']:
            with self.subTest(value=value):
                self.assertEqual(w.normalize_publish_date(value), '2024-02-29')
        for value in [None, 42, {}, [], '', 'unknown', '2023-02-29', '2024-02-30',
                      '2024-02-29junk', '2024-02-29T25:00:00Z']:
            with self.subTest(value=value):
                self.assertIsNone(w.normalize_publish_date(value))

    def test_watch_page_publication_date_sources(self):
        w = load()
        cases = [
            ('<meta itemprop="datePublished" content="2024-02-29">', '2024-02-29'),
            ("<META content='2024-02-29T00:30:00&#43;08:00' itemprop='datePublished' />", '2024-02-29'),
            ('<meta itemprop="uploadDate" content="2024-01-01">'
             '<meta itemprop="datePublished" content="2024-02-29">', '2024-02-29'),
            ('<script>var ytInitialPlayerResponse = {"microformat": {"playerMicroformatRenderer": '
             '{"description": {"simpleText": "nested {text}"}, "uploadDate": "2024-01-01", '
             '"publishDate": "2024-02-29T23:30:00-08:00"}}};</script>', '2024-02-29'),
            ('<meta itemprop="datePublished" content="invalid">'
             '<script>{"playerMicroformatRenderer": {"publishDate": "2024-02-29"}}</script>', '2024-02-29'),
            ('<meta itemprop="uploadDate" content="2024-01-01">', None),
            ('<script>{"recommendation": {"publishDate": "2024-01-01"}}</script>', None),
            ('<script>{"playerMicroformatRenderer": {"uploadDate": "2024-01-01"}}</script>', None),
            ('<script>{"playerMicroformatRenderer": {broken JSON}}</script>', None),
            ('<script>{"playerMicroformatRenderer": []}</script>', None),
            ('<meta itemprop="datePublished" content="2024-02-30">', None),
            ('<meta itemprop="datePublished">', None),
            ('<p>Sign in to confirm you are not a bot</p>', None),
        ]
        for html, expected in cases:
            with self.subTest(html=html):
                self.assertEqual(w.watch_publish_date(html), expected)

    def test_oembed_and_watch_metadata_are_combined(self):
        w = load()
        for url in ['https://youtu.be/jNQXAC9IVRw?t=2', 'https://youtube.com/shorts/jNQXAC9IVRw',
                    'https://www.youtube.com/live/jNQXAC9IVRw']:
            responses = [io.BytesIO(b'{"title": "Lesson 1", "author_name": "Teacher A"}'),
                         io.BytesIO(b'<meta itemprop="datePublished" content="2024-02-29T12:30:00Z">')]
            with self.subTest(url=url), patch.object(w, 'urlopen', side_effect=responses) as fetch:
                self.assertEqual(w.youtube_metadata(url, timeout=7),
                                 {'title': 'Lesson 1', 'uploader': 'Teacher A', 'publish_date': '2024-02-29'})
                self.assertEqual(fetch.call_count, 2)
                fetch.assert_any_call('https://www.youtube.com/oembed?url=' + w.quote(url, safe='') + '&format=json', timeout=7)
                fetch.assert_any_call('https://www.youtube.com/watch?v=jNQXAC9IVRw', timeout=7)

    def test_optional_metadata_sources_fail_independently(self):
        w = load()
        oembed = b'{"title": "Lesson 1", "author_name": "Teacher A"}'
        watch = b'<meta itemprop="datePublished" content="2024-02-29">'
        title_only = {'title': 'Lesson 1', 'uploader': 'Teacher A', 'publish_date': None}
        date_only = {'title': None, 'uploader': None, 'publish_date': '2024-02-29'}
        cases = [
            (oembed, TimeoutError('watch unavailable'), title_only),
            (oembed, w.HTTPException('watch read failed'), title_only),
            (oembed, b'<p>Sign in to confirm you are not a bot</p>', title_only),
            (TimeoutError('oEmbed unavailable'), watch, date_only),
            (b'not JSON', watch, date_only),
            (b'[]', watch, date_only),
            (b'{"title": 42}', watch, date_only),
            (b'{"author_name": []}', watch, date_only),
            (TimeoutError('oEmbed unavailable'), TimeoutError('watch unavailable'),
             {'title': None, 'uploader': None, 'publish_date': None}),
        ]
        for first, second, expected in cases:
            responses = [value if isinstance(value, Exception) else io.BytesIO(value) for value in (first, second)]
            with self.subTest(first=first, second=second), patch.object(w, 'urlopen', side_effect=responses):
                self.assertEqual(w.youtube_metadata('https://youtu.be/jNQXAC9IVRw'), expected)

    def test_invalid_source_is_rejected_before_metadata_requests(self):
        w = load()
        with patch.object(w, 'urlopen') as fetch, self.assertRaises(ValueError):
            w.youtube_metadata('https://youtube.com.evil.test/watch?v=jNQXAC9IVRw')
        fetch.assert_not_called()


class PipelineTests(unittest.TestCase):
    def audio(self, path):
        subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-y', '-f', 'lavfi',
                        '-i', 'anullsrc=r=16000:cl=mono', '-t', '0.3', '-f', 'mp3', str(path)], check=True)

    def test_single_url_fallback_transcription_and_resume(self):
        w = load()
        self.assertTrue(hasattr(w, 'run_pipeline'), 'Missing automatic pipeline')
        calls = []
        recognized = []
        def downloader(url, output, site, settings):
            calls.append(site)
            if site == 'tuberipper':
                raise RuntimeError('fixture converter unavailable')
            self.audio(output)
            return {'title': 'sample / video', 'suggested_filename': 'irrelevant provider branding.mp3',
                    'duration_seconds': 0.3, 'provider': site}
        def infer(job, settings):
            backend = Mock()
            def recognize(path):
                recognized.append(path)
                return 'sample transcript'
            backend.recognize.side_effect = recognize
            config = {k: settings[k] for k in ('model_path', 'vad_path', 'punc_path', 'chunk_seconds', 'threads')}
            return w.core.transcribe_job(job, config, lambda _: backend)
        metadata = Mock(return_value={'title': 'sample / video', 'uploader': 'channel / name',
                                      'publish_date': '2024-02-29T23:30:00-08:00'})
        with tempfile.TemporaryDirectory() as tmp:
            settings = w.load_settings(Path(tmp) / 'absent.json')
            job = Path(tmp) / 'job'
            url = 'https://youtu.be/jNQXAC9IVRw'
            result = w.run_pipeline(url, job, settings, downloader=downloader, infer=infer,
                                    metadata_fetcher=metadata)
            self.assertTrue(result['complete'])
            self.assertEqual(calls, ['tuberipper', 'onlymp3'])
            stem = '[channel _ name] [2024-02-29] sample _ video'
            self.assertTrue((job / 'media' / (stem + '.mp3')).is_file())
            self.assertEqual((job / 'txt' / (stem + '.txt')).read_text(), 'sample transcript\n')
            self.assertEqual((job / 'raw' / (stem + '.txt')).read_text(), 'sample transcript\n')
            entry = w.core.read_job(job)['entries'][0]
            self.assertEqual(entry['original_title'], 'sample / video')
            self.assertEqual(entry['uploader'], 'channel / name')
            self.assertEqual(entry['publish_date'], '2024-02-29')
            metadata.return_value = {'title': 'remote title changed', 'uploader': 'another channel',
                                     'publish_date': '2025-01-01'}
            result = w.run_pipeline(url, job, settings, downloader=downloader, infer=infer,
                                    metadata_fetcher=metadata)
            self.assertTrue(result['complete'])
            resumed = w.core.read_job(job)['entries'][0]
            self.assertEqual(resumed['name'], stem + '.mp3')
            self.assertEqual(resumed['publish_date'], '2024-02-29')
            self.assertEqual(len(recognized), 1)
            self.assertEqual(len(calls), 2)
            with self.assertRaises(ValueError):
                w.run_pipeline('https://youtu.be/BaW_jenozKc', job, settings, downloader=downloader, infer=infer,
                               metadata_fetcher=metadata)

    def test_download_only_never_invokes_inference_and_does_not_claim_complete(self):
        w = load()
        self.assertTrue(hasattr(w, 'run_pipeline'), 'Missing automatic pipeline')
        def downloader(url, output, site, settings):
            self.audio(output)
            return {'title': 'audio-check', 'suggested_filename': 'audio.mp3', 'provider': site}
        infer = Mock(side_effect=AssertionError('Must not transcribe'))
        metadata = Mock(return_value={'title': 'audio-check', 'uploader': 'fixture channel'})
        with tempfile.TemporaryDirectory() as tmp:
            settings = w.load_settings(Path(tmp) / 'absent')
            result = w.run_pipeline('https://youtu.be/jNQXAC9IVRw', Path(tmp) / 'job', settings,
                                    download_only=True, downloader=downloader, infer=infer,
                                    metadata_fetcher=metadata)
            self.assertFalse(result['complete'])
            self.assertEqual(result['verified_media'], 1)
            infer.assert_not_called()

    def test_optional_metadata_fallback_and_legacy_resume(self):
        w = load()
        def download(url, output, site, settings):
            self.audio(output)
            return {'title': 'audio-check', 'suggested_filename': 'audio.mp3', 'provider': site}
        cases = [
            ({'title': 'audio-check', 'uploader': 'fixture channel'}, '[fixture channel] audio-check.mp3', None),
            ({'publish_date': '2024-02-29T12:30:00Z'}, '[2024-02-29] audio-check.mp3', '2024-02-29'),
            ({'uploader': 'fixture channel', 'publish_date': 'not a date'}, '[fixture channel] audio-check.mp3', None),
            ({}, 'audio-check.mp3', None),
            (TimeoutError('metadata unavailable'), 'audio-check.mp3', None),
        ]
        for payload, expected_name, expected_date in cases:
            with self.subTest(payload=payload), tempfile.TemporaryDirectory() as tmp:
                metadata = Mock(side_effect=payload) if isinstance(payload, Exception) else Mock(return_value=payload)
                downloader = Mock(side_effect=download)
                infer = Mock()
                settings = w.load_settings(Path(tmp) / 'absent')
                job = Path(tmp) / 'job'
                url = 'https://youtu.be/jNQXAC9IVRw'
                result = w.run_pipeline(url, job, settings, download_only=True, downloader=downloader,
                                        infer=infer, metadata_fetcher=metadata)
                self.assertEqual(result['verified_media'], 1)
                state = w.core.read_job(job)
                entry = state['entries'][0]
                self.assertEqual(entry['name'], expected_name)
                self.assertEqual(entry['publish_date'], expected_date)
                if expected_date is None:
                    # Simulate a manifest made before publication-date support.
                    entry.pop('publish_date')
                    w.core.atomic_json(job / 'manifest.json', state)
                newer_metadata = Mock(return_value={'title': 'renamed', 'uploader': 'new channel',
                                                    'publish_date': '2025-01-01'})
                w.run_pipeline(url, job, settings, download_only=True, downloader=downloader,
                               infer=infer, metadata_fetcher=newer_metadata)
                resumed = w.core.read_job(job)['entries'][0]
                self.assertEqual(resumed['name'], expected_name)
                self.assertEqual(resumed.get('publish_date'), expected_date)
                self.assertTrue((job / 'media' / expected_name).is_file())
                downloader.assert_called_once()
                infer.assert_not_called()

    def test_explicit_title_remains_a_full_override(self):
        w = load()
        def download(url, output, site, settings):
            self.audio(output)
            return {'title': 'provider title', 'suggested_filename': 'audio.mp3', 'provider': site}
        downloader = Mock(side_effect=download)
        metadata = Mock(side_effect=AssertionError('Explicit title must skip metadata requests'))
        with tempfile.TemporaryDirectory() as tmp:
            settings = w.load_settings(Path(tmp) / 'absent')
            job = Path(tmp) / 'job'
            url = 'https://youtu.be/jNQXAC9IVRw'
            for _ in range(2):
                w.run_pipeline(url, job, settings, download_only=True, title='My / custom title',
                               downloader=downloader, metadata_fetcher=metadata)
            entry = w.core.read_job(job)['entries'][0]
            self.assertEqual(entry['name'], 'My _ custom title.mp3')
            self.assertIsNone(entry['publish_date'])
            downloader.assert_called_once()
            metadata.assert_not_called()
            with self.assertRaisesRegex(ValueError, 'Title changed'):
                w.run_pipeline(url, job, settings, download_only=True, title='Another title',
                               downloader=downloader, metadata_fetcher=metadata)

    def test_invalid_audio_fails_before_transcription(self):
        w = load()
        self.assertTrue(hasattr(w, 'run_pipeline'), 'Missing automatic pipeline')
        def downloader(url, output, site, settings):
            output.write_text('<html>payment required</html>')
            return {'title': 'bad', 'suggested_filename': 'bad.mp3', 'provider': site}
        infer = Mock()
        metadata = Mock(return_value={'title': 'bad', 'uploader': 'fixture channel'})
        with tempfile.TemporaryDirectory() as tmp:
            settings = w.load_settings(Path(tmp) / 'absent')
            job = Path(tmp) / 'job'
            with self.assertRaises(RuntimeError):
                w.run_pipeline('https://youtu.be/jNQXAC9IVRw', job, settings, downloader=downloader, infer=infer,
                               metadata_fetcher=metadata)
            infer.assert_not_called()
            self.assertEqual(w.core.read_job(job)['entries'][0]['download'], 'error')


class ProcessAndCLITests(unittest.TestCase):
    def test_process_timeout_and_json_worker_protocol(self):
        w = load()
        self.assertTrue(hasattr(w, 'run_child'), 'Missing bounded process supervisor')
        result = w.run_child([sys.executable, '-c', 'print("child-ok")'], 10)
        self.assertEqual(result.stdout.strip(), 'child-ok')
        with self.assertRaises(TimeoutError):
            w.run_child([sys.executable, '-c', 'import time; time.sleep(10)'], 0.1)
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / 'child.log'
            result = w.run_child([sys.executable, '-c', 'print("log-ok")'], 10, log)
            self.assertEqual(result.returncode, 0)
            self.assertIn('log-ok', log.read_text())
        from unittest.mock import patch
        options = w.load_settings()
        payload = {'title': 'fixture', 'suggested_filename': 'fixture.mp3', 'duration_seconds': 1, 'provider': 'tuberipper'}
        with patch.object(w, 'run_child', return_value=subprocess.CompletedProcess([], 0, json.dumps(payload), '')) as child:
            self.assertEqual(w.worker_download('https://youtu.be/jNQXAC9IVRw', Path('/tmp/test-output.mp3'), 'tuberipper', options), payload)
            self.assertIn('--url', child.call_args.args[0])

    def test_logged_timeout_stops_sigterm_resistant_descendants(self):
        w = load()
        import os
        import signal
        with tempfile.TemporaryDirectory() as tmp:
            pidfile = Path(tmp) / 'descendant.pid'
            code = ('import os, signal, time; from pathlib import Path\n'
                    'child = os.fork()\n'
                    'if child == 0:\n'
                    ' signal.signal(signal.SIGTERM, signal.SIG_IGN)\n'
                    f' Path({str(pidfile)!r}).write_text(str(os.getpid()))\n'
                    'time.sleep(60)\n')
            descendant = None
            try:
                with self.assertRaises(TimeoutError):
                    w.run_child([sys.executable, '-c', code], 1, Path(tmp) / 'asr.log')
                descendant = int(pidfile.read_text())
                stat = Path(f'/proc/{descendant}/stat')
                import time
                deadline = time.monotonic() + 0.5
                while True:
                    try:
                        state = stat.read_text().split()[2]
                    except FileNotFoundError:
                        state = None
                    if state in (None, 'Z') or time.monotonic() >= deadline:
                        break
                    # SIGKILL delivery can lag behind reaping the direct child.
                    time.sleep(0.01)
                self.assertIn(state, (None, 'Z'), 'Timed-out process group still has a live descendant')
            finally:
                if descendant is None and pidfile.exists():
                    descendant = int(pidfile.read_text())
                if descendant:
                    try:
                        os.kill(descendant, signal.SIGKILL)
                    except ProcessLookupError:
                        pass

    def test_asr_subprocess_uses_configured_interpreter_and_cli_preflights(self):
        w = load()
        self.assertTrue(hasattr(w, 'run_asr'), 'Missing FunASR handoff')
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp:
            settings = w.load_settings(Path(tmp) / 'missing')
            settings['model_path'] = '/trusted/model'
            with patch.object(w, 'run_child', return_value=subprocess.CompletedProcess([], 0, '', '')) as child:
                self.assertEqual(w.run_asr(Path(tmp), settings), 0)
                command = child.call_args.args[0]
                self.assertEqual(command[0], settings['asr_python'])
                self.assertIn('/trusted/model', command)
                self.assertNotIn('--punc-path', command)
            config = Path(tmp) / 'missing-config.json'
            result = subprocess.run([sys.executable, str(SCRIPT), '--help'], capture_output=True, text=True)
            self.assertIn('--download-only', result.stdout)
            result = subprocess.run([sys.executable, str(SCRIPT), 'https://youtu.be/jNQXAC9IVRw',
                                     '--config', str(config), '--job', str(Path(tmp) / 'job')], capture_output=True, text=True)
            self.assertEqual(result.returncode, 1)
            self.assertIn('model', result.stderr)
            self.assertFalse((Path(tmp) / 'job').exists(), 'Missing model must fail before remote work')


if __name__ == '__main__':
    unittest.main()
