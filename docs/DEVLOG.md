# AgentFlow OS — 开发日志（DEVLOG）

> 本文件只记录**真实执行过**的命令与结果。未验证的事项明确标注未验证，绝不编造。

## 2026-11-17 — P0 骨架搭建与验证

### 环境（实测）

- OS：Linux（VM）；Node v24.22.1；npm 10.9.4；无 pnpm（使用 npm workspaces）；Python 3.12.3；git 2.43；**无 Docker**。

### 后端（services/agent-core，FastAPI + SQLAlchemy + SQLite）

已实现：全部数据模型（ModelProvider / Agent / Team / TeamMember / Task / TaskNode / AgentMessage / SchedulerDecision / VerificationRecord / ExecutionSnapshot / Skill / MCPServer / MemoryItem / Workflow / EvaluationRun）；providers CRUD + 真实 `/models` 拉取；agents CRUD/复制/启停；teams CRUD；tasks 创建/列表/详情/取消/重试/执行；调度决策与验证记录查询；skills/mcp/memories/workflows/evaluations 基础 CRUD；工具注册表；WebSocket 任务状态端点 `/ws/tasks/{id}`。

命令与结果：

```bash
python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt   # 成功
./.venv/bin/python -m pytest tests/ -q
# 首轮：15 passed, 1 failed —— test_self_loop_terminates_at_max_retries 期望执行 3 次，
# 实际 2 次。原因：相同错误签名第二次出现时「重复错误检测」提前终止（设计行为），
# 测试预期写错。修正测试预期并注明原因后：
# 复跑：16 passed in 0.09s（最终一次复跑 16 passed in 0.20s）
```

真实 HTTP 冒烟（uvicorn 短暂启动后 curl）：

- `GET /health` → `{"status":"ok","service":"agentflow-agent-core","version":"0.1.0"}`
- `POST /api/agents` 创建 Planner → 201 并落库；`GET /api/agents` 可读回。
- `POST /api/tasks` → 自动生成 DAG（plan、research 无依赖；execute 依赖 plan；verify 依赖 execute+research），节点真实持久化。
- `POST /api/providers`（base_url 指向不可达的 127.0.0.1:9）后 `POST /api/providers/1/fetch-models` → **HTTP 502**，如实返回上游连接错误，未伪造模型列表。
- `POST /api/tasks/1/execute`（无模型供应商）→ 任务 `status=failed`、`error_type=MODEL_ERROR`；research 节点因无符合能力硬约束的 Agent 记 `DEPENDENCY_ERROR`；落库 4 条 SchedulerDecision（含候选分数与选择原因，如 adaptive 总分 0.545）与 4 条 AgentMessage（TASK_ASSIGNMENT/ERROR_REPORT）；`GET /api/stats` 由真实行数计算。

### 冒烟中发现并修复的两个真实 Bug

1. **并行节点共用同一 SQLAlchemy Session** → `sqlite3.IntegrityError: UNIQUE constraint failed: scheduler_decisions.id`，执行接口 500。修复：每个节点执行使用独立 Session（`_run_node` 按 task_id/node_id 自建会话），并行仍 via `asyncio.to_thread`。
2. **失败节点在同一轮 DAG 内被无限重跑**：已失败的无依赖节点每轮都被当作 ready 再次执行，接口挂死 >90s。修复：单轮内只运行 `pending` 节点，失败节点由 Self-Loop 在下一轮尝试前统一重置。修复后执行接口约秒级返回上述如实失败结果。
   （另：任务失败信息已改为透出根因节点的 error_message，而不是 Self-Loop 的通用描述。）

### 前端（apps/desktop，React TS + Vite）

- AgentFlow Pure Light配色已落地为 CSS 变量（背景 #FFFFFF、侧边栏 #F7F7F5、边框 #E8E8E5、主文字 #202020、次级 #777777、主按钮 #242424、成功 #238B61、警告 #C58A32、错误 #D9534F、信息 #3978C6；卡片圆角 12px、按钮 8px、极浅阴影、Lucide 图标）。
- 12 个页面路由齐全：工作台、AI 员工、AI 团队、任务中心、工作流、AI 电脑、Skills、MCP、知识库、可信验证、性能评测、设置；列表/表单均接真实 API 并处理 Loading/Error/Empty，无写死假数据。
- 工作流页使用 `@xyflow/react`（已成功安装，无需降级为自研 SVG 画布）：可拖拽增删节点、连线、缩放平移，保存前校验 Start/End/连通性，图结构保存/加载到后端。
- 命令与结果：

```bash
npm install            # 仓库根 workspaces 提升依赖，成功
npm run build          # 首轮失败：TS2339 Property 'env' does not exist on type 'ImportMeta'
                       # 修复：新增 src/vite-env.d.ts 引入 vite/client 类型
npm run build          # 复跑成功：tsc --noEmit（strict）无错误；vite build 1616 modules，
                       # dist/assets/index-*.js 374.34 kB（gzip 121.28 kB），css 20.42 kB
```

### Electron

- main/preload/services 三个 JS 文件通过 `node --check` 语法检查；安全配置：contextIsolation=true、nodeIntegration=false、sandbox preload、仅暴露 `getBackendUrl`/`selectFile` 两个受限 IPC、CSP 注入；pythonService 负责以 `python -m uvicorn` 启动/停止后端，找不到时明确报错。
- electron 二进制已安装（v33.4.11）；本 VM 以 root 运行时需 `ELECTRON_DISABLE_SANDBOX=1` 才能打印版本（无头环境限制），**未做窗口级人工验收**。

### 明确未完成 / 未验证

- ❌ **Windows 安装包未构建、未验证**（当前为 Linux 环境；electron-builder 配置已写好，待 Windows 机器执行 `npm run dist:win`）。
- ❌ **Docker 沙箱未实现/未验证**（本机无 Docker；当前为本地受限进程执行器，文件工具限制在 data/workspaces 内）。
- ❌ 未配置真实模型供应商做端到端 LLM 成功执行（无可用 API Key）；无供应商路径已验证为如实失败。
- ⚠️ 系统托盘、自动更新、浏览器执行视图、向量检索、批量实验运行器未实现（见 ROADMAP 的 P1/P2）。
- 验证用 agentflow.db 已删除，交付为干净骨架（首次启动自动建表，不含种子假数据）。

---

## 第二轮：Shared Linux Workspace（2026-11-17）

增量开发，未重建项目、未改全局 UI、未重写 Runtime/调度/验证/Self-Loop，数据库仅新增表。

### 后端（services/agent-core/app/workspace/）
- 新增 7 张表：workspace_sessions、workspace_command_logs、desktop_leases、desktop_lease_queue、workspace_events、shared_file_meta、shared_file_locks。已验证在旧库上 create_all 仅新增表、不动旧数据。
- 单一共享环境 env_id=shared-linux-1：逻辑根 /workspace，本地映射 data/shared_workspace（AGENTFLOW_SHARED_ROOT 可覆盖）；runtime_kind 如实报告 local-process / docker。
- 终端会话：每 Agent 独立会话（独立 cwd + 会话锁串行、跨会话真并发），命令经 bash 执行并施加 RLIMIT_CPU/AS/NOFILE；全部命令落审计日志（执行者、任务、时间、退出码、输出）。
- 共享桌面租约：单持有者 + FIFO 等待队列 + 心跳续租 + 超时惰性过期 + 用户强制接管，全部状态变更进协作事件流。
- 共享文件：版本+SHA256 乐观并发（冲突 409）、咨询性文件锁、文件监听线程（2s 轮询，外部变更也产生事件）。
- 安全：高风险命令正则拦截（sudo/docker/mount/rm -rf / 等），未经 approved=true 一律 blocked 落日志；路径逃逸拒绝；exec/写文件/桌面控制均校验 Agent 的统一 Tool Registry 工具权限（403）。
- Tool Registry 新增 5 个工具：linux_exec（高风险+需审批）、linux_file_read/write/list、desktop_control，处理器委托 workspace service，与 REST API 同一代码路径。
- 桌面运行时（desktop.py）：按真实二进制探测 XFCE/TigerVNC/websockify/noVNC，缺失时状态 unavailable 并列明缺失项，不伪造运行。

### 前端
- 导航新增 Linux Workspace 页（三栏：员工/会话 | 桌面+终端 | 协作事件/文件/任务日志），复用现有 AgentFlow Pure Light设计令牌与 .card/.btn/.badge 组件，仅新增 .ws-* 布局类。注：现有设计系统实测为 AgentFlow Pure Light（项目自有配色）（globals.css），本轮按「不重新设计全局 UI」保留，未引入新组件库。
- 构建：npm run build:web 通过（387.43 kB JS / gzip 125.07 kB）。

### 测试
- 新增 tests/test_workspace.py（8 例）+ tests/test_workspace_acceptance.py（三 Agent 同环境协作全流程：调度分配→开发建项目→测试失败→AgentMessage 通知→Self-Loop 修复→pytest 复验通过→PM 查看成果）。
- 全量 pytest：25 passed（原 16 + 新 9）。另以真实 uvicorn 进程对全部 /api/workspace 接口做过 HTTP 实测（seed/status/session/exec/权限 403/高风险拦截/文件 409/租约/强制接管/事件流均符合预期）。

### 桌面实测（同日补充）
- 本机 apt 换源后装齐 xfce4-session/websockify/novnc/tigervnc-tools/scrot。经真实 API 实测：POST /desktop/start → Xtigervnc :1（-localhost）+ xfce4-session + websockify 全部真实进程在跑，noVNC vnc.html HTTP 200，VNC 握手横幅 RFB 003.008，scrot 截圖存 docs/assets/linux-workspace-desktop.png（XFCE 面板/图标/时钟可见）；POST /desktop/stop 后进程清零。
- 实测中修复两个真实 bug：Ubuntu 的 vncpasswd 在 tigervnc-tools 包（已补进 Dockerfile）；vncpasswd 输出为二进制，text 模式解码崩溃，改二进制读写。

### 明确未验证
- ❌ Docker 镜像未构建（本机无 Docker）：docker/linux-workspace/ 提供 Dockerfile+compose（非 root、资源限制、不挂 docker.sock、VNC 不发布、noVNC 仅 127.0.0.1），待有 Docker 的机器验证。
- ⚠️ 本机后端进程为 root（VM 运行时用户），非 root 仅在 Docker 镜像内成立，状态接口 is_root 字段如实标注。

## 端到端三员工演示（2026-11-17 补跑固化）

第二轮验收清单中唯一缺持久证据的叙事级演示，现已补齐。此前 `tests/test_workspace_acceptance.py` 仅在临时目录以 calc 项目跑通，本次按验收叙事重做并固化：

- 新增可重复脚本 `examples/e2e-demo/run_e2e_demo.py`：产品经理（无 linux_exec，Tool Registry 实测 PERMISSION_ERROR 拒绝）/ 开发 / 测试三员工同连 shared-linux-1、各自独立会话；开发在共享目录建 user_api 项目并注入权限 Bug（admin_stats 缺角色校验）；测试会话内 pytest 真实失败（exit 1，DID NOT RAISE PermissionError）；Self-Loop 驱动 VERIFY→ANALYZE→REPAIR（AgentMessage ERROR_REPORT 通知开发→开发重写修复）→REVERIFY→COMPLETE；测试复验 3 passed（exit 0）并落 VerificationRecord；PM 只读查看成果（user_api.py v2，sha256=a03b151777f7…）；桌面租约申请/排队/释放无冲突；审计日志 4 条齐全。
- 证据全部落盘 `examples/e2e-demo/evidence/`（execution_log / pytest 前后原始输出 / self_loop_history / audit_log / workspace_events / final_artifacts / summary）；报告见 `docs/E2E_DEMO_REPORT.md`。
- 实测：脚本连续 3 次运行均 exit 0，修复后文件 Hash 三次一致；后端全量回归 25 passed，无回归。本次未改前端，未重跑 build。
- 如实标注：未调用 LLM（无供应商 Key，员工决策与修复内容由脚本写定，不冒充智能推理）；环境为本地受限进程执行器（root），Docker/XFCE/VNC 真实桌面操作未验证（仅租约逻辑）。

## 三轮欠账清零工程（2026-11-17）

逐项把前三轮「明确未验证/后续迭代」欠账清零，全部增量实现，结案报告见 `~/workspace/agentflow-round3/COMPLETION_REPORT.md`。

- 基线复验：pytest 37 passed、前端 build、e2e 与 v3 demo 各 exit 0 后开工。
- Computer Use 真实 GUI：装 xdotool 升级 `app/computer_use`（有效 DISPLAY 自动取共享桌面 :1、租约门禁、move 回读校验、click、高风险组合键未批准返回 awaiting_confirmation）；`tests/test_computer_use.py` 2 passed，真实终端打字执行命令落盘为证，截图 `docs/assets/computer-use-{before,after}.png`。
- Reviewer 交叉验证（验证第四层）：新表 `ReviewVerification` + `app/verification/reviewer.py`（reviewer 须具 review 能力且 ≠ 作者、成果变版联动 stale）；4 passed。
- 工作流图驱动执行：`app/workflows/engine.py`（图编译→TaskNode DAG→既有 runner；工具节点经 Tool Registry 按被分配 Agent 权限执行；环/不支持类型拒绝）；3 passed。
- 团队群聊 + @Agent：`app/teams/chat.py`（@ 解析为结构化路由字段并生成 MENTION 定向消息）；前端 Teams 群聊面板；4 passed。
- 知识库检索：`app/memory/search.py`（父子分块 + FTS5 BM25 + 手写 TF-IDF 余弦混合，对外口径如实标注「关键词+TF-IDF 混合检索，非神经嵌入」）；4 passed。
- MCP 真实链路：`app/mcp/client.py`（stdio JSON-RPC：initialize→notifications/initialized→tools/list 分页→tools/call；pending-request 映射、stderr 分离、命名空间 `mcp.<server>.<tool>` 注册进 Tool Registry）；随仓示例 server `examples/mcp-server/server.py`；4 passed。
- chief 停滞信号：给总管加 `assess_stagnation` 纯信号函数并写入报告 `stagnation` 字段，不动调度引擎；4 passed。
- 非 root 实证：新建用户 agentflow（uid 1000）独立 venv 启动后端，health ok、`is_root: False`、会话 exec whoami=agentflow（日志 `agentflow-round3/nonroot-run.log`）。
- Docker 尝试：docker.io/compose 安装成功；默认 dockerd 因 VM 无 CAP_NET_ADMIN 失败，降级参数可启动，但 Docker Hub 拉取 ubuntu:24.04 TLS 超时，镜像未构建（证据 `agentflow-round3/docker-attempt.log`）。
- Electron 窗口级验收：显示 :1 实拍 `docs/assets/electron-window.png`；过程中真修 5 个 Bug（pythonService 路径少算一层、vite base 改相对、file: 协议 API 回退 127.0.0.1、CORS 加 "null" 源、preload 改 CommonJS .cjs）。已知竞态：窗口先于后端就绪时首批请求失败停在错误态，刷新恢复（未做自动重试）。
- Windows 安装包尝试：win-unpacked 真实产出（exe 约 189 MB）；NSIS 安装包被 wine32:i386 多架构依赖冲突挡死（188 KB 文件为未完成 stub，不冒充安装包），未在 Windows 上安装验证（证据 `agentflow-round3/dist-win-attempt.log`）。
- 托盘 + 自动更新：主进程 Tray（显示/隐藏/退出）；electron-updater 仅在打包且配置 AGENTFLOW_UPDATE_URL 时启用，否则如实 disabled 并在 README 标注。
- 前端合并「AI 电脑」：导航只保留一个入口（原 Linux Workspace 页更名 + Tab：工作区/共享桌面/工具与权限/命令审计），未删任何功能与接口。截图过程中真修一 Bug：加载经 API 保存、无 position 字段的工作流图会让 React Flow 崩溃白屏，`WorkflowPage` 加载时为缺失 position 的节点合成级联布局。
- 材料回填：proposal 补 V3.0 A/B/C/D 实验一节并重导出 PDF（9425 汉字 / 19 页）；PPT 第 14 页回填 V3 数据（仍 15 页）；video/script.md 补第 8 段 V3.0 协作（总时长 4:55）；CHECKLIST 全面更新。主题名全量统一为「AgentFlow Pure Light」（用户自有配色），相关文件 grep「Codex」=0。
- 完工截图组：`agentflow-submission/screenshots/01–09`（真实启动 + 真实后端数据；播种的工作流执行 completed、Reviewer 复核记录、SACAS 决策均真实落库后拍摄）。
- 全量回归：pytest **62 passed**（基线 37 + 新增 25）、前端 build 通过、e2e/v3 demo exit 0、两轮 benchmarks 复跑数字精确复现。
- 仍阻塞（不伪造）：LLM 成功路径实测需用户提供 API Key；Windows 安装验证需 Windows 真机；仓库发布需用户账号。

## AI 工作室（像素办公室）— 工作台改造（2026-11-17）
- 「工作台」页（路由 key 不变）改造为「AI 工作室」：Phaser 3.90（MIT，按需分片加载）渲染 46×30 像素办公室（总管办公室/开发/测试/数据/会议/休息六区），全部素材程序化原创绘制，无第三方素材。
- 角色与真实 Agent 一一对应：状态由后端 activity_status（idle/model_calling/tool_calling/running/failed/disabled）+ 任务节点（plan/verify/display_status/retry_count）派生（映射表见 features/pixel-studio/events/AgentEventAdapter.ts），A* 寻路、到达后播对应动作；调度决策出现时总管走向被分配员工交接；验证/重试/租约事件进底部时间线。数据通道：2.5s REST 轮询 + 现有单任务 WebSocket 仅作加速刷新信号；隐藏页（保活池）暂停轮询并 sleep 场景。
- 原工作台功能保留：统计进顶部 chips、任务分布/最近任务/最近验证进底部「数据概览」页签、共享电脑状态进右侧面板。
- 验证：tsc/vite build 通过（主包 861KB，Phaser 分片 1.5MB 按需）；Electron+Xvfb 实测截图 docs/assets/pixel-studio.png（真实 4 名员工、真实统计 60%/220477 Token），控制台 0 错误；空闲员工真实步行至休息区、总管 failed 状态如实显示。FPS：无头离屏软件渲染 rAF 14（本 VM 无 GPU，不代表真机），真机未测。
- 人物模型升级（2026-11-17 晚）：角色画布 16×24 → 24×32 重绘——完整脸部（眼/眉/鼻/嘴、部分角色眼镜）、5 种发型（短发/偏分/长发/丸子头/卷发）、带衣领门襟纽扣与明暗 shading 的服装、脚下椭圆阴影；行走改为三帧循环（stride/passing/stride + 手臂摆动）。同时修复移动速度 bug：原用 Phaser 平滑后的 step delta，低帧率环境下人物只有约 1/12 速度；改用真实时钟（100ms 钳制）后探针实测三名空闲员工 9 秒内走完 16~24 步 A* 路径到达休息区。
- 美术升级（2026-11-17 晚，研读 Pixel Agents 等开源像素办公室后原创重绘）：学到并落地四招——①深色描边：人物改四向轮廓描边渲染（先画纯色剪影再画本体），家具加描边框；②接触阴影：家具与人物脚下统一软阴影；③配色纵深：墙体改深板岩灰带、地面改木板拼接地板（板缝+错缝+逐板色阶）、会议/测试/数据/休息区改低饱和彩色地毯，会议桌与休息区加地毯垫；④墙面道具：总管办公室挂钟、会议区三幅挂画、开发区海报、分区名改白底铭牌。注意：Pixel Agents 的人物家具美术源自第三方素材包 JIK-A-4「Metro City」（非 MIT 覆盖），只学技法、未使用其任何素材，本项目全部素材仍为程序化原创绘制。
- AI 团队群聊协作中心（2026-11-18）：Teams 页重构为三栏——左团队列表（团队身份色块+成员状态汇总+最近消息预览+未读数，本机已读位置存 localStorage）、中群聊、右成员栏（真实 activity_status，点成员经 appStore.openStudio 深链跳 AI 工作室并定位）+群聊纪要（置顶消息+最近工作卡）。后端新增 app/teams/dispatch.py 三模式：指定模式 @成员（前端 @菜单提交结构化 mentions，后端正文解析兜底）为每位被@成员建真实 Task（单节点「执行」钉选该成员，后台线程跑 runner，run_task 传 required_output_fields=["执行"] 过结构验证）并回 TASK_CARD/RESULT_CARD（结果卡摘要取节点 output 的 summary）；总管模式接 chief create_mission 并回汇报卡（节点明细）；讨论模式成员按负责人优先顺序用各自供应商/模型经 llm.client 真实发言一轮（无模型发 SYSTEM 说明并停，不编造）。防循环：仅用户消息触发 dispatch。chat 增量：meta 存 mode/reply_to、置顶切换、GET channel/summary 抽取式纪要。实测：@苏晴 任务 #20 真实完成并回卡、@陈工 #19 曾因验证字段不符失败（已修，失败卡如实保留）、讨论话题获林晓真实长回复；pytest 125 passed+1 skipped（旧 test_team_chat 计数按新契约更新：@消息另产任务卡）；tsc/build 通过，Electron 实拍 docs/assets/team-chat.png 控制台 0 错误。期间后端进程曾无日志退出一次（疑 VM 资源回收），讨论后两位成员发言随进程丢失、未补造。
- 群聊观感重做 + Markdown 渲染（2026-11-18，用户指出「不像真实聊天、简陋」与「markdown 裸文本」）：引入 react-markdown 9 + remark-gfm（均 MIT）做共享组件 components/Markdown.tsx（紧凑样式、代码块/表格/引用齐全、禁原始 HTML），群聊消息与工作卡内容改走 Markdown（自己的深色气泡配 tone="dark" 变体）。聊天区按真实聊天软件重排：浅灰消息区、成员白色左气泡（圆角缺口朝头像侧）、我的深色右气泡、5 分钟间隔时间分隔丸、2 分钟内同人消息合并（不重复头像/名字）、引用沉进气泡内、引用/置顶按钮悬停才出现、任务/结果/总管卡居中成通知卡、底部圆角输入框（@ 按钮唤成员菜单、胶囊发送键）。实拍 docs/assets/team-chat.png：林晓回复中的 Markdown 表格已正确渲染，控制台 0 错误，tsc/build 通过。
