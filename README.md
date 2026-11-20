# AgentFlow OS

**基于自适应调度与可信验证的多智能体桌面工作空间**
Self-Evolving Multi-Agent Desktop Workspace

让用户创建多个 AI 数字员工，为其分配角色、技能、工具、记忆与执行环境，通过自然语言或可视化工作流分配复杂任务；系统自动规划任务 DAG、按能力画像调度 Agent、执行工具、验证结果、修复错误，并记录全过程。

## 核心创新

1. **自适应调度**：硬约束过滤（状态/工具/能力）+ 归一化综合评分（能力匹配、历史成功率、可用性、优先级适配 − 成本 − 延迟 − 负载），每次决策落库可解释；执行反馈用指数移动平均更新 Agent 画像。提供 fixed / round_robin / adaptive 三种模式做对比实验。
2. **可信验证**：四层验证均已实现并落库——结构验证（JSON Schema/必填/文件存在）、规则验证、工具验证（真实运行 pytest 并保存输出证据）、Reviewer 交叉验证（独立评审复核产出与证据，并与状态感知验证联动）。
3. **Self-Loop 自我修复**：EXECUTE→VERIFY→ANALYZE→REPAIR→REVERIFY→COMPLETE/FAILED 状态机，最大重试/时间预算/重复错误检测，禁止无限循环。

## 技术栈

Electron（contextIsolation、受限 preload、CSP）+ React TypeScript + Vite + React Flow + Zustand ｜ Python 3.12 FastAPI + SQLAlchemy + SQLite + Pydantic ｜ OpenAI 兼容 API ｜ 测试：Pytest / TypeScript strict。桌面端共 13 个功能页面，全部接入真实后端 API。

## 目录

```
apps/desktop/          Electron(main/preload) + React 前端(src: app/pages/components/stores/services/styles/types)
services/agent-core/   FastAPI 后端(app: api/scheduler/verification/self_loop/execution/tools/llm/models/schemas)
packages/              shared-types、workflow-schema
docs/                  DEVLOG.md（真实开发与验证记录）、ROADMAP.md（P0/P1/P2 倒排）
examples/              比赛演示任务说明（5 Agent 协作开发 + 权限 Bug 故障注入）
```

## 快速开始（开发模式）

```bash
# 后端
cd services/agent-core
python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt
./.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
curl http://127.0.0.1:8000/health

# 前端（仓库根目录执行 npm install，workspaces 会提升依赖）
cd ../.. && npm install
npm run dev:web        # http://localhost:5173（/api 自动代理到 :8000）

# 测试
npm run test:core      # pytest（当前 64 个用例全部通过）
npm run build:web      # tsc --noEmit (strict) + vite build
```

使用流程：设置页添加模型供应商 → 测试连接/拉取模型 → 创建 Agent（选模型、能力、工具）→ 组建团队 → 在任务中心创建并执行任务 → 在可信验证页查看调度决策与验证证据。

## 真实性原则

禁止假数据冒充功能：没有可用模型供应商时，任务执行会明确失败并记录 `MODEL_ERROR`；未运行的实验指标显示「未测试」；Docker 已安装并完成两轮尝试（基础镜像拉取成功，受虚拟机命名空间权限限制容器无法启动，镜像构建与容器运行未完成验证）；Windows 免安装版 win-unpacked 已真实产出（约 189 MB），NSIS 安装包未产出、未在 Windows 真机验证安装（见 docs/DEVLOG.md、docs/ROADMAP.md）。

## 环境要求

Node.js 20+（开发实测 v24.22.1）+ npm｜Python 3.12+｜（可选）Electron 桌面运行环境。Docker 与 Windows 打包为可选部署方式，见下方已知问题。

## 系统架构（简述）

Electron 主进程拉起本地 Python 服务（FastAPI，`127.0.0.1:8000`）→ React 前端经 HTTP/WebSocket 调用 → 后端内含调度引擎（`app/scheduler`）、验证引擎（`app/verification`）、Self-Loop（`app/self_loop`）、执行器（`app/execution`）、工具注册表（`app/tools`）与共享 Linux 工作区（`app/workspace`，多 Agent 共用同一环境、各自独立终端会话），状态统一落 SQLite。详细设计见技术方案书与 `docs/`。

## 配置说明

- 模型供应商：在应用「设置」页添加（base_url + API Key，存数据库），不使用环境变量传 Key。
- 环境变量：全部可选项及默认值见仓库根目录 `.env.example`（只列代码真实读取的变量；后端不自动加载 `.env`，需 export 后启动）。

## 演示与实验

- 比赛演示任务（5 Agent 协作开发 + 权限 Bug 故障注入 + 共享工作区/调度对比演示步骤）：见 `examples/demo-task.md`。
- 对比实验（调度四模式 × Self-Loop 开关，真实运行）：`benchmarks/run_benchmark.py`，结果与方法见 `benchmarks/summary.md`、复现说明见 `benchmarks/README.md`。

## 已知问题

- 当前开发环境为 Linux：Docker 已安装并两轮尝试，基础镜像（ubuntu:24.04）拉取成功，但容器因虚拟机命名空间权限（setns 被拒）无法启动，镜像构建/容器运行未验证；Windows 免安装版 win-unpacked 已产出，NSIS 安装包（`npm run dist:win`）未产出、安装未在真机验证，需在对应环境补验（见 `docs/DEVLOG.md`、`docs/ROADMAP.md`）。
- 未配置可用模型供应商时，依赖 LLM 的任务会明确以 MODEL_ERROR 失败（预期行为）；不依赖 LLM 的共享工作区与 benchmarks 实验可独立运行。
- 系统托盘已接入（显示/隐藏/退出菜单）；自动更新已预留 electron-updater 接入点，但**当前无发布源，默认禁用**：仅当打包后的应用设置了 `AGENTFLOW_UPDATE_URL`（真实 generic 更新源）时才启用检查，未配置时主进程日志会如实打印 disabled。

## 许可证

MIT（见 LICENSE）。参考项目（Grok Bot / Dify / LangGraph / CrewAI 等）仅借鉴功能设计与架构思想，不复制其界面、商标或代码。
