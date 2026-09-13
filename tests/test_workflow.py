"""Offline regression tests; external services/models are explicitly mocked."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'media_transcribe.py'


def load():
    spec = importlib.util.spec_from_file_location('workflow', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class InventoryTests(unittest.TestCase):
    def test_inventory_preserves_names_and_rejects_collisions(self):
        self.assertTrue(SCRIPT.exists(), 'Missing workflow implementation')
        w = load()
        entries = w.make_entries([('abc_123', 'lesson-one.mp4'), ('def_456', 'subdir/lesson-two.mp3')])
        self.assertEqual(entries[0]['name'], 'lesson-one.mp4')
        self.assertEqual(entries[1]['name'], 'subdir/lesson-two.mp3')
        for names in [[('a', '../escape.mp4')], [('a', '/tmp/a.mp4')],
                      [('a', 'a\\b.mp4')], [('a', 'x.mp4'), ('b', 'x.mp3')],
                      [('a', 'X.mp4'), ('b', 'x.mp4')],
                      [('a', 'a.mp4'), ('b', 'a.txt/b.mp4')],
                      [('a', 'a.txt/b.mp4'), ('b', 'a.mp4')],
                      [('a', 'A.TXT/b.mp4'), ('b', 'a.mp4')],
                      [('a', 'a.mp4'), ('b', 'a.mp4/b.mp3')]]:
            with self.subTest(names=names), self.assertRaises(ValueError):
                w.make_entries(names)
        with self.assertRaises(ValueError):
            w.make_entries([])
        with tempfile.TemporaryDirectory() as tmp:
            job = Path(tmp) / 'job'
            w.create_job(job, entries, 'test fixture')
            state = w.read_job(job)
            self.assertEqual(state['entries'], entries)
            with self.assertRaises(FileExistsError):
                w.create_job(job, entries, 'do not overwrite')


class DownloadTests(unittest.TestCase):
    def test_download_probes_before_commit_and_resumes_by_hash(self):
        w = load()
        self.assertTrue(hasattr(w, 'download_job'), 'Missing download behavior')
        import wave
        with tempfile.TemporaryDirectory() as tmp:
            job = Path(tmp) / 'job'
            w.create_job(job, w.make_entries([('id_1', 'lesson.wav')]), 'fixture')
            calls = []
            def downloader(file_id, output):
                calls.append(file_id)
                with wave.open(str(output), 'wb') as f:
                    f.setnchannels(1)
                    f.setsampwidth(2)
                    f.setframerate(16000)
                    f.writeframes(b'\x00\x00' * 1600)
                return str(output)
            self.assertEqual(w.download_job(job, downloader), 0)
            self.assertEqual(w.download_job(job, downloader), 0)
            self.assertEqual(calls, ['id_1'])
            self.assertEqual(w.read_job(job)['entries'][0]['download'], 'done')
            (job / 'media' / 'lesson.wav').write_bytes(b'<html>login</html>')
            self.assertEqual(w.download_job(job, downloader), 1)
            self.assertEqual(calls, ['id_1'], 'Do not overwrite changed local media')

    def test_invalid_media_never_becomes_completed_download(self):
        w = load()
        self.assertTrue(hasattr(w, 'download_job'), 'Missing download behavior')
        with tempfile.TemporaryDirectory() as tmp:
            job = Path(tmp) / 'job'
            w.create_job(job, w.make_entries([('id_1', 'fake.mp4')]), 'fixture')
            def downloader(file_id, output):
                output.write_text('<html>sign in</html>')
                return str(output)
            self.assertEqual(w.download_job(job, downloader), 1)
            self.assertFalse((job / 'media' / 'fake.mp4').exists())
            self.assertEqual(w.read_job(job)['entries'][0]['download'], 'error')

    def test_drive_link_validation_and_expected_inventory_count(self):
        w = load()
        self.assertTrue(hasattr(w, 'parse_drive_url'), 'Missing Drive URL validation')
        self.assertEqual(w.parse_drive_url('https://drive.google.com/file/d/Abc_123/view'), ('file', 'Abc_123'))
        self.assertEqual(w.parse_drive_url('https://drive.google.com/drive/folders/Abc-123?usp=sharing'), ('folder', 'Abc-123'))
        for url in ['https://evil.com/file/d/abc/view', 'http://drive.google.com/file/d/abc/view', 'https://drive.google.com/file/d/bad%20id/view']:
            with self.assertRaises(ValueError):
                w.parse_drive_url(url)
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                w.inventory('https://drive.google.com/file/d/abc/view', Path(tmp) / 'job', 'sample.mp4', 2)


class TranscriptionTests(unittest.TestCase):
    def make_job(self, w, root):
        import wave
        source = root / 'input' / 'lesson-audio.wav'
        source.parent.mkdir()
        with wave.open(str(source), 'wb') as f:
            f.setnchannels(1)
            f.setsampwidth(2)
            f.setframerate(16000)
            f.writeframes(b'\x00\x00' * 32000)
        job = root / 'job'
        w.import_local(source.parent, job)
        return job

    def test_chunked_transcription_punctuation_resume_and_edit_protection(self):
        w = load()
        self.assertTrue(hasattr(w, 'transcribe_job'), 'Missing transcription behavior')
        class FixtureBackend:
            def recognize(self, path):
                self.calls += 1
                self.durations.append(w.probe(path))
                return 'this is a test'
            def punctuate(self, text):
                return text + '.'
            calls = 0
            durations = []
        backend = FixtureBackend()
        with tempfile.TemporaryDirectory() as tmp:
            job = self.make_job(w, Path(tmp))
            config = {'model_path': 'fixture', 'vad_path': None, 'punc_path': 'fixture', 'chunk_seconds': 1, 'threads': 1}
            self.assertEqual(w.transcribe_job(job, config, lambda c: backend), 0)
            output = job / 'txt' / 'lesson-audio.txt'
            self.assertEqual(output.read_text(), 'this is a test\nthis is a test.\n')
            self.assertEqual((job / 'raw' / 'lesson-audio.txt').read_text(), 'this is a test\nthis is a test\n')
            self.assertEqual(backend.calls, 2)
            self.assertTrue(all(d <= 1.01 for d in backend.durations))
            self.assertEqual(w.transcribe_job(job, config, lambda c: backend), 0)
            self.assertEqual(backend.calls, 2)
            output.write_text('user edit')
            self.assertEqual(w.transcribe_job(job, config, lambda c: backend), 1)
            self.assertEqual(output.read_text(), 'user edit')

    def test_redownloaded_media_cannot_reuse_stale_transcript(self):
        w = load()
        from unittest.mock import Mock
        backend = Mock()
        backend.recognize.return_value = 'first recognition'
        config = {'model_path': 'fixture', 'vad_path': None, 'punc_path': None, 'chunk_seconds': 60, 'threads': 1}
        with tempfile.TemporaryDirectory() as tmp:
            job = self.make_job(w, Path(tmp))
            self.assertEqual(w.transcribe_job(job, config, lambda c: backend), 0)
            media = job / 'media' / 'lesson-audio.wav'
            media.unlink()
            import wave
            def different_media(file_id, output):
                with wave.open(str(output), 'wb') as f:
                    f.setnchannels(1)
                    f.setsampwidth(2)
                    f.setframerate(16000)
                    f.writeframes(b'\x00\x00' * 16000)
                return str(output)
            self.assertEqual(w.download_job(job, different_media), 0)
            self.assertFalse(w.status(job)['complete'], 'New media must invalidate old transcript')
            self.assertEqual(w.transcribe_job(job, config, lambda c: backend), 1)

    def test_empty_asr_is_error_not_success(self):
        w = load()
        self.assertTrue(hasattr(w, 'transcribe_job'), 'Missing transcription behavior')
        from unittest.mock import Mock
        backend = Mock()
        backend.recognize.return_value = ''
        with tempfile.TemporaryDirectory() as tmp:
            job = self.make_job(w, Path(tmp))
            config = {'model_path': 'fixture', 'vad_path': None, 'punc_path': None, 'chunk_seconds': 60, 'threads': 1}
            self.assertEqual(w.transcribe_job(job, config, lambda c: backend), 1)
            self.assertFalse((job / 'txt' / 'lesson-audio.txt').exists())
            self.assertEqual(w.read_job(job)['entries'][0]['transcription'], 'error')

    def test_punctuation_failure_preserves_raw(self):
        w = load()
        self.assertTrue(hasattr(w, 'transcribe_job'), 'Missing transcription behavior')
        from unittest.mock import Mock
        backend = Mock()
        backend.recognize.return_value = 'raw text'
        backend.punctuate.side_effect = RuntimeError('fixture model failure')
        with tempfile.TemporaryDirectory() as tmp:
            job = self.make_job(w, Path(tmp))
            config = {'model_path': 'fixture', 'vad_path': None, 'punc_path': 'fixture', 'chunk_seconds': 60, 'threads': 1}
            self.assertEqual(w.transcribe_job(job, config, lambda c: backend), 1)
            self.assertEqual((job / 'raw' / 'lesson-audio.txt').read_text(), 'raw text\n')
            self.assertFalse((job / 'txt' / 'lesson-audio.txt').exists())

    def test_backend_requires_local_weights_and_valid_result_text(self):
        w = load()
        self.assertTrue(hasattr(w, 'FunASRBackend'), 'Missing local backend')
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                w.FunASRBackend({'model_path': tmp, 'vad_path': None, 'punc_path': None, 'threads': 1})
        self.assertEqual(w.result_text([{'text': 'hello'}]), 'hello')
        for value in [{'error': 'bad'}, None, [{'text': 5}]]:
            with self.assertRaises(ValueError):
                w.result_text(value)


class CLITests(unittest.TestCase):
    def test_help_and_status_verify_real_artifacts(self):
        import subprocess
        import sys
        result = subprocess.run([sys.executable, str(SCRIPT), '--help'], capture_output=True, text=True)
        self.assertIn('transcribe', result.stdout, 'Missing CLI commands')
        w = load()
        with tempfile.TemporaryDirectory() as tmp:
            job = Path(tmp) / 'job'
            w.create_job(job, w.make_entries([('abc', 'not-downloaded.mp4')]), 'fixture')
            result = subprocess.run([sys.executable, str(SCRIPT), 'status', '--job', str(job)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 1)
            report = json.loads(result.stdout)
            self.assertEqual(report['total'], 1)
            self.assertEqual(report['verified_transcripts'], 0)
            self.assertFalse(report['complete'])

    def test_symlinks_and_job_lock_are_rejected(self):
        w = load()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            job = root / 'job'
            w.create_job(job, w.make_entries([('abc', 'x.mp4')]), 'fixture')
            (job / 'media').symlink_to(root, target_is_directory=True)
            with self.assertRaises(ValueError):
                w.job_path(job, 'media', 'x.mp4')
            with w.job_lock(job):
                with self.assertRaises(RuntimeError):
                    with w.job_lock(job):
                        pass

    def test_folder_inventory_uses_metadata_only_and_count_guard(self):
        w = load()
        from unittest.mock import Mock, patch
        from types import SimpleNamespace
        listing = Mock(return_value=[SimpleNamespace(id='abc', path='folder/a.mp4')])
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict('sys.modules', {'gdown': SimpleNamespace(download_folder=listing)}):
                result = w.inventory('https://drive.google.com/drive/folders/abc', Path(tmp) / 'job', expected_count=1)
                self.assertEqual(result['names'], ['folder/a.mp4'])
                self.assertFalse((Path(tmp) / 'job' / 'media').exists())
                listing.assert_called_once_with(id='abc', output='inventory', skip_download=True,
                                                use_cookies=False, remaining_ok=False, quiet=True)


if __name__ == '__main__':
    unittest.main()
