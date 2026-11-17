#!/usr/bin/env bash
# Run on the GPU host; intentionally excludes container environment (credentials).
set -euo pipefail
: "${1:?usage: record.sh NEW_OUTPUT_DIRECTORY}"
umask 077
mkdir "$1"
gcp() { curl -sf --max-time 2 -H Metadata-Flavor:Google "http://metadata.google.internal/computeMetadata/v1/instance/$1" || echo unavailable; }
{
  date -u
  uname -a
  echo "zone: $(gcp zone)"
  echo "machine-type: $(gcp machine-type)"
  echo "container-started: $(docker inspect --format '{{.State.StartedAt}}' stt-vllm)"
  docker version
  docker inspect --format '{{.Image}} {{json .Config.Cmd}} {{json .HostConfig}}' stt-vllm
  # Non-secret vLLM settings passed with -e; the API key and other variables stay out.
  docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' stt-vllm |
    awk '/^VLLM_/ && !/^VLLM_API_KEY=/'
  docker exec stt-vllm python3 -m pip freeze
} > "$1/server.txt"
nvidia-smi -q > "$1/gpu.txt"
docker logs stt-vllm > "$1/vllm.log" 2>&1
