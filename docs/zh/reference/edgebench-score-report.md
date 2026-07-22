---
title: "EdgeBench 成绩换算与官方结果对比"
---

# EdgeBench 成绩换算与官方结果对比

`scripts/report_edgebench_scores.py` 用于把单个任务的 judge 原始分换算成统一的
`0–100` EdgeBench 分数，并与仓库公开的同任务、同模型、同时间检查点成绩对比。
以后查看本地任务效果时应优先运行这个脚本，不要直接比较不同任务的原始分。

## 最常用命令

查看某个任务最新一次本地运行；下面也是日常进度汇报使用的命令：

```bash
python scripts/report_edgebench_scores.py \
  --latest tree_block_partitioning \
  --model gpt-5.5 \
  --budget-hours 2
```

`--latest` 会在 `logs/runs/` 中寻找该任务最近修改的一次 run。运行尚未结束时，只要
已经存在有效 submission，也可以查看当前最好成绩；输出状态为 `in_progress`。

## 其他输入方式

指定已知 run 的任务目录：

```bash
python scripts/report_edgebench_scores.py \
  --run-dir logs/runs/<run-id>/<task> \
  --model gpt-5.5 \
  --budget-hours 2
```

只有一个 judge 原始分时直接换算：

```bash
python scripts/report_edgebench_scores.py \
  --raw-score 48260987772 \
  --task ad_placement_optimization \
  --model gpt-5.5 \
  --budget-hours 2
```

供其他脚本消费时输出 JSON：

```bash
python scripts/report_edgebench_scores.py \
  --latest tree_block_partitioning \
  --model gpt-5.5 \
  --budget-seconds 7200 \
  --json
```

三个输入参数 `--latest`、`--run-dir`、`--raw-score` 必须且只能选择一个。
`--raw-score` 必须同时提供 `--task`。时间预算不传时默认按 2 小时对比；也可用
`--budget-hours` 或 `--budget-seconds` 明确指定。

完整参数随代码保持同步，可随时查看：

```bash
python scripts/report_edgebench_scores.py --help
```

## 输出怎么看

报告包括以下内容：

- `Raw score`：judge 返回的任务原始分，只适合在同一个任务内比较；
- `EdgeBench score`：根据 `tasks/<task>.json` 的官方 rescale 规则得到的统一
  `0–100` 分；
- `Local extended score`：当有效结果低于官方 baseline、正式分被截为 `0.0` 时，
  显示一个 `0–0.01` 的浮点尾分用于区分优化进展；它仅是本地诊断值，不参与官方
  排名或结果对比；
- `Pass rate`：submission 通过的测试比例（若报告中存在）；
- `Same model`：公开表中同模型、同时间检查点的参考分；
- `delta`：当前 EdgeBench 分减去同模型参考分，单位为百分点；
- `attainment`：当前分数占同模型参考分的百分比；
- `Published leader`：该任务、该时间检查点的公开最高模型结果；
- `Position including this run`：把本次 run 临时放入公开模型结果后的参考位置。

公开曲线只有 `2/4/6/8/10/12` 小时检查点。传入其他预算时，脚本会选择最近的公开
检查点，并在输出中标记 `nearest published checkpoint`。

## 数据来源和边界

换算规则来自 `tasks/<task>.json`，官方对比数据来自仓库根目录 `README.md` 的
51-task 公开结果表。这样任务换算逻辑和公开曲线都会随仓库更新，不需要在报告脚本中
手工维护一份重复数据。

`Position including this run` 只是同量纲参考，不是正式 leaderboard 名次。Agent、
模型版本、CPU、运行时限或其他评测设置与官方配置不一致时，汇报应写成“官方结果
对比”或“参考位置”。如果任务不在公开的 51-task 表中，脚本仍会输出换算分，但不会
生成官方对比。

## 夜间批跑中的自动调用

`scripts/run_pi_goal_plus_overnight.sh` 会在每个任务正常完成后自动执行该报告，传入
当前模型和该任务的实际预算：

```text
--run-dir logs/runs/<run-id>/<task>
--model <batch-model>
--budget-seconds <task-timeout>
```

成绩报告失败不会删除已经完成的 run；批跑日志会记录失败信息，之后可使用上面的
`--run-dir` 命令重新生成。

## 长期实验记录

`logs/runs/` 保存完整原始数据，但默认不提交到 Git。需要跨机器或跨会话长期查阅时，
把实际配置、最终结果和结论写入[本地实验台账](../experiment-records/index.md)。台账按实验拆页，
不复制逐轮日志，也不得包含认证信息或个人绝对路径。
