"""Deterministic LibriSpeech inputs for AIPerf; no downloads or cloud calls."""

import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess
import soundfile as sf


def normalize(text):
    return ' '.join(re.findall(r'\w+', text.casefold()))


def prepare(root, audio_dir):
    # AIPerf accepts only wav/mp3, so convert once; hashes cover the WAV that is actually sent.
    audio_dir.mkdir(parents=True)
    records = []
    for transcript in sorted(Path(root).rglob('*.trans.txt')):
        for line in transcript.read_text().splitlines():
            name, text = line.split(' ', 1)
            source = (transcript.parent / f'{name}.flac').resolve()
            wav = (audio_dir / f'{name}.wav').resolve()
            data, rate = sf.read(source, dtype='int16')
            sf.write(wav, data, rate, subtype='PCM_16')
            records.append(
                dict(
                    audio=str(wav),
                    source=str(source),
                    text=text,
                    sha256=hashlib.sha256(wav.read_bytes()).hexdigest(),
                )
            )
    if not records:
        raise ValueError(f'No LibriSpeech *.trans.txt files under {root}')
    return records


def schedule(audio, phases):
    if not audio or not phases:
        raise ValueError('Audio and phases must not be empty')
    rows, offset = [], 0
    for seconds, rate in phases:
        if not all(math.isfinite(v) and v > 0 for v in (seconds, rate)):
            raise ValueError('Duration and rate must be finite and positive')
        for i in range(math.ceil(seconds * rate)):
            rows.append(
                dict(audio=audio[len(rows) % len(audio)], timestamp=round((offset + i / rate) * 1000))
            )
        offset += seconds
    return rows


def transcribe(path, url, key, model):
    # curl handles multipart boundaries and audio streaming without another dependency.
    result = subprocess.run(
        [
            'curl',
            '--fail-with-body',
            '--silent',
            '--show-error',
            '--max-time',
            '300',
            '--config',
            '-',
            '--form',
            f'file=@{path}',
            '--form-string',
            f'model={model}',
            url.rstrip('/') + '/audio/transcriptions',
        ],
        input='header = "Authorization: Bearer ' + key.replace('\\', '\\\\').replace('"', '\\"') + '"\n',
        capture_output=True,
        text=True,
    )
    if result.returncode:
        raise RuntimeError(f'curl exit {result.returncode}: {result.stdout.strip()} {result.stderr.strip()}')
    return json.loads(result.stdout)['text']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('librispeech', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--phase', action='append', help='seconds:requests_per_second')
    args = parser.parse_args()
    phases = [
        tuple(map(float, p.split(':'))) for p in (args.phase or ['60:1', '15:8', '60:1', '15:16', '60:1'])
    ]
    args.output.mkdir(parents=True, exist_ok=False)
    manifest = prepare(args.librispeech, args.output / 'audio')
    rows = schedule([row['audio'] for row in manifest], phases)
    (args.output / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    (args.output / 'schedule.jsonl').write_text(''.join(json.dumps(row) + '\n' for row in rows))
    (args.output / 'phases.json').write_text(json.dumps(phases) + '\n')


if __name__ == '__main__':
    main()
