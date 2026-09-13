"""Offline orchestration tests; downloader/ASR are explicit test doubles."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock

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
        self.assertEqual(w.title_filename('中文 / 演讲: 第一课'), '中文 _ 演讲_ 第一课.mp3')
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / 'config.json'
            config.write_text(json.dumps({'asr_python': sys.executable, 'model_path': None, 'threads': 2}))
            options = w.load_settings(config)
            self.assertEqual(options['asr_python'], sys.executable)
            with self.assertRaisesRegex(ValueError, 'model'):
                w.preflight_asr(options)


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
            return {'title': '测试 / 视频', 'suggested_filename': 'irrelevant provider branding.mp3',
                    'duration_seconds': 0.3, 'provider': site}
        def infer(job, settings):
            backend = Mock()
            def recognize(path):
                recognized.append(path)
                return '测试文字稿'
            backend.recognize.side_effect = recognize
            config = {k: settings[k] for k in ('model_path', 'vad_path', 'punc_path', 'chunk_seconds', 'threads')}
            return w.core.transcribe_job(job, config, lambda _: backend)
        with tempfile.TemporaryDirectory() as tmp:
            settings = w.load_settings(Path(tmp) / 'absent.json')
            job = Path(tmp) / 'job'
            url = 'https://youtu.be/jNQXAC9IVRw'
            result = w.run_pipeline(url, job, settings, downloader=downloader, infer=infer)
            self.assertTrue(result['complete'])
            self.assertEqual(calls, ['tuberipper', 'onlymp3'])
            self.assertEqual((job / 'txt' / '测试 _ 视频.txt').read_text(), '测试文字稿\n')
            result = w.run_pipeline(url, job, settings, downloader=downloader, infer=infer)
            self.assertTrue(result['complete'])
            self.assertEqual(len(recognized), 1)
            self.assertEqual(len(calls), 2)
            with self.assertRaises(ValueError):
                w.run_pipeline('https://youtu.be/BaW_jenozKc', job, settings, downloader=downloader, infer=infer)

    def test_download_only_never_invokes_inference_and_does_not_claim_complete(self):
        w = load()
        self.assertTrue(hasattr(w, 'run_pipeline'), 'Missing automatic pipeline')
        def downloader(url, output, site, settings):
            self.audio(output)
            return {'title': '音频验证', 'suggested_filename': 'audio.mp3', 'provider': site}
        infer = Mock(side_effect=AssertionError('Must not transcribe'))
        with tempfile.TemporaryDirectory() as tmp:
            settings = w.load_settings(Path(tmp) / 'absent')
            result = w.run_pipeline('https://youtu.be/jNQXAC9IVRw', Path(tmp) / 'job', settings,
                                    download_only=True, downloader=downloader, infer=infer)
            self.assertFalse(result['complete'])
            self.assertEqual(result['verified_media'], 1)
            infer.assert_not_called()

    def test_invalid_audio_fails_before_transcription(self):
        w = load()
        self.assertTrue(hasattr(w, 'run_pipeline'), 'Missing automatic pipeline')
        def downloader(url, output, site, settings):
            output.write_text('<html>payment required</html>')
            return {'title': 'bad', 'suggested_filename': 'bad.mp3', 'provider': site}
        infer = Mock()
        with tempfile.TemporaryDirectory() as tmp:
            settings = w.load_settings(Path(tmp) / 'absent')
            job = Path(tmp) / 'job'
            with self.assertRaises(RuntimeError):
                w.run_pipeline('https://youtu.be/jNQXAC9IVRw', job, settings, downloader=downloader, infer=infer)
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
