from __future__ import annotations

import json
import logging

import pytest

from sforge.harness.agent.factory import get_agent_class
from sforge.harness.agent.pi_goal_plus_provider import (
    PiGoalPlusProviderAgent,
)
from sforge.harness.agent.pi_provider import PiProviderAgent
from sforge.harness.backend.base import ExecResult
from sforge.harness.config import SForgeConfig


def test_pi_provider_agents_are_registered_without_local_alias() -> None:
    assert get_agent_class("pi-provider") is PiProviderAgent
    assert get_agent_class("pi-goal-plus-provider") is PiGoalPlusProviderAgent
    with pytest.raises(ValueError, match="Unknown agent"):
        get_agent_class("pi-local-provider")


@pytest.mark.parametrize(
    "wire_api",
    ["anthropic-messages", "openai-completions", "openai-responses"],
)
def test_pi_provider_accepts_pi_supported_wire_apis(
    tmp_path, monkeypatch, wire_api
) -> None:
    models_file = tmp_path / "models.json"
    models_file.write_text(
        json.dumps(
            {
                "providers": {
                    "glm-proxy": {
                        "api": wire_api,
                        "models": [{"id": "GLM-5.2"}],
                    }
                }
            }
        )
    )
    monkeypatch.setenv("SFORGE_PI_MODELS_FILE", str(models_file))
    agent = object.__new__(PiProviderAgent)
    env = {"PI_MODEL": "glm-proxy/GLM-5.2"}

    agent.augment_env(env, "glm-proxy/GLM-5.2")

    assert env["PI_PROVIDER"] == "glm-proxy"
    assert env["PI_MODEL"] == "GLM-5.2"


def test_pi_provider_uses_platform_neutral_home_registry(
    tmp_path, monkeypatch
) -> None:
    models_file = tmp_path / ".pi" / "agent" / "models.json"
    models_file.parent.mkdir(parents=True)
    models_file.write_text(
        json.dumps(
            {
                "providers": {
                    "glm-proxy": {"models": [{"id": "GLM-5.2"}]}
                }
            }
        )
    )
    monkeypatch.delenv("SFORGE_PI_MODELS_FILE", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    agent = object.__new__(PiProviderAgent)

    agent.augment_env({}, "glm-proxy/GLM-5.2")

    assert agent._pi_models_source == models_file.resolve()


def test_goal_plus_provider_uses_selected_model_for_outer_and_workers(
    tmp_path, monkeypatch
) -> None:
    models_file = tmp_path / "models.json"
    models_file.write_text(
        json.dumps(
            {
                "providers": {
                    "glm-proxy": {"models": [{"id": "GLM-5.2"}]}
                }
            }
        )
    )
    monkeypatch.setenv("SFORGE_PI_MODELS_FILE", str(models_file))
    agent = PiGoalPlusProviderAgent(
        SForgeConfig(
            agent_extra_env={"SFORGE_GOAL_PLUS_PARALLEL_NUM": "2"}
        )
    )
    env: dict[str, str] = {}

    command = agent.format_run_cmd(
        "/tmp/prompt.md", model="glm-proxy/GLM-5.2"
    )
    agent.augment_env(env, "glm-proxy/GLM-5.2")

    assert '--provider "$PI_PROVIDER" --model "$PI_MODEL"' in command
    assert "--provider openai-codex" not in command
    assert "budget.max_parallel to 2" in command
    assert "-e /opt/goal-plus/.pi/extensions/goal-plus.ts" in command
    assert env["PI_PROVIDER"] == "glm-proxy"
    assert env["PI_MODEL"] == "GLM-5.2"
    assert env["GOAL_PLUS_PI_MODEL"] == "glm-proxy/GLM-5.2"


def test_goal_plus_provider_prepares_provider_and_goal_plus(
    monkeypatch,
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        "sforge.harness.agent.pi_goal_plus_provider.prepare_pi_provider_container",
        lambda *args: calls.append("provider"),
    )
    monkeypatch.setattr(
        "sforge.harness.agent.pi_goal_plus_provider.prepare_goal_plus_container",
        lambda *args: calls.append("goal-plus"),
    )
    agent = object.__new__(PiGoalPlusProviderAgent)

    agent.prepare_container(object(), object(), logging.getLogger(__name__))

    assert calls == ["provider", "goal-plus"]


def test_pi_provider_requires_qualified_model() -> None:
    agent = object.__new__(PiProviderAgent)

    with pytest.raises(RuntimeError, match="PROVIDER/MODEL"):
        agent.augment_env({}, "GLM-5.2")


def test_pi_provider_passes_zai_api_key(monkeypatch) -> None:
    monkeypatch.setenv("ZAI_API_KEY", "test-zai-key")
    agent = object.__new__(PiProviderAgent)
    env = {}

    agent.augment_env(env, "zai/glm-5.2")

    assert env["PI_PROVIDER"] == "zai"
    assert env["PI_MODEL"] == "glm-5.2"
    assert env["ZAI_API_KEY"] == "test-zai-key"


def test_pi_provider_requires_zai_api_key(monkeypatch) -> None:
    monkeypatch.delenv("ZAI_API_KEY", raising=False)
    agent = object.__new__(PiProviderAgent)

    with pytest.raises(RuntimeError, match="ZAI_API_KEY"):
        agent.augment_env({}, "zai/glm-5.2")


def test_pi_provider_uses_builtin_zai_without_models_file(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("ZAI_API_KEY", "test-zai-key")
    monkeypatch.setenv("SFORGE_PI_MODELS_FILE", str(tmp_path / "missing.json"))

    class FakeBackend:
        def copy_to_container(self, *args, **kwargs) -> None:
            raise AssertionError("built-in provider must not copy models.json")

        def exec_run(self, *args, **kwargs) -> ExecResult:
            raise AssertionError("built-in provider needs no credential files")

    agent = object.__new__(PiProviderAgent)
    agent.augment_env({}, "zai/glm-5.2")
    agent.prepare_container(
        FakeBackend(), object(), logging.getLogger(__name__)
    )


def test_pi_provider_copies_matching_models_file(tmp_path, monkeypatch) -> None:
    models_file = tmp_path / "models.json"
    models_file.write_text(
        json.dumps(
            {
                "providers": {
                    "glm-proxy": {
                        "baseUrl": "http://example.invalid/v1",
                        "apiKey": "redacted",
                        "models": [{"id": "GLM-5.2"}],
                    }
                }
            }
        )
    )
    monkeypatch.setenv("SFORGE_PI_MODELS_FILE", str(models_file))

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
    agent = object.__new__(PiProviderAgent)
    agent.augment_env({}, "glm-proxy/GLM-5.2")
    agent.prepare_container(backend, object(), logging.getLogger(__name__))

    assert backend.copies == [
        (models_file.resolve(), "/home/agent/.pi/agent/models.json")
    ]
    assert "chmod 600 /home/agent/.pi/agent/models.json" in backend.commands[-1][0]
    assert all("models.json" not in command for command in agent.install_cmds)


@pytest.mark.parametrize(
    "api_key_reference",
    ["GLM_PROXY_API_KEY", "$GLM_PROXY_API_KEY", "${GLM_PROXY_API_KEY}"],
)
def test_pi_provider_forwards_configured_api_key_env(
    tmp_path, monkeypatch, api_key_reference
) -> None:
    models_file = tmp_path / "models.json"
    models_file.write_text(
        json.dumps(
            {
                "providers": {
                    "glm-proxy": {
                        "apiKey": api_key_reference,
                        "models": [{"id": "GLM-5.2"}],
                    }
                }
            }
        )
    )
    monkeypatch.setenv("SFORGE_PI_MODELS_FILE", str(models_file))
    monkeypatch.setenv("GLM_PROXY_API_KEY", "test-key")
    agent = object.__new__(PiProviderAgent)
    env: dict[str, str] = {}

    agent.augment_env(env, "glm-proxy/GLM-5.2")

    assert env["GLM_PROXY_API_KEY"] == "test-key"
