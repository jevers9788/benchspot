# benchspot

A benchmarking tool for provisioning infra, running experiments, and collecting results. 
Currently benchmarks `openai/whisper-large-v3-turbo` on vLLM (one GCP L4) with AIPerf. Future intent is to generalize across models, inference servers, cloud providers, and hardware

Provisioning is manual, with the native SkyPilot CLI.

## Setup (GCP)

```sh
just sync && just test
uv tool install 'skypilot[gcp]==0.14.2'
sky check gcp

set -a && source .env && set +a
```

Source `.env` in every new shell: the key must match the one passed to `sky launch`.

## Workload

Do this once per load machine.
Download [LibriSpeech test-clean](https://www.openslr.org/12) onto the machine that will
generate load, then prepare there. The manifest stores absolute audio paths, so keep them
stable and reuse the prepared directory for every comparison.

```sh
just prepare data/LibriSpeech/test-clean data/workload
```

Optional `--phase SECONDS:RATE` (repeatable) overrides the default phases: 60 s at 1 req/s, 15 s at 8, 60 s at 1, 15 s at 16, 60 s at 1.

## GPU server

```sh
sky launch -c stt-gpu sky/gpu.yaml --secret VLLM_API_KEY --secret HF_TOKEN
```

The task prints `ready on INTERNAL_IP:8000`. Port 8000 has no public firewall rule; only
the internal network and the API key reach it. Server settings live as a literal argument
list in `sky/gpu.yaml`: change them there, and the saved config shows the change.

The task also starts a GPU sampler that appends to `~/telemetry/gpu.csv` for the machine's
life. Autostop (240 idle min) starts counting when the launch task finishes; relaunch to reset.

## Local development run

Results are labelled `development` and must not be compared with load-VM results.

SkyPilot adds an `stt-gpu` SSH alias. A 200 from the check below means the tunnel and key work;
401 means the key differs from the server's.

```sh
ssh -N -L 127.0.0.1:18000:127.0.0.1:8000 stt-gpu &
curl -s -w '\n%{http_code}\n' -H "Authorization: Bearer $VLLM_API_KEY" http://127.0.0.1:18000/v1/models
just benchmark data/workload results --name dev --client host \
  --url http://127.0.0.1:18000 --model openai/whisper-large-v3-turbo
```

## Load-VM run (performance measurement)

The load VM must be in the GPU VM's zone. If SkyPilot picked the GPU zone, read it from
`sky status -v stt-gpu` first; if you pin the zone for both, launch in any order.

```sh
sky launch -c stt-load sky/load.yaml --infra gcp/REGION/ZONE
sky exec stt-load 'mkdir -p ~/data && cd ~/data && curl -sL https://www.openslr.org/resources/12/test-clean.tar.gz | tar xz'
sky exec stt-load '~/.local/bin/uv run --locked python -m benchspot.prepare_workload ~/data/LibriSpeech/test-clean ~/workload'
sky exec stt-load --secret VLLM_API_KEY \
  '~/.local/bin/uv run --locked python -m benchspot.run_benchmark ~/workload ~/results \
   --name baseline --client load-vm --url http://INTERNAL_IP:8000 --model openai/whisper-large-v3-turbo'
sky queue stt-load
```

Wait until `sky queue` shows SUCCEEDED or FAILED before collecting.

Optional `--metadata FILE` copies supplied deployment details into `system/supplied.json`.

## Collect

Run this after each run, while the server that was measured is still up, and before `sky down`:

```sh
just collect dev-20261113T213951Z-10655d
```

It accepts the run name or `results/<run>`. In order, it:

1. Copies a load-VM run from `stt-load` and verifies the copy. If verification fails, it stops
   and leaves the remote files in place.
2. Runs the laptop's `sky/record.sh` on `stt-gpu` (image, args, `VLLM_*` settings, packages,
   GPU, zone, vLLM logs, container start time).
3. Adds that record, `sky/gpu.yaml` (and `sky/load.yaml` for load-VM runs) and the telemetry rows
   inside the run window.

It refuses if the vLLM container started after the run began: a relaunch means the record would
describe a different server. Check the printed telemetry row count, then `sky down stt-gpu stt-load`.

## Result directory

```text
results/<name>-<utc>-<id>/
  run.json        endpoint, model, client, development flag, redacted AIPerf command,
                  versions, lockfile hash, input hashes, UTC start/finish, status, exit code
  config/         AIPerf command; SkyPilot task files (added by collect)
  inputs/         manifest, schedule, phases
  system/         client.json (observed), supplied.json, server/ (record.sh output)
  smoke.json      one transcription vs its reference
  aiperf/         summaries, per-request records, raw exports, server metrics
  telemetry/      GPU rows inside [started_utc, finished_utc]
  console.log
```

Status is one of `completed`, `failed` (AIPerf exit code kept), `smoke_failed` or `error`.

## Comparing runs

`just plot results/RUN` writes AIPerf charts to `RUN/aiperf/plots/` (latency over time, 5 s timeslices).
`aiperf_plot.yaml` drops the token-based LLM charts, which are empty for transcription.

1. Check `run.json` `input_hashes` are identical and `development` is false for both.
2. Diff `config/` and `system/server/` to see what changed on the server.
3. In AIPerf records, check actual dispatch lateness and errors: an overloaded client can
   look like a slow server.
4. Report latency distributions and aggregate audio duration / stated time window, per phase
   (see `inputs/phases.json`). Do not average per-request throughput. Warmup requests are a
   separate AIPerf phase and are excluded from profile results.

