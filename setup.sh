#!/usr/bin/env bash
# Linux entry point. Install Docker Engine/Compose and NVIDIA Container Toolkit first.
set -euo pipefail
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
action="${1:-check}"
profile="${2:-cpu}"
sip_address="${3:-127.0.0.1}"
case "$profile" in cpu|gpu) ;; *) echo 'Profile must be cpu or gpu'; exit 1;; esac
command -v docker >/dev/null || { echo 'Install Docker Engine and Compose first; see SETUP_GUIDE.md'; exit 1; }
docker info >/dev/null
docker compose version
compose=(docker compose -f "$root/runtime/deployment/compose.json")
bootstrap() {
    docker run --rm --user "$(id -u):$(id -g)" --mount "type=bind,source=$root,target=/workspace" -w /workspace \
        python:3.11-slim-bookworm python scripts/portable_setup.py "$@"
}
case "$action" in
    check) bootstrap check --profile "$profile" --online ;;
    install)
        bootstrap check --profile "$profile" --online --hashes
        bootstrap download
        bootstrap configure --profile "$profile" --host-root "$root" --sip-address "$sip_address"
        "${compose[@]}" config --quiet
        "${compose[@]}" build
        if [[ "$profile" == gpu ]]; then
            docker run --rm --gpus all college-parent-speech:gpu python -c \
                'import torch; assert torch.cuda.is_available(); print(torch.cuda.get_device_name(0))'
        fi
        "${compose[@]}" run --rm --no-deps voice-export
        "${compose[@]}" up -d ollama
        sleep 3
        "${compose[@]}" exec -T ollama ollama create gemma4-e2b-college:qat4 \
            -f /workspace/models/gemma-4-E2B-text-qat/Modelfile.docker
        "${compose[@]}" up -d
        "${compose[@]}" run --rm --no-deps caller python scripts/portable_setup.py verify --internal --profile "$profile"
        ;;
    start) "${compose[@]}" up -d ;;
    verify) "${compose[@]}" run --rm --no-deps caller python scripts/portable_setup.py verify --internal --profile "$profile" ;;
    stop) "${compose[@]}" stop ;;
    logs) "${compose[@]}" logs --tail 100 ;;
    *) echo 'Usage: bash setup.sh check|install|start|verify|stop|logs cpu|gpu [SIP host IP]'; exit 1 ;;
esac
