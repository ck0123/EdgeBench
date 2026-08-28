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

"""Codex coding agent with Goal Plus project assets and lifecycle hooks."""

from __future__ import annotations

import logging
import shlex
from pathlib import Path

from sforge.harness.agent.codex import CodexAgent, _enable_codex_hooks
from sforge.harness.agent.goal_plus_runtime import (
    DEFAULT_GOAL_PLUS_FINALIZATION_GRACE_SECONDS,
    DEFAULT_GOAL_PLUS_PARALLEL_NUM,
    DEFAULT_GOAL_PLUS_WORKER_RUNTIME_SECONDS,
    GOAL_PLUS_CONTAINER_DIR,
    GOAL_PLUS_FINALIZATION_GRACE_ENV,
    GOAL_PLUS_PARALLEL_NUM_ENV,
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
from sforge.harness.backend import ContainerBackend, ContainerHandle


CODEX_GOAL_PLUS_MCP_OVERRIDES = (
    'mcp_servers.goal-plus.command="goal-plus"',
    f'mcp_servers.goal-plus.args=["--root", "{GOAL_PLUS_STATE_DIR}"]',
    "mcp_servers.goal-plus.startup_timeout_sec=30",
    "mcp_servers.goal-plus.tool_timeout_sec=300",
    "mcp_servers.goal-plus.enabled=true",
    'mcp_servers.goal-plus.default_tools_approval_mode="approve"',
)
CODEX_GOAL_PLUS_MCP_FLAGS = " ".join(
    f"-c {shlex.quote(value)}" for value in CODEX_GOAL_PLUS_MCP_OVERRIDES
)


class CodexGoalPlusAgent(CodexAgent):
    """Run Goal Plus through Codex's exact host command and project hooks."""

    name = "codex-goal-plus"
    install_goal_plus_bridge = True
    stop_hook = "codex-native-goal-plus"
    live_status_interval_seconds = 15.0
    install_cmds = [
        *CodexAgent.install_cmds,
        *goal_plus_runtime_install_cmds(),
        rf'''set -euo pipefail
TASK_ROOT="${{SFORGE_PATCH_DIR:?}}"
CODEX_DIR="$TASK_ROOT/.codex"
mkdir -p "$CODEX_DIR/skills" "$CODEX_DIR/agents" {GOAL_PLUS_STATE_DIR}
cp {GOAL_PLUS_CONTAINER_DIR}/.codex/config.example.toml "$CODEX_DIR/config.toml"
sed -i 's|args = \["--root", ".gp"\]|args = ["--root", "{GOAL_PLUS_STATE_DIR}"]|' "$CODEX_DIR/config.toml"
HOOK_SOURCE=""
for CANDIDATE in \
    {GOAL_PLUS_CONTAINER_DIR}/hooks/hooks.json \
    {GOAL_PLUS_CONTAINER_DIR}/.codex/hooks.example.json \
    {GOAL_PLUS_CONTAINER_DIR}/.codex/hooks.json; do
    if [ -s "$CANDIDATE" ]; then
        HOOK_SOURCE="$CANDIDATE"
        break
    fi
done
test -n "$HOOK_SOURCE"
cp "$HOOK_SOURCE" "$CODEX_DIR/hooks.json"
for SKILL in goal-plus goal-plus-with-final-check search; do
    rm -rf "$CODEX_DIR/skills/$SKILL"
    cp -a "{GOAL_PLUS_CONTAINER_DIR}/.codex/skills/$SKILL" "$CODEX_DIR/skills/$SKILL"
done
for AGENT in search_candidate_agent goal_plus_final_checker; do
    cp "{GOAL_PLUS_CONTAINER_DIR}/.codex/agents/$AGENT.toml" "$CODEX_DIR/agents/$AGENT.toml"
done
test -s "$CODEX_DIR/config.toml"
test -s "$CODEX_DIR/hooks.json"
test -s "$CODEX_DIR/skills/goal-plus/SKILL.md"
test -s "$CODEX_DIR/skills/goal-plus/agents/openai.yaml"
test -s "$CODEX_DIR/skills/search/SKILL.md"
test -s "$CODEX_DIR/agents/search_candidate_agent.toml"
grep -F 'args = ["--root", "{GOAL_PLUS_STATE_DIR}"]' "$CODEX_DIR/config.toml"''',
    ]
    run_cmd = (
        'export GOAL_PLUS_OUTER_DEADLINE_AT="$SFORGE_AGENT_DEADLINE"; '
        'REMAINING=$((SFORGE_AGENT_DEADLINE - $(date +%s))); '
        'HARD_REMAINING=$((SFORGE_AGENT_HARD_DEADLINE - $(date +%s))); '
        f'exec codex exec --disable plugins {CODEX_GOAL_PLUS_MCP_FLAGS} '
        '--json --dangerously-bypass-approvals-and-sandbox '
        '"\\$goal-plus mode=autonomous $(cat {prompt_file})\n\n'
        'Use the Goal Plus framework to perform deep search optimization for this task. '
        'For the initial frozen SearchSpec, set strategy.worker_host to codex and '
        'set budget.max_parallel to __GOAL_PLUS_PARALLEL_NUM__ and omit the '
        'deprecated budget.max_candidates field. max_parallel is the single '
        'EdgeBench K value. Set '
        'strategy.worker_budget to {{\"max_runtime_seconds\": '
        '__GOAL_PLUS_WORKER_RUNTIME_SECONDS__, \"on_exceed\": \"interrupt\"}}; '
        'do not prescribe a turn limit. This is the '
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
        'and /opt/sforge-agent-hard-deadline. Refresh the exploration time before each '
        'rolling-pool decision and reserve time for final verification, selection, '
        'promotion, and Judge feedback. No round count is prescribed.\n\n'
        'EdgeBench integration requirement: Goal Plus candidate workspaces are '
        'isolated from the main task workspace. After every search_promote call, '
        'the outer/main Codex session must run sforge-goal-plus-submit --details. '
        'That command atomically copies the selected candidate submitted files '
        'into the main workspace, verifies their hashes, then synchronously calls '
        'the Judge and returns its raw score, official 0-100 score, validity, and '
        'test details. Never call it from a candidate worker. Do not record the '
        'search result or mark the goal complete when this command fails. Use the '
        'returned Judge result when deciding whether another search task is needed."'
    )
    resume_cmd = (
        'export GOAL_PLUS_OUTER_DEADLINE_AT="$SFORGE_AGENT_DEADLINE"; '
        'REMAINING=$((SFORGE_AGENT_DEADLINE - $(date +%s))); '
        'HARD_REMAINING=$((SFORGE_AGENT_HARD_DEADLINE - $(date +%s))); '
        'SYNC_OUTPUT=$(sforge-goal-plus-submit --details --if-new 2>&1); '
        'SYNC_STATUS=$?; '
        'printf "%s\\n%s\\n" "$SYNC_STATUS" "$SYNC_OUTPUT" '
        f'>> {GOAL_PLUS_STATE_DIR}/edgebench-resume-sync.log; '
        f'exec codex exec --disable plugins {CODEX_GOAL_PLUS_MCP_FLAGS} '
        '--json resume --last --dangerously-bypass-approvals-and-sandbox '
        '"\\$goal-plus resume"'
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
        return cmd.replace(
            "__GOAL_PLUS_PARALLEL_NUM__", str(parallel_num)
        ).replace(
            "__GOAL_PLUS_WORKER_RUNTIME_SECONDS__", str(worker_runtime)
        )

    def prepare_container(
        self,
        backend: ContainerBackend,
        handle: ContainerHandle,
        logger: logging.Logger,
    ) -> None:
        super().prepare_container(backend, handle, logger)
        prepare_goal_plus_container(backend, handle, logger)

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

    def install_stop_hook(
        self,
        backend: ContainerBackend,
        handle: ContainerHandle,
        log_dir: Path,
        logger: logging.Logger,
    ) -> None:
        """Enable the project-local Goal Plus hooks, not Codex's generic blocker."""

        _enable_codex_hooks(backend, handle, log_dir, logger)
        logger.info(
            "Goal Plus stop gate is provided by Codex project hooks in the task workspace"
        )

    def augment_env(self, env: dict[str, str], model: str | None) -> None:
        super().augment_env(env, model)
        env["GOAL_PLUS_SOURCE_PATH"] = GOAL_PLUS_CONTAINER_DIR
        env["GOAL_PLUS_ROOT"] = GOAL_PLUS_STATE_DIR
        env["GOAL_PLUS_SEARCH_ROOT"] = GOAL_PLUS_STATE_DIR
        env["GOAL_PLUS_ROLE"] = "main"
        env["GOAL_PLUS_CODEX_ROLE"] = "main"
        if model:
            env["GOAL_PLUS_CODEX_MODEL"] = model

    def collect_artifacts(
        self,
        backend: ContainerBackend,
        handle: ContainerHandle,
        log_dir: Path,
        logger: logging.Logger,
    ) -> None:
        super().collect_artifacts(backend, handle, log_dir, logger)
        collect_goal_plus_artifacts(backend, handle, log_dir, logger)
