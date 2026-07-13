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
        'Use the Goal Plus framework to perform deep search optimization for this task."'
    )
    resume_cmd = (
        'pi -p --mode json -c '
        '-e /opt/goal-plus/.pi/extensions/goal-plus.ts '
        '--provider openai-codex --model "$PI_MODEL" '
        '"Continue working. Use the Goal Plus framework to continue deep search '
        'optimization for this task."'
    )

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
