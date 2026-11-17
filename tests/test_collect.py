import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

CSV = """utc,index,memory_used_mib,memory_total_mib,utilization_gpu_pct
2026-11-11T10:00:00.000Z,0,100,23034,0
2026-11-11T10:00:05.000Z,0,200,23034,50
2026-11-11T10:00:10.000Z,0,300,23034,90
2026-11-11T10:00:15.000Z,0,100,23034,0
"""


class CollectTests(unittest.TestCase):
    def collect(self, meta, server_text='image sha256:abc', expect_ok=True):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        run = root / 'run'
        run.mkdir()
        (run / 'run.json').write_text(json.dumps(meta))
        (root / 'gpu.csv').write_text(CSV)
        server = root / 'record'
        server.mkdir()
        (server / 'server.txt').write_text(server_text)
        result = subprocess.run(
            [
                sys.executable,
                '-m',
                'benchspot.collect',
                str(run),
                '--telemetry',
                str(root / 'gpu.csv'),
                '--server-record',
                str(server),
                '--config',
                str(Path(__file__).parent / 'test_collect.py'),
            ],
            capture_output=True,
            text=True,
            cwd=Path(__file__).parents[1],
        )
        if expect_ok:
            self.assertEqual(result.returncode, 0, result.stderr)
        self.result = result
        return run

    def test_telemetry_is_sliced_to_run_window(self):
        run = self.collect(
            dict(started_utc='2026-11-11T10:00:04+00:00', finished_utc='2026-11-11T10:00:10+00:00')
        )
        lines = (run / 'telemetry/gpu.csv').read_text().splitlines()
        self.assertEqual(lines[0], CSV.splitlines()[0])
        self.assertEqual([l.split(',')[2] for l in lines[1:]], ['200', '300'])
        self.assertEqual((run / 'system/server/server.txt').read_text(), 'image sha256:abc')
        self.assertTrue((run / 'config/test_collect.py').exists())

    def test_run_without_measurement_window_marks_telemetry_unavailable(self):
        run = self.collect(dict(status='smoke_failed', finished_utc='2026-11-11T10:00:10+00:00'))
        self.assertFalse((run / 'telemetry/gpu.csv').exists())
        self.assertIn('unavailable', (run / 'telemetry/UNAVAILABLE').read_text())

    def test_server_relaunched_after_run_is_refused(self):
        run = self.collect(
            dict(
                created_utc='2026-11-11T10:00:00+00:00',
                started_utc='2026-11-11T10:00:04+00:00',
                finished_utc='2026-11-11T10:00:10+00:00',
            ),
            server_text='container-started: 2026-11-11T10:30:00.123456789Z\n',
            expect_ok=False,
        )
        self.assertNotEqual(self.result.returncode, 0)
        self.assertIn('relaunched', self.result.stderr)
        self.assertFalse((run / 'system/server').exists())
        self.assertFalse((run / 'telemetry').exists())

    def test_server_started_before_run_is_accepted_and_summarised(self):
        self.collect(
            dict(
                created_utc='2026-11-11T10:00:00+00:00',
                started_utc='2026-11-11T10:00:04+00:00',
                finished_utc='2026-11-11T10:00:10+00:00',
            ),
            server_text='container-started: 2026-11-11T09:00:00.123456789Z\n',
        )
        self.assertIn('telemetry rows: 2', self.result.stdout)
