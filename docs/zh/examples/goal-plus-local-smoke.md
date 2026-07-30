---
title: "Goal Plus 本地 Smoke Task 清单"
---

# Goal Plus 本地 Smoke Task 清单

这份清单记录一组适合在本地 Docker backend 中验证 `pi-goal-plus` 的
EdgeBench task。所有镜像均为 `linux/amd64`。

## 已准备的 task 与镜像

| Task | Base | Work | Judge | 用途 |
|------|------|------|-------|------|
| `ad_placement_optimization` | `edgebench.base.cpp:19685ea8d3f4` | `edgebench.work.ad_placement_optimization:49747cad3ebd` | `edgebench.judge.ad_placement_optimization:56cbfc81cfa1` | C++ 连续优化；Goal Plus EdgeBench 集成基线 |
| `wireless_electricity_layout` | `edgebench.base.cpp:19685ea8d3f4` | `edgebench.work.wireless_electricity_layout:c3179795f69f` | `edgebench.judge.wireless_electricity_layout:1b918c76e808` | 单文件 C++、本地生成器和 tester |
| `tree_block_partitioning` | `edgebench.base.cpp:19685ea8d3f4` | `edgebench.work.tree_block_partitioning:f282a9f7e05a` | `edgebench.judge.tree_block_partitioning:f74f0ef897ce` | 多阶段 C++ 正确性与得分改进 |
| `triangulation_coloring_optimization` | `edgebench.base.python310:6fd084182df4` | `edgebench.work.triangulation_coloring_optimization:d3af8893fa81` | `edgebench.judge.triangulation_coloring_optimization:568aa1a5a8ff` | 单文件 Python、连续优化目标和本地 tester |
| `apple_incremental_game` | `edgebench.base.python310:6fd084182df4` | `edgebench.work.apple_incremental_game:d3c6ed381c59` | `edgebench.judge.apple_incremental_game:16968d8ec7f2` | 单文件 Python、可生成本地测试和连续分数 |
| `vliw_kernel_optimization` | `edgebench.base.python:e4670062c1cb` | `edgebench.work.vliw_kernel_optimization:9fa380a0ebef` | `edgebench.judge.vliw_kernel_optimization:5cdef0021634` | 确定性 simulator/verifier 和性能优化 |

## 空间记录

一次本地实测中，上述 6 个 task 共对应 15 个本地镜像标签。按共享层口径，
EdgeBench 镜像约占 3.2 GB；不同 Docker 存储后端的结果可能不同，不能把
`docker image ls` 中每个标签的显示大小直接相加。

完整的 51-task 公开集曾实测占用约 312 GB。全量下载建议至少准备 300 GB，
并为运行容器、日志和中间产物预留更多空间；本地 smoke test 不应执行
`sforge pull --all`。

查看当前实际占用：

```bash
docker system df
docker system df -v | grep '^edgebench\.'
```

## Docker context

SForge 使用 Python Docker SDK。Docker Desktop 或 OrbStack 的 socket 不一定是
`/var/run/docker.sock`，可以从当前 Docker context 动态设置：

```bash
export DOCKER_HOST="$(docker context inspect "$(docker context show)" \
  --format '{{.Endpoints.docker.Host}}')"
docker info --format 'Architecture={{.Architecture}} DockerRootDir={{.DockerRootDir}}'
```

预期架构是 `x86_64`/`amd64`。

## 短预算验证

`pi-goal-plus` 使用 Pi 自己的 OpenAI Codex 登录。先在宿主机完成 Pi 登录，确认
`~/.pi/agent/auth.json` 中存在 `openai-codex` 项。这个文件只在运行时复制到工作
容器，不要提交到仓库，也不要把 token 或 account ID 写进脚本、配置或文档。

```bash
python3 -m sforge.cli run \
  --task vliw_kernel_optimization \
  --agent pi-goal-plus \
  --model gpt-5.5 \
  --timeout 600 \
  --enable-internet \
  --run-id vliw-pi-goal-plus-smoke
```

建议先用 10–30 分钟验证安装、verifier、提交循环和日志，再增加预算。

6 个 task 的顺序长跑配置见 [Pi + Goal Plus 夜间顺序运行配置](./pi-goal-plus-overnight.md)。

## Codex + Goal Plus 的探索与收尾预算

`codex-goal-plus` 默认把 `--timeout` 作为探索预算，并额外提供 300 秒收尾宽限期。
例如 `--timeout 600` 会在 10 分钟时停止创建/恢复候选，但宿主进程最晚可运行到
15 分钟，以便完成最终 verifier、`search_select`、`search_promote`、同步 Judge、
`goal_plus_record_search_result`、raw-goal audit、终态落盘和一次
`search_report`。这段宽限期不能用于继续优化。

如需覆盖或禁用宽限期，可通过 agent extra env 设置：

```bash
SFORGE_AGENT_EXTRA_ENV="SFORGE_GOAL_PLUS_FINALIZATION_GRACE_SECONDS=120" \
python3 -m sforge.cli run \
  --task vliw_kernel_optimization \
  --agent codex-goal-plus \
  --model gpt-5.6-sol \
  --timeout 600 \
  --enable-internet \
  --run-id vliw-codex-goal-plus-smoke
```

正常情况下，宽限期内完成会记录为 `completed_in_finalization_grace`，而不是
`timeout`。Codex 正常退出后，适配器还会检查持久化的 Goal Plus 状态；只有所有
Goal 记录都已终止且每个已记录 Search run 的报告实际存在时，才会跳过自动恢复。
