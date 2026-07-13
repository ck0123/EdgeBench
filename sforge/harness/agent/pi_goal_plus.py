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
import os
from pathlib import Path, PurePosixPath

from sforge.harness.agent.pi import PiAgent
from sforge.harness.backend import ContainerBackend, ContainerHandle


GOAL_PLUS_COMMIT = "3f97cf3ea44096ead375e4cb7238c6ef007fb4ab"
GOAL_PLUS_CONTAINER_DIR = "/opt/goal-plus"
GOAL_PLUS_STATE_DIR = "/home/agent/.goal-plus"
PYTHON_CONTAINER_DIR = "/opt/sforge-python"


class PiGoalPlusAgent(PiAgent):
    """Run the normal EdgeBench prompt through Pi's ``/goal-plus`` entrypoint."""

    name = "pi-goal-plus"
    install_cmds = [
        *PiAgent.install_cmds,
        f'''if [ ! -f {GOAL_PLUS_CONTAINER_DIR}/pyproject.toml ]; then
    TMP=$(mktemp -d)
    curl -fsSL "https://github.com/ck0123/goal-plus/archive/{GOAL_PLUS_COMMIT}.tar.gz" \
      | tar -xz -C "$TMP"
    sudo mkdir -p {GOAL_PLUS_CONTAINER_DIR}
    sudo cp -a "$TMP"/goal-plus-{GOAL_PLUS_COMMIT}/. {GOAL_PLUS_CONTAINER_DIR}/
    rm -rf "$TMP"
fi''',
        r'''PYTHON=""
for CANDIDATE in \
    /opt/sforge-python/bin/python3.11 \
    /opt/sforge-python/bin/python3.10 \
    /opt/sforge-python/bin/python3; do
    if [ -x "$CANDIDATE" ] && "$CANDIDATE" -c 'import sys; raise SystemExit(sys.version_info < (3, 10))'; then
        PYTHON="$CANDIDATE"
        break
    fi
done
if [ -n "$PYTHON" ]; then
    :
elif command -v python3 >/dev/null 2>&1 && python3 -c 'import sys; raise SystemExit(sys.version_info < (3, 10))'; then
    PYTHON=$(command -v python3)
elif command -v python >/dev/null 2>&1 && python -c 'import sys; raise SystemExit(sys.version_info < (3, 10))'; then
    PYTHON=$(command -v python)
elif command -v apt-get >/dev/null 2>&1; then
    if [ -f /etc/apt/sources.list ]; then
        sudo sed -i \
          -e 's|http://archive.ubuntu.com/ubuntu|https://mirrors.tuna.tsinghua.edu.cn/ubuntu|g' \
          -e 's|http://security.ubuntu.com/ubuntu|https://mirrors.tuna.tsinghua.edu.cn/ubuntu|g' \
          /etc/apt/sources.list
    fi
    sudo -E apt-get update
    sudo -E env DEBIAN_FRONTEND=noninteractive apt-get install -y python3 python3-pip
    PYTHON=$(command -v python3)
    "$PYTHON" -c 'import sys; raise SystemExit(sys.version_info < (3, 10))'
else
    curl -fsSL https://astral.sh/uv/0.8.22/install.sh -o /tmp/install-uv.sh
    sudo env UV_INSTALL_DIR=/usr/local/bin sh /tmp/install-uv.sh
    sudo env UV_PYTHON_INSTALL_DIR=/opt/uv-python uv python install 3.11
    PYTHON=$(UV_PYTHON_INSTALL_DIR=/opt/uv-python uv python find 3.11)
fi
sudo "$PYTHON" -m ensurepip --upgrade >/dev/null 2>&1 || true
sudo "$PYTHON" -m pip install --disable-pip-version-check \
  --index-url "${SFORGE_GOAL_PLUS_PYPI_INDEX_URL:-https://pypi.tuna.tsinghua.edu.cn/simple}" \
  /opt/goal-plus
sudo ln -sf "$PYTHON" /usr/local/bin/python
python -c 'import goal_plus' ''',
        r'''mkdir -p ~/.pi/agent/prompts ~/.pi/agent/skills
cp /opt/goal-plus/.pi/prompts/goal-plus.md ~/.pi/agent/prompts/goal-plus.md
rm -rf ~/.pi/agent/skills/goal-plus
cp -a /opt/goal-plus/.pi/skills/goal-plus ~/.pi/agent/skills/goal-plus
test -f /opt/goal-plus/.pi/extensions/goal-plus.ts
mkdir -p /home/agent/.goal-plus''',
    ]
    run_cmd = (
        'pi -p --mode json '
        '-e /opt/goal-plus/.pi/extensions/goal-plus.ts '
        '--provider openai-codex --model "$PI_MODEL" '
        '"/goal-plus $(cat {prompt_file})\n\n'
        'SForge benchmark integration constraints:\n'
        '- Paths listed under Submitted Files are mutable candidate artifacts. '
        'They must be in the edit surface and must never be included in '
        'verifier_artifact_paths or frozen verifier artifacts.\n'
        '- Freeze only immutable testing assets such as generators, testers, '
        'test scripts, and verifier configuration.\n'
        '- Keep the SForge hidden judge external to candidate ranking; use '
        'task-local tools for Goal Plus process verification.\n'
        '- A process verifier must emit the structured metric expected by '
        'Goal Plus and reject invalid candidate output.\n'
        '- The entire deadline-bounded SForge benchmark is one Goal Plus task, '
        'not a smoke test or a short promotion cycle. Read the Unix deadline from '
        '/opt/sforge-agent-deadline and use the available time for deep optimization '
        'inside the same Goal Plus record.\n'
        '- One planning batch or search round is insufficient while substantial '
        'time remains. Size the frozen search budget from the remaining deadline; '
        'when more than 30 minutes remain, max_candidates must exceed max_parallel '
        'and allow at least three sequential planning rounds. Exhaust that '
        'multi-round budget before selecting and promoting a winner.\n'
        '- Call search_plan_next repeatedly. Later-round proposals must use '
        'verifier history and derive from or deliberately challenge the best '
        'passing candidates from earlier rounds, rather than launching another '
        'independent smoke batch. If a search run is exhausted with ample time '
        'remaining, select and promote its winner, then create another search run '
        'under the same goal_plus_id and continue deep optimization.\n'
        '- Before proposing candidates, run sforge-submit --list and use the '
        'visible score history as outer-loop evidence. Never expose hidden Judge '
        'feedback to candidate workers or make it an internal verifier.\n'
        '- Do not call sforge-submit without --list. SForge owns formal Judge '
        'submissions and background evaluation.\n'
        '- Do not mark the Goal Plus task complete merely because one batch, one '
        'round, or one search run finished. Reserve the final portion of the '
        'deadline for selection, promotion, and final audit; only then complete '
        'the single Goal Plus task."'
    )
    # One Goal Plus record owns the complete SForge task and may contain multiple
    # planning rounds and search runs. If Pi exits before the benchmark deadline,
    # continue the same conversation and durable Goal Plus record instead of
    # creating a sequence of shallow records.
    resume_cmd = r'''set -u
DEADLINE=$(cat /opt/sforge-agent-deadline 2>/dev/null || printf '%s' "${SFORGE_AGENT_DEADLINE:-}")
case "$DEADLINE" in
    ''|*[!0-9]*)
        echo "[pi-goal-plus] invalid or missing SForge deadline: $DEADLINE" >&2
        exit 2
        ;;
esac
NOW=$(date +%s)
REMAINING=$((DEADLINE - NOW))
echo "[pi-goal-plus] deadline=$DEADLINE remaining=${REMAINING}s" >&2
if [ "$REMAINING" -le 120 ]; then
    [ "$REMAINING" -gt 0 ] && sleep "$((REMAINING + 5))"
    exit 0
fi
echo "[pi-goal-plus] continuing the existing Pi session and Goal Plus task" >&2
exec pi -p --mode json -c \
  -e /opt/goal-plus/.pi/extensions/goal-plus.ts \
  --provider openai-codex --model "$PI_MODEL" \
  "Continue the existing Goal Plus benchmark task from its durable goal and search state. Do not invoke /goal-plus and do not create a new Goal Plus record.

The previous Pi process ended with substantial benchmark time remaining. If the existing Goal Plus record was marked complete before deep multi-round optimization and the final deadline window, reactivate that same record with a clear reason and evidence. Continue from the current promoted files and existing verifier history. If the prior search run is terminal, create and link another search run under the same goal_plus_id.

The benchmark hard deadline is Unix timestamp $(cat /opt/sforge-agent-deadline). Use the remaining time for deep optimization with multiple sequential planning rounds. Do not complete after a single candidate batch or search round. Later rounds must build on verifier-backed history from earlier rounds. Reserve the final deadline window for selecting, promoting, and auditing the best candidate. Candidate workers must never call sforge-submit or receive hidden Judge feedback."'''

    def prepare_container(
        self,
        backend: ContainerBackend,
        handle: ContainerHandle,
        logger: logging.Logger,
    ) -> None:
        super().prepare_container(backend, handle, logger)

        source = os.environ.get("SFORGE_GOAL_PLUS_SOURCE_DIR")
        if not source:
            logger.info("Goal Plus source not configured; install will download pinned commit")
        else:
            source_path = Path(source).expanduser().resolve()
            if not (source_path / "pyproject.toml").is_file():
                raise RuntimeError(
                    "SFORGE_GOAL_PLUS_SOURCE_DIR is not a Goal Plus checkout: "
                    f"{source_path}"
                )
            backend.copy_to_container(
                handle, source_path, PurePosixPath(GOAL_PLUS_CONTAINER_DIR)
            )
            logger.info("Copied Goal Plus source from %s", source_path)

        python_source = os.environ.get("SFORGE_GOAL_PLUS_PYTHON_DIR")
        if not python_source:
            return
        python_path = Path(python_source).expanduser().resolve()
        python_candidates = tuple(
            python_path / "bin" / name
            for name in ("python3.11", "python3.10", "python3")
        )
        if not any(candidate.is_file() for candidate in python_candidates):
            raise RuntimeError(
                "SFORGE_GOAL_PLUS_PYTHON_DIR does not contain a supported "
                "Python 3.10+ executable under bin/: "
                f"{python_path}"
            )
        backend.copy_to_container(
            handle, python_path, PurePosixPath(PYTHON_CONTAINER_DIR)
        )
        logger.info("Copied portable Python from %s", python_path)

    def augment_env(self, env: dict[str, str], model: str | None) -> None:
        super().augment_env(env, model)
        env["GOAL_PLUS_SOURCE_PATH"] = GOAL_PLUS_CONTAINER_DIR
        env["GOAL_PLUS_ROOT"] = GOAL_PLUS_STATE_DIR
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
        try:
            archive = backend.copy_from_container(
                handle, PurePosixPath(GOAL_PLUS_STATE_DIR)
            )
            if archive:
                (log_dir / "goal-plus-state.tar").write_bytes(archive)
                logger.info("Collected Goal Plus state: %d bytes", len(archive))
        except Exception as exc:
            logger.warning("Failed to collect Goal Plus state: %s", exc)
