# Copyright (c) 2026 ByteDance Ltd. and/or its affiliates
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

"""Helpers for aggregating independent agent replicas into pass@N results."""

from __future__ import annotations

from math import comb
from statistics import median
from typing import Any


def replica_run_id(group_run_id: str, replica: int, replica_count: int) -> str:
    """Return a stable physical run ID for one independent replica."""
    if replica_count < 1:
        raise ValueError("replica_count must be positive")
    if replica < 1 or replica > replica_count:
        raise ValueError("replica must be between 1 and replica_count")
    if replica_count == 1:
        return group_run_id
    width = max(2, len(str(replica_count)))
    return f"{group_run_id}-r{replica:0{width}d}"


def estimate_pass_at_k(sample_count: int, success_count: int) -> dict[str, float]:
    """Return the standard unbiased pass@k estimate for every k in the sample."""
    if sample_count < 1:
        return {}
    if success_count < 0 or success_count > sample_count:
        raise ValueError("success_count must be between zero and sample_count")

    failures = sample_count - success_count
    estimates: dict[str, float] = {}
    for k in range(1, sample_count + 1):
        if failures < k:
            value = 1.0
        else:
            value = 1.0 - comb(failures, k) / comb(sample_count, k)
        estimates[str(k)] = value
    return estimates


def aggregate_replicas(
    task_id: str,
    trials: list[dict[str, Any]],
    *,
    score_direction: str,
    success_threshold: float | None = None,
) -> dict[str, Any]:
    """Aggregate independent trial summaries without losing per-trial evidence."""
    if score_direction not in ("minimize", "maximize"):
        raise ValueError("score_direction must be 'minimize' or 'maximize'")

    successes = 0
    scores: list[float] = []
    rendered_trials: list[dict[str, Any]] = []
    infrastructure_errors = 0

    for trial in sorted(trials, key=lambda item: item.get("replica", 0)):
        pass_rate = float(trial.get("best_pass_rate", 0.0) or 0.0)
        raw_score = trial.get("best_score")
        score = float(raw_score) if raw_score is not None else None
        if score is not None:
            scores.append(score)

        success = pass_rate >= 1.0
        if success_threshold is not None:
            if score is None:
                success = False
            elif score_direction == "minimize":
                success = success and score <= success_threshold
            else:
                success = success and score >= success_threshold
        if trial.get("error") is not None:
            infrastructure_errors += 1
            success = False
        if success:
            successes += 1

        rendered_trials.append({**trial, "success": success})

    sample_count = len(rendered_trials)
    if scores:
        best_score = min(scores) if score_direction == "minimize" else max(scores)
        worst_score = max(scores) if score_direction == "minimize" else min(scores)
        median_score = median(scores)
    else:
        best_score = median_score = worst_score = None

    return {
        "task": task_id,
        "replicas": sample_count,
        "successes": successes,
        "infrastructure_errors": infrastructure_errors,
        "success_criterion": {
            "minimum_pass_rate": 1.0,
            "score_direction": score_direction,
            "score_threshold": success_threshold,
        },
        "pass_at_k": estimate_pass_at_k(sample_count, successes),
        "best_score": best_score,
        "median_score": median_score,
        "worst_score": worst_score,
        "scores": scores,
        "trials": rendered_trials,
    }
