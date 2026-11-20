# 端到端三员工演示报告（第二轮验收 · 叙事级演示）

- 日期：2026-11-17（Asia/Shanghai）
- 脚本：`examples/e2e-demo/run_e2e_demo.py`（可重复运行，每次重建演示 DB 与共享目录）
- 运行命令：`cd services/agent-core && ./.venv/bin/python ../../examples/e2e-demo/run_e2e_demo.py`
- 实测结果：连续运行 3 次，均 exit 0、全步断言通过；修复后 `user_api.py` 的 SHA-256 三次一致（`a03b151777f7…`）
- 证据目录：`examples/e2e-demo/evidence/`

## 真实性声明（必读）

- 本演示是**工具/会话级真实执行**：所有命令经 workspace service 在真实 bash 子进程中执行，文件真实落盘，pytest 真实运行，消息/租约/审计全部落 SQLite 可查。
- **未调用 LLM**：本机没有配置任何模型供应商/API Key。员工的“决策”（谁修、怎么修）由演示脚本按流程写定，修复代码内容也是脚本预置的——不冒充智能推理。调度分配则真实调用了现有 Scheduler 的 adaptive 评分。
- 运行环境为本机 Linux 的本地受限进程执行器（`runtime_kind=local-process`，进程用户为 root，VM 限制），**不是** Docker/XFCE/VNC 桌面环境；桌面部分只验证了租约逻辑，没有真实图形桌面操作。

## 逐步记录

| 步 | 执行者 | 会话 | 动作 / 命令 | 结果 | 证据 |
|---|---|---|---|---|---|
| 1 | 系统 | — | 创建产品经理#1、开发工程师#2、测试工程师#3、团队#1、任务#1 | 成功。PM 工具仅 `linux_file_read/linux_file_list`（无 `linux_exec`）；开发/测试为完整 Linux 工具集 | `evidence/execution_log.txt`、`evidence/summary.json` |
| 1b | Scheduler（adaptive） | — | `select_agent`：coding→开发（score 0.56），review→测试（score 0.551） | 与预期一致，2 条 SchedulerDecision 落库 | `evidence/execution_log.txt` |
| 1c | 产品经理 | — | 经 Tool Registry 尝试 `linux_exec`（approved=True，排除审批因素） | **被拒绝**：`PERMISSION_ERROR: agent lacks permission for tool: linux_exec`，权限差异真实生效 | `evidence/summary.json`（`pm_permission_denied`） |
| 2 | 三人 | PM#1 / 开发#2 / 测试#3 | `service.create_session` 各开独立终端会话 | 三个会话 `env_id` 均为同一台 `shared-linux-1`；后续实测测试会话 cwd=`user_api`、开发会话 cwd=根目录，互不干扰 | `evidence/workspace_events.json`（`session_created`×3） |
| 3 | 开发工程师 | #2 | 经 `write_file` 在共享目录建项目 `user_api/user_api.py`（v1, sha256=`a142247f6a85…`，含故意注入 Bug：`admin_stats` 缺角色校验）+ `test_user_api.py`（3 个测试）；会话内 `ls -la user_api` | 文件真实落盘，`ls` rc=0 | `evidence/dev_ls_output.txt`、共享目录 `examples/e2e-demo/shared_workspace/user_api/` |
| 4 | 测试工程师 | #3 | 先读同一份共享文件确认 Bug 存在；`cd user_api` 后执行 `python -m pytest -q` | **真实失败**：exit_code=1，`test_regular_user_cannot_access_admin_stats` — `Failed: DID NOT RAISE PermissionError`（1 failed, 2 passed） | `evidence/pytest_before_fix.txt`（pytest 原始输出） |
| 5 | 测试→开发 + Self-Loop | — | Self-Loop（现有引擎未修改）：EXECUTE→VERIFY（失败）→ANALYZE→REPAIR→EXECUTE→REVERIFY→COMPLETE。REPAIR 中测试经真实 `AgentMessage`（ERROR_REPORT）通知开发，开发重写 `user_api.py` 加入 `role != "admin"` 校验（v2） | Self-Loop 终态 `COMPLETE`；ERROR_REPORT 落库 1 条（sender=测试#3, receiver=开发#2） | `evidence/self_loop_history.json`、`examples/e2e-demo/demo.db` 的 `agent_messages` 表 |
| 6 | 测试工程师 | #3 | 会话内重跑 `python -m pytest -q`；另存 VerificationRecord（tool 验证） | **全部通过**：exit_code=0，`3 passed`；VerificationRecord passed=True, score=1.0；测试写 `user_api/test-report.txt` | `evidence/pytest_after_fix.txt` |
| 7 | 产品经理 | #1（只读） | `read_file` 查看最终 `user_api.py`（v2, sha256=`a03b151777f7…`，含修复校验）与 `test-report.txt`（含 `exit_code: 0`）；`list_files` 清点成果 | 成果确认存在，共 3 个成果文件（代码/测试/报告）带版本与 SHA-256 | `evidence/final_artifacts.json` |
| X1 | 测试→开发 | — | 桌面租约：测试 `acquire`（held, lease#1）→ 开发 `acquire`（waiting, 队首）→ 测试 `release` → 开发被授予 → 开发 `release` | 全程单持有者、**无冲突**，4 类租约事件齐全 | `evidence/workspace_events.json` |
| X2 | 系统 | — | 审计日志核查（任务#1） | 4 条命令记录，每条含执行者、会话、任务、开始时间、退出码、状态；16 条 workspace 事件 | `evidence/audit_log.json`、`evidence/workspace_events.json` |

## 与已有验收测试的关系

`services/agent-core/tests/test_workspace_acceptance.py`（第二轮已有）用 calc 小项目在临时目录里跑同一叙事，属测试级证据、跑完即焚。本演示按验收要求补齐：换成 user_api 权限 Bug 项目、证据持久化落盘、PM 权限拒绝单独实证、成果 Hash 留证，且脚本可独立重跑。两者都通过。

## 回归

- 后端全量 pytest：**25 passed**（与此前基线一致，无回归）
- 前端：本次未改动前端代码，未重跑 build（第二轮构建记录见 DEVLOG）

## 仍未验证

- ❌ Docker 镜像构建与容器内运行（本机无 Docker）；XFCE/TigerVNC/noVNC 真实桌面操作未在本演示中执行（仅租约逻辑）。
- ❌ LLM 驱动的自主规划/修复（无 API Key）；接入供应商后需另跑一轮“LLM 版”演示。
- ⚠️ 本机进程为 root；非 root 仅 Docker 镜像内成立。
