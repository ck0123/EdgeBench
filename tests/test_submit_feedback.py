from __future__ import annotations

import logging
import json
import subprocess
import threading
import time
from types import SimpleNamespace

import pytest

from sforge.harness.backend.base import ExecResult
from sforge.harness.agent.codex import CodexAgent, _generate_codex_stop_hook
from sforge.harness.evolve_scripts import generate_submit_script
from sforge.harness.config import SForgeConfig
from sforge.harness import judge_server
from sforge.harness.run_agent import RunResult
from sforge.harness.judge_server import JudgeState, SubmissionStatus
from sforge.harness.run_agent import (
    _build_agent_env,
    _install_tools,
    _validate_judge_registration,
)


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


def test_judge_group_registration_shares_one_concurrency_limiter() -> None:
    state = object.__new__(JudgeState)
    state.tasks = {"task": object()}
    state.tokens = {}
    state.run_resource_limits = {}
    state.run_backends = {}
    state.run_judge_groups = {}
    state.judge_group_limits = {}
    state.judge_group_semaphores = {}
    state._tokens_lock = threading.Lock()

    token_1 = state.register_session(
        "task", "group-r01", judge_group_id="group", judge_concurrency=1
    )
    token_2 = state.register_session(
        "task", "group-r02", judge_group_id="group", judge_concurrency=1
    )

    assert token_1 != token_2
    assert state.run_judge_groups == {
        "group-r01": "group",
        "group-r02": "group",
    }
    assert len(state.judge_group_semaphores) == 1
    with pytest.raises(ValueError, match="already registered"):
        state.register_session(
            "task", "group-r03", judge_group_id="group", judge_concurrency=2
        )


def test_judge_group_limiter_serializes_ephemeral_evaluations(
    tmp_path, monkeypatch
) -> None:
    state = object.__new__(JudgeState)
    state.config = SForgeConfig(log_dir=tmp_path)
    state.tasks = {"task": object()}
    state.backend = object()
    state.run_backends = {}
    state.run_history = {}
    state._history_lock = threading.Lock()
    state._tokens_lock = threading.Lock()
    state.run_judge_groups = {"group-r01": "group", "group-r02": "group"}
    state.judge_group_semaphores = {
        "group": threading.BoundedSemaphore(1),
    }
    state.submissions = {
        "sub-1": {"status": SubmissionStatus.QUEUED, "error": None},
        "sub-2": {"status": SubmissionStatus.QUEUED, "error": None},
    }

    active = 0
    maximum_active = 0
    active_lock = threading.Lock()

    class FakeReport:
        def to_dict(self):
            return {"pass_rate": 1.0, "score": 1.0, "valid": True}

    def fake_judge_submission(**kwargs):
        nonlocal active, maximum_active
        with active_lock:
            active += 1
            maximum_active = max(maximum_active, active)
        time.sleep(0.05)
        with active_lock:
            active -= 1
        return FakeReport()

    monkeypatch.setattr(judge_server, "judge_submission", fake_judge_submission)

    threads = [
        threading.Thread(
            target=state._grade_worker,
            args=("sub-1", "task", b"", "group-r01", "agent-1"),
        ),
        threading.Thread(
            target=state._grade_worker,
            args=("sub-2", "task", b"", "group-r02", "agent-1"),
        ),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert maximum_active == 1
    assert state.submissions["sub-1"]["status"] == SubmissionStatus.COMPLETED
    assert state.submissions["sub-2"]["status"] == SubmissionStatus.COMPLETED


def test_judge_registration_must_confirm_group_concurrency() -> None:
    assert _validate_judge_registration(
        {
            "token": "token-1",
            "judge_group_id": "group",
            "judge_concurrency": 1,
        },
        run_id="group-r01",
        judge_group_id="group",
        judge_concurrency=1,
    ) == "token-1"

    with pytest.raises(RuntimeError, match="Restart `sforge serve`"):
        _validate_judge_registration(
            {"token": "old-server-token"},
            run_id="group-r01",
            judge_group_id="group",
            judge_concurrency=1,
        )


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
    agent = CodexAgent(SForgeConfig())
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
            return ExecResult(output="codex-cli 0.150.1")

    backend = FakeBackend()
    agent = CodexAgent(SForgeConfig())
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


def test_codex_api_mode_skips_oauth_and_maps_openai_environment(
    monkeypatch,
) -> None:
    monkeypatch.setenv("SFORGE_CODEX_AUTH_FILE", "/missing/auth.json")
    monkeypatch.setenv("SFORGE_CODEX_RUNTIME_ARCHIVE", "")

    class FakeBackend:
        def copy_to_container(self, *args, **kwargs) -> None:
            raise AssertionError("API mode must not copy an OAuth auth file")

        def exec_run(self, *args, **kwargs) -> ExecResult:
            raise AssertionError("API mode needs no credential file setup")

    config = SForgeConfig(
        agent_api_key="test-api-key",
        agent_api_base_url="http://host.docker.internal:3788/",
    )
    agent = CodexAgent(config)
    env = _build_agent_env(agent, "gpt-5.6-sol")

    agent.prepare_container(
        FakeBackend(),
        object(),
        logging.getLogger(__name__),
    )

    assert env["OPENAI_API_KEY"] == "test-api-key"
    assert env["CODEX_API_KEY"] == "test-api-key"
    assert env["OPENAI_BASE_URL"] == "http://host.docker.internal:3788/"
    assert any("OPENAI_API_KEY" in command for command in CodexAgent.install_cmds)


def test_codex_api_base_requires_api_key() -> None:
    agent = CodexAgent(
        SForgeConfig(
            agent_api_base_url="http://host.docker.internal:3788/",
        )
    )

    with pytest.raises(ValueError, match="SFORGE_AGENT_API_KEY"):
        _build_agent_env(agent, "gpt-5.6-sol")


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

    expected = (
        "codex exec --dangerously-bypass-hook-trust "
        "-c 'model_reasoning_effort=\"medium\"' --model gpt-5.5"
    )
    assert run_cmd.startswith(f"{expected} --json ")
    assert resume_cmd.startswith(f"{expected} --json resume ")


def test_run_result_separates_budget_exhaustion_from_raw_timeout() -> None:
    result = RunResult(
        timed_out=True,
        termination_reason="budget_exhausted",
        runtime_seconds=600.1,
        best_pass_rate=1.0,
        best_score=2258.0,
        total_rounds=1,
        exploration_budget_seconds=600.0,
        finalization_grace_seconds=300.0,
        finalization_runtime_seconds=15.0,
        hard_timeout_seconds=900.0,
    )

    payload = result.to_dict()

    assert payload["timed_out"] is True
    assert payload["termination_reason"] == "budget_exhausted"
    assert payload["budget_exhausted"] is True
    assert payload["exploration_budget_seconds"] == 600.0
    assert payload["finalization_grace_seconds"] == 300.0
    assert payload["finalization_runtime_seconds"] == 15.0
    assert payload["hard_timeout_seconds"] == 900.0


def test_run_result_marks_normal_completion_during_finalization_grace() -> None:
    payload = RunResult(
        timed_out=False,
        termination_reason="completed_in_finalization_grace",
        runtime_seconds=615.0,
        exploration_budget_seconds=600.0,
        finalization_grace_seconds=300.0,
        finalization_runtime_seconds=15.0,
        hard_timeout_seconds=900.0,
    ).to_dict()

    assert payload["timed_out"] is False
    assert payload["budget_exhausted"] is True
    assert payload["termination_reason"] == "completed_in_finalization_grace"


def test_codex_rejects_unknown_reasoning_effort(monkeypatch) -> None:
    monkeypatch.setenv("SFORGE_CODEX_REASONING_EFFORT", "guess")
    agent = object.__new__(CodexAgent)

    with pytest.raises(ValueError, match="SFORGE_CODEX_REASONING_EFFORT"):
        agent.format_run_cmd("/tmp/prompt.md", model="gpt-5.5")


def test_codex_stop_hook_persists_structured_event() -> None:
    hook_script = _generate_codex_stop_hook()

    assert 'hook_event_name\\":\\"Stop' in hook_script
    assert 'decision\\":\\"block' in hook_script
    assert 'started_at\\":\\"$started_at' in hook_script
    assert 'finished_at\\":\\"$finished_at' in hook_script
    assert 'event_dir="${CODEX_HOME:-/home/agent/.codex}/hook-events"' in hook_script


def test_codex_collect_artifacts_preserves_sessions_and_hook_events(
    tmp_path,
) -> None:
    class FakeBackend:
        def __init__(self) -> None:
            self.paths = []

        def copy_from_container(self, handle, path):
            self.paths.append(str(path))
            return f"archive:{path}".encode()

    backend = FakeBackend()
    agent = object.__new__(CodexAgent)
    agent.collect_artifacts(
        backend,
        object(),
        tmp_path,
        logging.getLogger(__name__),
    )

    assert backend.paths == [
        "/home/agent/.codex/sessions",
        "/home/agent/.codex/hook-events",
    ]
    assert (tmp_path / "codex-sessions.tar").is_file()
    assert (tmp_path / "codex-hook-events.tar").is_file()


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
