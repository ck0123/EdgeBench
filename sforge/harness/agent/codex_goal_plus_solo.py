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

from sforge.harness.agent.codex_goal_plus import CodexGoalPlusAgent


class CodexGoalPlusSoloAgent(CodexGoalPlusAgent):
    """Give one candidate worker a two-hour autonomous research lease."""

    name = "codex-goal-plus-solo"
    run_cmd = (
        'export GOAL_PLUS_OUTER_DEADLINE_AT="$SFORGE_AGENT_DEADLINE"; '
        'REMAINING=$((SFORGE_AGENT_DEADLINE - $(date +%s))); '
        'exec codex exec --dangerously-bypass-approvals-and-sandbox '
        '"\\$goal-plus mode=autonomous $(cat {prompt_file})\n\n'
        'Run this as a controlled single-worker AutoResearch experiment. Freeze '
        'exactly one SearchSpec with strategy.worker_host=codex, '
        'budget.max_parallel=1, budget.max_candidates=1, and '
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
    resume_cmd = (
        'export GOAL_PLUS_OUTER_DEADLINE_AT="$SFORGE_AGENT_DEADLINE"; '
        'REMAINING=$((SFORGE_AGENT_DEADLINE - $(date +%s))); '
        'SYNC_OUTPUT=$(sforge-goal-plus-submit --details --if-new 2>&1); '
        'SYNC_STATUS=$?; '
        'exec codex exec resume --last --dangerously-bypass-approvals-and-sandbox '
        '"Resume the controlled single-worker Goal Plus experiment. The promotion '
        'bridge preflight returned exit status ${SYNC_STATUS}:\n${SYNC_OUTPUT}\n\n'
        'Keep the frozen contract at worker_host=codex, max_parallel=1, '
        'max_candidates=1, and a single 7200-second worker lease without a turn '
        'limit. Restore and wait for the already-created worker/session. Do not '
        'spawn, steer, message, redispatch, or continue a worker, and do not make '
        'optimization judgments or edit solution.py in the main workspace. If no '
        'worker was ever created because setup did not complete, create exactly '
        'one with the original directive and then wait. After the sole worker has '
        'finished, mechanically select its best valid verifier iteration, promote '
        'it, and run sforge-goal-plus-submit --details from this outer session. '
        'The outer hard deadline is ${SFORGE_AGENT_DEADLINE}; ${REMAINING} seconds '
        'remain."'
    )
