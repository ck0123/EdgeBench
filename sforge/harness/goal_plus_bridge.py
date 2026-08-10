#!/usr/bin/env python
"""Materialize a Goal Plus promotion into an EdgeBench submission workspace.

This file is intentionally stdlib-only because the harness copies it directly
into benchmark work containers as a standalone executable.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
from typing import Any


class BridgeError(RuntimeError):
    pass


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BridgeError(f"cannot read Goal Plus state: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise BridgeError(f"Goal Plus state is not an object: {path}")
    return value


def write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        json.dump(value, handle, indent=2, sort_keys=True)
        handle.write("\n")
        temp_path = Path(handle.name)
    os.replace(temp_path, path)


def _sha256(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise BridgeError(f"submission artifact is not a regular file: {path}")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_relative(value: str) -> Path:
    path = Path(value)
    if not value or path.is_absolute() or ".." in path.parts:
        raise BridgeError(f"unsafe submission path: {value!r}")
    return path


def _latest_promotion(root: Path, run_id: str | None = None) -> dict[str, Any]:
    runs_dir = root / "runs"
    candidates: list[tuple[int, Path, dict[str, Any], str, Path]] = []
    run_paths = [runs_dir / run_id / "run.json"] if run_id else runs_dir.glob("*/run.json")
    for run_path in run_paths:
        if not run_path.is_file():
            continue
        run = _read_json(run_path)
        candidate_id = run.get("selected_candidate_id")
        if run.get("state") != "promoted" or not isinstance(candidate_id, str):
            continue
        patch_path = run_path.parent / "promotion" / f"{candidate_id}.patch"
        candidate_path = run_path.parent / "candidates" / candidate_id / "candidate.json"
        if not patch_path.is_file() or not candidate_path.is_file():
            continue
        candidates.append(
            (
                max(run_path.stat().st_mtime_ns, patch_path.stat().st_mtime_ns),
                run_path,
                run,
                candidate_id,
                candidate_path,
            )
        )
    if not candidates:
        suffix = f" {run_id}" if run_id else ""
        raise BridgeError(f"no complete promoted Goal Plus run found{suffix}")
    _, run_path, run, candidate_id, candidate_path = max(candidates, key=lambda item: item[0])
    return {
        "run_path": run_path,
        "run": run,
        "candidate_id": candidate_id,
        "candidate_path": candidate_path,
        "candidate": _read_json(candidate_path),
        "patch_path": run_path.parent / "promotion" / f"{candidate_id}.patch",
    }


def _latest_run(root: Path) -> tuple[Path, dict[str, Any]] | None:
    runs: list[tuple[str, int, Path, dict[str, Any]]] = []
    for run_path in (root / "runs").glob("*/run.json"):
        if not run_path.is_file():
            continue
        run = _read_json(run_path)
        runs.append(
            (
                str(run.get("created_at") or ""),
                run_path.stat().st_mtime_ns,
                run_path,
                run,
            )
        )
    if not runs:
        return None
    _, _, run_path, run = max(runs, key=lambda item: (item[0], item[1]))
    return run_path, run


def archive_best(
    *,
    root: Path,
    submit_paths: list[str],
    output: Path,
) -> dict[str, Any] | None:
    """Archive the latest run's exact verifier-backed best Git revision."""
    latest = _latest_run(root)
    if latest is None:
        return None
    run_path, run = latest
    candidate_id = run.get("best_candidate_id")
    if not isinstance(candidate_id, str) or not candidate_id:
        return None

    candidate_path = run_path.parent / "candidates" / candidate_id / "candidate.json"
    candidate = _read_json(candidate_path)
    score_report = candidate.get("score_report")
    if not isinstance(score_report, dict):
        raise BridgeError("best candidate has no score_report")
    iteration_number = score_report.get("best_iteration")
    commit = score_report.get("best_git_head")
    if not isinstance(iteration_number, int) or not isinstance(commit, str) or not commit:
        raise BridgeError("best candidate has no verifier-backed Git revision")

    iteration = next(
        (
            item
            for item in candidate.get("iterations") or []
            if isinstance(item, dict) and item.get("iteration") == iteration_number
        ),
        None,
    )
    if (
        iteration is None
        or iteration.get("process_passed") is not True
        or iteration.get("git_head") != commit
        or iteration.get("git_artifact_clean") is not True
        or iteration.get("touched_denied_files") is True
        or iteration.get("changed_outside_allowed") is True
        or iteration.get("score") != run.get("best_score")
    ):
        raise BridgeError("Goal Plus best fields do not identify one settled iteration")

    task = candidate.get("task")
    if not isinstance(task, dict) or not isinstance(task.get("workspace"), str):
        raise BridgeError("best candidate has no workspace in candidate.json")
    workspace = Path(task["workspace"]).resolve()
    if not workspace.is_dir():
        raise BridgeError(f"best candidate workspace is missing: {workspace}")

    resolved = subprocess.run(
        ["git", "-C", str(workspace), "rev-parse", "--verify", f"{commit}^{{commit}}"],
        text=True,
        capture_output=True,
    )
    if resolved.returncode != 0 or resolved.stdout.strip() != commit:
        raise BridgeError(f"best candidate Git revision is unavailable: {commit}")

    safe_submit_paths = [_safe_relative(value) for value in submit_paths]
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        dir=output.parent,
        prefix=f".{output.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        temp_path = Path(handle.name)
    try:
        archived = subprocess.run(
            [
                "git",
                "-C",
                str(workspace),
                "archive",
                "--format=tar.gz",
                f"--output={temp_path}",
                commit,
                "--",
                *(str(path) for path in safe_submit_paths),
            ],
            text=True,
            capture_output=True,
        )
        if archived.returncode != 0:
            raise BridgeError(
                "cannot archive Goal Plus best revision: "
                + (archived.stderr.strip() or archived.stdout.strip())
            )
        with tarfile.open(temp_path, "r:gz") as archive:
            if not any(not member.isdir() for member in archive.getmembers()):
                raise BridgeError("Goal Plus best archive contains no files")
        os.replace(temp_path, output)
    finally:
        if temp_path.exists():
            temp_path.unlink()

    return {
        "run_id": str(run.get("run_id") or run_path.parent.name),
        "candidate_id": candidate_id,
        "iteration": iteration_number,
        "commit": commit,
        "local_score": iteration.get("score"),
    }


def sync_promotion(
    *,
    root: Path,
    source: Path,
    submit_paths: list[str],
    run_id: str | None = None,
) -> dict[str, Any]:
    promotion = _latest_promotion(root, run_id)
    run = promotion["run"]
    candidate = promotion["candidate"]
    candidate_id = promotion["candidate_id"]
    patch_path: Path = promotion["patch_path"]

    run_source = Path(str(run.get("source_path", ""))).resolve()
    source = source.resolve()
    if run_source != source:
        raise BridgeError(
            f"promoted run targets {run_source}, but EdgeBench submits {source}"
        )
    task = candidate.get("task")
    if not isinstance(task, dict) or not isinstance(task.get("workspace"), str):
        raise BridgeError("selected candidate has no workspace in candidate.json")
    candidate_workspace = Path(task["workspace"]).resolve()
    if not candidate_workspace.is_dir():
        raise BridgeError(f"selected candidate workspace is missing: {candidate_workspace}")

    patch_text = patch_path.read_text(encoding="utf-8", errors="replace")
    changed = candidate.get("detected_changed_files")
    changed_files = set(changed) if isinstance(changed, list) else set()
    safe_submit_paths = [_safe_relative(value) for value in submit_paths]
    if not patch_text.strip() or not any(str(path) in changed_files for path in safe_submit_paths):
        raise BridgeError(
            "selected promotion contains no change to an EdgeBench submitted file"
        )

    file_rows: list[dict[str, str]] = []
    pending: list[tuple[Path, Path]] = []
    for relative in safe_submit_paths:
        candidate_file = candidate_workspace / relative
        source_file = source / relative
        if not candidate_file.is_file() or candidate_file.is_symlink():
            raise BridgeError(
                f"selected candidate is missing submitted file: {relative}"
            )
        candidate_hash = _sha256(candidate_file)
        source_hash = _sha256(source_file) if source_file.exists() else ""
        file_rows.append(
            {
                "path": str(relative),
                "source_sha256_before": source_hash,
                "candidate_sha256": candidate_hash,
            }
        )
        if source_hash != candidate_hash:
            pending.append((candidate_file, source_file))

    for candidate_file, source_file in pending:
        source_file.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            dir=source_file.parent,
            prefix=f".{source_file.name}.",
            suffix=".sforge-gp.tmp",
            delete=False,
        ) as handle:
            temp_path = Path(handle.name)
        try:
            shutil.copy2(candidate_file, temp_path)
            os.replace(temp_path, source_file)
        finally:
            if temp_path.exists():
                temp_path.unlink()

    for row in file_rows:
        target = source / row["path"]
        actual = _sha256(target)
        if actual != row["candidate_sha256"]:
            raise BridgeError(f"materialized hash mismatch: {row['path']}")
        row["source_sha256_after"] = actual

    fingerprint_payload = {
        "run_id": run.get("run_id"),
        "candidate_id": candidate_id,
        "files": {row["path"]: row["candidate_sha256"] for row in file_rows},
    }
    fingerprint = hashlib.sha256(
        json.dumps(fingerprint_payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    score_report = candidate.get("score_report")
    local_score = (
        score_report.get("aggregate_score")
        if isinstance(score_report, dict)
        else None
    )
    result = {
        "status": "materialized" if pending else "already_materialized",
        "run_id": run.get("run_id"),
        "candidate_id": candidate_id,
        "local_score": local_score,
        "source_path": str(source),
        "promotion_artifact_path": str(patch_path.resolve()),
        "fingerprint": fingerprint,
        "files": file_rows,
    }
    write_json_atomic(root / "edgebench" / "latest-materialization.json", result)
    return result


def submit_promotion(
    sync_result: dict[str, Any],
    *,
    root: Path,
    details: bool,
    if_new: bool,
) -> int:
    marker_path = root / "edgebench" / "latest-submission.json"
    if if_new and marker_path.is_file():
        marker = _read_json(marker_path)
        if marker.get("fingerprint") == sync_result["fingerprint"]:
            print(
                json.dumps(
                    {
                        "goal_plus_sync": sync_result,
                        "judge_submission": "already_submitted",
                        "previous_result": marker.get("judge_result"),
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
            return 0

    command = ["sforge-submit"]
    if details:
        command.append("--details")
    completed = subprocess.run(command, text=True, capture_output=True)
    if completed.stdout:
        print(completed.stdout, end="")
    if completed.stderr:
        print(completed.stderr, end="", file=sys.stderr)
    if completed.returncode != 0:
        return completed.returncode

    judge_result_path = Path("/tmp/sforge_last_submit.json")
    judge_result = _read_json(judge_result_path) if judge_result_path.is_file() else None
    marker = {
        "fingerprint": sync_result["fingerprint"],
        "run_id": sync_result["run_id"],
        "candidate_id": sync_result["candidate_id"],
        "judge_result": judge_result,
    }
    write_json_atomic(marker_path, marker)
    return 0


def _parse_args(argv: list[str]) -> argparse.Namespace:
    default_submit = Path(sys.argv[0]).name.endswith("-submit")
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id")
    parser.add_argument("--submit", action="store_true", default=default_submit)
    parser.add_argument("--details", action="store_true")
    parser.add_argument("--if-new", action="store_true")
    parser.add_argument("--archive-best", metavar="PATH")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    root = Path(os.environ.get("GOAL_PLUS_ROOT", ".gp")).resolve()
    source = Path(os.environ.get("SFORGE_PATCH_DIR", os.getcwd())).resolve()
    submit_paths = shlex.split(os.environ.get("SFORGE_SUBMIT_PATHS", ""))
    if not submit_paths:
        print("ERROR: SFORGE_SUBMIT_PATHS is empty", file=sys.stderr)
        return 2
    try:
        if args.archive_best:
            result = archive_best(
                root=root,
                submit_paths=submit_paths,
                output=Path(args.archive_best),
            )
            print(json.dumps({"goal_plus_best": result}, indent=2, sort_keys=True))
            return 0
        result = sync_promotion(
            root=root,
            source=source,
            submit_paths=submit_paths,
            run_id=args.run_id,
        )
        print(json.dumps({"goal_plus_sync": result}, indent=2, sort_keys=True))
        if args.submit:
            return submit_promotion(
                result,
                root=root,
                details=args.details,
                if_new=args.if_new,
            )
        return 0
    except BridgeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
