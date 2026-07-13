---
title: "Pi + Goal Plus 夜间顺序运行配置"
---

# Pi + Goal Plus 夜间顺序运行配置

这份报告记录 6 个本地 EdgeBench task 的可复现长跑配置。入口脚本是
`scripts/run_pi_goal_plus_overnight.sh`：每个 task 给 Pi + Goal Plus 2 小时，严格
顺序执行，使用 `gpt-5.5`。总 agent 预算为 12 小时，另需预留每个新容器的安装和
最终评测时间。

## 固定配置

| 项目 | 值 |
|------|----|
| Agent | `pi-goal-plus` |
| Pi provider | `openai-codex` |
| Model | `gpt-5.5` |
| 单 task agent 时限 | `7200` 秒 |
| task 并发数 | `1`，严格顺序 |
| Work CPU 上限 | `3` |
| Judge CPU 上限 | `2` |
| Auto eval | 每 `300` 秒 |
| Auto resume | 开启；沿用同一个 Pi session，Goal Plus 完成后再切换 cycle |
| 容器网络 | 开启 |
| Node.js / npm | npmmirror |
| C++ task 的 Python | 清华 Ubuntu 镜像提供 Python 3.10 |
| Python task 的 Python | 复用 work image 自带版本 |
| Python 包 | 清华 PyPI 镜像 |
| Goal Plus 来源 | 固定 commit `3f97cf3ea44096ead375e4cb7238c6ef007fb4ab` |

执行顺序如下：

1. `ad_placement_optimization`
2. `wireless_electricity_layout`
3. `tree_block_partitioning`
4. `triangulation_coloring_optimization`
5. `apple_incremental_game`
6. `vliw_kernel_optimization`

这些 task 对应的 15 个镜像标签和共享 base 镜像说明见
[Goal Plus 本地 Smoke Task 清单](./goal-plus-local-smoke.md)。

---

## 凭据与源码

凭据来自 Pi 自己的 `~/.pi/agent/auth.json`，文件中必须存在 `openai-codex` 登录。
SForge 在容器创建后把该文件复制为 `/home/agent/.pi/agent/auth.json`，并调整为
`agent:agent` 所有权和 `0600` 权限；随后 Pi 使用
`--provider openai-codex --model gpt-5.5` 启动。这里不使用 Codex CLI 的凭据，
也不要求 `SFORGE_AGENT_API_KEY`。

仓库和日志不得保存 `auth.json` 内容、access/refresh token、account ID、用户名或
个人绝对路径。需要覆盖默认位置时，只设置路径：

```bash
export SFORGE_PI_AUTH_FILE="$HOME/.pi/agent/auth.json"
```

Goal Plus 不从宿主机 checkout 复制。每个新 work container 都下载上表中的固定
commit。Node.js 和 Pi 仍随新容器安装，但默认从 npmmirror 下载。没有 Python 3.10
的 Ubuntu 22.04 C++ work image 通过清华 Ubuntu 镜像安装系统 Python；Python task
直接使用镜像内版本。两条路径都不会经过 `uv` 下载 GitHub Release，Python 包使用
清华 PyPI。Docker task 镜像本身不会重复下载。

SForge 的普通 Codex agent 使用 `codex exec resume --last` 延续最近会话；这里的 Pi
采用等价的 `pi -c`。因此 cycle 之间保留主 agent 的对话上下文、当前工作区和 Goal
Plus 持久化搜索记录。每条 Goal Plus record 仍在完成一次优化 cycle 后终止，随后在
同一个 Pi session 中从已提升的 winner 创建下一条 record。SForge 不再用固定的
20 分钟 segment timeout 强制切断尚未完成的 Goal Plus record；仅有单 task 的全局
`7200` 秒时限会结束 agent。

---

## 网络与代理

Pi 的 `openai-codex` provider 需要访问 ChatGPT Codex backend，所以脚本显式传入
`--enable-internet`。如果宿主代理是 `127.0.0.1` 或 `localhost`，脚本会仅针对
SForge work container 将其改写为 `host.docker.internal`。这样容器不会把自己的
loopback 误认为宿主代理。

启动前可从任一 work image 验证代理出口。命令中不要写带用户名或密码的代理 URL：

```bash
docker run --rm \
  -e HTTPS_PROXY=http://host.docker.internal:8118 \
  edgebench.work.ad_placement_optimization:49747cad3ebd \
  curl -I https://chatgpt.com/backend-api/codex/responses
```

端点可达时，未授权的 `HEAD` 请求通常返回 `405`；这只验证连通性，不验证 Pi 登录。

---

## 启动与验证

先查看计划，不创建容器：

```bash
./scripts/run_pi_goal_plus_overnight.sh --dry-run
```

立即开始，并在 macOS 睡眠期间保持运行：

```bash
caffeinate -dimsu ./scripts/run_pi_goal_plus_overnight.sh
```

也可以等待到下一次本地 `23:00`：

```bash
caffeinate -dimsu ./scripts/run_pi_goal_plus_overnight.sh --start-at 23:00
```

启动正常时，首个 task 的 `run_agent.log` 应依次出现：

```text
Copied host Pi openai-codex login into the work container
Goal Plus source not configured; install will download pinned commit
Agent installation complete
Running agent: ... --provider openai-codex --model "$PI_MODEL" ...
```

运行输出位于以下相对目录，不依赖个人用户名：

```text
logs/overnight/<batch-id>/overnight.log
logs/runs/<run-id>/<task>/run_agent.log
logs/runs/<run-id>/<task>/agent_output.txt
logs/runs/<run-id>/<task>/final_result.json
```

脚本会检查镜像、Pi auth 文件和 Judge 服务。每个 task 结束后还会检查
`final_result.json` 中的 agent runtime；若安装或认证失败导致运行时间明显不足，
队列立即停止，不会继续消耗后续 5 个 task。
