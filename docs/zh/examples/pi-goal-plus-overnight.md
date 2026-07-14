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
| Auto resume | 开启；与普通 Pi 一样通过 `pi -c` 和 `Continue working.` 延续 session |
| Stop gate | Goal Plus Pi extension 的原生 `agent_end` hook |
| 容器网络 | 开启 |
| Node.js / npm | npmmirror |
| C++ task 的 Python | 清华 Ubuntu 镜像提供 Python 3.10 |
| Python task 的 Python | 复用 work image 自带版本 |
| Python 包 | 清华 PyPI 镜像 |
| Goal Plus 来源 | 每个新 worker 容器启动时 shallow clone 上游 `main` |

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

Goal Plus 不从宿主机 checkout 复制。每个新 work container 都 shallow clone 上游
`main`，并在安装日志中记录实际解析出的 commit SHA。Node.js 和 Pi 仍随新容器安装，
但默认从 npmmirror 下载。没有 Python 3.10
的 Ubuntu 22.04 C++ work image 通过清华 Ubuntu 镜像安装系统 Python；Python task
直接使用镜像内版本。两条路径都不会经过 `uv` 下载 GitHub Release，Python 包使用
清华 PyPI。Docker task 镜像本身不会重复下载。

Codex agent 会把 SForge 生成的 EdgeBench prompt 原样交给 `codex exec`；普通 Pi 也会
原样交给 `pi`。`pi-goal-plus` 保持同一份原始 prompt，只通过 `/goal-plus` 入口加载
Goal Plus，并在 prompt 末尾追加深度搜索和宿主提供的时间信息：

```text
Use the Goal Plus framework to perform deep search optimization for this task.
The total exploration time budget for this task is <total-seconds> seconds.
The hard deadline is Unix timestamp <deadline>, and <remaining-seconds> seconds remain at this launch.
```

总预算来自本次 `sforge run --timeout` 的实际值，不写死为 2 小时。硬截止时间由宿主在
agent 正式开始前生成，安装环境的时间不计入；启动和每次 resume 都会用当前时间重新
计算剩余秒数。容器内 `/opt/sforge-agent-deadline` 始终保留权威 deadline，prompt 会
要求 agent 在决定下一轮搜索前刷新剩余时间。

这里不额外规定 round 数、candidate 数、并行数或按剩余时间划分的阈值；时间只是给
Goal Plus 自主规划 SearchSpec、搜索深度和最终验证留时使用。若 Pi 提前退出，auto
resume 与普通 Pi 一致，通过同一个 session 执行 `Continue working.` 并注入新的剩余
时间。SForge 不使用固定的 20 分钟 segment timeout；只有单 task 的全局 `7200` 秒
时限会结束 agent。

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

前序任务已经完成、需要从某个 task 续跑时：

```bash
caffeinate -dimsu ./scripts/run_pi_goal_plus_overnight.sh \
  --from-task tree_block_partitioning
```

启动正常时，首个 task 的 `run_agent.log` 应依次出现：

```text
Copied host Pi openai-codex login into the work container
Install step 4/6: ... git clone --depth 1 --branch main ...
Agent installation complete
Goal Plus stop gate is provided by the Pi extension's native agent_end hook
Agent time budget installed: total=7200s, deadline=<unix-timestamp>
Running agent: ... --provider openai-codex --model "$PI_MODEL" ...
```

deadline 由宿主计算后通过容器 shell 写入
`/opt/sforge-agent-deadline`，SForge 会立即回读校验，并同时通过
`SFORGE_AGENT_DEADLINE` 环境变量提供后备值。续跑日志会记录时间戳和剩余秒数；文件
缺失或格式错误会显式失败，不能再被静默当作预算耗尽。

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

## 成绩换算与官方结果对比

EdgeBench judge 同时记录任务原始分和统一的 `0–100` 换算分。不要直接用原始分
判断结果好坏；不同任务可能分别采用最大化、最小化、对数或分段换算。

完整命令、参数和输出解释见
[EdgeBench 成绩换算与官方结果对比](../reference/edgebench-score-report.md)。

运行中或结束后，可直接生成同预算的官方公开结果对比：

```bash
python scripts/report_edgebench_scores.py \
  --latest tree_block_partitioning \
  --model gpt-5.5 \
  --budget-hours 2
```

也可以指定一次 run 的任务目录：

```bash
python scripts/report_edgebench_scores.py \
  --run-dir logs/runs/<run-id>/<task> \
  --budget-hours 2
```

脚本从 `tasks/<task>.json` 读取官方换算规则，从仓库 `README.md` 读取 51 个公开任务
的官方 `@2h/@4h/.../@12h` 曲线，并输出：

- 原始分与 EdgeBench `0–100` 分；
- 同模型、同时间检查点的差值和达成率；
- 官方公开模型中的领先者；
- 将当前 run 加入公开模型后的参考位置。

顺序批处理脚本会在每个任务完成后自动调用该报告。该结果属于同量纲参考；若
Agent、CPU、超时或其他运行设置与官方 leaderboard 配置不同，不应表述为正式
leaderboard 名次。
