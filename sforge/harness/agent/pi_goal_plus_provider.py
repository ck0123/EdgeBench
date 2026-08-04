# Copyright (c) 2026 ByteDance Ltd. and/or its affiliates
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""Goal Plus + Pi adapter for explicit API providers in the host registry."""

from __future__ import annotations

import logging

from sforge.harness.agent.goal_plus_runtime import prepare_goal_plus_container
from sforge.harness.agent.pi_goal_plus import PiGoalPlusAgent
from sforge.harness.agent.pi_provider import (
    PI_PROVIDER_RUNTIME_GATE_CMD,
    configure_pi_provider,
    prepare_pi_provider_container,
)
from sforge.harness.backend import ContainerBackend, ContainerHandle


class PiGoalPlusProviderAgent(PiGoalPlusAgent):
    """Run Goal Plus through Pi with an explicit provider/model pair."""

    name = "pi-goal-plus-provider"
    default_api_base_url = None
    # The provider registry is copied by ``prepare_pi_provider_container``.
    # Drop PiAgent's third install step, which writes the OAuth/sforge-proxy
    # registry and would otherwise overwrite the selected provider config.
    install_cmds = [
        *PiGoalPlusAgent.install_cmds[:2],
        PI_PROVIDER_RUNTIME_GATE_CMD,
        *PiGoalPlusAgent.install_cmds[3:],
    ]
    run_cmd = PiGoalPlusAgent.run_cmd.replace(
        "--provider openai-codex", '--provider "$PI_PROVIDER"'
    )
    resume_cmd = PiGoalPlusAgent.resume_cmd.replace(
        "--provider openai-codex", '--provider "$PI_PROVIDER"'
    )

    def augment_env(self, env: dict[str, str], model: str | None) -> None:
        super().augment_env(env, model)
        provider, model_id = configure_pi_provider(self, env, model)
        env["GOAL_PLUS_PI_MODEL"] = env.get(
            "SFORGE_GOAL_PLUS_WORKER_MODEL", f"{provider}/{model_id}"
        )

    def prepare_container(
        self,
        backend: ContainerBackend,
        handle: ContainerHandle,
        logger: logging.Logger,
    ) -> None:
        prepare_pi_provider_container(self, backend, handle, logger)
        prepare_goal_plus_container(backend, handle, logger)
