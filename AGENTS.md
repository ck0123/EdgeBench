# EdgeBench Agent Instructions

<!-- codebase-memory-mcp:start -->
## Codebase Knowledge Graph (codebase-memory-mcp)

This project uses codebase-memory-mcp to maintain a knowledge graph of the codebase.
Always prefer MCP graph tools over grep, glob, or file search for code discovery.

Priority order:

1. `search_graph` — find functions, classes, routes, and variables by pattern.
2. `trace_path` — trace who calls a function or what it calls.
3. `get_code_snippet` — read specific function or class source code.
4. `query_graph` — run Cypher queries for complex patterns.
5. `get_architecture` — get a high-level project summary.

Fall back to grep or glob when searching string literals, error messages,
configuration values, non-code files, or when graph results are insufficient.
<!-- codebase-memory-mcp:end -->

## Read this before starting any local agent run

Do not reconstruct the local Docker, proxy, authentication, or timeout setup
from memory. Before proposing or starting a run, read these repository records
in order:

1. `docs/zh/examples/goal-plus-local-smoke.md` — locally available task images,
   the OrbStack/Docker context setup, and the known local CPU layout.
2. `docs/zh/examples/pi-goal-plus-overnight.md`, especially **网络与代理** and
   **启动与验证** — the established loopback-proxy rewrite, package mirrors,
   `caffeinate`, Judge startup, log locations, and startup checks. Although the
   example runs Pi, its macOS/Docker network plumbing is the local source of
   truth for auth-based Codex and Pi runs.
3. The completed VLIW plain-Codex run under
   `logs/runs/codex-gpt55-2h-repeat-20260715-095016/vliw_kernel_optimization/`.
   Treat `run_config.json`, `run_agent.log`, and `final_result.json` there as
   the primary local source of truth for a Codex-only VLIW launch. That run used
   `agent=codex`, `model=gpt-5.5`, `timeout=7200`, `eval_interval=300`,
   `internet=true`, Work CPU `4`, and Judge CPU `3`; it ran the full 7200 seconds
   and reached hidden `score_cycles=1447` at `agent-14`.
4. `docs/zh/experiment-records/2026-07-14-wireless-codex-gpt55-2h.md` — the
   committed summary of another completed plain-Codex run, useful when ignored
   local run artifacts are unavailable.
5. `docs/zh/configuration/network-setup.md` and
   `docs/zh/configuration/environment-variables.md` — generic behavior and the
   separate strict `--disable-internet` plus `sforge proxy` route. Do not treat
   that alternate route as the default for a Codex `auth.json` run.

Use `scripts/run_codex_goal_plus_solo.sh` and
`scripts/run_pi_goal_plus_overnight.sh` as the executable reference for shared
local plumbing: derive `DOCKER_HOST` from the active Docker context, rewrite
host `127.0.0.1`/`localhost` HTTP(S) proxies to `host.docker.internal`, select
the Node/npm mirrors, keep auth files outside the repository, and pass
`--enable-internet`. Do not invoke a Goal Plus launcher for a plain Codex run;
reuse only its plumbing.

Use the dedicated plain-Codex launcher for a new run:

```bash
./scripts/run_codex_only.sh
```

It always selects `--agent codex` and never installs or invokes Goal Plus. Its
defaults follow the current reproducible plain-Codex policy: explicit `medium`
reasoning, a 7200-second budget, 300-second auto-evaluation, Work CPU `4`, Judge
CPU `3`, internet enabled, and the local Judge at
`http://host.docker.internal:8080`. Override the model and budget explicitly for
a six-hour Terra run:

```bash
MODEL=gpt-5.6-terra \
TIMEOUT_SECONDS=21600 \
SFORGE_CODEX_REASONING_EFFORT=medium \
./scripts/run_codex_only.sh
```

Run `./scripts/run_codex_only.sh --help` for all supported environment
variables. The launcher derives `DOCKER_HOST` from the active Docker context,
rewrites host loopback proxies for the Work container, verifies auth, Docker,
SForge, and the Judge API before starting, and generates a unique run ID.

For independent pass@N trajectories, use the same launcher with explicit
replica and Judge concurrency. This starts three isolated Work containers while
allowing at most one ephemeral Judge container at a time:

```bash
REPLICAS=3 \
REPLICA_CONCURRENCY=3 \
JUDGE_CONCURRENCY=1 \
./scripts/run_codex_only.sh
```

Each trial receives a sibling Run ID ending in `-r01`, `-r02`, and so on. The
group result is written to `logs/runs/<group-run-id>/pass_at_n.json`. Restart a
Judge server that was already running before the pass@N framework code was
installed so its `/register` endpoint accepts the group concurrency fields.

The launcher was verified end to end on 2026-07-22 with run ID
`codex-only-launcher-smoke-20260722-155300`, `MODEL=gpt-5.5`,
`SFORGE_CODEX_REASONING_EFFORT=medium`, `TIMEOUT_SECONDS=180`, and
`EVAL_INTERVAL=60`. The live header reported Codex `0.144.1`, model `gpt-5.5`,
and reasoning effort `medium`; three automatic Judge evaluations completed,
the best hidden result passed `9/9` at `4941` cycles, the final archive was
written, and the Work container was removed normally.

For audit purposes, the exact successful VLIW Codex-only command recorded on
2026-07-15 was:

```bash
SFORGE_HTTP_PROXY=http://host.docker.internal:8118 \
SFORGE_HTTPS_PROXY=http://host.docker.internal:8118 \
SFORGE_NODEJS_MIRROR_URL=https://npmmirror.com/mirrors/node \
SFORGE_NPM_REGISTRY_URL=https://registry.npmmirror.com \
.venv/bin/sforge run \
  --task vliw_kernel_optimization \
  --agent codex \
  --model gpt-5.5 \
  --timeout 7200 \
  --eval-interval 300 \
  --judge-url http://host.docker.internal:8080 \
  --run-id codex-gpt55-2h-repeat-20260715-095016 \
  --enable-internet \
  --work-cpu-limit 4 \
  --judge-cpu-limit 3
```

The launcher above is the executable form of this command. When invoking SForge
directly, change only the model, timeout, and unique run ID unless a current
document or explicit user request requires another difference. Do not copy
either failed precursor:
`codex-gpt55-2h-control-20260715-093422` omitted `--enable-internet`, while
`codex-gpt55-2h-control-20260715-093422-r1` passed the host loopback proxy into
the container without rewriting it. Their `run_agent.log` files record the
expected failures.

Plain Codex containers should reuse the host-side Linux runtime cache instead
of downloading Node and the npm package on every run. The default cache for the
currently pinned adapter version is:

```text
$HOME/.cache/sforge/codex/codex-0.144.1-linux-x64.tgz
```

`sforge/harness/agent/codex.py` automatically copies that archive into the Work
container, installs its `x86_64-unknown-linux-musl/codex` binary as
`/usr/local/bin/codex`, and verifies `codex --version`. If the default archive
is absent, the adapter retains the Node/npm installation fallback. Set
`SFORGE_CODEX_RUNTIME_ARCHIVE` to a different archive path to override the
cache, or to an empty value to disable cache injection deliberately. The
archive must be the Linux x64 platform package, not the host macOS Codex npm
installation.

Rust-base tasks use the toolchain already embedded in their Work/Judge images
when it matches the pinned version. SForge verifies it with a non-login shell so
the image's Docker `PATH`, `CARGO_HOME`, and `RUSTUP_HOME` remain intact. If the
toolchain is absent or has drifted, both container lifecycles use the pinned
host fallback at:

```text
$HOME/.cache/sforge/rust/rust-1.88.0-x86_64-unknown-linux-gnu.tar.xz
```

The control-plane `repro_env.py bootstrap --only edgebench` command downloads
the official archive and verifies the SHA256 recorded in
`sforge/harness/runtime_assets.json`. `SFORGE_RUST_RUNTIME_ARCHIVE` overrides
the path; `SFORGE_RUST_RUNTIME_SHA256` can validate a custom archive; an empty
archive override disables fallback injection. Never download Rust or crates
from inside a task container. CAS uses its submitted vendor tree and Jagua uses
the image's Cargo registry cache for offline builds.

Plain Codex supports either an OpenAI-compatible API endpoint or Codex OAuth.
For API mode, `run_codex_only.sh` accepts `OPENAI_API_KEY` plus
`OPENAI_BASE_URL` (or the corresponding `SFORGE_AGENT_*` variables) and rewrites
a host-local `127.0.0.1`/`localhost` URL to `host.docker.internal`. For OAuth
mode, unset all API key/base URL variables and use
`SFORGE_CODEX_AUTH_FILE`/`~/.codex/auth.json`. Never require or copy the OAuth
file in API mode, and never print either credential value. Preserve the full API
base path; the local proxy on port `3788` uses `/v1`.

Plain Codex reasoning effort must be explicit. The adapter defaults
`SFORGE_CODEX_REASONING_EFFORT` to `medium` and passes it as the highest-priority
Codex CLI override:

```text
codex exec -c 'model_reasoning_effort="medium"' --model <model> ...
```

Set `SFORGE_CODEX_REASONING_EFFORT` explicitly for a reproducible experiment.
Do not infer the effective value from an install command, a host config, or a
model-catalog default. After launch, read the header at the start of
`agent_output.txt` and require both the intended model and the exact line
`reasoning effort: <value>` before considering the run valid. Stop and relaunch
the run if the header does not match.

Before launch, explicitly confirm all of the following instead of guessing:

- Agent identity: plain Codex is `--agent codex`; Codex + Goal Plus is
  `--agent codex-goal-plus`; the controlled single-worker GP experiment is
  `--agent codex-goal-plus-solo` through its launcher.
- Exact model slug from the current Codex model catalog or an already validated
  run; for example, Terra is `gpt-5.6-terra`.
- Exact reasoning effort, passed through `SFORGE_CODEX_REASONING_EFFORT`, and a
  matching `reasoning effort:` line in the live Codex session header.
- Agent budget in seconds (`6h = 21600`) and Work/Judge CPU limits. The current
  local formal-run defaults are Work CPU `4` and Judge CPU `3`.
- The three task images, the Codex/Pi auth file, Docker access, and a ready Judge
  server on port `8080`.
- For a plain Codex run, the cached Linux runtime archive above; if it is absent,
  expect the one-time in-container Node/npm fallback rather than assuming Codex
  is bundled in the Work image.
- The run ID and its expected paths under `logs/runs/<run-id>/<task>/`.

For plain Codex, monitor `run_agent.log`, `agent_output.txt`, or the SForge
visualizer. `scripts/monitor_goal_plus.sh` is only for Goal Plus containers and
must not be presented as the plain-Codex monitor.

## Monitor Goal Plus runs

Run the monitor from the repository root:

```bash
./scripts/monitor_goal_plus.sh
```

With no arguments it discovers every running SForge work container whose main
agent has `GOAL_PLUS_ROLE=main`, then refreshes every five seconds. Legacy Pi
containers exposing only `GOAL_PLUS_PI_ROLE=main` are also recognized. It shows
the model and Goal Plus revision, remaining task time, host-agent pools, Search
runs, candidate and verifier-ledger progress, and the latest explicit Goal Plus
Judge submission.

Useful variants:

```bash
# Print one snapshot and exit.
./scripts/monitor_goal_plus.sh --once

# Refresh every two seconds.
INTERVAL=2 ./scripts/monitor_goal_plus.sh

# Show every persisted GP run instead of only the newest three.
RUN_LIMIT=0 ./scripts/monitor_goal_plus.sh

# Monitor one known container.
./scripts/monitor_goal_plus.sh sforge.run.<task>.<run-id>
```

The monitor's `judge:` line is sourced from Goal Plus's explicit submission
record. Periodic SForge `auto-N` Judge reports are host-side artifacts under
`logs/runs/<run-id>/<task>/submissions/auto-*/report.json` and are not included
in that line.

## Run Codex + Goal Plus

Use the registered `codex-goal-plus` agent. Pin the Goal Plus branch while
running a reproducible experiment and keep Codex credentials outside the repo:

```bash
export SFORGE_CODEX_AUTH_FILE="$HOME/.codex/auth.json"
export SFORGE_GOAL_PLUS_REF="experiment/async-research-flow"

python -m sforge run \
  --task vliw_kernel_optimization \
  --agent codex-goal-plus \
  --model gpt-5.5 \
  --timeout 7200 \
  --work-cpu-limit 4 \
  --judge-cpu-limit 3
```

The adapter installs Goal Plus and Codex project hooks in the work container,
uses Codex's rolling worker pool, synchronizes every promoted candidate through
`sforge-goal-plus-submit`, and archives the durable Goal Plus state as
`goal-plus-state.tar`. Do not copy or commit either Codex or Pi auth files.

For the controlled VLIW single-worker experiment, use the dedicated launcher:

```bash
./scripts/run_codex_goal_plus_solo.sh
```

It normalizes the current Docker context for the Python Docker SDK, rewrites a
host loopback HTTP(S) proxy to `host.docker.internal`, selects the Node/npm
mirrors, and starts `codex-goal-plus-solo`. The frozen Search run is limited to
one candidate and one parallel worker; that worker receives a 7200-second lease
without a turn limit, while the outer 8100-second timeout leaves setup and final
Judge time.
