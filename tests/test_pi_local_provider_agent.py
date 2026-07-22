from __future__ import annotations

import json
import logging

import pytest

from sforge.harness.agent.factory import get_agent_class
from sforge.harness.agent.pi_local_provider import PiLocalProviderAgent
from sforge.harness.backend.base import ExecResult


def test_pi_local_provider_is_registered() -> None:
    assert get_agent_class("pi-local-provider") is PiLocalProviderAgent


def test_pi_local_provider_splits_provider_and_model() -> None:
    agent = object.__new__(PiLocalProviderAgent)
    env = {"PI_MODEL": "glm-proxy/GLM-5.2"}

    agent.augment_env(env, "glm-proxy/GLM-5.2")

    assert env["PI_PROVIDER"] == "glm-proxy"
    assert env["PI_MODEL"] == "GLM-5.2"


def test_pi_local_provider_requires_qualified_model() -> None:
    agent = object.__new__(PiLocalProviderAgent)

    with pytest.raises(RuntimeError, match="PROVIDER/MODEL"):
        agent.augment_env({}, "GLM-5.2")


def test_pi_local_provider_passes_zai_api_key(monkeypatch) -> None:
    monkeypatch.setenv("ZAI_API_KEY", "test-zai-key")
    agent = object.__new__(PiLocalProviderAgent)
    env = {}

    agent.augment_env(env, "zai/glm-5.2")

    assert env["PI_PROVIDER"] == "zai"
    assert env["PI_MODEL"] == "glm-5.2"
    assert env["ZAI_API_KEY"] == "test-zai-key"


def test_pi_local_provider_requires_zai_api_key(monkeypatch) -> None:
    monkeypatch.delenv("ZAI_API_KEY", raising=False)
    agent = object.__new__(PiLocalProviderAgent)

    with pytest.raises(RuntimeError, match="ZAI_API_KEY"):
        agent.augment_env({}, "zai/glm-5.2")


def test_pi_local_provider_uses_builtin_zai_without_models_file(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("ZAI_API_KEY", "test-zai-key")
    monkeypatch.setenv("SFORGE_PI_MODELS_FILE", str(tmp_path / "missing.json"))

    class FakeBackend:
        def copy_to_container(self, *args, **kwargs) -> None:
            raise AssertionError("built-in provider must not copy models.json")

        def exec_run(self, *args, **kwargs) -> ExecResult:
            raise AssertionError("built-in provider needs no credential files")

    agent = object.__new__(PiLocalProviderAgent)
    agent.augment_env({}, "zai/glm-5.2")
    agent.prepare_container(
        FakeBackend(), object(), logging.getLogger(__name__)
    )


def test_pi_local_provider_copies_matching_models_file(tmp_path, monkeypatch) -> None:
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
    agent = object.__new__(PiLocalProviderAgent)
    agent.augment_env({}, "glm-proxy/GLM-5.2")
    agent.prepare_container(backend, object(), logging.getLogger(__name__))

    assert backend.copies == [
        (models_file.resolve(), "/home/agent/.pi/agent/models.json")
    ]
    assert "chmod 600 /home/agent/.pi/agent/models.json" in backend.commands[-1][0]
    assert all("models.json" not in command for command in agent.install_cmds)
