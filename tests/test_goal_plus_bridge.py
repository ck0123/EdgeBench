from __future__ import annotations

import json
from pathlib import Path

import pytest

from sforge.harness.goal_plus_bridge import BridgeError, sync_promotion


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _promotion_fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    root = tmp_path / ".goal-plus"
    source = tmp_path / "source"
    workspace = root / "runs" / "run_1" / "workspace" / "c001"
    source.mkdir()
    workspace.mkdir(parents=True)
    (source / "solution.cpp").write_text("baseline\n", encoding="utf-8")
    (workspace / "solution.cpp").write_text("optimized\n", encoding="utf-8")

    run_dir = root / "runs" / "run_1"
    _write_json(
        run_dir / "run.json",
        {
            "run_id": "run_1",
            "state": "promoted",
            "source_path": str(source),
            "selected_candidate_id": "c001",
        },
    )
    _write_json(
        run_dir / "candidates" / "c001" / "candidate.json",
        {
            "candidate_id": "c001",
            "detected_changed_files": ["solution.cpp"],
            "score_report": {"aggregate_score": 12.5},
            "task": {"workspace": str(workspace)},
        },
    )
    promotion = run_dir / "promotion" / "c001.patch"
    promotion.parent.mkdir()
    promotion.write_text("diff --git a/solution.cpp b/solution.cpp\n", encoding="utf-8")
    return root, source, workspace


def test_sync_promotion_materializes_selected_candidate(tmp_path: Path) -> None:
    root, source, _ = _promotion_fixture(tmp_path)

    result = sync_promotion(
        root=root,
        source=source,
        submit_paths=["solution.cpp"],
    )

    assert result["status"] == "materialized"
    assert result["run_id"] == "run_1"
    assert result["candidate_id"] == "c001"
    assert result["local_score"] == 12.5
    assert (source / "solution.cpp").read_text(encoding="utf-8") == "optimized\n"
    assert result["files"][0]["candidate_sha256"] == result["files"][0][
        "source_sha256_after"
    ]
    receipt = json.loads(
        (root / "edgebench" / "latest-materialization.json").read_text(
            encoding="utf-8"
        )
    )
    assert receipt["fingerprint"] == result["fingerprint"]

    repeated = sync_promotion(
        root=root,
        source=source,
        submit_paths=["solution.cpp"],
    )
    assert repeated["status"] == "already_materialized"
    assert repeated["fingerprint"] == result["fingerprint"]


def test_sync_promotion_rejects_no_submitted_change(tmp_path: Path) -> None:
    root, source, _ = _promotion_fixture(tmp_path)
    candidate_path = root / "runs" / "run_1" / "candidates" / "c001" / "candidate.json"
    candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
    candidate["detected_changed_files"] = ["notes.txt"]
    _write_json(candidate_path, candidate)

    with pytest.raises(BridgeError, match="no change"):
        sync_promotion(root=root, source=source, submit_paths=["solution.cpp"])


def test_sync_promotion_rejects_different_source_workspace(tmp_path: Path) -> None:
    root, source, _ = _promotion_fixture(tmp_path)

    with pytest.raises(BridgeError, match="but EdgeBench submits"):
        sync_promotion(
            root=root,
            source=source.parent / "other",
            submit_paths=["solution.cpp"],
        )
