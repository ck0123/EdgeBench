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


def test_pi_provider_install_fails_fast_when_provider_is_not_visible() -> None:
    for agent_class in (PiProviderAgent, PiGoalPlusProviderAgent):
        commands = "\n".join(agent_class.install_cmds)
        assert 'pi --list-models "$PI_PROVIDER"' in commands
        assert 'grep -F -- "$PI_PROVIDER"' in commands
        assert 'grep -F -- "$PI_MODEL"' in commands


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
                        "apiKey": "$GLM_PROXY_API_KEY",
                        "models": [{"id": "GLM-5.2"}],
                    }
                }
            }
        )
    )
    monkeypatch.setenv("SFORGE_PI_MODELS_FILE", str(models_file))
    monkeypatch.setenv("GLM_PROXY_API_KEY", "test-key")
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
                    "glm-proxy": {
                        "apiKey": "$GLM_PROXY_API_KEY",
                        "models": [{"id": "GLM-5.2"}],
                    }
                }
            }
        )
    )
    monkeypatch.delenv("SFORGE_PI_MODELS_FILE", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("GLM_PROXY_API_KEY", "test-key")
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
                    "glm-proxy": {
                        "apiKey": "$GLM_PROXY_API_KEY",
                        "models": [{"id": "GLM-5.2"}],
                    }
                }
            }
        )
    )
    monkeypatch.setenv("SFORGE_PI_MODELS_FILE", str(models_file))
    monkeypatch.setenv("GLM_PROXY_API_KEY", "test-key")
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


def test_goal_plus_provider_does_not_overwrite_copied_models_registry() -> None:
    assert all(
        "models.json" not in command
        for command in PiGoalPlusProviderAgent.install_cmds
    )


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


def test_pi_provider_passes_deepseek_api_key(monkeypatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-deepseek-key")
    agent = object.__new__(PiProviderAgent)
    env = {}

    agent.augment_env(env, "deepseek/deepseek-chat")

    assert env["PI_PROVIDER"] == "deepseek"
    assert env["PI_MODEL"] == "deepseek-chat"
    assert env["DEEPSEEK_API_KEY"] == "test-deepseek-key"


def test_pi_provider_prefers_anthropic_oauth_token(monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_OAUTH_TOKEN", "test-oauth-token")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-api-key")
    agent = object.__new__(PiProviderAgent)
    env = {}

    agent.augment_env(env, "anthropic/claude-sonnet-4-20250514")

    assert env["ANTHROPIC_OAUTH_TOKEN"] == "test-oauth-token"
    assert "ANTHROPIC_API_KEY" not in env


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


def test_pi_provider_installs_only_selected_provider_config(
    tmp_path, monkeypatch
) -> None:
    models_file = tmp_path / "models.json"
    models_file.write_text(
        json.dumps(
            {
                "providers": {
                    "glm-proxy": {
                        "baseUrl": "http://example.invalid/v1",
                        "apiKey": "$GLM_PROXY_API_KEY",
                        "models": [
                            {"id": "GLM-5.2"},
                            {
                                "id": "other-model",
                                "headers": {
                                    "Authorization": "literal-selected-provider-secret"
                                },
                            },
                        ],
                    },
                    "unselected": {
                        "apiKey": "literal-secret-that-must-not-be-copied",
                        "models": [{"id": "other-model"}],
                    },
                }
            }
        )
    )
    monkeypatch.setenv("SFORGE_PI_MODELS_FILE", str(models_file))
    monkeypatch.setenv("GLM_PROXY_API_KEY", "test-key")

    class FakeBackend:
        def __init__(self) -> None:
            self.writes = []
            self.commands = []

        def write_to_container(self, handle, data, destination) -> None:
            self.writes.append((data, str(destination)))

        def exec_run(self, handle, command, *, user=None) -> ExecResult:
            self.commands.append((command, user))
            return ExecResult()

    backend = FakeBackend()
    agent = object.__new__(PiProviderAgent)
    agent.augment_env({}, "glm-proxy/GLM-5.2")
    agent.prepare_container(backend, object(), logging.getLogger(__name__))

    assert len(backend.writes) == 1
    written_registry = json.loads(backend.writes[0][0])
    assert backend.writes[0][1] == "/home/agent/.pi/agent/models.json"
    assert set(written_registry["providers"]) == {"glm-proxy"}
    assert written_registry["providers"]["glm-proxy"]["apiKey"] == (
        "$GLM_PROXY_API_KEY"
    )
    assert written_registry["providers"]["glm-proxy"]["models"] == [
        {"id": "GLM-5.2"}
    ]
    assert "literal-selected-provider-secret" not in backend.writes[0][0]
    assert "literal-secret-that-must-not-be-copied" not in backend.writes[0][0]
    assert backend.commands[0][0] == ["mkdir", "-p", "/home/agent/.pi/agent"]
    assert backend.commands[-1][0] == [
        "chmod",
        "600",
        "/home/agent/.pi/agent/models.json",
    ]
    assert all("models.json" not in command for command in agent.install_cmds)


@pytest.mark.parametrize(
    "api_key_reference",
    ["$GLM_PROXY_API_KEY", "${GLM_PROXY_API_KEY}"],
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


@pytest.mark.parametrize("api_key", ["GLM_PROXY_API_KEY", "literal-secret"])
def test_pi_provider_rejects_non_reference_api_key(
    tmp_path, monkeypatch, api_key
) -> None:
    models_file = tmp_path / "models.json"
    models_file.write_text(
        json.dumps(
            {
                "providers": {
                    "glm-proxy": {
                        "apiKey": api_key,
                        "models": [{"id": "GLM-5.2"}],
                    }
                }
            }
        )
    )
    monkeypatch.setenv("SFORGE_PI_MODELS_FILE", str(models_file))
    agent = object.__new__(PiProviderAgent)

    with pytest.raises(RuntimeError, match=r"\$NAME"):
        agent.augment_env({}, "glm-proxy/GLM-5.2")


def test_pi_provider_requires_custom_api_key_reference(
    tmp_path, monkeypatch
) -> None:
    models_file = tmp_path / "models.json"
    models_file.write_text(
        json.dumps(
            {
                "providers": {
                    "glm-proxy": {
                        "models": [{"id": "GLM-5.2"}],
                    }
                }
            }
        )
    )
    monkeypatch.setenv("SFORGE_PI_MODELS_FILE", str(models_file))
    agent = object.__new__(PiProviderAgent)

    with pytest.raises(RuntimeError, match="Custom Pi providers require apiKey"):
        agent.augment_env({}, "glm-proxy/GLM-5.2")
