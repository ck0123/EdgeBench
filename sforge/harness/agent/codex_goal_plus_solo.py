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

"""Controlled Codex + Goal Plus experiment with one long-lived worker."""

from __future__ import annotations

from sforge.harness.agent.codex_goal_plus import (
    CODEX_GOAL_PLUS_MCP_FLAGS,
    CodexGoalPlusAgent,
)
from sforge.harness.agent.goal_plus_runtime import goal_plus_model_token


class CodexGoalPlusSoloAgent(CodexGoalPlusAgent):
    """Give one candidate worker a two-hour autonomous research lease."""

    name = "codex-goal-plus-solo"
    run_cmd = (
        'export GOAL_PLUS_OUTER_DEADLINE_AT="$SFORGE_AGENT_DEADLINE"; '
        'REMAINING=$((SFORGE_AGENT_DEADLINE - $(date +%s))); '
        f'exec codex exec --disable plugins {CODEX_GOAL_PLUS_MCP_FLAGS} '
        '--json --dangerously-bypass-approvals-and-sandbox '
        '"\\$goal-plus mode=autonomous max_parallel=1 '
        'workspace_backend=git_worktree promotion_mode=artifact_only '
        'strategy=agent_guided __GOAL_PLUS_SOLO_ROLE_COMMAND_CONFIG__'
        '$(cat {prompt_file})\n\n'
        'Run this as a controlled single-worker AutoResearch experiment. Freeze '
        'exactly one SearchSpec with strategy.worker_host=codex, '
        'honor the leading typed command config, omit deprecated '
        'budget.max_candidates, and '
        'strategy.worker_budget={{\"max_runtime_seconds\":7200,'
        '\"on_exceed\":\"interrupt\"}}. Do not set max_turns. Create and '
        'start exactly one candidate worker. Its candidate directive must tell it '
        'to own the research strategy, actively use the full lease until the '
        'watchdog closeout, keep iterating over real verifier-backed artifacts, '
        'and target fewer than 1600 cycles rather than stopping after the first '
        'valid improvement. It may build bounded analysis tools inside its '
        'candidate workspace.\n\n'
        'After dispatch, the main agent is an orchestration shell only: do not '
        'edit solution.py, inspect or judge intermediate candidate results, '
        'propose optimizations, steer or message the worker, spawn another worker, '
        'or redispatch/continue the candidate. Wait for that same worker, using '
        'multiple wait calls if a host wait call is shorter than the lease. If it '
        'returns early, do not replace it; preserve that outcome as experimental '
        'evidence. Once it finishes or is closed out, mechanically select its best '
        'valid verifier-recorded iteration, promote it, and run '
        'sforge-goal-plus-submit --details. The Judge result is the experiment '
        'result.\n\n'
        'The outer task budget is ${{SFORGE_AGENT_TOTAL_BUDGET_SECONDS}} seconds; '
        'the hard deadline is Unix timestamp ${{SFORGE_AGENT_DEADLINE}}, and '
        '${{REMAINING}} seconds remain. The extra outer time exists only for spec '
        'setup and final promotion/Judge work; the worker research lease is 7200 '
        'seconds. The deadline is also available in /opt/sforge-agent-deadline.\n\n'
        'EdgeBench candidate workspaces are isolated from the main workspace. '
        'Only the outer/main Codex session may run '
        'sforge-goal-plus-submit --details, and only after search_promote. Require '
        'a successful Judge response before recording completion."'
    )
    # Resume preserves this experiment's original objective and worker policy.
    # Reuse the same guarded session/Goal Plus resume contract as the base agent.
    resume_cmd = CodexGoalPlusAgent.resume_cmd

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
        worker_model = goal_plus_model_token(
            model or self._config.agent_model or self.default_model,
            "Goal Plus Codex solo worker model",
        )
        role_config = f"workers={worker_model}*1"
        annotator_model = self._config.agent_extra_env.get(
            "GOAL_PLUS_EVIDENCE_ANNOTATOR_MODEL"
        )
        if annotator_model:
            role_config += " annotator=" + goal_plus_model_token(
                annotator_model,
                "Goal Plus Codex solo annotator model",
            )
        return cmd.replace(
            "__GOAL_PLUS_SOLO_ROLE_COMMAND_CONFIG__",
            role_config + " ",
        )
