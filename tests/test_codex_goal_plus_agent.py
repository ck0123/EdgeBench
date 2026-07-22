from __future__ import annotations

import logging
import subprocess

from sforge.harness.agent.codex_goal_plus import CodexGoalPlusAgent
from sforge.harness.agent.codex_goal_plus_solo import CodexGoalPlusSoloAgent
from sforge.harness.agent.factory import get_agent_class
from sforge.harness.agent.pi_goal_plus import PiGoalPlusAgent
from sforge.harness.backend.base import ExecResult
from sforge.harness.config import SForgeConfig


def test_codex_goal_plus_is_registered() -> None:
    assert get_agent_class("codex-goal-plus") is CodexGoalPlusAgent


def test_codex_goal_plus_solo_is_registered() -> None:
    assert get_agent_class("codex-goal-plus-solo") is CodexGoalPlusSoloAgent


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
    assert "\\$goal-plus mode=autonomous" in run_cmd
    assert "strategy.worker_host to codex" in run_cmd
    assert "budget.max_parallel to 3" in run_cmd
    assert "sforge-goal-plus-submit --details" in run_cmd
    assert "sforge-goal-plus-submit --details --if-new" in resume_cmd
    assert "--model gpt-5.5 resume --last" in resume_cmd
    assert "${SYNC_STATUS}" in resume_cmd
    assert "${{SYNC_STATUS}}" not in resume_cmd


def test_codex_goal_plus_solo_enforces_one_long_lived_worker() -> None:
    agent = CodexGoalPlusSoloAgent(SForgeConfig())

    run_cmd = agent.format_run_cmd("/tmp/prompt.md", model="gpt-5.5")
    resume_cmd = agent.format_run_cmd(
        "/tmp/prompt.md", model="gpt-5.5", resume=True
    )

    assert "model_reasoning_effort=\"medium\"" in run_cmd
    assert "--model gpt-5.5" in run_cmd
    assert "budget.max_parallel=1" in run_cmd
    assert "budget.max_candidates=1" in run_cmd
    assert '\"max_runtime_seconds\":7200' in run_cmd
    assert "Do not set max_turns" in run_cmd
    assert "do not make optimization judgments" in resume_cmd
    assert "max_parallel=1" in resume_cmd
    assert "max_candidates=1" in resume_cmd
    assert "7200-second worker lease" in resume_cmd


def test_codex_goal_plus_sets_shared_state_environment() -> None:
    agent = CodexGoalPlusAgent(SForgeConfig())
    env: dict[str, str] = {}

    agent.augment_env(env, "gpt-5.5")

    assert env["GOAL_PLUS_ROOT"] == "/home/agent/.goal-plus"
    assert env["GOAL_PLUS_SOURCE_PATH"] == "/opt/goal-plus"
    assert env["GOAL_PLUS_ROLE"] == "main"
    assert env["GOAL_PLUS_CODEX_ROLE"] == "main"
    assert env["GOAL_PLUS_CODEX_MODEL"] == "gpt-5.5"


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
