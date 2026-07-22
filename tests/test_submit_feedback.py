from __future__ import annotations

import logging
import json
import subprocess
import threading
from types import SimpleNamespace

import pytest

from sforge.harness.backend.base import ExecResult
from sforge.harness.agent.codex import CodexAgent
from sforge.harness.evolve_scripts import generate_submit_script
from sforge.harness.judge_server import JudgeState
from sforge.harness.run_agent import _install_tools


def test_generated_submit_script_has_valid_shell_syntax(tmp_path) -> None:
    script = tmp_path / "sforge-submit"
    script.write_text(generate_submit_script(), encoding="utf-8")

    completed = subprocess.run(
        ["bash", "-n", str(script)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr


def test_submit_script_rejects_empty_inputs_and_preserves_judge_feedback() -> None:
    script = generate_submit_script()

    assert "Required submission path is missing" in script
    assert "Submission archive contains no files" in script
    assert 'CURRENT_SCORE_0_100=$(echo "$REPORT"' in script
    assert 'CURRENT_SCORE_0_100_EXTENDED=$(echo "$REPORT"' in script
    assert 'printf \'%s\\n\' "$RESULT" > "$LAST_RESULT_FILE"' in script
    assert "Official score 0-100:" in script
    assert "Local extended score:" in script


def test_history_keeps_official_normalized_score() -> None:
    state = object.__new__(JudgeState)
    state.run_history = {}
    state._history_lock = threading.Lock()

    state._record_submission(
        "run_1",
        "submission_1",
        "task_1",
        "agent-1",
        {
            "pass_rate": 1.0,
            "score": 123.0,
            "score_0_100": 67.25,
            "score_0_100_extended": 67.25,
            "valid": True,
        },
        None,
    )

    entry = state.run_history["run_1/task_1"][0]
    assert entry["score"] == 123.0
    assert entry["score_0_100"] == 67.25
    assert entry["score_0_100_extended"] == 67.25


def test_codex_prepare_container_copies_host_auth(tmp_path, monkeypatch) -> None:
    auth_file = tmp_path / "auth.json"
    auth_file.write_text(json.dumps({"tokens": {"access_token": "redacted"}}))
    monkeypatch.setenv("SFORGE_CODEX_AUTH_FILE", str(auth_file))
    monkeypatch.setenv("SFORGE_CODEX_RUNTIME_ARCHIVE", "")

    class FakeBackend:
        def __init__(self) -> None:
            self.copies = []
            self.commands = []

        def copy_to_container(self, handle, source, destination) -> None:
            self.copies.append((source, str(destination)))

        def exec_run(self, handle, command, *, user=None) -> ExecResult:
            self.commands.append((command, user))
            return ExecResult()

    backend = FakeBackend()
    agent = object.__new__(CodexAgent)
    agent.prepare_container(
        backend,
        object(),
        logging.getLogger(__name__),
    )

    assert backend.copies == [
        (auth_file.resolve(), "/home/agent/.codex/auth.json")
    ]
    assert backend.commands[-1][1] == "root"
    assert "chmod 600 /home/agent/.codex/auth.json" in backend.commands[-1][0][-1]
    assert any("codex login status" in command for command in CodexAgent.install_cmds)


def test_codex_prepare_container_copies_cached_linux_runtime(
    tmp_path, monkeypatch
) -> None:
    auth_file = tmp_path / "auth.json"
    auth_file.write_text(json.dumps({"tokens": {"access_token": "redacted"}}))
    runtime_archive = tmp_path / "codex-linux-x64.tgz"
    runtime_archive.write_bytes(b"runtime archive")
    monkeypatch.setenv("SFORGE_CODEX_AUTH_FILE", str(auth_file))
    monkeypatch.setenv("SFORGE_CODEX_RUNTIME_ARCHIVE", str(runtime_archive))

    class FakeBackend:
        def __init__(self) -> None:
            self.copies = []
            self.commands = []

        def copy_to_container(self, handle, source, destination) -> None:
            self.copies.append((source, str(destination)))

        def exec_run(self, handle, command, *, user=None) -> ExecResult:
            self.commands.append((command, user))
            return ExecResult(output="codex-cli 0.144.1")

    backend = FakeBackend()
    agent = object.__new__(CodexAgent)
    agent.prepare_container(
        backend,
        object(),
        logging.getLogger(__name__),
    )

    assert (runtime_archive.resolve(), "/tmp/sforge-codex-linux-x64.tgz") in (
        backend.copies
    )
    runtime_commands = [command for command, _ in backend.commands]
    assert any(
        "x86_64-unknown-linux-musl" in " ".join(command)
        for command in runtime_commands
        if isinstance(command, list)
    )
    assert "command -v codex" in "\n".join(CodexAgent.install_cmds)


def test_codex_run_and_resume_commands_pass_model_and_reasoning_explicitly(
    monkeypatch,
) -> None:
    monkeypatch.setenv("SFORGE_CODEX_REASONING_EFFORT", "medium")
    agent = object.__new__(CodexAgent)

    run_cmd = agent.format_run_cmd(
        "/tmp/prompt.md",
        model="gpt-5.5",
    )
    resume_cmd = agent.format_run_cmd(
        "/tmp/prompt.md",
        model="gpt-5.5",
        resume=True,
    )

    expected = "codex exec -c 'model_reasoning_effort=\"medium\"' --model gpt-5.5"
    assert run_cmd.startswith(f"{expected} ")
    assert resume_cmd.startswith(f"{expected} resume ")


def test_codex_rejects_unknown_reasoning_effort(monkeypatch) -> None:
    monkeypatch.setenv("SFORGE_CODEX_REASONING_EFFORT", "guess")
    agent = object.__new__(CodexAgent)

    with pytest.raises(ValueError, match="SFORGE_CODEX_REASONING_EFFORT"):
        agent.format_run_cmd("/tmp/prompt.md", model="gpt-5.5")


def test_goal_plus_bridge_install_uses_explicit_container_commands(tmp_path) -> None:
    class FakeBackend:
        def __init__(self) -> None:
            self.commands = []
            self.destinations = []

        def copy_to_container(self, handle, source, destination) -> None:
            self.destinations.append(str(destination))

        def exec_run(self, handle, command, *, user=None) -> ExecResult:
            self.commands.append(command)
            return ExecResult()

    backend = FakeBackend()
    agent = SimpleNamespace(
        install_goal_plus_bridge=True,
        install_stop_hook=lambda *args: None,
    )

    _install_tools(
        backend,
        object(),
        None,
        agent,
        tmp_path,
        logging.getLogger(__name__),
    )

    assert backend.commands[-3:] == [
        ["chmod", "a+x", "/usr/local/bin/sforge-goal-plus-sync"],
        [
            "ln",
            "-sf",
            "/usr/local/bin/sforge-goal-plus-sync",
            "/usr/local/bin/sforge-goal-plus-submit",
        ],
        ["/usr/local/bin/sforge-goal-plus-submit", "--help"],
    ]
    assert "/usr/local/bin/sforge-goal-plus-sync" in backend.destinations
