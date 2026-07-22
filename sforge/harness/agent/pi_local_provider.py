# Copyright (c) 2026 ByteDance Ltd. and/or its affiliates
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""Pi adapter for providers declared in the host's local models.json."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path, PurePosixPath

from sforge.harness.agent.pi import PiAgent
from sforge.harness.backend.base import ContainerBackend, ContainerHandle


class PiLocalProviderAgent(PiAgent):
    """Run Pi with a provider/model pair from the host's local registry."""

    name = "pi-local-provider"
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

    _builtin_provider_api_keys = {
        "zai": "ZAI_API_KEY",
    }

    def augment_env(self, env: dict[str, str], model: str | None) -> None:
        super().augment_env(env, model)
        model_ref = model or env.get("PI_MODEL")
        if not model_ref or "/" not in model_ref:
            raise RuntimeError(
                "pi-local-provider model must be PROVIDER/MODEL, "
                "for example glm-proxy/GLM-5.2"
            )
        provider, model_id = model_ref.split("/", 1)
        if not provider or not model_id:
            raise RuntimeError("pi-local-provider requires non-empty provider and model")
        self._pi_provider = provider
        self._pi_model_id = model_id
        env["PI_PROVIDER"] = provider
        env["PI_MODEL"] = model_id

        api_key_env = self._builtin_provider_api_keys.get(provider)
        if api_key_env:
            api_key = os.environ.get(api_key_env)
            if not api_key:
                raise RuntimeError(
                    f"Pi provider {provider} requires host environment variable "
                    f"{api_key_env}"
                )
            env[api_key_env] = api_key

    def prepare_container(
        self,
        backend: ContainerBackend,
        handle: ContainerHandle,
        logger: logging.Logger,
    ) -> None:
        provider = getattr(self, "_pi_provider", None)
        model_id = getattr(self, "_pi_model_id", None)
        if provider in self._builtin_provider_api_keys:
            logger.info(
                "Using Pi built-in provider %s for model %s",
                provider,
                model_id,
            )
            return

        models_source = Path(
            os.environ.get(
                "SFORGE_PI_MODELS_FILE",
                Path.home() / ".pi" / "agent" / "models.json",
            )
        ).expanduser().resolve()
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
        registered_models = (
            provider_config.get("models", [])
            if isinstance(provider_config, dict)
            else []
        )
        registered_ids = {
            entry.get("id")
            for entry in registered_models
            if isinstance(entry, dict)
        }
        if model_id not in registered_ids:
            raise RuntimeError(
                f"Pi model {provider}/{model_id} is not registered in {models_source}"
            )

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
