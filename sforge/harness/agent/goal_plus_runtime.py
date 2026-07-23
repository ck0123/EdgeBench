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

"""Shared Goal Plus runtime installation and state helpers."""

from __future__ import annotations

import logging
import os
import shlex
from pathlib import Path, PurePosixPath

from sforge.harness.backend import ContainerBackend, ContainerHandle


GOAL_PLUS_REPOSITORY = "https://github.com/ck0123/goal-plus.git"
GOAL_PLUS_REF = os.environ.get(
    "SFORGE_GOAL_PLUS_REF", "experiment/async-research-flow"
)
GOAL_PLUS_CONTAINER_DIR = "/opt/goal-plus"
GOAL_PLUS_STATE_DIR = "/home/agent/.goal-plus"
PYTHON_CONTAINER_DIR = "/opt/sforge-python"


def goal_plus_runtime_install_cmds() -> list[str]:
    """Return host-neutral commands that install Goal Plus in a work container."""

    return [
        f'''set -euo pipefail
if [ -f {GOAL_PLUS_CONTAINER_DIR}/pyproject.toml ]; then
    echo "Using controller-provided Goal Plus source"
else
    TMP=$(mktemp -d)
    trap 'rm -rf "$TMP"' EXIT
    GOAL_PLUS_REF={shlex.quote(GOAL_PLUS_REF)}
    git clone --depth 1 --branch "$GOAL_PLUS_REF" {GOAL_PLUS_REPOSITORY} "$TMP/goal-plus"
    sudo rm -rf {GOAL_PLUS_CONTAINER_DIR}
    sudo mkdir -p {GOAL_PLUS_CONTAINER_DIR}
    sudo cp -a "$TMP/goal-plus/." {GOAL_PLUS_CONTAINER_DIR}/
    echo "Goal Plus ref: $GOAL_PLUS_REF"
fi
echo "Goal Plus commit: $(git -C {GOAL_PLUS_CONTAINER_DIR} rev-parse HEAD)"''',
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
          -e 's|http://mirrors.byted.org/ubuntu|https://mirrors.tuna.tsinghua.edu.cn/ubuntu|g' \
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
if ! "$PYTHON" -m pip --version >/dev/null 2>&1; then
    sudo "$PYTHON" -m ensurepip --upgrade >/dev/null 2>&1 || true
fi
if ! "$PYTHON" -m pip --version >/dev/null 2>&1 && command -v apt-get >/dev/null 2>&1; then
    if [ -f /etc/apt/sources.list ]; then
        sudo sed -i \
          -e 's|http://mirrors.byted.org/ubuntu|https://mirrors.tuna.tsinghua.edu.cn/ubuntu|g' \
          -e 's|http://archive.ubuntu.com/ubuntu|https://mirrors.tuna.tsinghua.edu.cn/ubuntu|g' \
          -e 's|http://security.ubuntu.com/ubuntu|https://mirrors.tuna.tsinghua.edu.cn/ubuntu|g' \
          /etc/apt/sources.list
    fi
    sudo -E apt-get update
    sudo -E env DEBIAN_FRONTEND=noninteractive apt-get install -y python3-pip
    PYTHON=$(command -v python3)
fi
"$PYTHON" -m pip --version
sudo "$PYTHON" -m pip install --disable-pip-version-check \
  --index-url "${SFORGE_GOAL_PLUS_PYPI_INDEX_URL:-https://pypi.tuna.tsinghua.edu.cn/simple}" \
  /opt/goal-plus
sudo ln -sf "$PYTHON" /usr/local/bin/python
python -c 'import goal_plus' ''',
    ]


def prepare_goal_plus_container(
    backend: ContainerBackend,
    handle: ContainerHandle,
    logger: logging.Logger,
) -> None:
    """Copy pinned Goal Plus source and an optional portable Python runtime."""

    source = os.environ.get("SFORGE_GOAL_PLUS_SOURCE_DIR")
    if source:
        source_path = Path(source).expanduser().resolve()
        if not (source_path / "pyproject.toml").is_file():
            raise RuntimeError(
                "SFORGE_GOAL_PLUS_SOURCE_DIR is not a Goal Plus checkout: "
                f"{source_path}"
            )
        backend.copy_to_container(
            handle, source_path, PurePosixPath(GOAL_PLUS_CONTAINER_DIR)
        )
        logger.info("Copied pinned Goal Plus source from %s", source_path)

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


def collect_goal_plus_artifacts(
    backend: ContainerBackend,
    handle: ContainerHandle,
    log_dir: Path,
    logger: logging.Logger,
) -> None:
    """Archive durable Goal Plus state before the work container is removed."""

    try:
        archive = backend.copy_from_container(
            handle, PurePosixPath(GOAL_PLUS_STATE_DIR)
        )
        if archive:
            (log_dir / "goal-plus-state.tar").write_bytes(archive)
            logger.info("Collected Goal Plus state: %d bytes", len(archive))
    except Exception as exc:
        logger.warning("Failed to collect Goal Plus state: %s", exc)
