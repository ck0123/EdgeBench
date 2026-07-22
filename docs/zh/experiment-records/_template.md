---
title: "EdgeBench 实验记录模板"
---

# 实验记录：`<task>`

## 实验身份

| 字段 | 值 |
| --- | --- |
| 日期 | `YYYY-MM-DD` |
| Run ID | `<run-id>` |
| Task | `<task>` |
| Agent | `<agent>` |
| Model | `<model>` |
| 代码版本 | `<branch / commit>` |
| 目的 | `<本次实验要验证的问题>` |

## 实际运行配置

| 配置 | 值 |
| --- | ---: |
| 时间预算 | `<seconds / hours>` |
| Work CPU | `<count>` |
| Judge CPU | `<count>` |
| Auto-eval 间隔 | `<seconds>` |
| Stop hook | `<enabled / disabled>` |
| Internet | `<enabled / disabled>` |

只记录 `run_config.json` 中的实际值。不要写入认证文件内容、token、本机用户名或主目录
绝对路径。

## 最终结果

| 指标 | 值 |
| --- | ---: |
| 最佳 round | `<round>` |
| Raw score | `<score>` |
| EdgeBench score | `<0-100>` |
| 扩展尾部分 | `<optional>` |
| Pass rate | `<rate>` |
| Agent submissions | `<count>` |
| Auto submissions | `<count>` |
| Resume count | `<count>` |
| 实际运行时间 | `<seconds>` |
| 结束状态 | `<completed / budget exhausted / failed>` |

## 官方参考

- 同模型、同预算检查点：`<score>`
- 公开领先结果：`<model / score>`
- 可比性说明：`<硬件、Agent 或预算差异>`

## 结论

- `<这次实验确认了什么>`
- `<主要失败模式或异常>`
- `<下一次实验建议>`

## 本地原始数据

```text
logs/runs/<run-id>/<task>/final_result.json
logs/runs/<run-id>/<task>/run_history.json
logs/runs/<run-id>/<task>/final_archive.tar.gz
```

这些路径默认不提交到 Git。需要跨机器保留产物时，应使用单独的制品存储，并在这里记录
制品标识，不要把认证文件打包进去。
