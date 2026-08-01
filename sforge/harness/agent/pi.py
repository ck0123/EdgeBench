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

"""Pi coding agent integration."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path, PurePosixPath

from sforge.harness.agent.base import Agent
from sforge.harness.backend import ContainerBackend, ContainerHandle


DEFAULT_PI_PACKAGE_VERSION = "latest"


class PiJsonOutputLogFilter:
    """Remove duplicate cumulative snapshots from Pi JSON delta events.

    Pi emits the complete partial assistant message twice for every streamed
    delta: once as ``message`` and once as
    ``assistantMessageEvent.partial``. The delta itself is sufficient to
    preserve the stream, while the complete message remains available in the
    corresponding start/end events.
    """

    def __init__(self) -> None:
        self._buffer = bytearray()

    @staticmethod
    def _compact_line(line: bytes) -> bytes:
        ending = b""
        body = line
        if body.endswith(b"\n"):
            body = body[:-1]
            ending = b"\n"
            if body.endswith(b"\r"):
                body = body[:-1]
                ending = b"\r\n"
        try:
            event = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return line
        if not isinstance(event, dict) or event.get("type") != "message_update":
            return line
        assistant_event = event.get("assistantMessageEvent")
        if not isinstance(assistant_event, dict):
            return line
        event_type = assistant_event.get("type")
        if not isinstance(event_type, str) or not event_type.endswith("_delta"):
            return line
        message = event.get("message")
        partial = assistant_event.get("partial")
        if not isinstance(message, dict) or message != partial:
            return line

        compacted = dict(event)
        compacted.pop("message", None)
        compacted_assistant_event = dict(assistant_event)
        compacted_assistant_event.pop("partial", None)
        compacted["assistantMessageEvent"] = compacted_assistant_event
        compacted["sforge_compacted"] = True
        return (
            json.dumps(
                compacted,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
            + ending
        )

    def feed(self, chunk: bytes) -> bytes:
        if not chunk:
            return b""
        self._buffer.extend(chunk)
        lines = self._buffer.split(b"\n")
        self._buffer = bytearray(lines.pop())
        return b"".join(self._compact_line(line + b"\n") for line in lines)

    def finish(self) -> bytes:
        if not self._buffer:
            return b""
        line = bytes(self._buffer)
        self._buffer.clear()
        return self._compact_line(line)


class PiAgent(Agent):

    name = "pi"
    install_cmds = [
        "sudo -E bash -c 'NODE_MIRROR=${SFORGE_NODEJS_MIRROR_URL:-https://nodejs.org/dist} && curl -fsSL $NODE_MIRROR/v22.23.1/node-v22.23.1-linux-x64.tar.xz | tar -xJ -C /usr/local --strip-components=1'",
        'sudo -E npm install -g "@earendil-works/pi-coding-agent@${SFORGE_PI_PACKAGE_VERSION:-latest}" && pi --version',
        r'''mkdir -p ~/.pi/agent
cat > ~/.pi/agent/models.json << EOF
{
  "providers": {
    "sforge-proxy": {
      "baseUrl": "${OPENAI_BASE_URL}",
      "api": "openai-responses",
      "apiKey": "\$OPENAI_API_KEY",
      "authHeader": true,
      "compat": {
        "supportsDeveloperRole": false,
        "supportsReasoningEffort": false,
        "supportsStore": false
      },
      "models": [
        {
          "id": "${PI_MODEL:-gpt-5.6-sol}",
          "name": "${PI_MODEL:-gpt-5.6-sol}",
          "contextWindow": 1050000,
          "maxTokens": 128000
        }
      ]
    }
  }
}
EOF
chmod 600 ~/.pi/agent/models.json''',
    ]
    run_cmd = (
        'pi -p --mode json --provider openai-codex --model "$PI_MODEL" '
        '--thinking "$SFORGE_PI_REASONING_EFFORT" '
        '"$(cat {prompt_file})"'
    )
    resume_cmd = (
        'pi -p --mode json -c --provider openai-codex --model "$PI_MODEL" '
        '--thinking "$SFORGE_PI_REASONING_EFFORT" '
        '"Continue working."'
    )
    api_key_env = "OPENAI_API_KEY"
    api_base_env = "OPENAI_BASE_URL"
    # Pi's built-in openai-codex provider talks to the ChatGPT Codex backend.
    default_api_base_url = "https://chatgpt.com/backend-api"
    model_env = "PI_MODEL"

    def create_output_log_filter(self) -> PiJsonOutputLogFilter:
        return PiJsonOutputLogFilter()

    def augment_env(self, env: dict[str, str], model: str | None) -> None:
        env["PI_CODING_AGENT_DIR"] = "/home/agent/.pi/agent"
        env["PI_CODING_AGENT_SESSION_DIR"] = "/home/agent/.pi/agent/sessions"
        env["PI_SKIP_VERSION_CHECK"] = "1"
        env["PI_TELEMETRY"] = "0"
        env.setdefault(
            "SFORGE_PI_PACKAGE_VERSION",
            os.environ.get(
                "SFORGE_PI_PACKAGE_VERSION", DEFAULT_PI_PACKAGE_VERSION
            ),
        )
        env.setdefault("SFORGE_PI_REASONING_EFFORT", "medium")

    def prepare_container(
        self,
        backend: ContainerBackend,
        handle: ContainerHandle,
        logger: logging.Logger,
    ) -> None:
        auth_source = Path(
            os.environ.get(
                "SFORGE_PI_AUTH_FILE",
                Path.home() / ".pi" / "agent" / "auth.json",
            )
        ).expanduser().resolve()
        if not auth_source.is_file():
            raise RuntimeError(
                "Pi auth file not found; run `pi` and log in first, or set "
                f"SFORGE_PI_AUTH_FILE (looked for {auth_source})"
            )

        try:
            auth = json.loads(auth_source.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Invalid Pi auth file: {auth_source}") from exc
        if not isinstance(auth.get("openai-codex"), dict):
            raise RuntimeError(
                f"Pi auth file has no openai-codex login: {auth_source}"
            )

        backend.copy_to_container(
            handle,
            auth_source,
            PurePosixPath("/home/agent/.pi/agent/auth.json"),
        )
        backend.exec_run(
            handle,
            "chown -R agent:agent /home/agent/.pi && "
            "chmod 600 /home/agent/.pi/agent/auth.json",
            user="root",
        )
        logger.info("Copied host Pi openai-codex login into the work container")
