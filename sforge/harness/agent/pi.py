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

from sforge.harness.agent.base import Agent


class PiAgent(Agent):

    name = "pi"
    install_cmds = [
        "sudo -E bash -c 'NODE_MIRROR=${SFORGE_NODEJS_MIRROR_URL:-https://nodejs.org/dist} && curl -fsSL $NODE_MIRROR/v22.23.1/node-v22.23.1-linux-x64.tar.xz | tar -xJ -C /usr/local --strip-components=1'",
        "sudo -E npm install -g @earendil-works/pi-coding-agent@0.80.6",
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
        'pi -p --mode json --provider sforge-proxy --model "$PI_MODEL" '
        '"$(cat {prompt_file})"'
    )
    resume_cmd = (
        'pi -p --mode json -c --provider sforge-proxy --model "$PI_MODEL" '
        '"Continue working."'
    )
    api_key_env = "OPENAI_API_KEY"
    api_base_env = "OPENAI_BASE_URL"
    default_api_base_url = "https://api.openai.com/v1"
    model_env = "PI_MODEL"

    def augment_env(self, env: dict[str, str], model: str | None) -> None:
        env["PI_CODING_AGENT_DIR"] = "/home/agent/.pi/agent"
        env["PI_CODING_AGENT_SESSION_DIR"] = "/home/agent/.pi/agent/sessions"
        env["PI_SKIP_VERSION_CHECK"] = "1"
        env["PI_TELEMETRY"] = "0"
