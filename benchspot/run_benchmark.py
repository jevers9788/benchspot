"""Benchmark an existing endpoint with AIPerf; never starts, stops or deploys inference."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import shutil
import subprocess
import uuid
from .prepare_workload import normalize, transcribe

ROOT = Path(__file__).resolve().parents[1]
WORKLOAD_FILES = ['manifest.json', 'schedule.jsonl', 'phases.json']


def now():
    return datetime.now(timezone.utc).isoformat()


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def command(inputs, output, url, model, warmup, key='<redacted>'):
    # ponytail: key goes in argv (AIPerf only expands ${VAR} in YAML configs); visible to
    # local `ps` on the load machine but never saved. Use a YAML config if VMs become shared.
    return [
        'aiperf',
        'profile',
        '--model',
        model,
        '--url',
        url,
        '--endpoint-type',
        'audio_transcription',
        '--api-key',
        key,
        '--input-file',
        str(inputs / 'schedule.jsonl'),
        '--custom-dataset-type',
        'single_turn',
        '--fixed-schedule',
        '--warmup-request-count',
        str(warmup),
        '--slice-duration',  # 5 s windows for `aiperf plot` timeslice charts
        '5',
        '--ui-type',
        'none',
        '--export-level',
        'raw',
        '--artifact-dir',
        str(output / 'aiperf'),
    ]


def verify(inputs):
    manifest = json.loads((inputs / 'manifest.json').read_text())
    if not manifest:
        raise ValueError('Empty manifest')
    for row in manifest:
        if sha256(row['audio']) != row['sha256']:
            raise ValueError(f'Audio changed: {row["audio"]}')
    audio_paths = {row['audio'] for row in manifest}
    previous = -1
    count = 0
    for line in (inputs / 'schedule.jsonl').read_text().splitlines():
        row = json.loads(line)
        if set(row) != {'audio', 'timestamp'} or row['audio'] not in audio_paths:
            raise ValueError('Schedule must reference only verified manifest audio')
        timestamp = row['timestamp']
        if (
            isinstance(timestamp, bool)
            or not isinstance(timestamp, (int, float))
            or not math.isfinite(timestamp)
            or timestamp < 0
            or timestamp < previous
        ):
            raise ValueError('Schedule timestamps must be finite, nonnegative and ordered')
        previous = timestamp
        count += 1
    if not count:
        raise ValueError('Empty schedule')
    return manifest


def smoke(row, output, url, key, model):
    actual = transcribe(row['audio'], url, key, model)
    result = dict(
        audio=row['audio'],
        expected=row['text'],
        actual=actual,
        passed=normalize(actual) == normalize(row['text']),
    )
    output.write_text(json.dumps(result, indent=2) + '\n')
    return result['passed']


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + '\n')


def version(cmd):
    try:
        return subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.CalledProcessError):
        return 'unavailable'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('workload', type=Path, help='directory from benchspot.prepare_workload')
    parser.add_argument('results', type=Path, help='parent directory; each run gets a new subdirectory')
    parser.add_argument('--name', required=True)
    parser.add_argument('--url', required=True, help='endpoint origin, e.g. http://10.0.0.5:8000')
    parser.add_argument('--model', required=True)
    parser.add_argument(
        '--client',
        required=True,
        choices=['host', 'load-vm'],
        help='where this process runs; host results are development-only',
    )
    parser.add_argument('--metadata', type=Path, help='supplied deployment metadata (JSON)')
    parser.add_argument('--warmup-requests', type=int, default=10)
    args = parser.parse_args()
    if not args.name or Path(args.name).name != args.name or args.name in {'.', '..'}:
        parser.error('--name must be a single directory name')
    if args.warmup_requests < 0:
        parser.error('--warmup-requests must be nonnegative')
    key = os.environ.get('VLLM_API_KEY', '')
    if not key or '\n' in key or '\r' in key:
        raise SystemExit('VLLM_API_KEY must be a nonempty single line')
    url = args.url.rstrip('/')

    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    output = args.results / f'{args.name}-{stamp}-{uuid.uuid4().hex[:6]}'
    output.mkdir(parents=True)
    cmd = command(output / 'inputs', output, url, args.model, args.warmup_requests)
    meta = dict(
        name=args.name,
        endpoint=url,
        model=args.model,
        client=args.client,
        development=args.client == 'host',
        command=cmd,
        status='started',
        created_utc=now(),
        supplied_metadata='system/supplied.json' if args.metadata else 'unavailable',
    )
    try:
        meta['input_hashes'] = {f: sha256(args.workload / f) for f in WORKLOAD_FILES}
        meta['versions'] = dict(
            python=platform.python_version(),
            platform=platform.platform(),
            aiperf=version(['aiperf', '--version']),
            lockfile_sha256=sha256(ROOT / 'uv.lock') if (ROOT / 'uv.lock').exists() else 'unavailable',
            git=version(['git', '-C', str(ROOT), 'rev-parse', 'HEAD']),
            source_sha256={p.name: sha256(p) for p in sorted(Path(__file__).parent.glob('*.py'))},
        )
        write_json(output / 'config/aiperf_command.json', cmd)
        write_json(
            output / 'system/client.json',
            dict(hostname=platform.node(), platform=platform.platform(), cpus=os.cpu_count()),
        )
        if args.metadata:
            write_json(output / 'system/supplied.json', json.loads(args.metadata.read_text()))
        (output / 'inputs').mkdir()
        for filename in WORKLOAD_FILES:
            shutil.copy(args.workload / filename, output / 'inputs' / filename)
        manifest = verify(output / 'inputs')
        if not smoke(manifest[0], output / 'smoke.json', url + '/v1', key, args.model):
            meta['status'] = 'smoke_failed'
            raise SystemExit('Smoke transcript mismatch; see smoke.json')
        meta['started_utc'] = now()
        with (output / 'console.log').open('w') as log:
            run = subprocess.run(
                command(output / 'inputs', output, url, args.model, args.warmup_requests, key),
                stdout=log,
                stderr=subprocess.STDOUT,
            )
        meta.update(exit_code=run.returncode, status='completed' if run.returncode == 0 else 'failed')
    except Exception as error:
        meta.update(status='error', error=str(error).replace(key, '<redacted>'))
        raise SystemExit(f'error: {meta["error"]}')
    finally:
        meta['finished_utc'] = now()
        write_json(output / 'run.json', meta)
        print(output)
    raise SystemExit(run.returncode)


if __name__ == '__main__':
    main()
