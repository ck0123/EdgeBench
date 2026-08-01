from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path

from sforge.harness.agent.codex_goal_plus import (
    CodexGoalPlusAgent,
)
from sforge.harness.agent.codex_goal_plus_solo import CodexGoalPlusSoloAgent
from sforge.harness.agent.factory import get_agent_class
from sforge.harness.agent.goal_plus_runtime import (
    DEFAULT_GOAL_PLUS_FINALIZATION_GRACE_SECONDS,
    collect_goal_plus_live_status,
    prepare_goal_plus_container,
)
from sforge.harness.agent.goal_plus_status_probe import build_snapshot
from sforge.harness.agent.pi import DEFAULT_PI_PACKAGE_VERSION, PiAgent
from sforge.harness.agent.pi_goal_plus import PiGoalPlusAgent
from sforge.harness.backend.base import ExecResult
from sforge.harness.config import SForgeConfig


def test_codex_goal_plus_is_registered() -> None:
    assert get_agent_class("codex-goal-plus") is CodexGoalPlusAgent


def test_codex_goal_plus_solo_is_registered() -> None:
    assert get_agent_class("codex-goal-plus-solo") is CodexGoalPlusSoloAgent


def test_pi_goal_plus_is_registered() -> None:
    assert get_agent_class("pi-goal-plus") is PiGoalPlusAgent


def test_plain_pi_pins_reasoning_effort() -> None:
    agent = PiAgent(SForgeConfig())
    env: dict[str, str] = {}

    command = agent.format_run_cmd("/tmp/prompt.md", model="gpt-5.6-sol")
    agent.augment_env(env, "gpt-5.6-sol")

    assert '--thinking "$SFORGE_PI_REASONING_EFFORT"' in command
    assert env["SFORGE_PI_REASONING_EFFORT"] == "medium"


def test_pi_tracks_latest_package_by_default_and_allows_exact_freeze() -> None:
    install_command = PiAgent.install_cmds[1]
    assert "SFORGE_PI_PACKAGE_VERSION:-latest" in install_command
    assert "pi --version" in install_command
    assert "@0.80.6" not in install_command

    default_env: dict[str, str] = {}
    PiAgent(SForgeConfig()).augment_env(default_env, "gpt-5.6-sol")
    assert default_env["SFORGE_PI_PACKAGE_VERSION"] == DEFAULT_PI_PACKAGE_VERSION

    frozen_env = {"SFORGE_PI_PACKAGE_VERSION": "0.83.0"}
    PiAgent(SForgeConfig()).augment_env(frozen_env, "gpt-5.6-sol")
    assert frozen_env["SFORGE_PI_PACKAGE_VERSION"] == "0.83.0"


def test_codex_goal_plus_installs_shared_runtime_and_codex_assets() -> None:
    commands = "\n".join(CodexGoalPlusAgent.install_cmds)

    assert "codex login status" in commands
    assert "Goal Plus commit:" in commands
    assert "/opt/goal-plus/.codex/config.example.toml" in commands
    assert "/opt/goal-plus/.codex/hooks.json" in commands
    assert "for SKILL in goal-plus goal-plus-with-final-check search" in commands
    assert "/opt/goal-plus/.codex/skills/$SKILL" in commands
    assert "for AGENT in search_candidate_agent goal_plus_final_checker" in commands
    assert "/opt/goal-plus/.codex/agents/$AGENT.toml" in commands
    assert 'args = ["--root", "/home/agent/.goal-plus"]' in commands
    assert "/opt/goal-plus/.pi/extensions/goal-plus.ts" not in commands
    assert "Using controller-provided Goal Plus source" in commands


def test_goal_plus_hosts_share_runtime_bootstrap_commands() -> None:
    pi_commands = PiGoalPlusAgent.install_cmds
    codex_commands = CodexGoalPlusAgent.install_cmds

    shared = [command for command in codex_commands if "Goal Plus commit:" in command]
    shared += [command for command in codex_commands if "python -c 'import goal_plus'" in command]
    assert len(shared) == 2
    for command in shared:
        assert command in pi_commands


def test_codex_goal_plus_install_commands_have_valid_shell_syntax(tmp_path) -> None:
    for index, command in enumerate(CodexGoalPlusAgent.install_cmds, start=1):
        script = tmp_path / f"install-{index}.sh"
        script.write_text(command, encoding="utf-8")
        completed = subprocess.run(
            ["bash", "-n", str(script)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr


def test_codex_goal_plus_run_and_resume_commands() -> None:
    agent = CodexGoalPlusAgent(SForgeConfig())

    run_cmd = agent.format_run_cmd("/tmp/prompt.md", model="gpt-5.5")
    resume_cmd = agent.format_run_cmd(
        "/tmp/prompt.md", model="gpt-5.5", resume=True
    )

    assert run_cmd.startswith('export GOAL_PLUS_OUTER_DEADLINE_AT=')
    assert "model_reasoning_effort=\"medium\"" in run_cmd
    assert "--model gpt-5.5" in run_cmd
    assert "--dangerously-bypass-hook-trust" in run_cmd
    assert "--dangerously-bypass-hook-trust" in resume_cmd
    assert "codex exec --dangerously-bypass-hook-trust -c" in run_cmd
    assert "--json" in run_cmd
    assert "\\$goal-plus mode=autonomous" in run_cmd
    assert "strategy.worker_host to codex" in run_cmd
    assert "set budget.max_parallel to 3" in run_cmd
    assert "omit the deprecated budget.max_candidates field" in run_cmd
    assert '"max_runtime_seconds": 1200' in run_cmd
    assert "do not prescribe a turn limit" in run_cmd
    assert '"max_turns"' not in run_cmd
    assert "sforge-goal-plus-submit --details" in run_cmd
    assert "sforge-goal-plus-submit --details --if-new" in resume_cmd
    assert "--model gpt-5.5 --json resume --last" in resume_cmd
    assert "--json" in resume_cmd
    assert "SFORGE_AGENT_FINALIZATION_GRACE_SECONDS" in run_cmd
    assert "SFORGE_AGENT_HARD_DEADLINE" in run_cmd
    assert "After the exploration cutoff" in run_cmd
    assert "finalization-only hard deadline" in resume_cmd
    assert "${SYNC_STATUS}" in resume_cmd
    assert "${{SYNC_STATUS}}" not in resume_cmd
    assert (
        agent.get_finalization_grace_seconds()
        == DEFAULT_GOAL_PLUS_FINALIZATION_GRACE_SECONDS
    )


def test_codex_goal_plus_accepts_experiment_concurrency_and_worker_lease() -> None:
    config = SForgeConfig(
        agent_extra_env={
            "SFORGE_GOAL_PLUS_PARALLEL_NUM": "5",
            "SFORGE_GOAL_PLUS_WORKER_RUNTIME_SECONDS": "900",
            "SFORGE_GOAL_PLUS_FINALIZATION_GRACE_SECONDS": "180",
        }
    )
    agent = CodexGoalPlusAgent(config)

    run_cmd = agent.format_run_cmd("/tmp/prompt.md", model="gpt-5.5")
    resume_cmd = agent.format_run_cmd(
        "/tmp/prompt.md", model="gpt-5.5", resume=True
    )

    assert "set budget.max_parallel to 5" in run_cmd
    assert '"max_runtime_seconds": 900' in run_cmd
    assert "budget.max_parallel to 5" in resume_cmd
    assert '"max_runtime_seconds": 900' in resume_cmd
    assert agent.get_finalization_grace_seconds() == 180


def test_pi_goal_plus_accepts_experiment_concurrency_and_worker_lease() -> None:
    config = SForgeConfig(
        agent_extra_env={
            "SFORGE_GOAL_PLUS_PARALLEL_NUM": "4",
            "SFORGE_GOAL_PLUS_WORKER_RUNTIME_SECONDS": "720",
            "SFORGE_GOAL_PLUS_FINALIZATION_GRACE_SECONDS": "150",
        }
    )
    agent = PiGoalPlusAgent(config)

    run_cmd = agent.format_run_cmd("/tmp/prompt.md", model="gpt-5.6-sol")
    resume_cmd = agent.format_run_cmd(
        "/tmp/prompt.md", model="gpt-5.6-sol", resume=True
    )

    assert "strategy.worker_host to pi" in run_cmd
    assert '--thinking "$SFORGE_PI_REASONING_EFFORT"' in run_cmd
    assert "set budget.max_parallel to 4" in run_cmd
    assert '"max_runtime_seconds": 720' in run_cmd
    assert '"max_turns"' not in run_cmd
    assert "budget.max_parallel to 4" in resume_cmd
    assert '"max_runtime_seconds": 720' in resume_cmd
    assert "SFORGE_AGENT_FINALIZATION_GRACE_SECONDS" in run_cmd
    assert "SFORGE_AGENT_HARD_DEADLINE" in run_cmd
    assert agent.get_finalization_grace_seconds() == 150


def test_codex_goal_plus_allows_disabling_finalization_grace() -> None:
    agent = CodexGoalPlusAgent(
        SForgeConfig(
            agent_extra_env={
                "SFORGE_GOAL_PLUS_FINALIZATION_GRACE_SECONDS": "0",
            }
        )
    )

    assert agent.get_finalization_grace_seconds() == 0


def test_goal_plus_hosts_skip_resume_only_after_terminal_reports_exist() -> None:
    class FakeBackend:
        def __init__(self, ready: bool) -> None:
            self.ready = ready

        def exec_run(self, handle, command):
            return ExecResult(
                output=(
                    '{"terminal_ready": true, "goal_statuses": '
                    '[{"status": "complete"}]}'
                    if self.ready
                    else '{"terminal_ready": false, "goal_statuses": '
                    '[{"status": "active"}]}'
                )
            )

    logger = logging.getLogger(__name__)
    for agent_type in (CodexGoalPlusAgent, PiGoalPlusAgent):
        agent = agent_type(SForgeConfig())
        assert agent.should_resume_after_exit(
            FakeBackend(ready=True),
            object(),
            logger,
        ) is False
        assert agent.should_resume_after_exit(
            FakeBackend(ready=False),
            object(),
            logger,
        ) is True


def test_goal_plus_live_status_is_published_atomically(tmp_path) -> None:
    class FakeBackend:
        def exec_run(self, handle, command):
            assert command == ["python", "/opt/sforge-goal-plus-status.py"]
            return ExecResult(
                output=(
                    '{"captured_at":"2026-07-31T10:40:00Z",'
                    '"candidate_count":2,"agent_session_count":2,'
                    '"worker_verifier_runs":11,"terminal_ready":false}'
                )
            )

    collect_goal_plus_live_status(
        FakeBackend(),
        object(),
        tmp_path,
        logging.getLogger(__name__),
    )

    payload = json.loads(
        (tmp_path / "goal-plus-live-status.json").read_text(encoding="utf-8")
    )
    assert payload["candidate_count"] == 2
    assert payload["worker_verifier_runs"] == 11
    assert not (tmp_path / "goal-plus-live-status.json.tmp").exists()


def test_goal_plus_live_status_probe_reads_pi_durable_state(tmp_path) -> None:
    root = tmp_path / ".goal-plus"
    run = root / "runs" / "run_1"
    candidate = run / "candidates" / "c001"
    sessions = run / "agent_sessions"
    goal = root / "goal-plus" / "gp_0001"
    for path in (candidate, sessions, goal):
        path.mkdir(parents=True)
    (run / "report.md").write_text("report", encoding="utf-8")
    (run / "report.html").write_text("<p>report</p>", encoding="utf-8")
    (run / "run.json").write_text(
        json.dumps(
            {
                "run_id": "run_1",
                "state": "promoted",
                "selected_candidate_id": "c001",
            }
        ),
        encoding="utf-8",
    )
    (candidate / "candidate.json").write_text(
        json.dumps(
            {
                "candidate_id": "c001",
                "iterations": [
                    {"iteration": 1, "process_passed": True, "score": 7.0},
                    {"iteration": 2, "process_passed": True, "score": 6.0},
                ],
            }
        ),
        encoding="utf-8",
    )
    (sessions / "agent_001.json").write_text(
        json.dumps(
            {
                "agent_session_id": "agent_001",
                "run_id": "run_1",
                "candidate_id": "c001",
                "host": "pi",
                "counters": {"verifier_runs": 2},
                "host_handle": {
                    "host": "pi-rpc",
                    "external_id": "agent_001",
                },
            }
        ),
        encoding="utf-8",
    )
    (goal / "goal.json").write_text(
        json.dumps(
            {
                "goal_plus_id": "gp_0001",
                "status": "complete",
                "search_tasks": [
                    {
                        "run_id": "run_1",
                        "result_recorded_at": "2026-07-31T10:45:00Z",
                        "report_path": "/home/agent/.goal-plus/runs/run_1/report.md",
                        "html_report_path": (
                            "/home/agent/.goal-plus/runs/run_1/report.html"
                        ),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    snapshot = build_snapshot(root)

    assert snapshot["candidate_count"] == 1
    assert snapshot["agent_session_count"] == 1
    assert snapshot["worker_verifier_runs"] == 2
    assert snapshot["promoted_candidate_ids"] == ["c001"]
    assert snapshot["terminal_ready"] is True


def test_codex_goal_plus_rejects_invalid_experiment_concurrency() -> None:
    agent = CodexGoalPlusAgent(
        SForgeConfig(
            agent_extra_env={"SFORGE_GOAL_PLUS_PARALLEL_NUM": "0"}
        )
    )

    try:
        agent.format_run_cmd("/tmp/prompt.md", model="gpt-5.5")
    except ValueError as exc:
        assert "SFORGE_GOAL_PLUS_PARALLEL_NUM" in str(exc)
    else:
        raise AssertionError("expected invalid Goal Plus concurrency to fail")


def test_codex_goal_plus_solo_enforces_one_long_lived_worker() -> None:
    agent = CodexGoalPlusSoloAgent(SForgeConfig())

    run_cmd = agent.format_run_cmd("/tmp/prompt.md", model="gpt-5.5")
    resume_cmd = agent.format_run_cmd(
        "/tmp/prompt.md", model="gpt-5.5", resume=True
    )

    assert "model_reasoning_effort=\"medium\"" in run_cmd
    assert "--model gpt-5.5" in run_cmd
    assert "budget.max_parallel=1" in run_cmd
    assert "omit deprecated budget.max_candidates" in run_cmd
    assert '\"max_runtime_seconds\":7200' in run_cmd
    assert "Do not set max_turns" in run_cmd
    assert "do not make optimization judgments" in resume_cmd
    assert "max_parallel=1" in resume_cmd
    assert "omit deprecated max_candidates" in resume_cmd
    assert "7200-second worker lease" in resume_cmd


def test_codex_goal_plus_sets_shared_state_environment() -> None:
    agent = CodexGoalPlusAgent(SForgeConfig())
    env: dict[str, str] = {}

    agent.augment_env(env, "gpt-5.5")

    assert env["GOAL_PLUS_ROOT"] == "/home/agent/.goal-plus"
    assert env["GOAL_PLUS_SEARCH_ROOT"] == "/home/agent/.goal-plus"
    assert env["GOAL_PLUS_SOURCE_PATH"] == "/opt/goal-plus"
    assert env["GOAL_PLUS_ROLE"] == "main"
    assert env["GOAL_PLUS_CODEX_ROLE"] == "main"
    assert env["GOAL_PLUS_CODEX_MODEL"] == "gpt-5.5"


def test_codex_goal_plus_can_copy_pinned_controller_source(
    tmp_path, monkeypatch
) -> None:
    source = tmp_path / "goal-plus"
    source.mkdir()
    (source / "pyproject.toml").write_text("[project]\nname='goal-plus'\n")
    monkeypatch.setenv("SFORGE_GOAL_PLUS_SOURCE_DIR", str(source))

    class FakeBackend:
        def __init__(self) -> None:
            self.copies: list[tuple[Path, str]] = []

        def copy_to_container(self, handle, local, remote) -> None:
            self.copies.append((Path(local), str(remote)))

    backend = FakeBackend()
    prepare_goal_plus_container(
        backend,
        object(),
        logging.getLogger(__name__),
    )

    assert (source.resolve(), "/opt/goal-plus") in backend.copies


def test_codex_goal_plus_enables_only_project_goal_plus_hooks(tmp_path) -> None:
    class FakeBackend:
        def __init__(self) -> None:
            self.commands = []
            self.destinations = []

        def copy_to_container(self, handle, source, destination) -> None:
            self.destinations.append(str(destination))

        def exec_run(self, handle, command, *, user=None) -> ExecResult:
            self.commands.append((command, user))
            return ExecResult()

    backend = FakeBackend()
    agent = CodexGoalPlusAgent(SForgeConfig())
    agent.install_stop_hook(
        backend,
        object(),
        tmp_path,
        logging.getLogger(__name__),
    )

    assert "/tmp/sforge-codex-config-append.toml" in backend.destinations
    assert "/etc/codex/hooks.json" not in backend.destinations
    assert "/tmp/sforge-codex-stop-hook.sh" not in backend.destinations
    assert any("hooks = true" in path.read_text() for path in tmp_path.iterdir())
