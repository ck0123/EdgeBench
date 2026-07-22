#!/usr/bin/env bash

set -u

interval="${INTERVAL:-5}"
run_limit="${RUN_LIMIT:-3}"
once=0
containers=()

usage() {
  cat <<'EOF'
Usage: ./scripts/monitor_goal_plus.sh [options] [container ...]

Monitor any running EdgeBench Goal Plus task.

With no container arguments, all running SForge work containers whose main
agent has GOAL_PLUS_ROLE=main are discovered automatically. Legacy Pi + Goal
Plus containers exposing only GOAL_PLUS_PI_ROLE=main are also supported.

Options:
  --once              Print one snapshot and exit.
  --interval SECONDS  Refresh interval (default: INTERVAL or 5).
  -h, --help          Show this help.

Environment:
  INTERVAL=2          Default refresh interval.
  RUN_LIMIT=3         Number of newest GP runs shown per container; 0 shows all.

Examples:
  ./scripts/monitor_goal_plus.sh
  INTERVAL=2 ./scripts/monitor_goal_plus.sh
  ./scripts/monitor_goal_plus.sh --once
  ./scripts/monitor_goal_plus.sh sforge.run.some_task.some_run_id
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --once)
      once=1
      shift
      ;;
    --interval)
      if [[ -z "${2:-}" ]]; then
        echo "--interval requires a value" >&2
        exit 2
      fi
      interval="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    --*)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
    *)
      containers+=("$1")
      shift
      ;;
  esac
done

if ! [[ "$interval" =~ ^[0-9]+([.][0-9]+)?$ ]]; then
  echo "Invalid interval: $interval" >&2
  exit 2
fi

if ! [[ "$run_limit" =~ ^[0-9]+$ ]]; then
  echo "Invalid RUN_LIMIT: $run_limit" >&2
  exit 2
fi

discover_containers() {
  local name
  while IFS= read -r name; do
    if docker inspect "$name" \
      --format '{{range .Config.Env}}{{println .}}{{end}}' 2>/dev/null \
      | grep -Eq '^GOAL_PLUS_(ROLE|PI_ROLE|CODEX_ROLE)=main$'; then
      printf '%s\n' "$name"
    fi
  done < <(docker ps --filter 'name=sforge.run.' --format '{{.Names}}')
}

while true; do
  targets=()
  if [[ "${#containers[@]}" -gt 0 ]]; then
    targets=("${containers[@]}")
  else
    while IFS= read -r container; do
      [[ -n "$container" ]] && targets+=("$container")
    done < <(discover_containers)
  fi

  [[ -t 1 ]] && clear
  date '+%Y-%m-%d %H:%M:%S %Z'

  if [[ "${#targets[@]}" -eq 0 ]]; then
    echo
    echo "No running EdgeBench Goal Plus work container found."
    if [[ "$once" -eq 1 ]]; then
      exit 1
    fi
    sleep "$interval"
    continue
  fi

  for container in "${targets[@]}"; do
    printf '\n================================================================================\n'
    printf 'container: %s\n' "$container"
    printf '================================================================================\n\n'

    if ! docker inspect "$container" >/dev/null 2>&1; then
      echo "Container has stopped or was removed."
      continue
    fi

    docker exec -e GP_MONITOR_RUN_LIMIT="$run_limit" -i "$container" python3 - <<'PY'
import glob
import json
import math
import os
import subprocess
import time
from pathlib import Path


root = Path("/home/agent/.goal-plus")


def load(path):
    try:
        with open(path) as handle:
            return json.load(handle)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def score_text(value):
    if not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        return "-"
    number = float(value)
    return str(int(number)) if number.is_integer() else f"{number:.6f}"


def short(text, limit=110):
    value = " ".join(str(text or "").split())
    return value if len(value) <= limit else value[: limit - 3] + "..."


try:
    gp_commit = subprocess.check_output(
        ["git", "-C", "/opt/goal-plus", "rev-parse", "--short", "HEAD"],
        text=True,
        stderr=subprocess.DEVNULL,
    ).strip()
except Exception:
    gp_commit = "unknown"

deadline_path = Path("/opt/sforge-agent-deadline")
remaining = None
if deadline_path.exists():
    try:
        remaining = max(0, int(deadline_path.read_text().strip()) - int(time.time()))
    except ValueError:
        pass

print(
    "host:",
    "codex" if os.environ.get("GOAL_PLUS_CODEX_ROLE") == "main" else "pi",
    "model:",
    os.environ.get(
        "GOAL_PLUS_CODEX_MODEL",
        os.environ.get(
            "GOAL_PLUS_PI_MODEL",
            os.environ.get("CODEX_MODEL", os.environ.get("PI_MODEL", "unknown")),
        ),
    ),
    "  goal-plus:",
    gp_commit,
    "  remaining:",
    f"{remaining // 60}m{remaining % 60:02d}s" if remaining is not None else "-",
)
print(
    "workspace:",
    Path(os.environ.get("SFORGE_PATCH_DIR", os.getcwd())).name,
    "  submitted:",
    os.environ.get("SFORGE_SUBMIT_PATHS", "-"),
)

goal_paths = sorted((root / "goal-plus").glob("gp_*/goal.json"))
if goal_paths:
    goal = load(goal_paths[-1])
    print(
        "goal:",
        goal.get("status", "-"),
        "phase=", goal.get("phase", "-"),
        "revision=", goal.get("goal_revision", "-"),
    )
print()

jobs_by_run = {}
pool_paths = sorted((root / "host-pools" / "pi").glob("pool_*/pool.json"))
for pool_path in pool_paths:
    pool = load(pool_path)
    run_id = pool.get("run_id")
    jobs = []
    for job_path in sorted(pool_path.parent.glob("jobs/job_*/job.json")):
        job = load(job_path)
        jobs.append(job)
        if run_id and job.get("candidate_id"):
            jobs_by_run[(run_id, job["candidate_id"])] = job
    counts = {}
    for job in jobs:
        state = job.get("status", "unknown")
        counts[state] = counts.get(state, 0) + 1
    count_text = ", ".join(f"{key}={value}" for key, value in sorted(counts.items()))
    print(
        f"pool {pool.get('pool_id', pool_path.parent.name)}: "
        f"state={pool.get('state', '-')} parallel={pool.get('max_parallel', '-')} "
        f"{count_text or 'no jobs'}"
    )
if pool_paths:
    print()

run_paths = sorted((root / "runs").glob("run_*/run.json"))
if not run_paths:
    print("No frozen Search run yet; main agent is still preparing the spec.")

run_limit = int(os.environ.get("GP_MONITOR_RUN_LIMIT", "3"))
if run_limit > 0 and len(run_paths) > run_limit:
    print(f"Showing newest {run_limit} of {len(run_paths)} runs; set RUN_LIMIT=0 for all.\n")
    run_paths = run_paths[-run_limit:]

for run_path in run_paths:
    run = load(run_path)
    run_id = run.get("run_id", run_path.parent.name)
    frozen = load(root / "specs" / str(run.get("frozen_spec_id")) / "frozen_spec.json")
    spec = frozen.get("spec", {})
    direction = spec.get("metric_direction", "maximize")
    budget = spec.get("budget", {})
    print(
        f"{run_id}: state={run.get('state', '-')} direction={direction} "
        f"runtime_best={score_text(run.get('best_score'))} "
        f"candidates={run.get('candidates_total', 0)}/{budget.get('max_candidates', '-')} "
        f"parallel={budget.get('max_parallel', '-')}"
    )

    candidate_paths = sorted(run_path.parent.glob("candidates/*/candidate.json"))
    for candidate_path in candidate_paths:
        candidate = load(candidate_path)
        candidate_id = candidate.get("candidate_id", candidate_path.parent.name)
        iterations = candidate.get("iterations") or []
        ledger = candidate.get("results_ledger") or []
        entries = ledger or iterations
        numeric = [
            entry
            for entry in entries
            if isinstance(entry.get("score"), (int, float))
            and math.isfinite(float(entry["score"]))
            and (
                entry.get("status") == "pass"
                if ledger
                else entry.get("process_passed") is True
            )
        ]
        best = None
        if numeric:
            chooser = min if direction == "minimize" else max
            best = chooser(numeric, key=lambda entry: float(entry["score"]))
        latest = entries[-1] if entries else {}
        job = jobs_by_run.get((run_id, candidate_id), {})
        worker_state = job.get("status", "-")
        error = job.get("error")
        print(
            f"  {candidate_id}: worker={worker_state} candidate={candidate.get('status', '-')} "
            f"iterations={len(iterations)} ledger={len(ledger)} "
            f"latest={score_text(latest.get('score'))}({latest.get('status', '-')}) "
            f"best={score_text(best.get('score') if best else None)}"
        )
        task = candidate.get("task") or {}
        proposal = task.get("proposal") or {}
        metadata = proposal.get("metadata") or {}
        action = metadata.get("search_action") or "-"
        family = metadata.get("feature_family") or metadata.get("focus") or "-"
        intent = (
            proposal.get("intent")
            or proposal.get("hypothesis")
            or task.get("hypothesis")
            or "-"
        )
        print(f"    decision={action}/{family}: {short(intent, 165)}")
        if latest:
            print(
                f"    latest i{latest.get('iteration', '?')} "
                f"git={str(latest.get('git_head') or '')[:7] or '-'} "
                f"ledger_git={str(latest.get('ledger_git_head') or '')[:7] or '-'} "
                f"{short(latest.get('hypothesis'))}"
            )
        if error:
            print("    worker error:", short(error))
    print()

submission_path = root / "edgebench" / "latest-submission.json"
if submission_path.exists():
    submission = load(submission_path)
    report = submission.get("judge_result", {}).get("report", {})
    print(
        "judge:",
        f"run={submission.get('run_id', '-')}",
        f"candidate={submission.get('candidate_id', '-')}",
        f"raw={score_text(report.get('score'))}",
        f"official={score_text(report.get('score_0_100'))}",
        f"valid={report.get('valid', '-')}",
    )
PY
  done

  if [[ "$once" -eq 1 ]]; then
    exit 0
  fi
  sleep "$interval"
done
