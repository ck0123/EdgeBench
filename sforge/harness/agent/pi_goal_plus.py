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

import json
import logging
from pathlib import Path

from sforge.harness.agent.goal_plus_runtime import (
    DEFAULT_GOAL_PLUS_FINALIZATION_GRACE_SECONDS,
    DEFAULT_GOAL_PLUS_CLOSEOUT_RESERVE_SECONDS,
    DEFAULT_GOAL_PLUS_MIN_VERIFIER_RUNS,
    DEFAULT_GOAL_PLUS_PARALLEL_NUM,
    DEFAULT_GOAL_PLUS_WORKER_MIN_RUNTIME_SECONDS,
    DEFAULT_GOAL_PLUS_WORKER_RUNTIME_SECONDS,
    GOAL_PLUS_CLOSEOUT_RESERVE_ENV,
    GOAL_PLUS_CONTAINER_DIR,
    GOAL_PLUS_FINALIZATION_GRACE_ENV,
    GOAL_PLUS_PARALLEL_NUM_ENV,
    GOAL_PLUS_MIN_VERIFIER_RUNS_ENV,
    GOAL_PLUS_STATE_DIR,
    GOAL_PLUS_WORKER_RUNTIME_ENV,
    GOAL_PLUS_WORKER_MIN_RUNTIME_ENV,
    collect_goal_plus_live_status,
    collect_goal_plus_artifacts,
    goal_plus_model_token,
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
        '"/goal-plus mode=autonomous max_parallel=__GOAL_PLUS_PARALLEL_NUM__ '
        'workspace_backend=git_worktree promotion_mode=artifact_only '
        'strategy=agent_guided __GOAL_PLUS_ROLE_COMMAND_CONFIG__'
        '$(cat {prompt_file})\n\n'
        'Use the Goal Plus framework to perform deep search optimization for this task.\n'
        'For the initial frozen SearchSpec, set strategy.worker_host to pi and '
        'omit the deprecated budget.max_candidates field. The leading typed '
        'command config is authoritative for max_parallel (the single EdgeBench K), '
        'workspace backend, promotion mode, strategy, and role models. Set '
        'strategy.worker_budget to '
        '__GOAL_PLUS_WORKER_BUDGET__, and '
        'strategy.config.reserve_closeout_seconds to '
        '__GOAL_PLUS_CLOSEOUT_RESERVE_SECONDS__; do not prescribe a turn limit. This is the '
        'normal first-dispatch budget for each candidate worker, not a cap on '
        'justified reinvestment.\n'
        '__GOAL_PLUS_ROLE_MODEL_CONFIG__'
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
        'Closeout discipline: during triage, create one Search-routed work item '
        'covering optimization, Judge-backed promotion, and the final raw-goal '
        'audit; do not create a separate final-audit work item. After successful '
        'Judge feedback and goal_plus_record_search_result, finish the active work '
        'item with exactly one result event followed by one accepted event, then '
        'call goal_plus_set_status immediately with the raw-goal audit evidence and '
        'call search_report once. accepted is valid only after result; do not retry '
        'an invalid work event or duplicate result, accepted, or audit events.\n\n'
        'EdgeBench integration requirement: Goal Plus candidate workspaces are '
        'isolated from the main task workspace and promotion_mode is artifact_only. '
        'After every search_promote call, '
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
        'SYNC_OUTPUT=$(sforge-goal-plus-submit --details --if-new 2>&1); '
        'SYNC_STATUS=$?; '
        'printf "%s\\n%s\\n" "$SYNC_STATUS" "$SYNC_OUTPUT" '
        f'>> {GOAL_PLUS_STATE_DIR}/edgebench-resume-sync.log; '
        'exec pi -p --mode json -c '
        '-e /opt/goal-plus/.pi/extensions/goal-plus.ts '
        '--provider openai-codex --model "$PI_MODEL" '
        '--thinking "$SFORGE_PI_REASONING_EFFORT" '
        '"/goal-plus resume"'
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
        parallel_num = positive_int_extra_env(
            self._config.agent_extra_env,
            GOAL_PLUS_PARALLEL_NUM_ENV,
            DEFAULT_GOAL_PLUS_PARALLEL_NUM,
        )
        worker_runtime = positive_int_extra_env(
            self._config.agent_extra_env,
            GOAL_PLUS_WORKER_RUNTIME_ENV,
            DEFAULT_GOAL_PLUS_WORKER_RUNTIME_SECONDS,
        )
        worker_min_runtime = nonnegative_int_extra_env(
            self._config.agent_extra_env,
            GOAL_PLUS_WORKER_MIN_RUNTIME_ENV,
            DEFAULT_GOAL_PLUS_WORKER_MIN_RUNTIME_SECONDS,
        )
        min_verifier_runs = nonnegative_int_extra_env(
            self._config.agent_extra_env,
            GOAL_PLUS_MIN_VERIFIER_RUNS_ENV,
            DEFAULT_GOAL_PLUS_MIN_VERIFIER_RUNS,
        )
        closeout_reserve = nonnegative_int_extra_env(
            self._config.agent_extra_env,
            GOAL_PLUS_CLOSEOUT_RESERVE_ENV,
            DEFAULT_GOAL_PLUS_CLOSEOUT_RESERVE_SECONDS,
        )
        if worker_min_runtime >= worker_runtime and worker_min_runtime:
            raise ValueError(
                f"{GOAL_PLUS_WORKER_MIN_RUNTIME_ENV} must be less than "
                f"{GOAL_PLUS_WORKER_RUNTIME_ENV}"
            )
        worker_budget = {
            "max_runtime_seconds": worker_runtime,
            "on_exceed": "interrupt",
        }
        if worker_min_runtime:
            worker_budget["min_runtime_seconds"] = worker_min_runtime
        if min_verifier_runs:
            worker_budget["min_verifier_runs"] = min_verifier_runs
        worker_budget_text = json.dumps(worker_budget)
        explicit_worker_model = self._config.agent_extra_env.get(
            "SFORGE_GOAL_PLUS_WORKER_MODEL"
        )
        effective_model = (
            explicit_worker_model
            or model
            or self._config.agent_model
            or self.default_model
        )
        if effective_model and "/" not in effective_model:
            effective_model = f"openai-codex/{effective_model}"
        worker_model = goal_plus_model_token(
            effective_model,
            "Goal Plus Pi worker model",
        )
        role_command_config = f"workers={worker_model}*{parallel_num}"
        annotator_model = self._config.agent_extra_env.get(
            "GOAL_PLUS_EVIDENCE_ANNOTATOR_MODEL"
        )
        if annotator_model:
            role_command_config += " annotator=" + goal_plus_model_token(
                annotator_model,
                "Goal Plus Pi annotator model",
            )
        role_command_config += " "
        role_model_config = ""
        if explicit_worker_model:
            role_model_config = (
                "The typed command config freezes the worker and annotator model "
                "roles. Freeze strategy.worker_launch.reasoning_effort to "
                "${SFORGE_GOAL_PLUS_WORKER_REASONING_EFFORT}. Freeze "
                "strategy.models to one entry with the same reasoning effort. "
                "For strategy.evidence_annotator derive pi_provider from its typed "
                "qualified model reference, set reasoning_effort to "
                "${GOAL_PLUS_EVIDENCE_ANNOTATOR_REASONING_EFFORT}, and set "
                "timeout_seconds to "
                "${SFORGE_GOAL_PLUS_EVIDENCE_ANNOTATOR_TIMEOUT_SECONDS}. "
            )
        return cmd.replace(
            "__GOAL_PLUS_PARALLEL_NUM__", str(parallel_num)
        ).replace(
            "__GOAL_PLUS_ROLE_COMMAND_CONFIG__", role_command_config
        ).replace(
            "__GOAL_PLUS_WORKER_BUDGET__", worker_budget_text
        ).replace(
            "__GOAL_PLUS_CLOSEOUT_RESERVE_SECONDS__", str(closeout_reserve)
        ).replace(
            "__GOAL_PLUS_ROLE_MODEL_CONFIG__", role_model_config
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
