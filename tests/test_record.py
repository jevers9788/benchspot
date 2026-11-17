import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class RecordTests(unittest.TestCase):
    def test_records_versions_without_container_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake = root / 'docker'
            fake.write_text(
                '#!/bin/sh\n'
                'case "$*" in *Config.Env*) printf "VLLM_API_KEY=sekrit\\nHF_TOKEN=hf-sekrit\\n'
                'VLLM_HTTP_TIMEOUT_KEEP_ALIVE=75\\nPATH=/bin\\n"; exit;; esac\n'
                'printf "%s\\n" "$*"\n'
            )
            fake.chmod(0o755)
            (root / 'nvidia-smi').symlink_to(fake)
            result = subprocess.run(
                ['bash', str(ROOT / 'sky/record.sh'), str(root / 'out')],
                env=os.environ | {'PATH': str(root) + ':' + os.environ['PATH']},
                text=True,
                capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            settings = (root / 'out/server.txt').read_text()
            self.assertIn('.Config.Cmd', settings)
            self.assertNotIn('.Config.Env', settings)
            self.assertIn('pip freeze', settings)
            self.assertIn('VLLM_HTTP_TIMEOUT_KEEP_ALIVE=75', settings)
            self.assertNotIn('sekrit', settings)
            self.assertIn('zone:', settings)
            self.assertIn('container-started:', settings)
            self.assertIn('logs stt-vllm', (root / 'out/vllm.log').read_text())
