# Copyright (c) 2026 ByteDance Ltd. and/or its affiliates
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Pi coding agent with the Goal Plus project extension loaded."""

from __future__ import annotations

import logging
from pathlib import Path

from sforge.harness.agent.goal_plus_runtime import (
    GOAL_PLUS_CONTAINER_DIR,
    GOAL_PLUS_STATE_DIR,
    collect_goal_plus_artifacts,
    goal_plus_runtime_install_cmds,
    prepare_goal_plus_container,
)
from sforge.harness.agent.pi import PiAgent
from sforge.harness.backend import ContainerBackend, ContainerHandle


class PiGoalPlusAgent(PiAgent):
    """Run the normal EdgeBench prompt through Pi's ``/goal-plus`` entrypoint."""

    name = "pi-goal-plus"
    install_goal_plus_bridge = True
    # Goal Plus registers its stop gate through Pi's native ``agent_end`` event
    # in the extension loaded by run_cmd/resume_cmd.
    stop_hook = "pi-native-goal-plus"
    install_cmds = [
        *PiAgent.install_cmds,
        *goal_plus_runtime_install_cmds(),
        r'''mkdir -p ~/.pi/agent/prompts ~/.pi/agent/skills
cp /opt/goal-plus/.pi/prompts/goal-plus.md ~/.pi/agent/prompts/goal-plus.md
rm -rf ~/.pi/agent/skills/goal-plus
cp -a /opt/goal-plus/.pi/skills/goal-plus ~/.pi/agent/skills/goal-plus
test -f /opt/goal-plus/.pi/extensions/goal-plus.ts
mkdir -p /home/agent/.goal-plus''',
    ]
    run_cmd = (
        'REMAINING=$((SFORGE_AGENT_DEADLINE - $(date +%s))); '
        'exec pi -p --mode json '
        '-e /opt/goal-plus/.pi/extensions/goal-plus.ts '
        '--provider openai-codex --model "$PI_MODEL" '
        '"/goal-plus $(cat {prompt_file})\n\n'
        'Use the Goal Plus framework to perform deep search optimization for this task.\n'
        'For the initial frozen SearchSpec, set budget.max_parallel to 3 and '
        'choose budget.max_candidates yourself from the remaining task time and '
        'the search plan. Prefer a small number of serious directions and deep '
        'reinvestment over shallow breadth. Set strategy.worker_budget to '
        '{{"max_runtime_seconds": 1200, "max_turns": 40, '
        '"on_exceed": "interrupt"}}; this is the normal first-round budget for '
        'each candidate worker, not a cap on justified reinvestment. If a '
        'first-round proposal is already a particularly valuable macro direction, '
        'you may give it a larger one-dispatch worker_budgets entry.\n'
        'The total exploration time budget for this task is '
        '${{SFORGE_AGENT_TOTAL_BUDGET_SECONDS}} seconds. The hard deadline is Unix '
        'timestamp ${{SFORGE_AGENT_DEADLINE}}, and ${{REMAINING}} seconds remain at '
        'this launch. The authoritative deadline is also available in '
        '/opt/sforge-agent-deadline. Use this time information to decide the '
        'search budget, number of rounds, and final-verification time yourself; '
        'no round count is prescribed. Refresh the remaining time before deciding '
        'whether to start each next search round. After every completed batch, '
        'inspect each candidate research_summary and verifier trajectory. '
        'Selectively redispatch valuable directions with an explicit larger '
        'one-dispatch worker_budget; do not give every candidate the same extra '
        'time and do not stop a promising worker merely after a few artifacts.\n\n'
        'EdgeBench integration requirement: Goal Plus candidate workspaces are '
        'isolated from the main task workspace. After every search_promote call, '
        'the outer/main Pi session must run sforge-goal-plus-submit --details. '
        'That command atomically copies the selected candidate submitted files '
        'into the main workspace, verifies their hashes, then synchronously calls '
        'the Judge and returns its raw score, official 0-100 score, validity, and '
        'test details. Never call it from a candidate worker. Do not record the '
        'search result or mark the goal complete when this command fails. Use the '
        'returned Judge result when deciding whether another search round is '
        'needed."'
    )
    resume_cmd = (
        'REMAINING=$((SFORGE_AGENT_DEADLINE - $(date +%s))); '
        'SYNC_OUTPUT=$(sforge-goal-plus-submit --details --if-new 2>&1); '
        'SYNC_STATUS=$?; '
        'exec pi -p --mode json -c '
        '-e /opt/goal-plus/.pi/extensions/goal-plus.ts '
        '--provider openai-codex --model "$PI_MODEL" '
        '"Continue working. Before this resume, the EdgeBench Goal Plus promotion '
        'bridge returned exit status ${SYNC_STATUS}:\n${SYNC_OUTPUT}\n\n'
        'Use the Goal Plus framework to continue deep search '
        'optimization for this task. The total exploration time budget is '
        '${SFORGE_AGENT_TOTAL_BUDGET_SECONDS} seconds; the hard deadline is Unix '
        'timestamp ${SFORGE_AGENT_DEADLINE}, and ${REMAINING} seconds remain now. '
        'If the initial SearchSpec has not been frozen yet, set '
        'budget.max_parallel to 3, choose budget.max_candidates yourself from '
        'the remaining task time and a depth-first search plan, and set '
        'strategy.worker_budget to {"max_runtime_seconds": 1200, '
        '"max_turns": 40, "on_exceed": "interrupt"}. Treat 1200 seconds as '
        'the normal first-round worker budget, not a reinvestment cap. '
        'Use the current remaining time to choose the next search work yourself; '
        'no round count is prescribed. After every completed batch, inspect '
        'research_summary and verifier trajectories, then selectively redispatch '
        'valuable directions with a larger one-dispatch worker_budget. After every '
        'search_promote, run '
        'sforge-goal-plus-submit --details from the outer/main session and require '
        'a successful Judge result before recording or completing the search."'
    )

    def prepare_container(
        self,
        backend: ContainerBackend,
        handle: ContainerHandle,
        logger: logging.Logger,
    ) -> None:
        super().prepare_container(backend, handle, logger)
        prepare_goal_plus_container(backend, handle, logger)

    def install_stop_hook(
        self,
        backend: ContainerBackend,
        handle: ContainerHandle,
        log_dir: Path,
        logger: logging.Logger,
    ) -> None:
        """Report the stop gate supplied by the loaded Goal Plus Pi extension."""
        logger.info(
            "Goal Plus stop gate is provided by the Pi extension's native agent_end hook"
        )

    def augment_env(self, env: dict[str, str], model: str | None) -> None:
        super().augment_env(env, model)
        env["GOAL_PLUS_SOURCE_PATH"] = GOAL_PLUS_CONTAINER_DIR
        env["GOAL_PLUS_ROOT"] = GOAL_PLUS_STATE_DIR
        env["GOAL_PLUS_ROLE"] = "main"
        env["GOAL_PLUS_PI_ROLE"] = "main"
        if model:
            env["GOAL_PLUS_PI_MODEL"] = f"openai-codex/{model}"

    def collect_artifacts(
        self,
        backend: ContainerBackend,
        handle: ContainerHandle,
        log_dir: Path,
        logger: logging.Logger,
    ) -> None:
        collect_goal_plus_artifacts(backend, handle, log_dir, logger)
