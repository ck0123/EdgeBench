---
title: "本地实验：2026-07-14 Wireless Electricity，Codex + GPT-5.5 2h"
---

# Wireless Electricity：Codex + GPT-5.5 2h

## 实验身份

| 字段 | 值 |
| --- | --- |
| 日期 | `2026-07-14` |
| Run ID | `codex-gpt55-wireless-2h-20260714-1440` |
| Task | `wireless_electricity_layout` |
| Agent | `codex` |
| Model | `gpt-5.5` |
| 代码版本 | `mac`；相关 harness 改动随后提交为 `391e365` |
| 目的 | 验证直接使用 Codex 时能否稳定产生有效、非纯零的优化反馈，并确认 submit/judge 闭环正常 |

## 实际运行配置

| 配置 | 值 |
| --- | ---: |
| 时间预算 | `7200s`（2h） |
| Work CPU | `3` |
| Judge CPU | `2` |
| Auto-eval 间隔 | `300s` |
| Stop hook | 启用 |
| Auto-resume | 启用；本次未触发 |
| Internet | 启用 |

Work 镜像为 `edgebench.work.wireless_electricity_layout:c3179795f69f`，Judge 镜像为
`edgebench.judge.wireless_electricity_layout:1b918c76e808`。

## 最终结果

| 指标 | 值 |
| --- | ---: |
| 最佳 round | `agent-22` |
| Raw score | `3.630255483719763e16`，越低越好 |
| EdgeBench 官方分 | `0.0 / 100` |
| 扩展尾部分 | `0.007178300916136047 / 100` |
| Pass rate | `50/50`（100%） |
| Agent submissions | `22` |
| Auto submissions | `23` |
| 总评测次数 | `45` |
| Resume count | `0` |
| 实际运行时间 | `7200.24s` |
| 结束状态 | 正常耗尽时间预算；最终 submission 自身无 TLE |

最早达到 `50/50` 的 `agent-3` raw score 为 `4.658321086827758e16`；最终最佳值在
保持完全有效的前提下继续下降约 `22.07%`。相较此前 `agent-16` 的
`3.7208080491245336e16`，最后阶段又下降约 `2.43%`。

## 官方参考

成绩换算工具对 `@2h` 公开检查点给出的参考是：

- 同模型 GPT-5.5：`6.2 / 100`
- 公开领先 GPT-5.4：`10.9 / 100`
- 本次：`0.0 / 100`

因此官方零分是优化效果仍低于官方曲线的结果，不是 submission 缺失、judge 未返回或
纯整数解析错误。`0.0071783` 是本地扩展尾部分，只用于区分多个官方零分的有效结果，
不属于 EdgeBench 官方 leaderboard 分数。

## 结论

- Codex 在 2 小时内持续提交并获得 judge 反馈，submit/judge 闭环正常。
- 方案从部分通过推进到 `50/50`，之后仍持续降低 raw score，说明任务并非固定卡在
  无效解或纯零反馈。
- 当前瓶颈是解的优化质量，而不是 CPU 限制、认证、提交路径或分数解析。
- 下一项优先运行 `vliw_kernel_optimization`；后续本机正式任务默认使用 Work CPU `4`、
  Judge CPU `3`，本页保留本次实际使用的 `3/2`，不回填计划配置。

## 本地原始数据

```text
logs/runs/codex-gpt55-wireless-2h-20260714-1440/wireless_electricity_layout/final_result.json
logs/runs/codex-gpt55-wireless-2h-20260714-1440/wireless_electricity_layout/run_history.json
logs/runs/codex-gpt55-wireless-2h-20260714-1440/wireless_electricity_layout/final_archive.tar.gz
```

原始目录由 `.gitignore` 排除；长期可提交的摘要以本页和[实验索引](index.md)为准。
