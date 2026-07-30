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

"""Codex agent — internet blocking and stop hook."""

from __future__ import annotations

import json
import logging
import os
import shlex
from pathlib import Path, PurePosixPath

from sforge.harness.agent.base import Agent
from sforge.harness.backend import ContainerBackend, ContainerHandle


CODEX_CLI_VERSION = "0.144.1"
CODEX_REASONING_EFFORT_ENV = "SFORGE_CODEX_REASONING_EFFORT"
DEFAULT_CODEX_REASONING_EFFORT = "medium"
SUPPORTED_CODEX_REASONING_EFFORTS = frozenset(
    {"none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"}
)
CODEX_LINUX_RUNTIME_CACHE = (
    Path.home()
    / ".cache"
    / "sforge"
    / "codex"
    / f"codex-{CODEX_CLI_VERSION}-linux-x64.tgz"
)


class CodexAgent(Agent):

    name = "codex"
    install_cmds = [
        "command -v codex >/dev/null 2>&1 && codex --version || sudo -E bash -c 'NODE_MIRROR=${SFORGE_NODEJS_MIRROR_URL:-https://nodejs.org/dist} && curl -fsSL $NODE_MIRROR/v20.18.0/node-v20.18.0-linux-x64.tar.xz | tar -xJ -C /usr/local --strip-components=1'",
        f"command -v codex >/dev/null 2>&1 && codex --version || sudo -E npm install -g @openai/codex@{CODEX_CLI_VERSION}",
        '''if [ -n "$OPENAI_BASE_URL" ]; then
    mkdir -p ~/.codex
    cat > ~/.codex/config.toml << EOF
model_provider = "sforge-proxy"
model_verbosity = "medium"
model_reasoning_effort = "medium"
model = "${CODEX_MODEL:-gpt-5.5}"

[model_providers.sforge-proxy]
name = "sforge-proxy"
base_url = "${OPENAI_BASE_URL}"
env_key = "OPENAI_API_KEY"
EOF
fi''',
        '''if [ -n "${OPENAI_API_KEY:-${CODEX_API_KEY:-}}" ]; then
    echo "Codex API key authentication configured"
else
    test -s ~/.codex/auth.json && codex login status >/dev/null
fi''',
    ]
    run_cmd = 'codex exec --json --dangerously-bypass-approvals-and-sandbox "$(cat {prompt_file})"'
    resume_cmd = 'codex exec --json resume --last --dangerously-bypass-approvals-and-sandbox "Continue working."'
    api_key_env = "OPENAI_API_KEY"
    api_base_env = "OPENAI_BASE_URL"
    default_api_base_url = "https://api.openai.com"
    model_env = "CODEX_MODEL"
    stop_hook = "codex"

    def prepare_container(
        self,
        backend: ContainerBackend,
        handle: ContainerHandle,
        logger: logging.Logger,
    ) -> None:
        if self._config.agent_api_key:
            logger.info("Using Codex API key authentication; OAuth file not copied")
        else:
            auth_source = Path(
                os.environ.get(
                    "SFORGE_CODEX_AUTH_FILE",
                    Path.home() / ".codex" / "auth.json",
                )
            ).expanduser().resolve()
            if not auth_source.is_file():
                raise RuntimeError(
                    "Codex auth file not found; run `codex` and log in first, "
                    "set SFORGE_CODEX_AUTH_FILE, or configure "
                    "SFORGE_AGENT_API_KEY for API key authentication"
                )

            try:
                auth = json.loads(auth_source.read_text())
            except (OSError, json.JSONDecodeError) as exc:
                raise RuntimeError("Invalid Codex auth file") from exc
            if not isinstance(auth, dict) or not auth:
                raise RuntimeError("Codex auth file must contain a JSON object")

            backend.copy_to_container(
                handle,
                auth_source,
                PurePosixPath("/home/agent/.codex/auth.json"),
            )
            result = backend.exec_run(
                handle,
                [
                    "/bin/bash",
                    "-lc",
                    "chown -R agent:agent /home/agent/.codex && "
                    "chmod 600 /home/agent/.codex/auth.json",
                ],
                user="root",
            )
            if result.exit_code != 0:
                raise RuntimeError(
                    f"Failed to install Codex auth file: {result.output}"
                )
            logger.info("Copied host Codex OAuth login into the work container")

        runtime_override = os.environ.get("SFORGE_CODEX_RUNTIME_ARCHIVE")
        if runtime_override is not None:
            runtime_archive = (
                Path(runtime_override).expanduser().resolve()
                if runtime_override.strip()
                else None
            )
            if runtime_archive is not None and not runtime_archive.is_file():
                raise RuntimeError(
                    f"Codex Linux runtime archive not found: {runtime_archive}"
                )
        else:
            runtime_archive = (
                CODEX_LINUX_RUNTIME_CACHE.resolve()
                if CODEX_LINUX_RUNTIME_CACHE.is_file()
                else None
            )

        if runtime_archive is not None:
            container_archive = PurePosixPath("/tmp/sforge-codex-linux-x64.tgz")
            backend.copy_to_container(
                handle,
                runtime_archive,
                container_archive,
            )
            install_runtime_cmd = r"""
set -euo pipefail
RUNTIME_ROOT=/opt/sforge-codex
rm -rf "$RUNTIME_ROOT"
mkdir -p "$RUNTIME_ROOT"
tar -xzf /tmp/sforge-codex-linux-x64.tgz \
    -C "$RUNTIME_ROOT" --strip-components=1
TARGET=x86_64-unknown-linux-musl
CODEX_BIN="$RUNTIME_ROOT/vendor/$TARGET/bin/codex"
test -x "$CODEX_BIN"
ln -sfn "$CODEX_BIN" /usr/local/bin/codex
codex --version
"""
            result = backend.exec_run(
                handle,
                ["/bin/bash", "-lc", install_runtime_cmd],
                user="root",
            )
            if result.exit_code != 0:
                raise RuntimeError(
                    f"Failed to install cached Codex Linux runtime: {result.output}"
                )
            logger.info("Copied cached Codex Linux runtime into the work container")

    def augment_env(self, env: dict[str, str], model: str | None) -> None:
        api_key = env.get("OPENAI_API_KEY") or env.get("CODEX_API_KEY")
        if self._config.agent_api_base_url and not api_key:
            raise ValueError(
                "SFORGE_AGENT_API_BASE_URL requires SFORGE_AGENT_API_KEY "
                "for Codex API authentication"
            )
        if api_key:
            env["OPENAI_API_KEY"] = api_key
            env["CODEX_API_KEY"] = api_key

    def collect_artifacts(
        self,
        backend: ContainerBackend,
        handle: ContainerHandle,
        log_dir: Path,
        logger: logging.Logger,
    ) -> None:
        """Preserve Codex rollouts for usage accounting without copying auth."""

        artifacts = (
            (
                PurePosixPath("/home/agent/.codex/sessions"),
                "codex-sessions.tar",
                "Codex sessions",
            ),
            (
                PurePosixPath("/home/agent/.codex/hook-events"),
                "codex-hook-events.tar",
                "Codex hook events",
            ),
        )
        for container_path, archive_name, label in artifacts:
            try:
                archive = backend.copy_from_container(handle, container_path)
                if archive:
                    (log_dir / archive_name).write_bytes(archive)
                    logger.info("Collected %s: %d bytes", label, len(archive))
            except Exception as exc:
                logger.warning("Failed to collect %s: %s", label, exc)

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
            prompt_path, model=model, cwd=cwd, internet=internet, resume=resume,
        )

        if model:
            cmd = cmd.replace(
                "codex exec",
                f"codex exec --model {shlex.quote(model)}",
                1,
            )

        reasoning_effort = os.environ.get(
            CODEX_REASONING_EFFORT_ENV,
            DEFAULT_CODEX_REASONING_EFFORT,
        ).strip().lower()
        if reasoning_effort not in SUPPORTED_CODEX_REASONING_EFFORTS:
            supported = ", ".join(sorted(SUPPORTED_CODEX_REASONING_EFFORTS))
            raise ValueError(
                f"Invalid {CODEX_REASONING_EFFORT_ENV}={reasoning_effort!r}; "
                f"expected one of: {supported}"
            )
        reasoning_override = shlex.quote(
            f'model_reasoning_effort="{reasoning_effort}"'
        )
        cmd = cmd.replace(
            "codex exec",
            f"codex exec -c {reasoning_override}",
            1,
        )

        if not internet:
            cmd = cmd.replace(
                "codex exec", 'codex exec -c web_search="disabled"', 1,
            )

        cmd = cmd.replace(
            "codex exec",
            "codex exec --dangerously-bypass-hook-trust",
            1,
        )
        return cmd

    def install_stop_hook(
        self,
        backend: ContainerBackend,
        handle: ContainerHandle,
        log_dir: Path,
        logger: logging.Logger,
    ) -> None:
        hook_path = "/tmp/sforge-codex-stop-hook.sh"

        hook_script = _generate_codex_stop_hook()
        local_hook = log_dir / "_codex-stop-hook.sh"
        local_hook.write_text(hook_script)
        backend.copy_to_container(handle, local_hook, PurePosixPath(hook_path))
        backend.exec_run(handle, f"chmod a+x {hook_path}", user="root")
        logger.info("Installed Codex stop hook")

        hooks_content = _generate_codex_hooks(hook_path)
        local_hooks = log_dir / "_codex_hooks.json"
        local_hooks.write_text(hooks_content)
        backend.exec_run(handle, "mkdir -p /etc/codex", user="root")
        backend.copy_to_container(
            handle,
            local_hooks,
            PurePosixPath("/etc/codex/hooks.json"),
        )
        _enable_codex_hooks(backend, handle, log_dir, logger)


# ---------------------------------------------------------------------------
# Script generators (private to this module)
# ---------------------------------------------------------------------------


def _generate_codex_stop_hook() -> str:
    return r"""#!/bin/bash
cat >/dev/null
set -u
event_dir="${CODEX_HOME:-/home/agent/.codex}/hook-events"
mkdir -p "$event_dir"
started_ns="$(date -u +'%s%N')"
started_at="$(date -u +'%Y-%m-%dT%H:%M:%S.%NZ')"
invocation_id="stop-$started_ns-$$-${RANDOM:-0}"
event_path="$event_dir/$invocation_id.json"
temporary_path="$event_path.tmp"
finished_at="$(date -u +'%Y-%m-%dT%H:%M:%S.%NZ')"
finished_ns="$(date -u +'%s%N')"
duration_ms="$(( (finished_ns - started_ns) / 1000000 ))"
printf '%s\n' \
  "{\"schema_version\":1,\"hook_event_name\":\"Stop\",\"invocation_id\":\"$invocation_id\",\"started_at\":\"$started_at\",\"finished_at\":\"$finished_at\",\"duration_ms\":$duration_ms,\"decision\":\"block\",\"outcome\":\"blocked\",\"reason\":\"Do not stop. Continue working on the implementation.\"}" \
  >"$temporary_path"
mv "$temporary_path" "$event_path"
echo '{"decision":"block","reason":"Do not stop. Continue working on the implementation."}'
"""


def _generate_codex_hooks(hook_path: str) -> str:
    hooks: dict = {
        "hooks": {
            "Stop": [
                {
                    "hooks": [
                        {
                            "type": "command",
                            "command": hook_path,
                            "timeout": 30,
                            "statusMessage": "SForge stop hook",
                        }
                    ]
                }
            ],
        }
    }
    return json.dumps(hooks, indent=2)


def _generate_codex_config_append() -> str:
    return """\n[features]\nhooks = true\n"""


def _enable_codex_hooks(
    backend: ContainerBackend,
    handle: ContainerHandle,
    log_dir: Path,
    logger: logging.Logger,
) -> None:
    """Enable Codex hooks without choosing which hook file supplies them."""

    config_append = _generate_codex_config_append()
    local_config_append = log_dir / "_codex_config_append.toml"
    local_config_append.write_text(config_append)
    backend.copy_to_container(
        handle,
        local_config_append,
        PurePosixPath("/tmp/sforge-codex-config-append.toml"),
    )

    enable_hooks_cmd = r"""
set -e
CONFIG=/home/agent/.codex/config.toml
TMP=$(mktemp)
mkdir -p /home/agent/.codex
touch "$CONFIG"
if grep -Eq '^[[:space:]]*hooks[[:space:]]*=' "$CONFIG"; then
    sed -E 's/^[[:space:]]*hooks[[:space:]]*=.*/hooks = true/' "$CONFIG" > "$TMP"
elif grep -Eq '^[[:space:]]*\[features\][[:space:]]*$' "$CONFIG"; then
    awk '
        /^[[:space:]]*\[features\][[:space:]]*$/ {
            print
            print "hooks = true"
            next
        }
        { print }
    ' "$CONFIG" > "$TMP"
else
    cp "$CONFIG" "$TMP"
    cat /tmp/sforge-codex-config-append.toml >> "$TMP"
fi
mv "$TMP" "$CONFIG"
chown -R agent:agent /home/agent/.codex
"""
    result = backend.exec_run(
        handle, ["/bin/bash", "-lc", enable_hooks_cmd], user="root",
    )
    if result.exit_code != 0:
        raise RuntimeError(f"Failed to configure Codex hooks: {result.output}")
    logger.info("Configured Codex hooks")
