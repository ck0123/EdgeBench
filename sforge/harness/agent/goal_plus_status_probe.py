"""Emit a compact live snapshot from durable Goal Plus state.

This script intentionally uses only the standard library.  The full Goal Plus
monitor includes rich observability and log inspection; SForge polls this much
smaller view during a timed benchmark run.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_ROOT = Path("/home/agent/.goal-plus")


def _load(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _runtime_file_exists(value: object, root: Path) -> bool:
    if not isinstance(value, str) or not value:
        return False
    path = Path(value)
    try:
        path = root / path.relative_to(DEFAULT_ROOT)
    except ValueError:
        pass
    return path.is_file()


def build_snapshot(root: Path) -> dict[str, Any]:
    goal_statuses: list[dict[str, Any]] = []
    terminal = {"complete", "abandoned"}
    terminal_records: list[dict[str, Any]] = []
    for path in sorted((root / "goal-plus").glob("*/goal.json")):
        payload = _load(path)
        if payload is None:
            continue
        reports_ready = True
        for task in payload.get("search_tasks") or []:
            if not isinstance(task, dict) or not task.get("result_recorded_at"):
                continue
            reports_ready = reports_ready and all(
                _runtime_file_exists(task.get(key), root)
                for key in ("report_path", "html_report_path")
            )
        active_session = payload.get("active_session")
        compact_session = (
            {
                key: active_session.get(key)
                for key in ("host", "session_id", "state")
                if active_session.get(key) is not None
            }
            if isinstance(active_session, dict)
            else None
        )
        compact_status = {
            key: payload.get(key)
            for key in ("goal_plus_id", "goal_revision", "status", "phase", "updated_at", "control")
            if payload.get(key) is not None
        }
        if compact_session:
            compact_status["active_session"] = compact_session
        goal_statuses.append(compact_status)
        terminal_records.append(
            {
                "goal_plus_id": str(payload.get("goal_plus_id") or path.parent.name),
                "status": str(payload.get("status") or "active"),
                "reports_ready": reports_ready,
                "active_session": compact_session,
                "goal_revision": payload.get("goal_revision"),
                "control": payload.get("control"),
            }
        )

    candidate_keys: set[tuple[str, str]] = set()
    candidate_ids: set[str] = set()
    worker_sessions: list[dict[str, Any]] = []
    confirmed_launches: dict[str, int] = {}
    bound_worker_handles: list[dict[str, Any]] = []
    verifier_ledger: list[dict[str, Any]] = []
    verifier_candidate_ids: set[str] = set()
    selected_candidate_ids: set[str] = set()
    promoted_candidate_ids: set[str] = set()
    search_run_states: dict[str, int] = {}
    session_verifier_runs = 0
    annotation_tasks = 0
    annotation_attempts = 0
    annotation_views_published = 0
    annotation_states: dict[str, int] = {}
    annotation_monitors: list[dict[str, Any]] = []

    for run_path in sorted((root / "runs").glob("*/run.json")):
        run = _load(run_path)
        if run is None:
            continue
        run_id = str(run.get("run_id") or run_path.parent.name)
        state = str(run.get("state") or "unknown")
        search_run_states[state] = search_run_states.get(state, 0) + 1
        selected = run.get("selected_candidate_id")
        if isinstance(selected, str) and selected:
            selected_candidate_ids.add(selected)
            if state == "promoted":
                promoted_candidate_ids.add(selected)

        for annotation_path in sorted(
            run_path.parent.glob(
                "candidates/*/evidence-annotations/iteration-*.json"
            )
        ):
            annotation = _load(annotation_path)
            if annotation is None:
                continue
            annotation_tasks += 1
            annotation_attempts += int(annotation.get("attempts") or 0)
            state = str(annotation.get("state") or "unknown")
            annotation_states[state] = annotation_states.get(state, 0) + 1
            if isinstance(annotation.get("view"), dict):
                annotation_views_published += 1

        for monitor_path in sorted(
            (run_path.parent / "evidence-annotator" / "attempts").glob("*.json")
        ):
            monitor = _load(monitor_path)
            if monitor is None:
                continue
            annotation_monitors.append(
                {
                    key: monitor.get(key)
                    for key in (
                        "run_id",
                        "candidate_id",
                        "iteration",
                        "attempt",
                        "host",
                        "model",
                        "reasoning_effort",
                        "timeout_seconds",
                        "state",
                        "started_at",
                        "updated_at",
                        "elapsed_seconds",
                        "pid",
                        "process_returncode",
                        "stdout_bytes",
                        "stderr_bytes",
                        "json_lines",
                        "non_json_lines",
                        "event_type_counts",
                        "assistant_event_type_counts",
                        "last_events",
                        "detail",
                    )
                    if monitor.get(key) is not None
                }
            )

        for candidate_path in sorted(
            (run_path.parent / "candidates").glob("*/candidate.json")
        ):
            candidate = _load(candidate_path)
            if candidate is None:
                continue
            candidate_id = str(
                candidate.get("candidate_id") or candidate_path.parent.name
            )
            candidate_keys.add((run_id, candidate_id))
            candidate_ids.add(candidate_id)
            for iteration in candidate.get("iterations") or []:
                if not isinstance(iteration, dict):
                    continue
                verifier_candidate_ids.add(candidate_id)
                verifier_ledger.append(
                    {
                        key: value
                        for key, value in {
                            "run_id": run_id,
                            "candidate_id": candidate_id,
                            "iteration": iteration.get("iteration"),
                            "process_passed": iteration.get("process_passed"),
                            "aggregate_score": iteration.get("score"),
                            "disposition": iteration.get("disposition"),
                            "created_at": iteration.get("created_at"),
                        }.items()
                        if value is not None
                    }
                )

        for session_path in sorted(
            (run_path.parent / "agent_sessions").glob("*.json")
        ):
            session = _load(session_path)
            if session is None:
                continue
            session_id = str(session.get("agent_session_id") or session_path.stem)
            counters = session.get("counters")
            counters = counters if isinstance(counters, dict) else {}
            verifier_runs = int(counters.get("verifier_runs") or 0)
            session_verifier_runs += verifier_runs
            worker_sessions.append(
                {
                    key: value
                    for key, value in {
                        "agent_session_id": session_id,
                        "run_id": session.get("run_id") or run_id,
                        "candidate_id": session.get("candidate_id"),
                        "host": session.get("host"),
                        "execution_generation": session.get("execution_generation", 0),
                        "verifier_runs": verifier_runs,
                        "updated_at": session.get("updated_at"),
                    }.items()
                    if value is not None
                }
            )
            handle = session.get("host_handle")
            if verifier_runs or isinstance(handle, dict) and (handle.get("metadata") or {}).get("bound_at"):
                confirmed_launches[session_id] = int(session.get("execution_generation") or 0)
            if isinstance(handle, dict):
                compact = {
                    key: handle.get(key)
                    for key in ("host", "task_name", "external_id")
                    if handle.get(key) is not None
                }
                if compact:
                    bound_worker_handles.append(
                        {"agent_session_id": session_id, **compact}
                    )

    ready = bool(terminal_records) and all(
        (record["status"] in terminal or (record.get("control") or {}).get("state") == "closed"
         or record["status"] == "blocked" and record.get("control") is None) and record["reports_ready"]
        for record in terminal_records
    )
    unfinished_records = [
        record for record in terminal_records if record["status"] not in terminal
        and (record.get("control") or {}).get("state") != "closed"
    ]
    continuation_blockers = [
        {
            "goal_plus_id": record["goal_plus_id"],
            "reason": (
                "goal_not_active"
                if record["status"] != "active"
                else "stop_not_controller_retryable"
                if (record.get("control") or {}).get("reason") not in {"execution_lost", "harness_interruption"}
                else "missing_attached_native_session"
                if not isinstance(record["active_session"], dict)
                or not record["active_session"].get("session_id")
                else f"native_session_{record['active_session'].get('state', 'unknown')}"
            ),
        }
        for record in unfinished_records
        if record["status"] != "active"
        or (record.get("control") or {}).get("state") != "paused"
        or (record.get("control") or {}).get("reason") not in {"execution_lost", "harness_interruption"}
        or not isinstance(record.get("goal_revision"), int)
        or not isinstance((record.get("control") or {}).get("version"), int)
        or (record.get("control") or {}).get("version", -1) < 0
        or not isinstance(record["active_session"], dict)
        or record["active_session"].get("state") != "attached"
        or not record["active_session"].get("session_id")
    ]
    continuation_ready = len(unfinished_records) == 1 and not continuation_blockers
    resume_expectation = None
    if continuation_ready:
        record = unfinished_records[0]
        resume_expectation = {
            "goal_plus_id": record["goal_plus_id"], "goal_revision": record["goal_revision"],
            "session_id": record["active_session"]["session_id"],
            "control_version": record["control"]["version"],
        }
    # Pi's host-owned job records describe actual launch intervals, not merely
    # allocated Goal Plus sessions. Missing process evidence stays unknown.
    pi_live = 0
    pi_live_known = True
    pi_jobs = list((root / "host-pools/pi").glob("*/jobs/*/job.json"))
    generations = {s["agent_session_id"]: s["execution_generation"] for s in worker_sessions}
    for path in pi_jobs:
        job = _load(path)
        if not job:
            pi_live_known = False
            continue
        sid = job.get("agent_session_id")
        if job.get("started_at") and sid in generations:
            confirmed_launches[sid] = generations[sid]
        if job.get("status") not in {"starting", "running"}:
            continue
        pid = job.get("pid")
        if not isinstance(pid, int) or pid <= 1:
            pi_live_known = False
            continue
        try:
            process = subprocess.run(["ps", "-p", str(pid), "-o", "stat="], capture_output=True, text=True, timeout=2)
            if process.returncode == 0 and process.stdout.strip() and not process.stdout.strip().startswith("Z"):
                pi_live += 1
            elif process.returncode not in {0, 1}:
                pi_live_known = False
        except (OSError, subprocess.TimeoutExpired):
            pi_live_known = False
    generation_counts: dict[str, int] = {}
    for generation in confirmed_launches.values():
        generation_counts[str(generation)] = generation_counts.get(str(generation), 0) + 1
    annotation_monitors.sort(
        key=lambda item: (
            str(item.get("updated_at") or ""),
            str(item.get("candidate_id") or ""),
            int(item.get("iteration") or 0),
            int(item.get("attempt") or 0),
        )
    )
    active_monitor_states = {"starting", "running", "process_exited"}
    active_annotation_monitors = [
        item
        for item in annotation_monitors
        if item.get("state") in active_monitor_states
    ]
    recent_annotation_monitors = annotation_monitors[-8:]
    return {
        "schema_version": 1,
        "captured_at": datetime.now(timezone.utc)
        .isoformat()
        .replace("+00:00", "Z"),
        "candidate_ids": sorted(candidate_ids),
        "candidate_count": len(candidate_keys),
        "worker_sessions": worker_sessions,
        "agent_session_count": len(worker_sessions),
        "confirmed_worker_launch_count": len(confirmed_launches),
        "worker_launches_by_generation": generation_counts,
        "pi_live_worker_count": pi_live if pi_jobs and pi_live_known else None,
        "bound_worker_handles": bound_worker_handles,
        "actual_worker_launch_count": len(
            {
                (item.get("host"), item.get("external_id"), item.get("task_name"))
                for item in bound_worker_handles
            }
        ),
        "verifier_ledger": verifier_ledger,
        "worker_verifier_runs": max(session_verifier_runs, len(verifier_ledger)),
        "verifier_candidate_ids": sorted(verifier_candidate_ids),
        "selected_candidate_ids": sorted(selected_candidate_ids),
        "promoted_candidate_ids": sorted(promoted_candidate_ids),
        "search_run_states": dict(sorted(search_run_states.items())),
        "evidence_annotations": {
            "tasks": annotation_tasks,
            "attempts": annotation_attempts,
            "views_published": annotation_views_published,
            "states": dict(sorted(annotation_states.items())),
            "active_attempts": active_annotation_monitors,
            "recent_attempts": active_annotation_monitors
            + [
                item
                for item in recent_annotation_monitors
                if item not in active_annotation_monitors
            ],
            "monitor_files": len(annotation_monitors),
        },
        "goal_statuses": goal_statuses,
        "terminal_ready": ready,
        "native_continuation_ready": continuation_ready,
        "resume_expectation": resume_expectation,
        "native_continuation_blockers": continuation_blockers,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    args = parser.parse_args()
    print(json.dumps(build_snapshot(args.root), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
