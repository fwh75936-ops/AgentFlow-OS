# AgentFlow OS 对比实验结果（真实运行）

- 运行时间：2026-11-17T02:23:49+00:00；Python 3.12.3；平台 Linux-7.0.0-44-generic-x86_64-with-glibc2.39
- 代码来源：`/home/hatch/workspace/agentflow-os/services/agent-core` 中未修改的生产引擎 （scheduler/engine.py、verification/engine.py、self_loop/engine.py）
- 复现命令：`services/agent-core/.venv/bin/python benchmarks/run_benchmark.py  (from repo root)`
- Token消耗：**未涉及（0）**。本实验不调用任何LLM：调度是确定性评分公式、验证是真实 pytest 子进程、修复是基于失败输出的规则修复，因此没有 Token 消耗可报告，未编造任何 Token 数字。

## 测试方法

**Part A 调度实验（6个任务 × 4种模式 = 24次真实调用）**：直接调用生产函数 `select_agent()`。每个任务预先声明难度门槛（所需能力分下限）与负载上限（current_load ≤ 0.90），四种模式使用完全相同的判定标准：被选中的 Agent 能力分 ≥ 门槛且负载 ≤ 上限记为成功，否则失败。Agent 池中每个 Agent 均通过硬过滤（持有能力标签），差异来自能力分、历史成功率、负载、成本、延迟（每个 Agent 的完整参数见 run_benchmark.py 中的 SCHED_TASKS，决策明细见 results.json 的 candidates 字段）。single 组将池裁剪为仅 id=1 一个 Agent；round_robin 的 rotation_index 在组内按任务顺序递增。耗时为 `time.perf_counter()` 对单次 `select_agent()` 调用的实测值（微秒级，仅反映调度计算开销）。

**Part B Self-Loop 修复实验（3个任务 × 2组 = 6次真实运行）**：每个任务在独立工作区写入真实的缺陷代码与 pytest 测试文件，验证使用生产函数 `tool_verify_pytest()` 真实执行 `python -m pytest -q` 子进程。修复函数读取真实 pytest 失败输出中的失败测试名/关键词，匹配后应用对应修复代码，再由 SelfLoop 重新执行并重新验证。关闭组使用 `SelfLoop(max_retries=0)`：首次验证失败即终止，修复函数从不被调用；开启组使用 `SelfLoop(max_retries=3)`。耗时为包含 pytest 子进程的整轮实测墙钟时间。

## 任务定义

| 任务 | 类型 | 定义 |
|---|---|---|
| S1_hard_coding_api | 调度选择 | Hard coding task (user-management API); needs capability >= 0.85. Weak agent id=1 is also slow/expensive (legacy setup)（要求能力 coding，池内3个Agent能力/负载/成本各异） |
| S2_research | 调度选择 | Research task; needs capability >= 0.80（要求能力 research，池内3个Agent能力/负载/成本各异） |
| S3_overloaded_expert | 调度选择 | Coding task; strongest agent is saturated (load 0.95 > 0.90 limit)（要求能力 coding，池内3个Agent能力/负载/成本各异） |
| S4_code_review | 调度选择 | Code-review task; the reviewer (highest capability) has the highest id（要求能力 review，池内3个Agent能力/负载/成本各异） |
| S5_easy_general | 调度选择 | Easy general task (control): every agent clears the bar（要求能力 general，池内3个Agent能力/负载/成本各异） |
| S6_cost_sensitive | 调度选择 | Cost-sensitive coding task (bar 0.65): expert id=2 is far costlier/slower; adequate agent id=3 (0.78) is much cheaper（要求能力 coding，池内3个Agent能力/负载/成本各异） |
| R1_permission_bug | 代码修复 | access_control.py 含真实缺陷（permission: admin_panel requires role == 'admin'），以自带 pytest 套件判定成败 |
| R2_validation_bug | 代码修复 | user_validation.py 含真实缺陷（validation: reject empty username and email without '@'），以自带 pytest 套件判定成败 |
| R3_discount_bug | 代码修复 | pricing.py 含真实缺陷（pricing: total = sum(prices) * (1 - discount_rate)），以自带 pytest 套件判定成败 |

## 结果汇总（由 results.json 自动计算）

| 实验组 | 运行次数 | 成功 | 失败 | 成功率 | 平均耗时(s) | 首次验证失败数 | 自动修复成功率 |
|---|---|---|---|---|---|---|---|
| 单Agent执行（池中仅保留 id=1 的Agent） | 6 | 1 | 5 | 17% | 3.5e-05 | — | — |
| 多Agent固定调度 fixed（最低id） | 6 | 1 | 5 | 17% | 3.8e-05 | — | — |
| 多Agent轮询调度 round_robin | 6 | 3 | 3 | 50% | 3.2e-05 | — | — |
| 多Agent自适应调度 adaptive（SACAS评分） | 6 | 6 | 0 | 100% | 3.3e-05 | — | — |
| Self-Loop关闭（max_retries=0） | 3 | 0 | 3 | 0% | 0.415 | 3 | 0%（0/3） |
| Self-Loop开启（max_retries=3） | 3 | 3 | 0 | 100% | 0.802933 | 3 | 100%（3/3） |

## 逐次运行明细

| 组 | 任务 | 结果 | 耗时(s) | 执行次数 | 选中Agent | 说明 |
|---|---|---|---|---|---|---|
| single | S1_hard_coding_api | 失败 | 7.7e-05 | 1 | 1 | selected capability=0.45 (bar 0.85), load=0.10 (limit 0.9) -> below bar |
| single | S2_research | 失败 | 4.3e-05 | 1 | 1 | selected capability=0.50 (bar 0.8), load=0.10 (limit 0.9) -> below bar |
| single | S3_overloaded_expert | 失败 | 3e-05 | 1 | 1 | selected capability=0.98 (bar 0.85), load=0.95 (limit 0.9) -> below bar |
| single | S4_code_review | 失败 | 2.1e-05 | 1 | 1 | selected capability=0.60 (bar 0.78), load=0.10 (limit 0.9) -> below bar |
| single | S5_easy_general | 成功 | 2e-05 | 1 | 1 | selected capability=0.65 (bar 0.5), load=0.10 (limit 0.9) -> meets bar |
| single | S6_cost_sensitive | 失败 | 1.8e-05 | 1 | 1 | selected capability=0.55 (bar 0.65), load=0.10 (limit 0.9) -> below bar |
| fixed | S1_hard_coding_api | 失败 | 6.3e-05 | 1 | 1 | selected capability=0.45 (bar 0.85), load=0.10 (limit 0.9) -> below bar |
| fixed | S2_research | 失败 | 3.6e-05 | 1 | 1 | selected capability=0.50 (bar 0.8), load=0.10 (limit 0.9) -> below bar |
| fixed | S3_overloaded_expert | 失败 | 3.4e-05 | 1 | 1 | selected capability=0.98 (bar 0.85), load=0.95 (limit 0.9) -> below bar |
| fixed | S4_code_review | 失败 | 3.2e-05 | 1 | 1 | selected capability=0.60 (bar 0.78), load=0.10 (limit 0.9) -> below bar |
| fixed | S5_easy_general | 成功 | 3.3e-05 | 1 | 1 | selected capability=0.65 (bar 0.5), load=0.10 (limit 0.9) -> meets bar |
| fixed | S6_cost_sensitive | 失败 | 3.3e-05 | 1 | 1 | selected capability=0.55 (bar 0.65), load=0.10 (limit 0.9) -> below bar |
| round_robin | S1_hard_coding_api | 失败 | 3.5e-05 | 1 | 1 | selected capability=0.45 (bar 0.85), load=0.10 (limit 0.9) -> below bar |
| round_robin | S2_research | 成功 | 3.2e-05 | 1 | 2 | selected capability=0.93 (bar 0.8), load=0.15 (limit 0.9) -> meets bar |
| round_robin | S3_overloaded_expert | 失败 | 3.1e-05 | 1 | 3 | selected capability=0.50 (bar 0.85), load=0.00 (limit 0.9) -> below bar |
| round_robin | S4_code_review | 失败 | 3.1e-05 | 1 | 1 | selected capability=0.60 (bar 0.78), load=0.10 (limit 0.9) -> below bar |
| round_robin | S5_easy_general | 成功 | 3.1e-05 | 1 | 2 | selected capability=0.80 (bar 0.5), load=0.10 (limit 0.9) -> meets bar |
| round_robin | S6_cost_sensitive | 成功 | 3.2e-05 | 1 | 3 | selected capability=0.78 (bar 0.65), load=0.10 (limit 0.9) -> meets bar |
| adaptive | S1_hard_coding_api | 成功 | 3.3e-05 | 1 | 2 | selected capability=0.95 (bar 0.85), load=0.20 (limit 0.9) -> meets bar |
| adaptive | S2_research | 成功 | 3.1e-05 | 1 | 2 | selected capability=0.93 (bar 0.8), load=0.15 (limit 0.9) -> meets bar |
| adaptive | S3_overloaded_expert | 成功 | 3.1e-05 | 1 | 2 | selected capability=0.88 (bar 0.85), load=0.00 (limit 0.9) -> meets bar |
| adaptive | S4_code_review | 成功 | 3.4e-05 | 1 | 3 | selected capability=0.90 (bar 0.78), load=0.10 (limit 0.9) -> meets bar |
| adaptive | S5_easy_general | 成功 | 3.6e-05 | 1 | 1 | selected capability=0.65 (bar 0.5), load=0.10 (limit 0.9) -> meets bar |
| adaptive | S6_cost_sensitive | 成功 | 3.5e-05 | 1 | 3 | selected capability=0.78 (bar 0.65), load=0.10 (limit 0.9) -> meets bar |
| self_loop_off | R1_permission_bug | 失败 | 0.4236 | 1 | — | first_verify_passed=False; final_state=FAILED; repairs=[]; workspace=workspaces/R1_permission_bug__self_loop_off |
| self_loop_off | R2_validation_bug | 失败 | 0.4264 | 1 | — | first_verify_passed=False; final_state=FAILED; repairs=[]; workspace=workspaces/R2_validation_bug__self_loop_off |
| self_loop_off | R3_discount_bug | 失败 | 0.395 | 1 | — | first_verify_passed=False; final_state=FAILED; repairs=[]; workspace=workspaces/R3_discount_bug__self_loop_off |
| self_loop_on | R1_permission_bug | 成功 | 0.8034 | 2 | — | first_verify_passed=False; final_state=COMPLETE; repairs=["attempt 0: matched 'permission: admin_panel requires role == 'admin''"]; workspace=workspaces/R1_permission_bug__self_loop_on |
| self_loop_on | R2_validation_bug | 成功 | 0.7847 | 2 | — | first_verify_passed=False; final_state=COMPLETE; repairs=["attempt 0: matched 'validation: reject empty username and email without '@''"]; workspace=workspaces/R2_validation_bug__self_loop_on |
| self_loop_on | R3_discount_bug | 成功 | 0.8207 | 2 | — | first_verify_passed=False; final_state=COMPLETE; repairs=["attempt 0: matched 'pricing: total = sum(prices) * (1 - discount_rate)'"]; workspace=workspaces/R3_discount_bug__self_loop_on |

## 调度决策日志样例（真实输出摘录）

- `fixed` / `S1_hard_coding_api`：fixed baseline: lowest-id eligible agent 1
- `round_robin` / `S3_overloaded_expert`：round-robin baseline: rotation index 2 -> agent 3
- `adaptive` / `S3_overloaded_expert`：adaptive: agent 2 highest score 0.6315 (capability=0.88, success=0.9, load=0.0)
- `adaptive` / `S6_cost_sensitive`：adaptive: agent 3 highest score 0.536 (capability=0.78, success=0.82, load=0.1)
- `adaptive` / `S1_hard_coding_api`：adaptive: agent 2 highest score 0.6285 (capability=0.95, success=0.93, load=0.2)

Self-Loop 状态轨迹样例（开启组，真实 history）：

- `R1_permission_bug`：EXECUTE@0 -> VERIFY@0 -> ANALYZE@0 -> REPAIR@0 -> EXECUTE@1 -> REVERIFY@1 -> COMPLETE@1
- `R2_validation_bug`：EXECUTE@0 -> VERIFY@0 -> ANALYZE@0 -> REPAIR@0 -> EXECUTE@1 -> REVERIFY@1 -> COMPLETE@1
- `R3_discount_bug`：EXECUTE@0 -> VERIFY@0 -> ANALYZE@0 -> REPAIR@0 -> EXECUTE@1 -> REVERIFY@1 -> COMPLETE@1

## 结论

1. 调度：在相同的预声明质量门槛下，各模式成功率为——自适应 100%（6/6）、轮询 50%（3/6）、固定 17%（1/6）、单Agent 17%（1/6）。自适应在本任务集上为最高：它按能力匹配、历史成功率与负载加权选人（如 S3 中避开了负载 0.95 的饱和专家），且在成本敏感的 S6 中选择了达标且更便宜的 Agent 而非最贵的最强专家，说明成本/延迟项真实参与了决策。
2. Self-Loop：开启组 3/3 个缺陷任务最终通过全部 pytest（自动修复成功率 100%，分母为首次验证失败的任务数）；关闭组 0/3 通过，首次失败即为最终失败。
3. 全部数字均来自本次脚本的真实执行（见 results.json / results.csv），工作区代码与测试保留在 workspaces/ 可复查。

## 局限（如实说明）

- Part A 的“成功”是调度质量判定（被选 Agent 是否达到预声明的能力/负载门槛），不是让该 Agent 真实生成代码再评分；它衡量的是调度决策质量，不等同于端到端任务完成率。门槛与负载上限是实验设计参数，已在上文公开。
- Part A 耗时仅为调度函数本身的计算时间（微秒级），不包含任何模型推理时间，不能用于推断端到端延迟。
- Part B 的修复是**规则修复**：修复函数依据真实 pytest 失败输出中的关键词选择预置的正确实现，不是 LLM 生成的修复。它证明的是 Self-Loop 的“验证→分析→修复→再验证”闭环与可信验证机制真实有效，不证明系统能修复任意未知缺陷。
- 调度评分对成本/延迟使用候选集内的 min-max 归一化：池内最贵/最慢者恒被计满额惩罚（各 0.10 权重），与绝对差值无关。本实验的池参数已全部公开；若池中专家的成本/延迟相对同侪极端高，评分可能选到能力较弱者（S6 即展示了这一权衡的温和版本：选达标的便宜 Agent 而非最强专家）。
- 任务规模小（调度6个 + 修复3个），结论仅适用于本任务集；未做统计显著性检验。
- 本实验未调用 LLM，故无 Token 数据；涉及 LLM 的端到端实验需另行在配置模型 API 后运行。
