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

import json
import logging
import os
import re
import shlex
from pathlib import Path, PurePosixPath

from sforge.harness.backend import ContainerBackend, ContainerHandle


GOAL_PLUS_REPOSITORY = "https://github.com/ck0123/goal-plus.git"
GOAL_PLUS_REF = os.environ.get(
    "SFORGE_GOAL_PLUS_REF", "experiment/async-research-flow"
)
GOAL_PLUS_CONTAINER_DIR = "/opt/goal-plus"
GOAL_PLUS_STATE_DIR = "/home/agent/.goal-plus"
GOAL_PLUS_EXTERNAL_EVIDENCE_ENV = "GOAL_PLUS_EXTERNAL_EVIDENCE_DIR"
GOAL_PLUS_EXTERNAL_EVIDENCE_DIR = f"{GOAL_PLUS_STATE_DIR}/edgebench/evaluations"
PYTHON_CONTAINER_DIR = "/opt/sforge-python"
GOAL_PLUS_PARALLEL_NUM_ENV = "SFORGE_GOAL_PLUS_PARALLEL_NUM"
GOAL_PLUS_WORKER_RUNTIME_ENV = "SFORGE_GOAL_PLUS_WORKER_RUNTIME_SECONDS"
GOAL_PLUS_WORKER_MIN_RUNTIME_ENV = "SFORGE_GOAL_PLUS_WORKER_MIN_RUNTIME_SECONDS"
GOAL_PLUS_MIN_VERIFIER_RUNS_ENV = "SFORGE_GOAL_PLUS_MIN_VERIFIER_RUNS"
GOAL_PLUS_CLOSEOUT_RESERVE_ENV = "SFORGE_GOAL_PLUS_CLOSEOUT_RESERVE_SECONDS"
GOAL_PLUS_FINALIZATION_GRACE_ENV = "SFORGE_GOAL_PLUS_FINALIZATION_GRACE_SECONDS"
DEFAULT_GOAL_PLUS_PARALLEL_NUM = 3
DEFAULT_GOAL_PLUS_WORKER_RUNTIME_SECONDS = 1200
DEFAULT_GOAL_PLUS_WORKER_MIN_RUNTIME_SECONDS = 0
DEFAULT_GOAL_PLUS_MIN_VERIFIER_RUNS = 0
DEFAULT_GOAL_PLUS_CLOSEOUT_RESERVE_SECONDS = 0
DEFAULT_GOAL_PLUS_FINALIZATION_GRACE_SECONDS = 300
GOAL_PLUS_LIVE_STATUS_FILENAME = "goal-plus-live-status.json"
GOAL_PLUS_STATUS_PROBE_CONTAINER_PATH = "/opt/sforge-goal-plus-status.py"
GOAL_PLUS_RESUME_EXPECTATION_PATH = f"{GOAL_PLUS_STATE_DIR}/resume-expectation.json"
# Preserve the admission snapshot; the actual host command rechecks it atomically.
GOAL_PLUS_RESUME_ENV = (
    'export GOAL_PLUS_RESUME_EXPECTATION="$(python -c \'from pathlib import Path; '
    f'print(Path("{GOAL_PLUS_RESUME_EXPECTATION_PATH}").read_text())\')"; '
    'test -n "$GOAL_PLUS_RESUME_EXPECTATION" || exit 1; '
    'export SFORGE_GOAL_PLUS_RESUME_SESSION_ID="$(python -c \'import json, os; '
    'print(json.loads(os.environ["GOAL_PLUS_RESUME_EXPECTATION"])["session_id"])\')"; '
)
GOAL_PLUS_MODEL_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]*\Z")


def goal_plus_model_token(value: str | None, name: str) -> str:
    """Validate one model reference before embedding it in a host command."""

    normalized = (value or "").strip()
    if not GOAL_PLUS_MODEL_TOKEN_PATTERN.fullmatch(normalized):
        raise ValueError(f"{name} must be one safe Goal Plus model token")
    return normalized


def positive_int_extra_env(
    values: dict[str, str],
    name: str,
    default: int,
) -> int:
    raw = values.get(name)
    if raw is None:
        return default
    try:
        parsed = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a positive integer, got {raw!r}") from exc
    if parsed < 1:
        raise ValueError(f"{name} must be a positive integer, got {raw!r}")
    return parsed


def nonnegative_int_extra_env(
    values: dict[str, str],
    name: str,
    default: int,
) -> int:
    raw = values.get(name)
    if raw is None:
        return default
    try:
        parsed = int(raw)
    except ValueError as exc:
        raise ValueError(
            f"{name} must be a non-negative integer, got {raw!r}"
        ) from exc
    if parsed < 0:
        raise ValueError(
            f"{name} must be a non-negative integer, got {raw!r}"
        )
    return parsed


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

    probe_source = Path(__file__).with_name("goal_plus_status_probe.py")
    backend.copy_to_container(
        handle,
        probe_source,
        PurePosixPath(GOAL_PLUS_STATUS_PROBE_CONTAINER_PATH),
    )

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


def goal_plus_status_snapshot(
    backend: ContainerBackend,
    handle: ContainerHandle,
) -> dict[str, object]:
    """Read a compact, host-neutral snapshot from durable Goal Plus state."""

    result = backend.exec_run(
        handle,
        ["python", GOAL_PLUS_STATUS_PROBE_CONTAINER_PATH],
    )
    if result.exit_code != 0:
        raise RuntimeError(result.output.strip() or "Goal Plus status probe failed")
    try:
        payload = json.loads(result.output.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError) as exc:
        raise RuntimeError("Goal Plus status probe returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("Goal Plus status probe returned a non-object")
    return payload


def collect_goal_plus_live_status(
    backend: ContainerBackend,
    handle: ContainerHandle,
    log_dir: Path,
    logger: logging.Logger,
) -> None:
    """Atomically publish compact Goal Plus state while the agent is running."""

    payload = goal_plus_status_snapshot(backend, handle)
    destination = log_dir / GOAL_PLUS_LIVE_STATUS_FILENAME
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(destination)


def goal_plus_should_resume_after_exit(
    backend: ContainerBackend,
    handle: ContainerHandle,
    logger: logging.Logger,
) -> bool:
    """Retry only a trusted host-recorded execution loss in this owned container.

    An unfinished task or an unknown exit is not automatic resume authority.
    User pause/interrupt, needs_user, terminal and identity mismatch fail closed.
    """

    try:
        payload = goal_plus_status_snapshot(backend, handle)
    except Exception as exc:
        logger.warning("Goal Plus terminal-state resume probe failed: %s", exc)
        return False
    if payload.get("terminal_ready") is True:
        logger.info(
            "Goal Plus records are terminal and final reports exist; "
            "native auto-resume is not needed"
        )
        return False
    expectation = payload.get("resume_expectation")
    if payload.get("native_continuation_ready") is True and isinstance(expectation, dict):
        result = backend.exec_run(handle, [
            "python", "-c",
            "import pathlib,sys; p=pathlib.Path(sys.argv[1]); "
            "t=p.with_suffix('.tmp'); t.write_text(sys.argv[2]); t.replace(p)",
            GOAL_PLUS_RESUME_EXPECTATION_PATH, json.dumps(expectation, sort_keys=True),
        ])
        return result.exit_code == 0
    logger.warning(
        "Goal Plus native auto-resume is not safe: %s",
        payload.get("native_continuation_blockers") or "no unfinished attached session",
    )
    return False


def goal_plus_prepare_timeout_resume(backend: ContainerBackend, handle: ContainerHandle, logger: logging.Logger) -> bool:
    """Record a known owned stop before terminating Main, never after user abort."""
    program = (
        "import json; from pathlib import Path; "
        "from goal_plus.goal_plus import FileGoalPlusRuntime; "
        "from goal_plus.host_recovery import interrupt_main_for_retry; "
        f"root=Path({GOAL_PLUS_STATE_DIR!r}); runtime=FileGoalPlusRuntime(root); "
        "records=[runtime.status(p.parent.name) for p in (root/'goal-plus').glob('gp_*/goal.json')]; "
        "records=[r for r in records if not runtime.is_terminal(r)]; "
        "assert len(records)==1, 'ambiguous Goal'; r=records[0]; "
        "expected=dict(goal_plus_id=r.goal_plus_id,goal_revision=r.goal_revision,"
        "session_id=r.active_session.session_id,control_version=r.control.version); "
        "ticket=interrupt_main_for_retry(root,r.goal_plus_id,expected); "
        f"p=Path({GOAL_PLUS_RESUME_EXPECTATION_PATH!r}); t=p.with_suffix('.tmp'); "
        "t.write_text(json.dumps(ticket)); t.replace(p)"
    )
    result = backend.exec_run(handle, ["python", "-c", program])
    if result.exit_code != 0:
        logger.warning("Goal Plus owned timeout admission rejected; no automatic resume")
    return result.exit_code == 0


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
