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


BUILTIN_PROVIDER_API_KEYS: dict[str, tuple[str, ...]] = {
    "github-copilot": ("COPILOT_GITHUB_TOKEN",),
    "anthropic": ("ANTHROPIC_OAUTH_TOKEN", "ANTHROPIC_API_KEY"),
    "ant-ling": ("ANT_LING_API_KEY",),
    "openai": ("OPENAI_API_KEY",),
    "azure-openai-responses": ("AZURE_OPENAI_API_KEY",),
    "nvidia": ("NVIDIA_API_KEY",),
    "deepseek": ("DEEPSEEK_API_KEY",),
    "google": ("GEMINI_API_KEY",),
    "google-vertex": ("GOOGLE_CLOUD_API_KEY",),
    "groq": ("GROQ_API_KEY",),
    "cerebras": ("CEREBRAS_API_KEY",),
    "xai": ("XAI_API_KEY",),
    "openrouter": ("OPENROUTER_API_KEY",),
    "vercel-ai-gateway": ("AI_GATEWAY_API_KEY",),
    "zai": ("ZAI_API_KEY",),
    "zai-coding-cn": ("ZAI_CODING_CN_API_KEY",),
    "mistral": ("MISTRAL_API_KEY",),
    "minimax": ("MINIMAX_API_KEY",),
    "minimax-cn": ("MINIMAX_CN_API_KEY",),
    "moonshotai": ("MOONSHOT_API_KEY",),
    "moonshotai-cn": ("MOONSHOT_API_KEY",),
    "huggingface": ("HF_TOKEN",),
    "fireworks": ("FIREWORKS_API_KEY",),
    "together": ("TOGETHER_API_KEY",),
    "opencode": ("OPENCODE_API_KEY",),
    "opencode-go": ("OPENCODE_API_KEY",),
    "kimi-coding": ("KIMI_API_KEY",),
    "cloudflare-workers-ai": ("CLOUDFLARE_API_KEY",),
    "cloudflare-ai-gateway": ("CLOUDFLARE_API_KEY",),
    "xiaomi": ("XIAOMI_API_KEY",),
    "xiaomi-token-plan-cn": ("XIAOMI_TOKEN_PLAN_CN_API_KEY",),
    "xiaomi-token-plan-ams": ("XIAOMI_TOKEN_PLAN_AMS_API_KEY",),
    "xiaomi-token-plan-sgp": ("XIAOMI_TOKEN_PLAN_SGP_API_KEY",),
}

PI_PROVIDER_RUNTIME_GATE_CMD = r'''set -euo pipefail
MODELS=$(pi --list-models "$PI_PROVIDER")
printf '%s\n' "$MODELS"
printf '%s\n' "$MODELS" | grep -F -- "$PI_PROVIDER" >/dev/null
printf '%s\n' "$MODELS" | grep -F -- "$PI_MODEL" >/dev/null
for REF in ${SFORGE_PI_AUX_MODELS:-}; do
    PROVIDER=${REF%%/*}
    MODEL=${REF#*/}
    test -n "$PROVIDER" && test -n "$MODEL" && test "$PROVIDER" != "$MODEL"
    AUX_MODELS=$(pi --list-models "$PROVIDER")
    printf '%s\n' "$AUX_MODELS"
    printf '%s\n' "$AUX_MODELS" | grep -F -- "$PROVIDER" >/dev/null
    printf '%s\n' "$AUX_MODELS" | grep -F -- "$MODEL" >/dev/null
done'''


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


def _api_key_env_name(provider_config: dict[str, object]) -> str:
    value = provider_config.get("apiKey")
    if value is None:
        raise RuntimeError(
            "Custom Pi providers require apiKey as $NAME or ${NAME}"
        )
    if not isinstance(value, str):
        raise RuntimeError("Pi provider apiKey must be an environment reference")
    match = re.fullmatch(
        r"(?:\$\{([A-Z][A-Z0-9_]*)\}|\$([A-Z][A-Z0-9_]*))",
        value,
    )
    if not match:
        raise RuntimeError(
            "Pi provider apiKey must reference a host environment variable "
            "as $NAME or ${NAME}; literal credentials are not allowed"
        )
    return next(group for group in match.groups() if group is not None)


def _selected_provider_config(
    provider_config: dict[str, object],
    model_id: str,
) -> dict[str, object]:
    selected = dict(provider_config)
    selected["models"] = [
        entry
        for entry in provider_config.get("models", [])
        if isinstance(entry, dict) and entry.get("id") == model_id
    ]
    return selected


def _merge_provider_config(
    selected: dict[str, object] | None,
    addition: dict[str, object],
) -> dict[str, object]:
    if selected is None:
        return addition
    models = list(selected.get("models", []))
    model_ids = {
        item.get("id") for item in models if isinstance(item, dict)
    }
    models.extend(
        item
        for item in addition.get("models", [])
        if isinstance(item, dict) and item.get("id") not in model_ids
    )
    selected["models"] = models
    return selected


def _model_refs(env: dict[str, str], main_ref: str) -> list[str]:
    refs = [main_ref, *env.get("SFORGE_PI_AUX_MODELS", "").split()]
    return list(dict.fromkeys(ref for ref in refs if ref))


def _builtin_api_key(
    provider: str,
) -> tuple[str, str] | None:
    for name in BUILTIN_PROVIDER_API_KEYS.get(provider, ()):
        value = os.environ.get(name)
        if value:
            return name, value
    return None


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

    provider_configs: dict[str, dict[str, object]] = {}
    for selected_ref in _model_refs(env, model_ref):
        selected_provider, separator, selected_model = selected_ref.partition("/")
        if not separator or not selected_provider or not selected_model:
            raise RuntimeError(
                "Pi auxiliary models must use PROVIDER/MODEL references"
            )
        builtin_api_keys = BUILTIN_PROVIDER_API_KEYS.get(selected_provider)
        if builtin_api_keys:
            credential = _builtin_api_key(selected_provider)
            if credential is None:
                expected = " or ".join(builtin_api_keys)
                raise RuntimeError(
                    f"Pi provider {selected_provider} requires host environment "
                    f"variable {expected}"
                )
            api_key_env, api_key = credential
            env[api_key_env] = api_key
            continue

        models_source, provider_config = _read_provider_config(
            selected_provider, selected_model
        )
        setattr(agent, "_pi_models_source", models_source)
        provider_config = _selected_provider_config(
            provider_config, selected_model
        )
        custom_api_key_env = _api_key_env_name(provider_config)
        api_key = os.environ.get(custom_api_key_env)
        if not api_key:
            raise RuntimeError(
                f"Pi provider {selected_provider} requires host environment "
                f"variable {custom_api_key_env}"
            )
        env[custom_api_key_env] = api_key
        provider_configs[selected_provider] = _merge_provider_config(
            provider_configs.get(selected_provider), provider_config
        )
    setattr(agent, "_pi_provider_configs", provider_configs)
    setattr(agent, "_pi_provider_config", provider_configs.get(provider))
    return provider, model_id


def prepare_pi_provider_container(
    agent: object,
    backend: ContainerBackend,
    handle: ContainerHandle,
    logger: logging.Logger,
) -> None:
    provider = getattr(agent, "_pi_provider", None)
    model_id = getattr(agent, "_pi_model_id", None)
    provider_configs = getattr(agent, "_pi_provider_configs", None)
    if provider_configs is None and provider in BUILTIN_PROVIDER_API_KEYS:
        logger.info("Using Pi built-in provider %s for model %s", provider, model_id)
        return
    if provider_configs is None:
        provider_config = getattr(agent, "_pi_provider_config", None)
        if not isinstance(provider_config, dict):
            _, provider_config = _read_provider_config(str(provider), str(model_id))
            provider_config = _selected_provider_config(
                provider_config,
                str(model_id),
            )
            _api_key_env_name(provider_config)
        provider_configs = {str(provider): provider_config}
    if not provider_configs:
        logger.info("Using only Pi built-in providers for this run")
        return
    for provider_config in provider_configs.values():
        _api_key_env_name(provider_config)
    destination = PurePosixPath("/home/agent/.pi/agent/models.json")
    selected_registry = json.dumps(
        {"providers": provider_configs},
        indent=2,
    )
    backend.exec_run(
        handle,
        ["mkdir", "-p", "/home/agent/.pi/agent"],
        user="root",
    )
    backend.exec_run(
        handle,
        ["chown", "-R", "agent:agent", "/home/agent/.pi"],
        user="root",
    )
    backend.write_to_container(handle, selected_registry, destination)
    backend.exec_run(
        handle,
        ["chown", "-R", "agent:agent", "/home/agent/.pi"],
        user="root",
    )
    backend.exec_run(
        handle,
        ["chmod", "600", "/home/agent/.pi/agent/models.json"],
        user="root",
    )
    logger.info(
        "Installed selected Pi provider registry in the work container for %s",
        ", ".join(sorted(provider_configs)),
    )


class PiProviderAgent(PiAgent):
    """Run Pi with an explicit API provider/model pair from the host registry."""

    name = "pi-provider"
    install_cmds = [
        *PiAgent.install_cmds[:2],
        PI_PROVIDER_RUNTIME_GATE_CMD,
    ]
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
