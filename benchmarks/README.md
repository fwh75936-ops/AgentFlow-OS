# Benchmarks — 对比实验（真实运行）

本目录是初赛实验数据的入仓版本，与提交材料目录中的原始产出
（`agentflow-submission/benchmarks/`）同源。全部数字由脚本真实执行
生产引擎代码产出，无人工编造。

## 文件

| 文件 | 内容 |
|---|---|
| `run_benchmark.py` | 实验脚本：直接 import 生产引擎 `select_agent` / `SelfLoop` / `tool_verify_pytest` 运行 |
| `results.json` / `results.csv` | 逐次运行明细与分组汇总（由脚本自动生成，请勿手改） |
| `summary.md` | 实验报告：方法、任务定义、结果、结论与局限（由脚本自动生成） |
| `workspaces/` | Part B 每个缺陷任务的证据工作区（关组保留缺陷代码、开组保留修复后代码与测试） |

## 实验构成

- Part A 调度：6 个任务 × 4 种模式（single / fixed / round_robin / adaptive），
  成败按预先声明且各模式统一的标准判定（被选 Agent 能力分 ≥ 任务门槛且负载 ≤ 0.90）。
- Part B 修复：3 个真实缺陷任务 × Self-Loop 开/关两组，真写文件 + 真跑 pytest 验证。
- 本实验不调用任何 LLM，Token 消耗如实记为未涉及（0）。

## 在仓库内运行（路径假设说明）

原版脚本（提交材料目录中）把后端路径写死为
`~/workspace/agentflow-os/services/agent-core`，且 Part B 的证据工作区写在
脚本所在目录下——在原开发机布局下成立，但仓库换个位置克隆就会失效。

入仓版已修正为**仓库相对路径**（`run_benchmark.py` 中 `AGENT_CORE` 的解析）：

- 后端路径优先按脚本位置推导：`<仓库根>/services/agent-core`
  （脚本位于 `<仓库根>/benchmarks/`）；仅当该相对路径不存在时，才回退到
  原开发机的 `~/workspace/agentflow-os/services/agent-core`。
- 输出（`results.json` / `results.csv` / `summary.md` / `workspaces/`）
  始终写在脚本所在目录，即本目录，与当前工作目录无关。

复现命令（仓库根目录执行，需先按主 README 建好后端虚拟环境）：

```bash
services/agent-core/.venv/bin/python benchmarks/run_benchmark.py
```

## 入仓重跑验证（2026-11-17）

修正路径后已在仓库内真实重跑一次，结果与入仓前一致：

- 逐次运行（组 / 任务 / 成功与否 / 被选 Agent / 执行次数）与原始
  `results.json` **逐条完全一致**；
- 分组成功率不变：single 17%、fixed 17%、round_robin 50%、adaptive 100%、
  Self-Loop 关 0%（修复 0/3）、Self-Loop 开 100%（修复 3/3）；
- 仅平均耗时有微小自然波动（调度为微秒级函数计时、修复组含 pytest
  子进程墙钟时间），本目录的 `results.*` 与 `summary.md` 已更新为
  本次重跑的真实产出。
