# AgentFlow OS

**基于自适应调度与可信验证的多智能体桌面工作空间**

AgentFlow OS 把 AI Agent 当作员工来组织：你创建多名 AI 员工，给他们分配岗位、技能、工具和记忆，组建团队，再把复杂任务交给他们。系统负责拆解任务、按能力派单、监督执行、验证成果，并在出错时自动修复——全过程有记录、可复盘。

## 功能

- **AI 员工与团队**：每名员工有岗位职责、能力画像、可用工具和专属记忆；员工可组队，团队有负责人和群聊，群聊里 @ 谁谁接活，也可以把目标交给总管自动组织。
- **任务调度（SACAS）**：任务被拆成带依赖的任务图。派单先过硬约束（状态、工具、能力门槛），再按能力匹配、历史成功率、成本、延迟和当前负载综合评分选人；每次决策的评分构成都落库可查。执行结果会以指数移动平均反馈更新员工画像，派单越用越准。
- **可信验证**：员工说完成不算数。系统依次做结构验证、规则验证、工具验证（真实运行测试并保存输出）和独立评审，成果每次变更都会生成新版本，旧验证自动过期。
- **Self-Loop 自我修复**：验证不通过时进入修复循环——分析错误、重新执行、再次验证，有重试上限、时间预算和重复错误检测，不会无限打转。
- **工作流编排**：在画布上拖节点、连线，保存的工作流图会编译成任务图由同一执行器真实运行，不是画着看的图。
- **共享 Linux 工作区**：多名员工共用一台 Linux 电脑，各自有独立终端会话，图形桌面同一时间只由一名员工操作（控制权租约），文件带版本管理。
- **工具与扩展**：内置文件、终端、浏览器等工具，统一经过权限校验和高风险审批；支持接入 MCP 工具服务器和技能包。
- **知识记忆**：导入的文档会被分块索引，员工执行任务时自动检索相关片段作为上下文。
- **AI 工作室**：像素风的办公室视图，员工与真实 Agent 一一对应，谁在干活、谁在休息，看状态就知道。

## 下载与安装

Windows 发行版在 [Releases](https://github.com/fwh75936-ops/AgentFlow-OS/releases) 页面下载：

- `AgentFlow OS Setup 0.1.7.exe`：安装包，双击安装。
- `AgentFlow-OS-0.1.7-win.zip`：免安装版，解压后运行 `AgentFlow OS.exe`。

首次启动后，在「设置」页添加你的大模型供应商（接口地址 + API Key），即可创建员工并执行任务。

## 从源码运行

环境要求：Node.js 20+、Python 3.12+。

```bash
# 后端（Agent Core 服务）
cd services/agent-core
python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt
./.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
# 健康检查：curl http://127.0.0.1:8000/health

# 前端（仓库根目录）
npm install
npm run dev:web        # http://localhost:5173，/api 自动代理到 :8000
npm run dev            # 或以 Electron 桌面应用方式启动
```

上手顺序：设置页添加模型供应商并测试连接 → 创建 AI 员工（选模型、岗位、工具）→ 组建团队 → 在任务中心发布任务，或把目标直接交给总管。

## 架构

三层结构，桌面端不保存业务数据，全部状态以本地服务为准：

- **桌面端**（Electron + React TypeScript）：13 个功能页面，负责操作与展示。
- **Agent Core 服务**（FastAPI + SQLAlchemy + SQLite）：调度、执行、验证、自循环、总管、工作流、工具注册表、MCP 接入、知识记忆、团队群聊等模块，模块之间只通过数据库状态和明确接口衔接。
- **执行层**：模型调用走统一客户端（OpenAI 兼容接口）；工具调用统一过注册表做权限与审批；员工在共享 Linux 工作区里真实执行命令和操作文件。

## 技术栈

Electron · React 18 + TypeScript + Vite · Tailwind CSS + shadcn/ui · React Flow · Zustand ｜ Python 3.12 · FastAPI · SQLAlchemy · SQLite · Pytest

## 项目结构

```
apps/desktop/          Electron 主进程与 React 前端
services/agent-core/   FastAPI 后端（调度 / 执行 / 验证 / 自循环 / 工作区 / MCP / 记忆）
packages/              shared-types、workflow-schema
examples/              示例 MCP 服务器
docs/                  开发日志与路线图
```

## 配置

- 模型供应商在应用「设置」页里添加，Key 保存在本地数据库，不经过环境变量。
- 可选环境变量及默认值见根目录 `.env.example`。

## 测试

```bash
npm run test:core      # 后端 pytest
npm run build:web      # TypeScript 严格模式检查 + 前端构建
```

## 许可证

MIT（见 LICENSE）。
