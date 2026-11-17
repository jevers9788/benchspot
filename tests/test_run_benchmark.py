import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from benchspot.run_benchmark import command, main, smoke

FAKE_AIPERF = """#!/usr/bin/env python3
import os, sys
if sys.argv[1] == '--version':
    print('0.13.0'); sys.exit(0)
out = sys.argv[sys.argv.index('--artifact-dir') + 1]
os.makedirs(out, exist_ok=True)
open(os.path.join(out, 'profile_export.jsonl'), 'w').write('{}\\n')
print('aiperf ran')
sys.exit(int(os.environ.get('FAKE_EXIT', '0')))
"""


def workload(root):
    audio = root / 'a.flac'
    audio.write_bytes(b'audio')
    inputs = root / 'workload'
    inputs.mkdir()
    (inputs / 'manifest.json').write_text(
        json.dumps([dict(audio=str(audio), text='HELLO', sha256=hashlib.sha256(b'audio').hexdigest())])
    )
    (inputs / 'schedule.jsonl').write_text(json.dumps(dict(audio=str(audio), timestamp=0)) + '\n')
    (inputs / 'phases.json').write_text('[[1, 1]]\n')
    return inputs


class RunTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.inputs = workload(self.root)
        bin_dir = self.root / 'bin'
        bin_dir.mkdir()
        (bin_dir / 'aiperf').write_text(FAKE_AIPERF)
        (bin_dir / 'aiperf').chmod(0o755)
        env = {'PATH': f'{bin_dir}:{os.environ["PATH"]}', 'VLLM_API_KEY': 'sekrit-key'}
        patcher = patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_main(self, *extra, text='hello'):
        argv = [
            'run_benchmark',
            str(self.inputs),
            str(self.root / 'results'),
            '--name',
            'base',
            '--url',
            'http://127.0.0.1:18000',
            '--model',
            'whisper',
            '--client',
            'host',
            *extra,
        ]
        with patch('sys.argv', argv), patch('benchspot.run_benchmark.transcribe', return_value=text):
            with self.assertRaises(SystemExit) as exit_:
                main()
        runs = sorted((self.root / 'results').iterdir())
        return exit_.exception.code, runs

    def test_aiperf_replays_schedule_with_warmup_and_raw_export(self):
        args = command(Path('input'), Path('output'), 'http://localhost:18000', 'whisper', 5)
        self.assertIn('--fixed-schedule', args)
        self.assertIn('audio_transcription', args)
        self.assertEqual(args[args.index('--export-level') + 1], 'raw')
        self.assertEqual(args[args.index('--warmup-request-count') + 1], '5')
        self.assertEqual(args[args.index('--slice-duration') + 1], '5')
        self.assertEqual(args[args.index('--api-key') + 1], '<redacted>')
        self.assertNotIn('--request-rate', args)

    def test_smoke_fails_on_wrong_text_and_saves_response(self):
        with patch('benchspot.run_benchmark.transcribe', return_value='goodbye'):
            row = json.loads((self.inputs / 'manifest.json').read_text())[0]
            self.assertFalse(smoke(row, self.root / 'smoke.json', 'http://x/v1', 'key', 'whisper'))
        self.assertEqual(json.loads((self.root / 'smoke.json').read_text())['actual'], 'goodbye')

    def test_successful_run_writes_bundle_without_secret(self):
        metadata = self.root / 'machines.json'
        metadata.write_text('{"gpu": "L4"}')
        code, runs = self.run_main('--metadata', str(metadata))
        self.assertEqual(code, 0)
        run = runs[0]
        self.assertTrue(run.name.startswith('base-'))
        meta = json.loads((run / 'run.json').read_text())
        self.assertEqual(meta['status'], 'completed')
        self.assertEqual(meta['exit_code'], 0)
        self.assertTrue(meta['development'])
        self.assertEqual(meta['endpoint'], 'http://127.0.0.1:18000')
        self.assertEqual(set(meta['input_hashes']), {'manifest.json', 'schedule.jsonl', 'phases.json'})
        self.assertLess(meta['started_utc'], meta['finished_utc'])
        self.assertEqual(json.loads((run / 'system/supplied.json').read_text()), {'gpu': 'L4'})
        self.assertTrue((run / 'inputs/schedule.jsonl').exists())
        self.assertTrue((run / 'aiperf/profile_export.jsonl').exists())
        self.assertIn('aiperf ran', (run / 'console.log').read_text())
        self.assertTrue((run / 'config/aiperf_command.json').exists())
        self.assertIn(str(run / 'inputs/schedule.jsonl'), meta['command'])
        self.assertIn('run_benchmark.py', meta['versions']['source_sha256'])
        for path in run.rglob('*'):
            if path.is_file():
                self.assertNotIn('sekrit-key', path.read_text(), path)

    def test_missing_metadata_is_marked_unavailable(self):
        _, runs = self.run_main()
        meta = json.loads((runs[0] / 'run.json').read_text())
        self.assertEqual(meta['supplied_metadata'], 'unavailable')

    def test_runs_never_overwrite_each_other(self):
        self.run_main()
        _, runs = self.run_main()
        self.assertEqual(len(runs), 2)

    def test_smoke_failure_is_recorded_and_aiperf_skipped(self):
        code, runs = self.run_main(text='goodbye')
        self.assertNotEqual(code, 0)
        meta = json.loads((runs[0] / 'run.json').read_text())
        self.assertEqual(meta['status'], 'smoke_failed')
        self.assertIn('finished_utc', meta)
        self.assertFalse((runs[0] / 'aiperf').exists())

    def test_aiperf_failure_keeps_output_and_exit_code(self):
        with patch.dict(os.environ, {'FAKE_EXIT': '3'}):
            code, runs = self.run_main('--client', 'load-vm')
        self.assertEqual(code, 3)
        meta = json.loads((runs[0] / 'run.json').read_text())
        self.assertEqual((meta['status'], meta['exit_code'], meta['development']), ('failed', 3, False))
        self.assertTrue((runs[0] / 'aiperf/profile_export.jsonl').exists())

    def test_changed_audio_is_rejected_before_any_request(self):
        Path(json.loads((self.inputs / 'manifest.json').read_text())[0]['audio']).write_bytes(b'x')
        code, runs = self.run_main()
        self.assertFalse((runs[0] / 'smoke.json').exists())
        self.assertNotEqual(code, 0)
        self.assertEqual(json.loads((runs[0] / 'run.json').read_text())['status'], 'error')

    def test_schedule_cannot_bypass_manifest_verification(self):
        (self.inputs / 'schedule.jsonl').write_text('{"audio": "unchecked.flac", "timestamp": 0}\n')
        code, runs = self.run_main()
        self.assertNotEqual(code, 0)
        self.assertFalse((runs[0] / 'smoke.json').exists())
        self.assertIn('verified manifest', json.loads((runs[0] / 'run.json').read_text())['error'])

    def test_missing_input_still_records_failure(self):
        (self.inputs / 'schedule.jsonl').unlink()
        code, runs = self.run_main()
        self.assertNotEqual(code, 0)
        self.assertEqual(json.loads((runs[0] / 'run.json').read_text())['status'], 'error')

    def test_missing_metadata_still_records_failure(self):
        code, runs = self.run_main('--metadata', str(self.root / 'missing.json'))
        self.assertNotEqual(code, 0)
        self.assertEqual(json.loads((runs[0] / 'run.json').read_text())['status'], 'error')
