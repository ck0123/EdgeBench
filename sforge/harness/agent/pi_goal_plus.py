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
    DEFAULT_GOAL_PLUS_FINALIZATION_GRACE_SECONDS,
    DEFAULT_GOAL_PLUS_MAX_PARALLEL,
    DEFAULT_GOAL_PLUS_WORKER_RUNTIME_SECONDS,
    GOAL_PLUS_CONTAINER_DIR,
    GOAL_PLUS_FINALIZATION_GRACE_ENV,
    GOAL_PLUS_MAX_PARALLEL_ENV,
    GOAL_PLUS_STATE_DIR,
    GOAL_PLUS_WORKER_RUNTIME_ENV,
    collect_goal_plus_live_status,
    collect_goal_plus_artifacts,
    goal_plus_should_resume_after_exit,
    goal_plus_runtime_install_cmds,
    nonnegative_int_extra_env,
    positive_int_extra_env,
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
    live_status_interval_seconds = 15.0
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
        'export GOAL_PLUS_OUTER_DEADLINE_AT="$SFORGE_AGENT_DEADLINE"; '
        'REMAINING=$((SFORGE_AGENT_DEADLINE - $(date +%s))); '
        'HARD_REMAINING=$((SFORGE_AGENT_HARD_DEADLINE - $(date +%s))); '
        'exec pi -p --mode json '
        '-e /opt/goal-plus/.pi/extensions/goal-plus.ts '
        '--provider openai-codex --model "$PI_MODEL" '
        '--thinking "$SFORGE_PI_REASONING_EFFORT" '
        '"/goal-plus $(cat {prompt_file})\n\n'
        'Use the Goal Plus framework to perform deep search optimization for this task.\n'
        'For the initial frozen SearchSpec, set strategy.worker_host to pi and '
        'budget.max_parallel to __GOAL_PLUS_MAX_PARALLEL__ and '
        'choose budget.max_candidates yourself from the remaining task time and '
        'the search plan. Prefer a small number of serious directions and deep '
        'reinvestment over shallow breadth. Set strategy.worker_budget to '
        '{{"max_runtime_seconds": __GOAL_PLUS_WORKER_RUNTIME_SECONDS__, '
        '"on_exceed": "interrupt"}}; do not prescribe a turn limit. This is the '
        'normal first-dispatch budget for each candidate worker, not a cap on '
        'justified reinvestment.\n'
        'The total exploration time budget for this task is '
        '${{SFORGE_AGENT_TOTAL_BUDGET_SECONDS}} seconds. The exploration cutoff is '
        'Unix timestamp ${{SFORGE_AGENT_DEADLINE}}, and ${{REMAINING}} exploration '
        'seconds remain at this launch. The host provides '
        '${{SFORGE_AGENT_FINALIZATION_GRACE_SECONDS}} additional seconds only for '
        'final verification, selection, promotion, synchronous Judge feedback, '
        'goal_plus_record_search_result, raw-goal audit, terminal status, and the '
        'one final search_report call. Its hard process deadline is '
        '${{SFORGE_AGENT_HARD_DEADLINE}}, with ${{HARD_REMAINING}} seconds remaining. '
        'After the exploration cutoff, do not freeze/create another Search run, '
        'launch/continue a worker, or perform more optimization. The authoritative '
        'cutoff and hard deadline are also available in /opt/sforge-agent-deadline '
        'and /opt/sforge-agent-hard-deadline. Refresh the exploration time before '
        'each rolling-pool decision and reserve time for final verification, '
        'selection, promotion, and Judge feedback. No round count is prescribed.\n\n'
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
        'export GOAL_PLUS_OUTER_DEADLINE_AT="$SFORGE_AGENT_DEADLINE"; '
        'REMAINING=$((SFORGE_AGENT_DEADLINE - $(date +%s))); '
        'HARD_REMAINING=$((SFORGE_AGENT_HARD_DEADLINE - $(date +%s))); '
        'SYNC_OUTPUT=$(sforge-goal-plus-submit --details --if-new 2>&1); '
        'SYNC_STATUS=$?; '
        'exec pi -p --mode json -c '
        '-e /opt/goal-plus/.pi/extensions/goal-plus.ts '
        '--provider openai-codex --model "$PI_MODEL" '
        '--thinking "$SFORGE_PI_REASONING_EFFORT" '
        '"Continue working. Before this resume, the EdgeBench Goal Plus promotion '
        'bridge returned exit status ${SYNC_STATUS}:\n${SYNC_OUTPUT}\n\n'
        'Use the Goal Plus framework to continue deep search '
        'optimization for this task. The total exploration time budget is '
        '${SFORGE_AGENT_TOTAL_BUDGET_SECONDS} seconds; its cutoff is Unix timestamp '
        '${SFORGE_AGENT_DEADLINE}, and ${REMAINING} exploration seconds remain. '
        'The finalization-only hard deadline is ${SFORGE_AGENT_HARD_DEADLINE}, with '
        '${HARD_REMAINING} seconds remaining. Once the exploration cutoff is reached, '
        'do not create a new Search run or launch/continue a worker; only finish the '
        'Judge, result recording, raw-goal audit, terminal status, and final report. '
        'If the initial SearchSpec has not been frozen yet, set '
        'strategy.worker_host to pi, budget.max_parallel to '
        '__GOAL_PLUS_MAX_PARALLEL__, choose budget.max_candidates from '
        'the remaining time, and set strategy.worker_budget to '
        '{"max_runtime_seconds": __GOAL_PLUS_WORKER_RUNTIME_SECONDS__, '
        '"on_exceed": "interrupt"} without a turn limit. After every '
        'search_promote, run '
        'sforge-goal-plus-submit --details from the outer/main session and require '
        'a successful Judge result before recording or completing the search."'
    )

    def format_run_cmd(
        self,
        prompt_path: str,
        *,
        model: str | None = None,
        cwd: str = "",
        internet: bool = True,
        resume: bool = False,
    ) -> str:
        cmd = super().format_run_cmd(
            prompt_path,
            model=model,
            cwd=cwd,
            internet=internet,
            resume=resume,
        )
        max_parallel = positive_int_extra_env(
            self._config.agent_extra_env,
            GOAL_PLUS_MAX_PARALLEL_ENV,
            DEFAULT_GOAL_PLUS_MAX_PARALLEL,
        )
        worker_runtime = positive_int_extra_env(
            self._config.agent_extra_env,
            GOAL_PLUS_WORKER_RUNTIME_ENV,
            DEFAULT_GOAL_PLUS_WORKER_RUNTIME_SECONDS,
        )
        return cmd.replace(
            "__GOAL_PLUS_MAX_PARALLEL__", str(max_parallel)
        ).replace(
            "__GOAL_PLUS_WORKER_RUNTIME_SECONDS__", str(worker_runtime)
        )

    def get_finalization_grace_seconds(self) -> int:
        return nonnegative_int_extra_env(
            self._config.agent_extra_env,
            GOAL_PLUS_FINALIZATION_GRACE_ENV,
            DEFAULT_GOAL_PLUS_FINALIZATION_GRACE_SECONDS,
        )

    def should_resume_after_exit(
        self,
        backend: ContainerBackend,
        handle: ContainerHandle,
        logger: logging.Logger,
    ) -> bool:
        return goal_plus_should_resume_after_exit(backend, handle, logger)

    def collect_live_status(
        self,
        backend: ContainerBackend,
        handle: ContainerHandle,
        log_dir: Path,
        logger: logging.Logger,
    ) -> None:
        collect_goal_plus_live_status(backend, handle, log_dir, logger)

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
        env["GOAL_PLUS_SEARCH_ROOT"] = GOAL_PLUS_STATE_DIR
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
