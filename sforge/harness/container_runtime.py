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

"""Deterministic host-cached runtimes for Work and Judge containers."""

from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path, PurePosixPath
from typing import Any

from sforge.harness.backend import ContainerBackend, ContainerHandle
from sforge.harness.task_spec import TaskSpec


RUNTIME_ASSETS_PATH = Path(__file__).with_name("runtime_assets.json")
RUST_RUNTIME_ARCHIVE_ENV = "SFORGE_RUST_RUNTIME_ARCHIVE"
RUST_RUNTIME_SHA256_ENV = "SFORGE_RUST_RUNTIME_SHA256"


def runtime_assets() -> dict[str, Any]:
    """Load the pinned runtime asset manifest shipped with SForge."""

    payload = json.loads(RUNTIME_ASSETS_PATH.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1 or not isinstance(payload.get("rust"), dict):
        raise RuntimeError(f"Invalid runtime asset manifest: {RUNTIME_ASSETS_PATH}")
    return payload


def rust_runtime_asset() -> dict[str, str]:
    asset = runtime_assets()["rust"]
    required = {"version", "target", "archive_name", "url", "sha256"}
    missing = sorted(required - set(asset))
    if missing:
        raise RuntimeError(
            f"Rust runtime asset is missing fields: {', '.join(missing)}"
        )
    return {key: str(asset[key]) for key in required}


def rust_runtime_cache_path() -> Path:
    asset = rust_runtime_asset()
    return Path.home() / ".cache" / "sforge" / "rust" / asset["archive_name"]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _resolve_rust_runtime_archive() -> tuple[Path | None, str | None]:
    asset = rust_runtime_asset()
    override = os.environ.get(RUST_RUNTIME_ARCHIVE_ENV)
    checksum_override = os.environ.get(RUST_RUNTIME_SHA256_ENV)
    if override is not None:
        if not override.strip():
            return None, None
        archive = Path(override).expanduser().resolve()
        expected_sha256 = checksum_override.strip() if checksum_override else None
    else:
        archive = rust_runtime_cache_path().resolve()
        expected_sha256 = asset["sha256"]

    if not archive.is_file():
        raise RuntimeError(
            f"Rust runtime archive not found: {archive}. Run the EdgeBench "
            "bootstrap first or set SFORGE_RUST_RUNTIME_ARCHIVE."
        )
    if expected_sha256:
        actual_sha256 = sha256_file(archive)
        if actual_sha256 != expected_sha256:
            raise RuntimeError(
                f"Rust runtime archive checksum mismatch: {archive}; "
                f"expected {expected_sha256}, got {actual_sha256}"
            )
    return archive, expected_sha256


def _rust_probe_command(version: str) -> list[str]:
    return [
        "/bin/bash",
        "-c",
        "set -e; "
        "command -v cargo >/dev/null; command -v rustc >/dev/null; "
        f"cargo --version | grep -F 'cargo {version} '; "
        f"rustc --version | grep -F 'rustc {version} '",
    ]


def ensure_task_runtime(
    backend: ContainerBackend,
    handle: ContainerHandle,
    task_spec: TaskSpec,
    logger: logging.Logger,
) -> None:
    """Ensure a task's language runtime exists without container downloads."""

    if task_spec.base_image != "rust":
        return

    asset = rust_runtime_asset()
    probe = backend.exec_run(handle, _rust_probe_command(asset["version"]))
    if probe.exit_code == 0:
        logger.info(
            "Rust %s runtime already available in task image", asset["version"]
        )
        return

    archive, _ = _resolve_rust_runtime_archive()
    if archive is None:
        raise RuntimeError(
            "Rust runtime is unavailable in the task container and host runtime "
            "injection was disabled by an empty SFORGE_RUST_RUNTIME_ARCHIVE"
        )

    container_archive = PurePosixPath("/tmp/sforge-rust-runtime.tar.xz")
    backend.copy_to_container(handle, archive, container_archive)
    install_command = f"""
set -euo pipefail
DIST_ROOT=/opt/sforge-rust-dist
RUNTIME_ROOT=/opt/sforge-rust
rm -rf "$DIST_ROOT" "$RUNTIME_ROOT"
mkdir -p "$DIST_ROOT"
tar -xJf {container_archive} -C "$DIST_ROOT" --strip-components=1
"$DIST_ROOT/install.sh" \
    --prefix="$RUNTIME_ROOT" \
    --without=rust-docs \
    --disable-ldconfig
for tool in cargo rustc rustdoc rustfmt cargo-fmt clippy-driver cargo-clippy; do
    if [ -x "$RUNTIME_ROOT/bin/$tool" ]; then
        ln -sfn "$RUNTIME_ROOT/bin/$tool" "/usr/local/bin/$tool"
    fi
done
rm -rf "$DIST_ROOT" {container_archive}
"""
    result = backend.exec_run(
        handle,
        ["/bin/bash", "-c", install_command],
        user="root",
    )
    if result.exit_code != 0:
        raise RuntimeError(
            f"Failed to install cached Rust runtime: {result.output}"
        )

    verify = backend.exec_run(handle, _rust_probe_command(asset["version"]))
    if verify.exit_code != 0:
        raise RuntimeError(
            "Cached Rust runtime installed but version verification failed: "
            f"{verify.output}"
        )
    logger.info(
        "Installed cached Rust %s runtime into the task container", asset["version"]
    )
