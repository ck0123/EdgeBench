"""Emit a compact live snapshot from durable Goal Plus state.

This script intentionally uses only the standard library.  The full Goal Plus
monitor includes rich observability and log inspection; SForge polls this much
smaller view during a timed benchmark run.
"""

from __future__ import annotations

import argparse
import json
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
    terminal = {"complete", "blocked", "abandoned"}
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
        goal_statuses.append(
            {
                key: payload.get(key)
                for key in ("goal_plus_id", "status", "phase", "updated_at")
                if payload.get(key) is not None
            }
        )
        terminal_records.append(
            {
                "status": str(payload.get("status") or "active"),
                "reports_ready": reports_ready,
            }
        )

    candidate_keys: set[tuple[str, str]] = set()
    candidate_ids: set[str] = set()
    worker_sessions: list[dict[str, Any]] = []
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
                        "verifier_runs": verifier_runs,
                        "updated_at": session.get("updated_at"),
                    }.items()
                    if value is not None
                }
            )
            handle = session.get("host_handle")
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
        record["status"] in terminal and record["reports_ready"]
        for record in terminal_records
    )
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
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    args = parser.parse_args()
    print(json.dumps(build_snapshot(args.root), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
