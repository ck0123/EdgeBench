---
title: "本地实验台账"
---

# 本地实验台账

这里保存需要跨机器、跨会话长期查阅的 EdgeBench 实验摘要。每次实验单独一页，
本页只维护可快速比较的索引；完整的逐轮评测数据仍以本地 run 目录为准。

## 记录分层

| 层级 | 位置 | 用途 | 是否提交到 Git |
| --- | --- | --- | --- |
| 原始运行数据 | `logs/runs/<run-id>/<task>/` | `final_result.json`、`run_history.json`、每轮 submission、最终归档 | 否，`logs/` 已被忽略 |
| 成绩报告 | `scripts/report_edgebench_scores.py` | 读取 run，换算统一分数并对比官方检查点 | 工具提交，生成结果默认不落盘 |
| 长期实验台账 | `docs/zh/experiment-records/` | 保存配置、最终结果、结论和原始数据路径 | 是 |

原始运行数据是结果追查的依据；实验台账用于回答“跑过什么、配置是什么、结果好不好、
下一步做什么”，不复制全部逐轮日志。

## 实验索引

| 日期 | Task | Agent / Model | 预算 | CPU（work/judge） | 最佳结果 | EdgeBench 分 | 状态 | 详情 |
| --- | --- | --- | ---: | ---: | ---: | ---: | --- | --- |
| 2026-07-14 | `wireless_electricity_layout` | Codex / `gpt-5.5` | 2h | 3 / 2 | `3.630255483719763e16` | `0.0` | 正常耗尽预算 | [记录](2026-07-14-wireless-codex-gpt55-2h.md) |

## 后续如何追加

1. 从 [_template.md](_template.md) 复制一份新页面，文件名使用
   `YYYY-MM-DD-<task>-<agent>-<model>-<budget>.md`。
2. 从 `run_config.json` 和 `final_result.json` 填入实际配置，不使用计划值代替实际值。
3. 用成绩报告工具生成统一分和官方对比：

   ```bash
   python scripts/report_edgebench_scores.py \
     --run-dir logs/runs/<run-id>/<task> \
     --model <model> \
     --budget-hours <hours> \
     --json
   ```

4. 在本页索引追加一行，并保留相对的原始 run 路径。
5. 不在记录中写入 `auth.json`、token、用户名、主目录绝对路径或其他个人信息。

---

相关说明：[EdgeBench 成绩换算与官方结果对比](../reference/edgebench-score-report.md)。
