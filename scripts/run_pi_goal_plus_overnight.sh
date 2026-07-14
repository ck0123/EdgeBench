#!/usr/bin/env bash

set -u
set -o pipefail

usage() {
    cat <<'EOF'
Usage: scripts/run_pi_goal_plus_overnight.sh [options]

Run six EdgeBench tasks sequentially, one task at a time.

Options:
  --start-at HH:MM   Wait until the next local occurrence of HH:MM before running.
  --from-task TASK   Start at TASK and run the remaining configured tasks.
  --dry-run          Print the planned commands without starting containers.
  --help             Show this help.

Environment overrides:
  MODEL              Model ID (default: gpt-5.5)
  TIMEOUT_SECONDS    Per-task agent runtime (default: 7200)
  WORK_CPU_LIMIT     CPU cap per work container (default: 3)
  JUDGE_CPU_LIMIT    CPU cap per judge container (default: 2)
  SFORGE_ENV_FILE    Private env file to source (default: ~/.config/sforge/agent.env)
  SFORGE_PI_AUTH_FILE
                      Pi auth file (default: ~/.pi/agent/auth.json)

The script adapts a localhost HTTP(S) proxy to host.docker.internal for work
containers. Node.js and npm default to npmmirror. Goal Plus is downloaded from
the latest upstream main branch; host Goal Plus checkouts are not copied.
EOF
}

start_at=""
from_task=""
dry_run=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --start-at)
            [[ $# -ge 2 ]] || { echo "--start-at requires HH:MM" >&2; exit 2; }
            start_at="$2"
            shift 2
            ;;
        --dry-run)
            dry_run=1
            shift
            ;;
        --from-task)
            [[ $# -ge 2 ]] || { echo "--from-task requires a task ID" >&2; exit 2; }
            from_task="$2"
            shift 2
            ;;
        --help|-h)
            usage
            exit 0
            ;;
        *)
            echo "Unknown option: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root" || exit 1

env_file="${SFORGE_ENV_FILE:-$HOME/.config/sforge/agent.env}"
if [[ -f "$env_file" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "$env_file"
    set +a
fi

# The host proxy listens on loopback. Inside Docker, loopback is the work
# container itself, so route the same proxy through Docker Desktop's host name.
adapt_proxy_for_container() {
    local proxy_url="$1"
    proxy_url="${proxy_url//127.0.0.1/host.docker.internal}"
    proxy_url="${proxy_url//localhost/host.docker.internal}"
    printf '%s' "$proxy_url"
}

host_http_proxy="${SFORGE_HTTP_PROXY:-${HTTP_PROXY:-${http_proxy:-}}}"
host_https_proxy="${SFORGE_HTTPS_PROXY:-${HTTPS_PROXY:-${https_proxy:-}}}"
if [[ -n "$host_http_proxy" ]]; then
    export SFORGE_HTTP_PROXY="$(adapt_proxy_for_container "$host_http_proxy")"
fi
if [[ -n "$host_https_proxy" ]]; then
    export SFORGE_HTTPS_PROXY="$(adapt_proxy_for_container "$host_https_proxy")"
fi

# Use domestic mirrors for the large repeated Node.js and npm downloads.
export SFORGE_NODEJS_MIRROR_URL="${SFORGE_NODEJS_MIRROR_URL:-https://npmmirror.com/mirrors/node}"
export SFORGE_NPM_REGISTRY_URL="${SFORGE_NPM_REGISTRY_URL:-https://registry.npmmirror.com}"

# Goal Plus is cloned from upstream main by the agent installer. C++ images
# install Python 3.10 from the configured Ubuntu mirror; Python images reuse
# their bundled runtime.
unset SFORGE_GOAL_PLUS_PYTHON_DIR

model="${MODEL:-gpt-5.5}"
timeout_seconds="${TIMEOUT_SECONDS:-7200}"
work_cpu_limit="${WORK_CPU_LIMIT:-3}"
judge_cpu_limit="${JUDGE_CPU_LIMIT:-2}"

if [[ -x "$repo_root/.venv/bin/sforge" ]]; then
    sforge_cmd=("$repo_root/.venv/bin/sforge")
    python_bin="$repo_root/.venv/bin/python"
elif command -v uv >/dev/null 2>&1; then
    sforge_cmd=(uv run sforge)
    python_bin="$(command -v python3 || command -v python)"
elif command -v sforge >/dev/null 2>&1; then
    sforge_cmd=(sforge)
    python_bin="$(command -v python3 || command -v python)"
else
    echo "Could not find .venv/bin/sforge, uv, or sforge." >&2
    exit 1
fi

batch_id="pi-gp-gpt55-2h-sequential-$(date '+%Y%m%d-%H%M%S')"
batch_log_dir="$repo_root/logs/overnight/$batch_id"
mkdir -p "$batch_log_dir"
master_log="$batch_log_dir/overnight.log"
exec > >(tee -a "$master_log") 2>&1

log() {
    printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S %Z')" "$*"
}

if [[ -n "$start_at" ]]; then
    if ! [[ "$start_at" =~ ^([01][0-9]|2[0-3]):[0-5][0-9]$ ]]; then
        echo "Invalid --start-at value: $start_at (expected HH:MM)" >&2
        exit 2
    fi
    target_epoch="$($python_bin - "$start_at" <<'PY'
import datetime as dt
import sys

now = dt.datetime.now().astimezone()
hour, minute = map(int, sys.argv[1].split(":"))
target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
if target <= now:
    target += dt.timedelta(days=1)
print(int(target.timestamp()))
PY
)"
    target_text="$(date -r "$target_epoch" '+%Y-%m-%d %H:%M:%S %Z')"
    if [[ "$dry_run" -eq 1 ]]; then
        log "Dry run would wait until $target_text"
    else
        wait_seconds=$((target_epoch - $(date +%s)))
        log "Waiting until $target_text ($wait_seconds seconds)"
        sleep "$wait_seconds"
    fi
fi

tasks=(
    ad_placement_optimization
    wireless_electricity_layout
    tree_block_partitioning
    triangulation_coloring_optimization
    apple_incremental_game
    vliw_kernel_optimization
)

if [[ -n "$from_task" ]]; then
    from_index=-1
    for index in "${!tasks[@]}"; do
        if [[ "${tasks[$index]}" == "$from_task" ]]; then
            from_index="$index"
            break
        fi
    done
    if [[ "$from_index" -lt 0 ]]; then
        echo "Unknown --from-task value: $from_task" >&2
        exit 2
    fi
    tasks=("${tasks[@]:$from_index}")
fi

required_images=(
    edgebench.base.cpp:19685ea8d3f4
    edgebench.base.python310:6fd084182df4
    edgebench.base.python:e4670062c1cb
    edgebench.work.ad_placement_optimization:49747cad3ebd
    edgebench.judge.ad_placement_optimization:56cbfc81cfa1
    edgebench.work.wireless_electricity_layout:c3179795f69f
    edgebench.judge.wireless_electricity_layout:1b918c76e808
    edgebench.work.tree_block_partitioning:f282a9f7e05a
    edgebench.judge.tree_block_partitioning:f74f0ef897ce
    edgebench.work.triangulation_coloring_optimization:d3af8893fa81
    edgebench.judge.triangulation_coloring_optimization:568aa1a5a8ff
    edgebench.work.apple_incremental_game:d3c6ed381c59
    edgebench.judge.apple_incremental_game:16968d8ec7f2
    edgebench.work.vliw_kernel_optimization:9fa380a0ebef
    edgebench.judge.vliw_kernel_optimization:5cdef0021634
)

missing=0
for task in "${tasks[@]}"; do
    if [[ ! -f "$repo_root/tasks/$task.json" ]]; then
        log "Missing task definition: tasks/$task.json"
        missing=1
    fi
done
for image in "${required_images[@]}"; do
    if ! docker image inspect "$image" >/dev/null 2>&1; then
        log "Missing image: $image"
        missing=1
    fi
done
if [[ "$missing" -ne 0 ]]; then
    log "Preflight failed; no task was started."
    exit 1
fi

task_count="${#tasks[@]}"
log "Plan: $task_count tasks, strictly sequential (maximum task concurrency: 1)"
log "Agent: pi-goal-plus; model: $model; timeout: ${timeout_seconds}s per task"
log "Resource caps: work=${work_cpu_limit} CPU, judge=${judge_cpu_limit} CPU"
log "Batch logs: $batch_log_dir"

pi_auth_file="${SFORGE_PI_AUTH_FILE:-$HOME/.pi/agent/auth.json}"
if [[ "$dry_run" -eq 0 && ! -f "$pi_auth_file" ]]; then
    log "Pi auth file is missing: $pi_auth_file"
    log "Log in with Pi first, or set SFORGE_PI_AUTH_FILE."
    exit 1
fi

judge_ready() {
    "$python_bin" -c 'import urllib.request; urllib.request.urlopen("http://127.0.0.1:8080/docs", timeout=2).read(1)' \
        >/dev/null 2>&1
}

judge_pid=""
cleanup() {
    if [[ -n "$judge_pid" ]] && kill -0 "$judge_pid" >/dev/null 2>&1; then
        log "Stopping judge server started by this script (PID $judge_pid)"
        kill "$judge_pid" >/dev/null 2>&1 || true
        wait "$judge_pid" 2>/dev/null || true
    fi
}
trap cleanup EXIT
trap 'exit 130' INT TERM

if [[ "$dry_run" -eq 0 ]]; then
    if judge_ready; then
        log "Reusing judge server already listening on port 8080"
    else
        judge_log="$batch_log_dir/judge-server.log"
        log "Starting judge server; log: $judge_log"
        "${sforge_cmd[@]}" serve --host 0.0.0.0 --port 8080 >"$judge_log" 2>&1 &
        judge_pid=$!
        for _ in $(seq 1 30); do
            judge_ready && break
            sleep 2
        done
        if ! judge_ready; then
            log "Judge server failed to become ready."
            exit 1
        fi
    fi
fi

overall_status=0
run_ids=()

for index in "${!tasks[@]}"; do
    task="${tasks[$index]}"
    task_number=$((index + 1))
    run_id="${batch_id}-t${task_number}"
    run_ids+=("$run_id")

    command=(
        "${sforge_cmd[@]}" run
        --task "$task"
        --agent pi-goal-plus
        --model "$model"
        --timeout "$timeout_seconds"
        --enable-internet
        --work-cpu-limit "$work_cpu_limit"
        --judge-cpu-limit "$judge_cpu_limit"
        --run-id "$run_id"
    )

    log "Task $task_number/$task_count: $task"
    if [[ "$dry_run" -eq 1 ]]; then
        printf '  '
        printf '%q ' "${command[@]}"
        printf '\n'
        continue
    fi

    "${command[@]}"
    rc=$?
    if [[ "$rc" -ne 0 ]]; then
        overall_status=1
        log "Task $task exited with status $rc; stopping the queue."
        break
    fi

    result_file="$repo_root/logs/runs/$run_id/$task/final_result.json"
    if ! "$python_bin" - "$result_file" "$timeout_seconds" <<'PY'
import json
import pathlib
import sys

result_path = pathlib.Path(sys.argv[1])
expected_runtime = int(sys.argv[2])
if not result_path.is_file():
    raise SystemExit(f"missing final result: {result_path}")
result = json.loads(result_path.read_text())
runtime = float(result.get("runtime_seconds") or 0)
if runtime < max(1, expected_runtime - 30):
    raise SystemExit(
        f"agent runtime was only {runtime:.1f}s; expected about {expected_runtime}s"
    )
PY
    then
        overall_status=1
        log "Task $task did not complete its agent runtime; stopping the queue."
        break
    fi

    log "Task $task completed."
    if ! "$python_bin" "$repo_root/scripts/report_edgebench_scores.py" \
        --run-dir "$repo_root/logs/runs/$run_id/$task" \
        --model "$model" \
        --budget-seconds "$timeout_seconds"
    then
        log "Score comparison failed for $task; the completed run is preserved."
    fi
done

if [[ "$dry_run" -eq 1 ]]; then
    log "Dry run complete; no task or judge container was started."
    exit 0
fi

log "All tasks finished. Result roots:"
for run_id in "${run_ids[@]}"; do
    log "  $repo_root/logs/runs/$run_id"
done
log "Master log: $master_log"
exit "$overall_status"
