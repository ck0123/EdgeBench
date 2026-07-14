#!/usr/bin/env python3
"""Report EdgeBench 0-100 scores with official per-task references.

The task JSON is the source of truth for raw-score rescaling.  The official
open-source leaderboard curves are parsed from the repository README so the
comparison stays in sync when the repository is updated.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sforge.harness.score_rescale import parse_rescale_spec, rescale_score


OFFICIAL_HOURS = (2, 4, 6, 8, 10, 12)
OFFICIAL_SOURCE_URL = "https://github.com/ByteDance-Seed/EdgeBench#open-source-subset-51-tasks"


@dataclass(frozen=True)
class ScoreObservation:
    task: str
    run_id: str | None
    model: str | None
    state: str
    raw_score: float
    edgebench_score: float
    pass_rate: float | None
    source: str


def _float_or_none(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _split_markdown_row(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def load_official_curves(readme_path: Path) -> dict[str, dict[str, Any]]:
    """Parse the official 51-task per-model curves from README.md."""
    lines = readme_path.read_text(encoding="utf-8").splitlines()
    header_index = next(
        (
            index
            for index, line in enumerate(lines)
            if line.startswith("| Task | Category |") and "GPT-5.5" in line
        ),
        None,
    )
    if header_index is None:
        raise ValueError(f"official per-task table not found in {readme_path}")

    header = _split_markdown_row(lines[header_index])
    models = header[2:]
    curves: dict[str, dict[str, Any]] = {}
    for line in lines[header_index + 2 :]:
        if not line.startswith("|"):
            break
        cells = _split_markdown_row(line)
        if len(cells) != len(header):
            continue
        task, category = cells[:2]
        model_curves: dict[str, list[float | None]] = {}
        for model, cell in zip(models, cells[2:], strict=True):
            values: list[float | None] = []
            for value in cell.split("/"):
                values.append(None if value == "—" else float(value))
            if len(values) != len(OFFICIAL_HOURS):
                raise ValueError(
                    f"expected {len(OFFICIAL_HOURS)} checkpoints for {task}/{model}, "
                    f"got {len(values)}"
                )
            model_curves[model] = values
        curves[task] = {"category": category, "models": model_curves}
    if not curves:
        raise ValueError(f"official per-task table in {readme_path} is empty")
    return curves


def load_task_config(task: str) -> dict[str, Any]:
    path = REPO_ROOT / "tasks" / f"{task}.json"
    if not path.is_file():
        raise FileNotFoundError(f"task definition not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def rescale_raw_score(task: str, raw_score: float) -> float:
    config = load_task_config(task)
    spec = parse_rescale_spec(config.get("judge", {}).get("rescale"))
    score = rescale_score(spec, raw_score)
    if score is None or not math.isfinite(score):
        raise ValueError(f"task {task} has no usable rescale result for raw score {raw_score}")
    return score


def _report_sort_key(report: dict[str, Any], direction: str) -> tuple[float, float]:
    normalized = _float_or_none(report.get("score_0_100"))
    raw = _float_or_none(report.get("score"))
    normalized_key = normalized if normalized is not None else -math.inf
    if raw is None:
        raw_key = -math.inf
    else:
        raw_key = raw if direction != "minimize" else -raw
    return normalized_key, raw_key


def observation_from_run_dir(run_dir: Path) -> ScoreObservation:
    """Load the best normalized submission from an active or completed task run."""
    run_dir = run_dir.resolve()
    final_path = run_dir / "final_result.json"
    final = json.loads(final_path.read_text(encoding="utf-8")) if final_path.is_file() else {}
    task = str(final.get("task") or run_dir.name)
    config = load_task_config(task)
    direction = str(config.get("judge", {}).get("score_direction") or "maximize")

    reports: list[tuple[Path, dict[str, Any]]] = []
    for path in (run_dir / "submissions").glob("*/report.json"):
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        raw = _float_or_none(report.get("score"))
        if raw is None:
            continue
        if _float_or_none(report.get("score_0_100")) is None:
            report["score_0_100"] = rescale_raw_score(task, raw)
        reports.append((path, report))

    if reports:
        report_path, best = max(reports, key=lambda item: _report_sort_key(item[1], direction))
        raw_score = float(best["score"])
        edgebench_score = float(best["score_0_100"])
        pass_rate = _float_or_none(best.get("pass_rate"))
        source = str(report_path)
    else:
        raw_score_value = _float_or_none(final.get("best_score"))
        if raw_score_value is None:
            raise ValueError(f"no scored submission found in {run_dir}")
        raw_score = raw_score_value
        edgebench_score = rescale_raw_score(task, raw_score)
        pass_rate = _float_or_none(final.get("best_pass_rate"))
        source = str(final_path)

    return ScoreObservation(
        task=task,
        run_id=str(final.get("run_id") or run_dir.parent.name),
        model=str(final.get("model")) if final.get("model") else None,
        state="final" if final_path.is_file() else "in_progress",
        raw_score=raw_score,
        edgebench_score=edgebench_score,
        pass_rate=pass_rate,
        source=source,
    )


def observation_from_raw(task: str, raw_score: float, model: str | None) -> ScoreObservation:
    return ScoreObservation(
        task=task,
        run_id=None,
        model=model,
        state="provided",
        raw_score=raw_score,
        edgebench_score=rescale_raw_score(task, raw_score),
        pass_rate=None,
        source=f"tasks/{task}.json",
    )


def find_latest_run_dir(task: str, logs_dir: Path) -> Path:
    candidates = [
        path
        for path in logs_dir.glob(f"*/{task}")
        if path.is_dir() and ((path / "submissions").is_dir() or (path / "final_result.json").is_file())
    ]
    if not candidates:
        raise FileNotFoundError(f"no run found for task {task} under {logs_dir}")
    return max(candidates, key=lambda path: path.stat().st_mtime)


def _canonical_model(model: str) -> str:
    text = model.casefold()
    return "".join(character for character in text if character.isalnum())


def _match_model(model: str | None, official_models: list[str]) -> str | None:
    if not model:
        return None
    wanted = _canonical_model(model)
    for official_model in official_models:
        candidate = _canonical_model(official_model)
        if wanted == candidate or wanted in candidate or candidate in wanted:
            return official_model
    return None


def _choose_checkpoint(budget_hours: float) -> tuple[int, int]:
    index = min(
        range(len(OFFICIAL_HOURS)),
        key=lambda item: (abs(OFFICIAL_HOURS[item] - budget_hours), OFFICIAL_HOURS[item]),
    )
    return OFFICIAL_HOURS[index], index


def build_comparison(
    observation: ScoreObservation,
    official_curves: dict[str, dict[str, Any]],
    budget_hours: float,
) -> dict[str, Any]:
    result: dict[str, Any] = asdict(observation)
    result["budget_hours"] = budget_hours
    result["official_source"] = OFFICIAL_SOURCE_URL
    task_curve = official_curves.get(observation.task)
    if task_curve is None:
        result["official_comparison"] = None
        return result

    checkpoint, checkpoint_index = _choose_checkpoint(budget_hours)
    references = {
        model: values[checkpoint_index]
        for model, values in task_curve["models"].items()
        if values[checkpoint_index] is not None
    }
    sorted_references = sorted(references.items(), key=lambda item: item[1], reverse=True)
    matched_model = _match_model(observation.model, list(references))
    same_model_score = references.get(matched_model) if matched_model else None
    score = observation.edgebench_score
    leaders = sum(1 for value in references.values() if value > score)
    rank_including_run = leaders + 1

    result["official_comparison"] = {
        "category": task_curve["category"],
        "checkpoint_hours": checkpoint,
        "checkpoint_is_nearest": checkpoint != budget_hours,
        "references": dict(sorted_references),
        "leader_model": sorted_references[0][0] if sorted_references else None,
        "leader_score": sorted_references[0][1] if sorted_references else None,
        "matched_model": matched_model,
        "matched_model_score": same_model_score,
        "delta_vs_matched_model": score - same_model_score if same_model_score is not None else None,
        "attainment_vs_matched_model_pct": (
            100.0 * score / same_model_score if same_model_score not in (None, 0.0) else None
        ),
        "rank_including_run": rank_including_run,
        "rank_population": len(references) + 1,
    }
    return result


def _fmt_score(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:.6f}".rstrip("0").rstrip(".")


def render_text(result: dict[str, Any]) -> str:
    lines = [
        f"Task: {result['task']} ({result['state']})",
        f"Run: {result.get('run_id') or '—'}; model: {result.get('model') or '—'}",
        f"Raw score: {_fmt_score(result['raw_score'])}",
        f"EdgeBench score: {_fmt_score(result['edgebench_score'])}/100",
    ]
    if result.get("pass_rate") is not None:
        lines.append(f"Pass rate: {100.0 * result['pass_rate']:.2f}%")

    comparison = result.get("official_comparison")
    if comparison is None:
        lines.append("Official comparison: unavailable for this task in the 51-task public table")
    else:
        checkpoint = comparison["checkpoint_hours"]
        nearest = " (nearest published checkpoint)" if comparison["checkpoint_is_nearest"] else ""
        lines.append(f"Official reference: @{checkpoint}h{nearest}")
        if comparison["matched_model_score"] is not None:
            delta = comparison["delta_vs_matched_model"]
            attainment = comparison["attainment_vs_matched_model_pct"]
            lines.append(
                f"Same model ({comparison['matched_model']}): "
                f"{_fmt_score(comparison['matched_model_score'])}; "
                f"delta {delta:+.2f} pp; attainment {attainment:.1f}%"
            )
        lines.append(
            f"Published leader: {comparison['leader_model']} "
            f"{_fmt_score(comparison['leader_score'])}"
        )
        lines.append(
            f"Position including this run: {comparison['rank_including_run']}/"
            f"{comparison['rank_population']}"
        )
        references = ", ".join(
            f"{model}={_fmt_score(score)}" for model, score in comparison["references"].items()
        )
        lines.append(f"Published models: {references}")
    lines.extend(
        [
            f"Score source: {result['source']}",
            f"Official data: {result['official_source']}",
            "Note: this is a same-scale reference comparison; agent, hardware, and run settings may differ from the official leaderboard.",
        ]
    )
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert EdgeBench raw scores to 0-100 and compare with official curves."
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--run-dir", type=Path, help="Task run directory containing submissions/")
    source.add_argument("--latest", metavar="TASK", help="Use the newest local run for TASK")
    source.add_argument("--raw-score", type=float, help="Raw judge score (requires --task)")
    parser.add_argument("--task", help="Task ID for --raw-score")
    parser.add_argument("--model", help="Model name; overrides or supplements run metadata")
    budget = parser.add_mutually_exclusive_group()
    budget.add_argument("--budget-hours", type=float, help="Planned run budget in hours")
    budget.add_argument("--budget-seconds", type=float, help="Planned run budget in seconds")
    parser.add_argument("--readme", type=Path, default=REPO_ROOT / "README.md")
    parser.add_argument("--logs-dir", type=Path, default=REPO_ROOT / "logs" / "runs")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    args = parser.parse_args()
    if args.raw_score is not None and not args.task:
        parser.error("--raw-score requires --task")
    if args.budget_hours is not None and args.budget_hours <= 0:
        parser.error("--budget-hours must be positive")
    if args.budget_seconds is not None and args.budget_seconds <= 0:
        parser.error("--budget-seconds must be positive")
    return args


def main() -> int:
    args = parse_args()
    if args.run_dir:
        observation = observation_from_run_dir(args.run_dir)
    elif args.latest:
        observation = observation_from_run_dir(find_latest_run_dir(args.latest, args.logs_dir))
    else:
        observation = observation_from_raw(args.task, args.raw_score, args.model)

    if args.model and observation.model != args.model:
        observation = ScoreObservation(**{**asdict(observation), "model": args.model})
    budget_hours = (
        args.budget_hours
        if args.budget_hours is not None
        else args.budget_seconds / 3600.0
        if args.budget_seconds is not None
        else 2.0
    )
    curves = load_official_curves(args.readme)
    result = build_comparison(observation, curves, budget_hours)
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True))
    else:
        print(render_text(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
