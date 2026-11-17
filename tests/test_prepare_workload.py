import tempfile
import unittest
from pathlib import Path
from benchspot.prepare_workload import schedule, prepare, normalize


class BenchmarkTests(unittest.TestCase):
    def test_burst_and_recovery_are_replayable(self):
        rows = schedule(['a', 'b'], [(2, 1), (1, 4), (2, 1)])
        self.assertEqual([r['timestamp'] for r in rows], [0, 1000, 2000, 2250, 2500, 2750, 3000, 4000])
        self.assertEqual([r['audio'] for r in rows], ['a', 'b'] * 4)
        self.assertEqual(rows, schedule(['a', 'b'], [(2, 1), (1, 4), (2, 1)]))

    def test_invalid_rates_rejected(self):
        for rate in [0, -1, float('inf'), float('nan')]:
            with self.assertRaises(ValueError):
                schedule(['a'], [(1, rate)])

    def test_librispeech_flac_becomes_reproducible_wav(self):
        import hashlib
        import numpy as np
        import soundfile as sf

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / '1-2.trans.txt').write_text('1-2-3 HELLO WORLD\n')
            samples = (np.sin(np.arange(1600) / 5) * 10000).astype('int16')
            sf.write(root / '1-2-3.flac', samples, 16000)
            first = prepare(root, root / 'a')[0]
            second = prepare(root, root / 'b')[0]
            self.assertEqual(first['text'], 'HELLO WORLD')
            self.assertEqual(first['source'], str((root / '1-2-3.flac').resolve()))
            self.assertTrue(first['audio'].endswith('.wav'))
            self.assertEqual(first['sha256'], hashlib.sha256(Path(first['audio']).read_bytes()).hexdigest())
            self.assertEqual(first['sha256'], second['sha256'])
            data, rate = sf.read(first['audio'], dtype='int16')
            self.assertEqual((rate, sf.info(first['audio']).format), (16000, 'WAV'))
            self.assertTrue((data == samples).all())

    def test_smoke_normalization(self):
        self.assertEqual(normalize(' Hello, WORLD! '), normalize('HELLO WORLD'))

    def test_transcribe_error_keeps_server_response(self):
        import os
        from unittest.mock import patch
        from benchspot.prepare_workload import transcribe

        with tempfile.TemporaryDirectory() as directory:
            curl = Path(directory) / 'curl'
            curl.write_text('#!/bin/sh\necho \'{"error": "Invalid or unsupported audio file."}\'\nexit 22\n')
            curl.chmod(0o755)
            with patch.dict(os.environ, {'PATH': f'{directory}:{os.environ["PATH"]}'}):
                with self.assertRaisesRegex(RuntimeError, 'unsupported audio'):
                    transcribe('a.flac', 'http://x/v1', 'key', 'whisper')
