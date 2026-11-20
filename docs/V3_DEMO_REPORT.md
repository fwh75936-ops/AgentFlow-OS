# V3.0 演示报告（实测，2026-11-17）

脚本：`examples/v3-demo/run_v3_demo.py`，连续 3 次运行 exit 0。证据：`examples/v3-demo/evidence/`（summary.json / chief_report.json / execution_log.json）。

## 如实口径
- **llm_called=false**：总管规划为规则驱动（关键词拆分 `rule-based-keyword-v1`），无供应商 Key，未冒充 LLM 推理。
- **runtime=local-process**，env=shared-linux-1（单一共享环境）；本机以 root 运行系 VM 限制。
- Docker 未在本机验证（见未验证清单）。GUI 真实动作已于 2026-11-17 补验（见下方「Computer Use 补验」）。

## 实测流程（全部真实执行）
1. 总管组队：chief(id=1, tools=[] 无任何 Linux 操作工具) + 产品经理/开发/测试，team leader=总管。
2. SACAS 分配：开发持 git 资源租约 `repo:user-mgmt`，冲突风险注入评分，决策与 candidates（含 resource_conflict_risk/env_readiness/dependency_readiness）落 `scheduler_decisions`。
3. 开发写 user_api **v1（sha bb8f0a4e…，含权限 Bug）** → 测试真实 pytest **exit 1** → 登记验证（passed=false, v1）。
4. workspace_events 产生 `test_failed` → 主动引擎生成 **L2 建议** → 未批准前 execute 被 PermissionError 拒绝 → 批准后 executed。
5. 结构化交接 AgentHandoff（meta 含 environmentVersion={user_api.py: v1}）。
6. 开发修复写 **v2（sha 6ace00ab…）** → `check_freshness` 将 v1 验证置 **stale（flipped=1）**，且系统不再报告 v2 已验证。
7. 复验：pytest **exit 0** → 登记 v2 验证 **current**，`is_verified=True`。
8. 总管汇总报告：status=completed，artifacts 含版本/Hash/当前有效验证状态。

## A/B/C/D 实验（benchmarks/run_v3_benchmark.py，3 次重复×6 任务）
见 `benchmarks/v3_results.json|csv`、`v3_summary.md`（已同步到 `~/workspace/agentflow-submission/benchmarks/`）。
要点：A fixed 成功率 0.3333/冲突 6；B adaptive 0.1667/冲突 15（无资源感知时倾向选最强但被占用的 Agent）；C sacas 0.8333/冲突 0；D sacas+状态感知+Self-Loop 0.8333/冲突 0、过期验证检出 3/3、自动修复成功（平均重试 1.0）。Token/成本：未调用 LLM，记 0。

## Computer Use 补验（2026-11-17，三轮欠账清零轮次）
- 本机 apt 安装 xdotool（1:3.20160805.1-5build1）；`app/computer_use` 升级：有效 DISPLAY 自动取共享桌面 :1（VNC 真实运行时），新增 lease 门禁内的 screenshot 动作（scrot 真实截图）、带坐标 click、move 后回读指针位置校验。
- 真实测试 `tests/test_computer_use.py`（2 passed）：经后端桌面服务启动 TigerVNC+XFCE（:1）→ Agent 持桌面租约 → screenshot（before）→ 鼠标移动 (320,210) 并经 getmouselocation 回读一致 → click → xdotool type 在真实 xfce4-terminal 中键入 `echo AGENTFLOW_CU_PROOF > /tmp/agentflow_cu_proof.txt` 并回车，proof 文件真实生成且内容一致 → screenshot（after，像素与 before 不同）→ 用户 force-takeover 后 act 被 rejected。
- 证据截图：`docs/assets/computer-use-before.png`、`docs/assets/computer-use-after.png`（after 图中终端可见键入并已执行的命令）。

## 未验证清单
- Docker 镜像内运行未验证（本机无 Docker）。
- Teach & Learn 完整版、多人协作/语音为初赛后 P1/P2，本轮未做。
