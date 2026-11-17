set positional-arguments

# Show available commands.
default:
    @just --list

# Sync local dependencies.
sync:
    uv sync --locked

# Format Python code with ruff.
fmt:
    uv run --locked ruff format

# Run the local test suite; no cloud resources needed.
test:
    uv run --locked python -m unittest discover -s tests -v

# Prepare fixed inputs once per load machine: just prepare AUDIO_DIR OUTPUT_DIR [--phase 60:1 ...]
prepare +args:
    uv run --locked python -m benchspot.prepare_workload "$@"

# Benchmark an existing endpoint: just benchmark WORKLOAD RESULTS --name N --url U --model M --client host|load-vm
benchmark +args:
    uv run --locked python -m benchspot.run_benchmark "$@"

# Collect a finished run before `sky down`: just collect RUN (fetches from stt-load if not local)
collect run:
    #!/usr/bin/env bash
    set -euo pipefail
    run=$(basename "$1")
    dir=results/$run
    if [ ! -d "$dir" ]; then
      rsync -a --checksum "stt-load:results/$run" results/
      if [ -n "$(rsync -a --checksum --dry-run --itemize-changes "stt-load:results/$run" results/)" ]; then
        echo "copy of $run not verified; remote files kept on stt-load" >&2; exit 1
      fi
    fi
    ssh stt-gpu "rm -rf ~/record-$run && bash -s ~/record-$run" < sky/record.sh  # laptop copy: always current
    tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
    rsync -a "stt-gpu:record-$run" "stt-gpu:telemetry/gpu.csv" "$tmp/"
    configs=(--config sky/gpu.yaml)
    if grep -q '"client": "load-vm"' "$dir/run.json"; then configs+=(--config sky/load.yaml); fi
    uv run --locked python -m benchspot.collect "$dir" --server-record "$tmp/record-$run" \
      --telemetry "$tmp/gpu.csv" "${configs[@]}"
    echo "collected $dir; check it before sky down"

# Plot a run with AIPerf (writes RUN/aiperf/plots): just plot results/RUN
plot run:
    uv run --locked aiperf plot {{run}}/aiperf --config aiperf_plot.yaml

# Compare runs, one point per run (p50 latency vs request rate): just compare results/A results/B
compare +runs:
    uv run --locked aiperf plot $(printf '%s/aiperf ' "$@") --config aiperf_plot.yaml --output compare-plots
