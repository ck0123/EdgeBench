from __future__ import annotations

import argparse
import json
from types import SimpleNamespace

import pytest

from sforge import cli
from sforge.harness.config import SForgeConfig
from sforge.harness.pass_at_n import (
    aggregate_replicas,
    estimate_pass_at_k,
    replica_run_id,
)


def test_replica_run_ids_are_unique_and_single_runs_stay_compatible() -> None:
    assert replica_run_id("run-1", 1, 1) == "run-1"
    assert [replica_run_id("run-1", i, 3) for i in range(1, 4)] == [
        "run-1-r01",
        "run-1-r02",
        "run-1-r03",
    ]


def test_estimate_pass_at_k_uses_standard_unbiased_estimator() -> None:
    estimates = estimate_pass_at_k(3, 2)

    assert estimates["1"] == pytest.approx(2 / 3)
    assert estimates["2"] == 1.0
    assert estimates["3"] == 1.0


def test_aggregate_replicas_applies_minimize_threshold_and_keeps_scores() -> None:
    aggregate = aggregate_replicas(
        "synthetic_minimize_task",
        [
            {"replica": 1, "run_id": "group-r01", "best_pass_rate": 1.0, "best_score": 10},
            {"replica": 2, "run_id": "group-r02", "best_pass_rate": 1.0, "best_score": 20},
            {"replica": 3, "run_id": "group-r03", "best_pass_rate": 1.0, "best_score": 30},
        ],
        score_direction="minimize",
        success_threshold=20,
    )

    assert aggregate["successes"] == 2
    assert aggregate["pass_at_k"] == pytest.approx(
        {"1": 2 / 3, "2": 1.0, "3": 1.0}
    )
    assert aggregate["best_score"] == 10
    assert aggregate["median_score"] == 20
    assert aggregate["worst_score"] == 30
    assert [trial["success"] for trial in aggregate["trials"]] == [True, True, False]


def test_aggregate_replicas_counts_infrastructure_errors_as_failed_trials() -> None:
    aggregate = aggregate_replicas(
        "task",
        [
            {"replica": 1, "best_pass_rate": 1.0, "best_score": 10},
            {"replica": 2, "error": "container failed"},
        ],
        score_direction="maximize",
    )

    assert aggregate["successes"] == 1
    assert aggregate["infrastructure_errors"] == 1
    assert aggregate["pass_at_k"] == {"1": 0.5, "2": 1.0}


def test_cmd_run_launches_isolated_replicas_and_writes_group_summary(
    tmp_path, monkeypatch
) -> None:
    config = SForgeConfig(log_dir=tmp_path / "logs", tasks_dir=tmp_path / "tasks")
    task = SimpleNamespace(
        task_id="synthetic_task",
        internet=True,
        judge=SimpleNamespace(score_direction="minimize"),
    )
    args = argparse.Namespace(
        experiment=None,
        task=[task.task_id],
        agent="codex",
        model="gpt-5.6-sol",
        timeout=3600,
        eval_interval=300,
        disable_stop_hook=False,
        disable_auto_eval=False,
        disable_auto_resume=False,
        disable_internet=False,
        enable_internet=True,
        max_submissions=None,
        submission_cooldown=None,
        stagger=None,
        judge_url="http://host.docker.internal:8080",
        run_id="pass3",
        replicas=3,
        replica_concurrency=3,
        judge_concurrency=1,
        success_threshold=None,
        backend=None,
        silent=True,
    )
    calls = []

    monkeypatch.setattr(cli, "_make_config", lambda _: config)
    monkeypatch.setattr(cli, "_resolve_tasks", lambda *_: [task])
    monkeypatch.setattr(cli, "create_backend_from_config", lambda _: object())

    def fake_run_single_task(
        task_spec,
        task_args,
        task_config,
        backend,
        run_id,
        **kwargs,
    ):
        calls.append((run_id, kwargs))
        replica = kwargs["replica"]
        return {
            "task": task_spec.task_id,
            "run_id": run_id,
            "group_run_id": kwargs["group_run_id"],
            "replica": replica,
            "replica_count": kwargs["replica_count"],
            "best_pass_rate": 1.0,
            "best_score": 9 + replica,
        }

    monkeypatch.setattr(cli, "_run_single_task", fake_run_single_task)

    cli.cmd_run(args)

    assert sorted(run_id for run_id, _ in calls) == [
        "pass3-r01",
        "pass3-r02",
        "pass3-r03",
    ]
    assert all(call[1]["group_run_id"] == "pass3" for call in calls)
    assert all(call[1]["judge_concurrency"] == 1 for call in calls)

    summary = json.loads((tmp_path / "logs/runs/pass3/pass_at_n.json").read_text())
    aggregate = summary["tasks"][0]
    assert aggregate["successes"] == 3
    assert aggregate["pass_at_k"]["3"] == 1.0
    assert aggregate["scores"] == [10.0, 11.0, 12.0]
