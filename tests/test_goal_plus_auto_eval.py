from __future__ import annotations

import logging
from pathlib import Path

import sforge.harness.run_agent as run_agent
from sforge.harness.agent.codex_goal_plus import CodexGoalPlusAgent
from sforge.harness.agent.goal_plus_runtime import (
    GOAL_PLUS_EXTERNAL_EVIDENCE_ENV,
    GOAL_PLUS_EXTERNAL_EVIDENCE_FILE,
)
from sforge.harness.config import SForgeConfig
from sforge.harness.run_agent import _build_agent_env, _compact_auto_eval_record


class _OneTickEvent:
    def __init__(self) -> None:
        self.waits = 0
        self.stopped = False

    def wait(self, timeout: float) -> bool:
        self.waits += 1
        if self.waits > 1:
            self.stopped = True
        return self.stopped

    def is_set(self) -> bool:
        return self.stopped


def test_goal_plus_auto_eval_external_evidence_is_enabled_by_default() -> None:
    env = _build_agent_env(CodexGoalPlusAgent(SForgeConfig()), "gpt-5.5")

    assert env[GOAL_PLUS_EXTERNAL_EVIDENCE_ENV] == GOAL_PLUS_EXTERNAL_EVIDENCE_FILE

    disabled = _build_agent_env(
        CodexGoalPlusAgent(
            SForgeConfig(
                agent_extra_env={GOAL_PLUS_EXTERNAL_EVIDENCE_ENV: ""},
            )
        ),
        "gpt-5.5",
    )
    assert disabled[GOAL_PLUS_EXTERNAL_EVIDENCE_ENV] == ""


def test_compact_auto_eval_record_keeps_actionable_official_feedback() -> None:
    record = _compact_auto_eval_record(
        artifact={
            "source": "goal_plus_best",
            "run_id": "run_1",
            "candidate_id": "c002",
            "iteration": 3,
            "commit": "abc123",
            "local_score": 12.0,
        },
        submission_id="submission-1",
        round_id="auto-1",
        result={
            "status": "completed",
            "error": None,
            "report": {
                "valid": True,
                "passed": 1,
                "total_tests": 2,
                "failed": 1,
                "pass_rate": 0.5,
                "score": 42,
                "score_0_100": 73.5,
                "summary": "case_slow timed out",
                "metrics": {"runtime_ms": 1200},
                "details": [
                    {"name": "case_fast", "status": "PASSED"},
                    {
                        "name": "case_slow",
                        "status": "FAILED",
                        "message": "timeout",
                    },
                ],
            },
        },
    )

    assert record["source"] == "edgebench"
    assert record["artifact"]["commit"] == "abc123"
    assert record["evaluation"]["authority"] == "edgebench_official_hidden_judge"
    assert record["evaluation"]["score"] == 42
    assert record["evaluation"]["score_0_100"] == 73.5
    assert record["evaluation"]["failed_checks"] == [
        {"name": "case_slow", "status": "FAILED", "message": "timeout"}
    ]


def test_goal_plus_auto_eval_submits_one_best_commit_and_publishes_feedback(
    tmp_path: Path,
    monkeypatch,
) -> None:
    best = {
        "run_id": "run_1",
        "candidate_id": "c002",
        "iteration": 3,
        "commit": "abc123",
        "local_score": 12.0,
    }
    monkeypatch.setattr(
        run_agent,
        "_extract_goal_plus_best_archive",
        lambda backend, handle: (b"best-archive", best),
    )
    monkeypatch.setattr(
        run_agent,
        "_wait_for_auto_eval_result",
        lambda *args: {
            "status": "completed",
            "error": None,
            "report": {"valid": True, "score": 42, "summary": "ok"},
        },
    )
    submitted = []

    class Response:
        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict:
            return {"submission_id": "submission-1", "round_id": "auto-1"}

    def post(url, *, data, files, timeout):
        submitted.append((url, data, files, timeout))
        return Response()

    published = []
    monkeypatch.setattr(run_agent.requests, "post", post)
    monkeypatch.setattr(
        run_agent,
        "_publish_goal_plus_auto_eval",
        lambda *args: published.append(args),
    )

    run_agent._auto_eval_loop(
        object(),
        object(),
        object(),
        "http://judge",
        "token",
        1800,
        _OneTickEvent(),
        logging.getLogger(__name__),
        tmp_path,
        True,
        "/home/agent/.goal-plus/edgebench/latest-auto-eval.json",
    )

    assert len(submitted) == 1
    assert submitted[0][2]["archive"][1] == b"best-archive"
    assert published[0][2]["artifact"] == {"source": "goal_plus_best", **best}
    assert published[0][3] == tmp_path / "submissions" / "auto-1" / "goal-plus.json"
