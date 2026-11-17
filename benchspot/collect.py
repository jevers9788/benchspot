"""Add deployment records, task files and the run's telemetry slice to a local result directory.

Copying files off the machines is done by hand (see README); this only files them away.
"""

import argparse
import csv
from datetime import datetime
import json
from pathlib import Path
import shutil


def refuse_if_relaunched(server_txt, meta):
    # A record taken after a relaunch describes a different server than the one measured.
    for line in server_txt.read_text().splitlines():
        if line.startswith('container-started:'):
            started = datetime.fromisoformat(line.split(':', 1)[1].strip())
            run_began = datetime.fromisoformat(meta.get('created_utc') or meta['started_utc'])
            if started > run_began:
                raise SystemExit(
                    f'vLLM container started {started} after the run began {run_began}: server was '
                    'relaunched, so this record does not describe the measured server. Nothing collected.'
                )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run', type=Path, help='result directory containing run.json')
    parser.add_argument('--telemetry', type=Path, help='full GPU telemetry CSV from the GPU machine')
    parser.add_argument('--server-record', type=Path, help='directory written by sky/record.sh')
    parser.add_argument(
        '--config', type=Path, action='append', default=[], help='task file used, e.g. sky/gpu.yaml'
    )
    args = parser.parse_args()
    meta = json.loads((args.run / 'run.json').read_text())
    if args.server_record:
        refuse_if_relaunched(args.server_record / 'server.txt', meta)

    for path in args.config:
        (args.run / 'config').mkdir(exist_ok=True)
        shutil.copy(path, args.run / 'config' / path.name)
    if args.server_record:
        shutil.copytree(args.server_record, args.run / 'system/server')
    if args.telemetry:
        (args.run / 'telemetry').mkdir(exist_ok=True)
        if 'started_utc' not in meta:
            (args.run / 'telemetry/UNAVAILABLE').write_text(f'unavailable: run status {meta.get("status")}\n')
            return
        start, end = (datetime.fromisoformat(meta[k]) for k in ('started_utc', 'finished_utc'))
        with (
            args.telemetry.open() as source,
            (args.run / 'telemetry/gpu.csv').open('w', newline='') as target,
        ):
            rows = csv.reader(source)
            out = csv.writer(target, lineterminator='\n')
            out.writerow(next(rows))
            kept = [r for r in rows if start <= datetime.fromisoformat(r[0]) <= end]
            out.writerows(kept)
        print(f'telemetry rows: {len(kept)}')


if __name__ == '__main__':
    main()
