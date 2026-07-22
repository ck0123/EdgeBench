#!/usr/bin/env bash

set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

adapt_proxy_for_container() {
    local proxy_url="$1"
    proxy_url="${proxy_url//127.0.0.1/host.docker.internal}"
    proxy_url="${proxy_url//localhost/host.docker.internal}"
    printf '%s' "$proxy_url"
}

if [[ -z "${DOCKER_HOST:-}" ]]; then
    docker_host="$(docker context inspect "$(docker context show)" \
        --format '{{.Endpoints.docker.Host}}')"
    if [[ -n "$docker_host" ]]; then
        export DOCKER_HOST="$docker_host"
    fi
fi

host_http_proxy="${SFORGE_HTTP_PROXY:-${HTTP_PROXY:-${http_proxy:-}}}"
host_https_proxy="${SFORGE_HTTPS_PROXY:-${HTTPS_PROXY:-${https_proxy:-}}}"
if [[ -n "$host_http_proxy" ]]; then
    export SFORGE_HTTP_PROXY="$(adapt_proxy_for_container "$host_http_proxy")"
fi
if [[ -n "$host_https_proxy" ]]; then
    export SFORGE_HTTPS_PROXY="$(adapt_proxy_for_container "$host_https_proxy")"
fi

export SFORGE_NODEJS_MIRROR_URL="${SFORGE_NODEJS_MIRROR_URL:-https://npmmirror.com/mirrors/node}"
export SFORGE_NPM_REGISTRY_URL="${SFORGE_NPM_REGISTRY_URL:-https://registry.npmmirror.com}"
export SFORGE_CODEX_AUTH_FILE="${SFORGE_CODEX_AUTH_FILE:-$HOME/.codex/auth.json}"
export SFORGE_GOAL_PLUS_REF="${SFORGE_GOAL_PLUS_REF:-experiment/async-research-flow}"

test -s "$SFORGE_CODEX_AUTH_FILE"
docker info >/dev/null

model="${MODEL:-gpt-5.5}"
timeout_seconds="${TIMEOUT_SECONDS:-8100}"
work_cpu_limit="${WORK_CPU_LIMIT:-4}"
judge_cpu_limit="${JUDGE_CPU_LIMIT:-3}"
run_id="${RUN_ID:-codex-gp-${model//./}-solo-worker2h-$(date '+%Y%m%d-%H%M%S')}"

printf 'Run ID: %s\n' "$run_id"
printf 'Goal Plus ref: %s\n' "$SFORGE_GOAL_PLUS_REF"
printf 'Outer timeout: %ss; worker lease: 7200s\n' "$timeout_seconds"

exec python -m sforge run \
    --task vliw_kernel_optimization \
    --agent codex-goal-plus-solo \
    --model "$model" \
    --timeout "$timeout_seconds" \
    --disable-auto-eval \
    --enable-internet \
    --work-cpu-limit "$work_cpu_limit" \
    --judge-cpu-limit "$judge_cpu_limit" \
    --run-id "$run_id"
