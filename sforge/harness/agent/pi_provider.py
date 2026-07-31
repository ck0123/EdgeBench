# Copyright (c) 2026 ByteDance Ltd. and/or its affiliates
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""Pi adapter for explicit API providers declared in the host registry."""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path, PurePosixPath

from sforge.harness.agent.pi import PiAgent
from sforge.harness.backend.base import ContainerBackend, ContainerHandle


BUILTIN_PROVIDER_API_KEYS = {
    "zai": "ZAI_API_KEY",
}


def _models_source() -> Path:
    return Path(
        os.environ.get(
            "SFORGE_PI_MODELS_FILE",
            Path.home() / ".pi" / "agent" / "models.json",
        )
    ).expanduser().resolve()


def _read_provider_config(
    provider: str,
    model_id: str,
) -> tuple[Path, dict[str, object]]:
    models_source = _models_source()
    if not models_source.is_file():
        raise RuntimeError(
            "Pi models file not found; set SFORGE_PI_MODELS_FILE "
            f"(looked for {models_source})"
        )
    try:
        models = json.loads(models_source.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Invalid Pi models file: {models_source}") from exc
    provider_config = models.get("providers", {}).get(provider)
    if not isinstance(provider_config, dict):
        raise RuntimeError(
            f"Pi provider {provider!r} is not registered in {models_source}"
        )
    registered_ids = {
        entry.get("id")
        for entry in provider_config.get("models", [])
        if isinstance(entry, dict)
    }
    if model_id not in registered_ids:
        raise RuntimeError(
            f"Pi model {provider}/{model_id} is not registered in {models_source}"
        )
    return models_source, provider_config


def _api_key_env_name(provider_config: dict[str, object]) -> str | None:
    value = provider_config.get("apiKey")
    if not isinstance(value, str):
        return None
    match = re.fullmatch(
        r"(?:\$\{([A-Z][A-Z0-9_]*)\}|\$([A-Z][A-Z0-9_]*)|([A-Z][A-Z0-9_]*))",
        value,
    )
    if not match:
        return None
    return next(group for group in match.groups() if group is not None)


def configure_pi_provider(
    agent: object,
    env: dict[str, str],
    model: str | None,
) -> tuple[str, str]:
    model_ref = model or env.get("PI_MODEL")
    if not model_ref or "/" not in model_ref:
        raise RuntimeError(
            "pi-provider model must be PROVIDER/MODEL, "
            "for example glm-proxy/GLM-5.2"
        )
    provider, model_id = model_ref.split("/", 1)
    if not provider or not model_id:
        raise RuntimeError("pi-provider requires non-empty provider and model")
    setattr(agent, "_pi_provider", provider)
    setattr(agent, "_pi_model_id", model_id)
    env["PI_PROVIDER"] = provider
    env["PI_MODEL"] = model_id

    api_key_env = BUILTIN_PROVIDER_API_KEYS.get(provider)
    if api_key_env:
        api_key = os.environ.get(api_key_env)
        if not api_key:
            raise RuntimeError(
                f"Pi provider {provider} requires host environment variable "
                f"{api_key_env}"
            )
        env[api_key_env] = api_key
        return provider, model_id

    models_source, provider_config = _read_provider_config(provider, model_id)
    setattr(agent, "_pi_models_source", models_source)
    custom_api_key_env = _api_key_env_name(provider_config)
    if custom_api_key_env:
        api_key = os.environ.get(custom_api_key_env)
        if not api_key:
            raise RuntimeError(
                f"Pi provider {provider} requires host environment variable "
                f"{custom_api_key_env}"
            )
        env[custom_api_key_env] = api_key
    return provider, model_id


def prepare_pi_provider_container(
    agent: object,
    backend: ContainerBackend,
    handle: ContainerHandle,
    logger: logging.Logger,
) -> None:
    provider = getattr(agent, "_pi_provider", None)
    model_id = getattr(agent, "_pi_model_id", None)
    if provider in BUILTIN_PROVIDER_API_KEYS:
        logger.info("Using Pi built-in provider %s for model %s", provider, model_id)
        return
    models_source = getattr(agent, "_pi_models_source", None)
    if not isinstance(models_source, Path):
        models_source, _ = _read_provider_config(str(provider), str(model_id))
    destination = PurePosixPath("/home/agent/.pi/agent/models.json")
    backend.copy_to_container(handle, models_source, destination)
    backend.exec_run(
        handle,
        "chown -R agent:agent /home/agent/.pi && "
        "chmod 600 /home/agent/.pi/agent/models.json",
        user="root",
    )
    logger.info(
        "Copied host Pi model registry into the work container for %s/%s",
        provider,
        model_id,
    )


class PiProviderAgent(PiAgent):
    """Run Pi with an explicit API provider/model pair from the host registry."""

    name = "pi-provider"
    install_cmds = PiAgent.install_cmds[:2]
    run_cmd = (
        'pi -p --mode json --provider "$PI_PROVIDER" --model "$PI_MODEL" '
        '"$(cat {prompt_file})"'
    )
    resume_cmd = (
        'pi -p --mode json -c --provider "$PI_PROVIDER" --model "$PI_MODEL" '
        '"Continue working."'
    )
    default_api_base_url = None

    def augment_env(self, env: dict[str, str], model: str | None) -> None:
        super().augment_env(env, model)
        configure_pi_provider(self, env, model)

    def prepare_container(
        self,
        backend: ContainerBackend,
        handle: ContainerHandle,
        logger: logging.Logger,
    ) -> None:
        prepare_pi_provider_container(self, backend, handle, logger)
