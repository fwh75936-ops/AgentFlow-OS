import { memo, useCallback, useEffect, useRef, useState, type CSSProperties } from "react";
import { ReactFlow, Background, Controls, MiniMap, Handle, Position, addEdge, useNodesState, useEdgesState, type Node, type Edge, type Connection, type ReactFlowInstance, type NodeProps } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { Bot, Flag, GitFork, Play, ShieldCheck, UserCheck, Wrench, type LucideIcon } from "lucide-react";
import { api } from "../services/api";
import { fmtAgo } from "../lib/time";
import { AnimatedModal, useAsync, activityLabel, agentTitle, capLabel, roleLabel, AgentStatusDot } from "../components/ui";
import type { Agent } from "../types";
import { useAppStore } from "../stores/appStore";
import { usePageVisible } from "../motion/visibility";

const NODE_KINDS = ["Start", "Agent", "Tool", "Condition", "Verification", "HumanApproval", "End"] as const;
const KIND_LABELS: Record<string, string> = {
  Start: "开始", Agent: "员工", Tool: "工具", Condition: "条件判断",
  Verification: "验证", HumanApproval: "人工审批", End: "结束",
};
function displayNodeLabel(label: string): string {
  const m = /^(Start|Agent|Tool|Condition|Verification|HumanApproval|End)([：:]\s*(.*))?$/.exec(label);
  if (!m) return label;
  const zh = KIND_LABELS[m[1]];
  return m[3] ? `${zh}：${m[3]}` : zh;
}
function kindOfLabel(label: string): string {
  const m = /^(Start|Agent|Tool|Condition|Verification|HumanApproval|End)\b/.exec(label);
  return m ? m[1] : "Agent";
}
function detailOfLabel(label: string): string {
  const shown = displayNodeLabel(label);
  const i = shown.indexOf("：");
  return i >= 0 ? shown.slice(i + 1) : "";
}
let seq = 3;

/* Node-kind colors come from the shared category palette (the --cat-*
 * tokens in globals.css / `cat` group in tailwind.config.js) so the
 * canvas speaks the same color language as the rest of the app:
 * green start, blue agent, teal tool, violet condition, gold
 * verification, rose human approval, slate end. */
const KIND_META: Record<string, { tone: string; color: string; tint: string; Icon: LucideIcon }> = {
  Start: { tone: "cat-green", color: "#238B61", tint: "#E3F2EC", Icon: Play },
  Agent: { tone: "cat-blue", color: "#3978C6", tint: "#E7EFF9", Icon: Bot },
  Tool: { tone: "cat-teal", color: "#1E8A7A", tint: "#E3F2EF", Icon: Wrench },
  Condition: { tone: "cat-violet", color: "#7C5CBF", tint: "#F0EAF9", Icon: GitFork },
  Verification: { tone: "cat-gold", color: "#C58A32", tint: "#F8F0E0", Icon: ShieldCheck },
  HumanApproval: { tone: "cat-rose", color: "#C6507B", tint: "#FAE9F1", Icon: UserCheck },
  End: { tone: "cat-slate", color: "#75756E", tint: "#EFEFEA", Icon: Flag },
};

/**
 * Display states for workflow execution, mapped from REAL backend data
 * only (TaskNode.status / display_status via the workflow run's node_map):
 *   pending -> queued, running -> running, completed -> success,
 *   failed -> failed, failed-then-pending/running again within the same
 *   run (the runner's Self-Loop reset) -> retrying,
 *   display_status == "verifying" -> verifying: the backend derives this
 *   in _task_out for a node_type == "verify" node that is currently
 *   running, i.e. the run is inside its verification step right now.
 */
type ExecDisplayStatus = "queued" | "running" | "success" | "failed" | "retrying" | "verifying";

/* Execution status overrides the kind-colored border while a run is
 * live (same semantic tokens as everywhere else); the kind color stays
 * on the header band so type remains readable during execution. */
const STATUS_BORDER: Record<ExecDisplayStatus, { color: string; dashed?: boolean }> = {
  queued: { color: "var(--disabled)" },
  running: { color: "var(--info)" },
  success: { color: "var(--success)" },
  failed: { color: "var(--destructive)" },
  retrying: { color: "var(--warning)" },
  verifying: { color: "var(--info)", dashed: true },
};

function KindNodeView({ data, selected }: NodeProps) {
  const kind = typeof data.kind === "string" ? data.kind : "Agent";
  const meta = KIND_META[kind] ?? KIND_META.Agent;
  const st = data.execStatus as ExecDisplayStatus | undefined;
  const title = typeof data.title === "string" ? data.title : "";
  const border = st ? STATUS_BORDER[st] : null;
  const handleStyle: CSSProperties = {
    background: meta.color, width: 9, height: 9, border: "2px solid #FFFFFF",
  };
  return (
    <div
      style={{
        width: 198, background: "var(--card)", borderRadius: 12, overflow: "hidden",
        border: `2px solid ${border ? border.color : meta.color}`,
        borderStyle: border?.dashed ? "dashed" : "solid",
        boxShadow: selected ? `0 0 0 3px ${meta.tint}, var(--shadow-2)` : "var(--shadow-1)",
      }}
    >
      <Handle type="target" position={Position.Top} style={handleStyle} />
      <div style={{
        display: "flex", alignItems: "center", gap: 6, padding: "5px 10px",
        background: meta.tint, color: meta.color, fontSize: 11.5, fontWeight: 600,
      }}>
        <meta.Icon size={13} />
        <span>{KIND_LABELS[kind] ?? kind}</span>
        {st && <span style={{ marginLeft: "auto" }}>{EXEC_LABELS[st]}</span>}
      </div>
      {title && (
        <div style={{ padding: "8px 10px", fontSize: 13, fontWeight: 500, lineHeight: 1.35, wordBreak: "break-word" }}>
          {title}
        </div>
      )}
      <Handle type="source" position={Position.Bottom} style={handleStyle} />
    </div>
  );
}
const NODE_TYPES = { kindNode: memo(KindNodeView) };

const STATUS_BADGE: Record<ExecDisplayStatus, string> = {
  queued: "", running: "info", success: "success", failed: "error", retrying: "warning", verifying: "info",
};

const EXEC_LABELS: Record<ExecDisplayStatus, string> = {
  queued: "排队", running: "执行中", success: "成功", failed: "失败", retrying: "重试中", verifying: "验证中",
};

/** 后端工作流运行状态 → 中文：兜底不再原样输出英文状态码。 */
function runStatusLabel(status: string): string {
  return status === "running" ? "执行中"
    : status === "completed" ? "已完成"
    : status === "failed" ? "失败"
    : status === "pending" ? "待执行"
    : status === "cancelled" ? "已取消"
    : "未知状态";
}

interface ExecState {
  workflowId: number;
  note: string;
  taskId?: number;
  /** graph node id -> display status */
  statuses: Record<string, ExecDisplayStatus>;
  /** task node name -> display status, for the run panel */
  nodeNames: Array<{ name: string; status: ExecDisplayStatus }>;
}

type NodeConfig = Record<string, unknown>;

function configOf(n: Node): NodeConfig {
  const d = (n.data ?? {}) as Record<string, unknown>;
  const cfg: NodeConfig = { ...((d.config as NodeConfig | undefined) ?? {}) };
  // Legacy graphs keep tool settings at data top level.
  if (cfg.tool_id === undefined && d.tool_id !== undefined) cfg.tool_id = d.tool_id;
  if (cfg.tool_args === undefined && d.tool_args !== undefined) cfg.tool_args = d.tool_args;
  return cfg;
}

interface GraphIssue { message: string; nodeId?: string }

function validateGraph(nodes: Node[], edges: Edge[]): GraphIssue[] {
  const issues: GraphIssue[] = [];
  const kinds = nodes.map((n) => kindOfLabel(String(n.data.label ?? "")));
  if (!kinds.includes("Start")) issues.push({ message: "缺少开始节点" });
  if (!kinds.includes("End")) issues.push({ message: "缺少结束节点" });
  const hasIn = new Set(edges.map((e) => e.target));
  const hasOut = new Set(edges.map((e) => e.source));
  nodes.forEach((n, i) => {
    const label = displayNodeLabel(String(n.data.label ?? ""));
    const kind = kinds[i];
    if (nodes.length > 1 && kind !== "Start" && !hasIn.has(n.id)) {
      issues.push({ message: `节点「${label}」没有连线进入`, nodeId: n.id });
    }
    if (nodes.length > 1 && kind !== "End" && !hasOut.has(n.id)) {
      issues.push({ message: `节点「${label}」没有连线出去`, nodeId: n.id });
    }
    const cfg = configOf(n);
    if (kind === "Tool" && !cfg.tool_id) {
      issues.push({ message: `工具节点「${label}」还没有选择工具`, nodeId: n.id });
    }
    if (kind === "Condition" && !String(cfg.condition ?? "").trim()) {
      issues.push({ message: `条件节点「${label}」还没有写判断条件`, nodeId: n.id });
    }
  });
  return issues;
}

/** One-line summary of an employee for the assignee dropdown, built only
 * from fields the agents API already returns: name + role (via
 * agentTitle, which avoids duplicating a role already embedded in the
 * name), the backend-derived activity state, and up to three skill tags.
 * Disabled employees say so here and are rendered non-selectable. */
function agentOptionLabel(a: Agent): string {
  const parts = [agentTitle(a.name, a.role),
                 a.status === "disabled" ? "已停用" : activityLabel(a.activity_status)];
  const tags = (a.capability_tags ?? []).slice(0, 3).map(capLabel);
  if (tags.length) parts.push(tags.join("、"));
  return parts.join(" · ");
}

/** Display-only detail line under the assignee dropdown for the pinned
 * employee: role, derived activity state, skill tags and the CURRENT
 * task count. The count comes from the existing agent-detail endpoint
 * (detail.current_tasks lists the nodes running right now); the agents
 * list payload carries no task count, and none is invented here. */
function AssigneeDetail({ agent }: { agent: Agent }) {
  const [info, setInfo] = useState<{ count: number | null; failed: boolean }>({ count: null, failed: false });
  useEffect(() => {
    let alive = true;
    setInfo({ count: null, failed: false });
    api.agentDetail(agent.id)
      .then((d) => { if (alive) setInfo({ count: d.detail?.current_tasks?.length ?? 0, failed: false }); })
      .catch(() => { if (alive) setInfo({ count: null, failed: true }); });
    return () => { alive = false; };
  }, [agent.id]);
  const tags = (agent.capability_tags ?? []).map(capLabel);
  return (
    <div className="muted" style={{ marginTop: 6, display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
      <AgentStatusDot runtimeStatus={agent.runtime_status} status={agent.status} activityStatus={agent.activity_status} />
      <span>{roleLabel(agent.role)} · {activityLabel(agent.activity_status)}</span>
      {tags.length > 0 && <span>技能：{tags.join("、")}</span>}
      <span>当前任务：{info.failed ? "暂无数据" : info.count === null ? "读取中" : `${info.count} 个`}</span>
    </div>
  );
}

function NodeConfigPanel({ node, agents, tools, agentsError, agentsLoading, onReloadAgents, toolsError, toolsLoading, onReloadTools, onUpdate, onDelete }: {
  node: Node;
  agents: Agent[];
  tools: Array<{ id: string; name: string; description: string }>;
  agentsError: string | null;
  agentsLoading: boolean;
  onReloadAgents: () => void;
  toolsError: string | null;
  toolsLoading: boolean;
  onReloadTools: () => void;
  onUpdate: (patch: { detail?: string; config?: NodeConfig }) => void;
  onDelete: () => void;
}) {
  const kind = kindOfLabel(String(node.data.label ?? ""));
  const cfg = configOf(node);
  const selectedAgent = cfg.agent_id ? agents.find((a) => a.id === cfg.agent_id) ?? null : null;
  const [argsText, setArgsText] = useState<string>(() => JSON.stringify(cfg.tool_args ?? {}, null, 1));
  const [argsError, setArgsError] = useState<string | null>(null);
  const set = (patch: NodeConfig) => onUpdate({ config: { ...cfg, ...patch } });

  function commitArgs() {
    try {
      const parsed = JSON.parse(argsText || "{}") as Record<string, unknown>;
      setArgsError(null);
      set({ tool_args: parsed });
    } catch {
      setArgsError("参数不是正确的 JSON 格式");
    }
  }

  return (
    <div className="card" style={{ marginTop: 12 }}>
      <div className="spread">
        <div style={{ fontWeight: 600 }}>节点设置 · {KIND_LABELS[kind] ?? kind}</div>
        <button className="btn secondary sm" onClick={onDelete}>删除这个节点</button>
      </div>
      <div className="grid grid-2" style={{ marginTop: 10, gap: 12 }}>
        <label className="small" style={{ display: "block" }}>
          <span className="muted">节点名称</span>
          <input className="input" style={{ marginTop: 4 }} value={detailOfLabel(String(node.data.label ?? ""))}
            onChange={(e) => onUpdate({ detail: e.target.value })} placeholder="给这个节点起个名字" />
        </label>
        {kind === "Agent" && (
          <div className="small" style={{ display: "block" }}>
            <label style={{ display: "block" }}>
              <span className="muted">负责员工</span>
              <select className="input" style={{ marginTop: 4 }} value={String(cfg.agent_id ?? "")}
                disabled={agentsLoading || !!agentsError}
                onChange={(e) => set({ agent_id: e.target.value ? Number(e.target.value) : undefined })}>
                <option value="">{agentsLoading ? "正在加载员工列表…" : "自动分配"}</option>
                {agents.map((a) => (
                  <option key={a.id} value={a.id} disabled={a.status === "disabled"}>
                    {agentOptionLabel(a)}
                  </option>
                ))}
              </select>
            </label>
            {agentsError && (
              <div style={{ marginTop: 6 }}>
                <span style={{ color: "var(--destructive)" }}>员工列表加载失败：{agentsError}</span>{" "}
                <button className="btn secondary sm" onClick={onReloadAgents}>重新加载</button>
              </div>
            )}
            {selectedAgent && <AssigneeDetail agent={selectedAgent} />}
          </div>
        )}
        {kind === "Tool" && (
          <label className="small" style={{ display: "block" }}>
            <span className="muted">使用工具</span>
            <select className="input" style={{ marginTop: 4 }} value={String(cfg.tool_id ?? "")}
              disabled={toolsLoading || !!toolsError}
              onChange={(e) => set({ tool_id: e.target.value || undefined })}>
              <option value="">{toolsLoading ? "正在加载工具列表…" : "请选择工具"}</option>
              {tools.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
            </select>
            {toolsError && (
              <div style={{ marginTop: 6 }}>
                <span style={{ color: "var(--destructive)" }}>工具列表加载失败：{toolsError}</span>{" "}
                <button className="btn secondary sm" onClick={onReloadTools}>重新加载</button>
              </div>
            )}
          </label>
        )}
        {kind === "Condition" && (
          <label className="small" style={{ display: "block" }}>
            <span className="muted">不成立时</span>
            <select className="input" style={{ marginTop: 4 }} value={String(cfg.on_false ?? "stop")}
              onChange={(e) => set({ on_false: e.target.value })}>
              <option value="stop">停止并报错</option>
              <option value="continue">记录后继续</option>
            </select>
          </label>
        )}
        {kind === "Verification" && (
          <label className="small" style={{ display: "block" }}>
            <span className="muted">验证方式</span>
            <select className="input" style={{ marginTop: 4 }} value={String(cfg.verify_mode ?? "structural")}
              onChange={(e) => set({ verify_mode: e.target.value })}>
              <option value="structural">结构验证，检查上游产出齐不齐</option>
              <option value="llm">模型复核，让验证员工再看一遍</option>
            </select>
          </label>
        )}
      </div>
      {kind === "Agent" && (
        <label className="small" style={{ display: "block", marginTop: 10 }}>
          <span className="muted">任务说明，告诉员工这一步要做什么</span>
          <textarea className="input" style={{ marginTop: 4, minHeight: 56 }} value={String(cfg.instruction ?? "")}
            onChange={(e) => set({ instruction: e.target.value })} placeholder="例如：结合文件清单给出巡检结论" />
        </label>
      )}
      {kind === "Tool" && (
        <label className="small" style={{ display: "block", marginTop: 10 }}>
          <span className="muted">工具参数，JSON 格式</span>
          <textarea className="input mono" style={{ marginTop: 4, minHeight: 52 }} value={argsText}
            onChange={(e) => setArgsText(e.target.value)} onBlur={commitArgs} placeholder='{"参数名": "值"}' />
          {argsError && <span style={{ color: "var(--destructive)" }}>{argsError}，改好后点到别处生效</span>}
        </label>
      )}
      {kind === "Condition" && (
        <label className="small" style={{ display: "block", marginTop: 10 }}>
          <span className="muted">判断条件，按上游产出写，例如 包含 name、count 大于 0、status 等于 done</span>
          <input className="input" style={{ marginTop: 4 }} value={String(cfg.condition ?? "")}
            onChange={(e) => set({ condition: e.target.value })} placeholder="包含 name" />
        </label>
      )}
      {kind === "HumanApproval" && (
        <label className="small" style={{ display: "block", marginTop: 10 }}>
          <span className="muted">审批说明，运行时会展示给确认人看</span>
          <input className="input" style={{ marginTop: 4 }} value={String(cfg.note ?? "")}
            onChange={(e) => set({ note: e.target.value })} placeholder="请确认结论无误后再继续" />
        </label>
      )}
      {(kind === "Start" || kind === "End") && (
        <div className="muted small" style={{ marginTop: 10 }}>这个节点没有需要设置的内容。</div>
      )}
    </div>
  );
}

function fmtDuration(created?: string | null, finished?: string | null): string {
  if (!created) return "";
  const end = finished ? new Date(finished).getTime() : Date.now();
  const sec = Math.max(0, Math.round((end - new Date(created).getTime()) / 1000));
  return sec < 60 ? `${sec} 秒` : `${Math.floor(sec / 60)} 分 ${sec % 60} 秒`;
}

export default function WorkflowPage() {
  const openTask = useAppStore((s) => s.openTask);
  const saved = useAsync(() => api.workflows());
  const agentsList = useAsync(() => api.agents());
  const toolsList = useAsync(() => api.tools());
  const [currentId, setCurrentId] = useState<number | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [selectedEdgeId, setSelectedEdgeId] = useState<string | null>(null);
  const [issues, setIssues] = useState<GraphIssue[]>([]);
  const [confirmClear, setConfirmClear] = useState(false);
  const [approvalFor, setApprovalFor] = useState<{ id: number; name: string; graph: { nodes: Node[]; edges: Edge[] } } | null>(null);
  const [approvalChoices, setApprovalChoices] = useState<Record<string, boolean>>({});
  const [runsData, setRunsData] = useState<import("../types").WorkflowRun[]>([]);
  const runs = {
    data: runsData,
    reload: async () => {
      try { setRunsData(await api.workflowRuns(currentIdRef.current ?? undefined)); } catch { /* keep last */ }
    },
  };
  const currentIdRef = useRef<number | null>(null);
  currentIdRef.current = currentId;
  useEffect(() => { void runs.reload(); }, [currentId]);
  // 首访且没有任何已保存工作流时才摆的示例草图；有保存记录时直接打开
  // 最新那张，不让示例图先闪一下再被替换（见下方自动加载）。
  const STARTER_NODES: Node[] = [
    { id: "1", position: { x: 40, y: 120 }, data: { label: "Start" } },
    { id: "2", position: { x: 260, y: 120 }, data: { label: "Agent：任务规划" } },
  ];
  const STARTER_EDGES: Edge[] = [{ id: "e1-2", source: "1", target: "2" }];
  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  const [name, setName] = useState("演示工作流");
  const [msg, setMsg] = useState<string | null>(null);
  const [exec, setExec] = useState<ExecState | null>(null);
  const [executingId, setExecutingId] = useState<number | null>(null);
  const [saving, setSaving] = useState(false);

  // Keep-alive: the page stays mounted while hidden, where React Flow
  // measures 0x0 — refit once when it becomes visible again.
  const visible = usePageVisible();
  const visibleRef = useRef(visible);
  visibleRef.current = visible;
  const flowRef = useRef<ReactFlowInstance | null>(null);
  useEffect(() => {
    if (visible) flowRef.current?.fitView({ duration: 0, maxZoom: 1, padding: 0.15 });
  }, [visible]);

  // Retry detection across polls of one run: a node seen failed and later
  // pending/running again was reset by the Self-Loop.
  const prevRawRef = useRef<Record<string, string>>({});
  const retriedRef = useRef<Set<string>>(new Set());

  const pollRun = useCallback(async (workflowId: number) => {
    const runs = await api.workflowRuns(workflowId);
    const run = runs[0];
    if (!run) return;
    const allTasks = await api.tasks();
    const task = allTasks.find((t) => t.id === run.task_id);
    if (!task) return;
    const invMap: Record<number, string> = {};
    for (const [gid, nodeId] of Object.entries(run.node_map)) invMap[nodeId] = gid;
    const statuses: Record<string, ExecDisplayStatus> = {};
    const nodeNames: ExecState["nodeNames"] = [];
    for (const n of task.nodes) {
      const raw = n.status;
      const gid = invMap[n.id];
      let display: ExecDisplayStatus =
        (n.display_status ?? raw) === "verifying" ? "verifying"
        : raw === "completed" ? "success" : raw === "failed" ? "failed" : raw === "running" ? "running" : "queued";
      if (gid) {
        const prev = prevRawRef.current[gid];
        if (prev === "failed" && (raw === "pending" || raw === "running")) retriedRef.current.add(gid);
        if (retriedRef.current.has(gid) && (raw === "pending" || raw === "running")) display = "retrying";
        prevRawRef.current[gid] = raw;
        statuses[gid] = display;
      }
      nodeNames.push({ name: displayNodeLabel(n.name), status: display });
    }
    setExec({
      workflowId,
      taskId: run.task_id,
      note: `运行 #${run.id} · 任务 #${run.task_id} · 状态 ${runStatusLabel(run.status)}${run.error ? ` · ${run.error}` : ""}`,
      statuses,
      nodeNames,
    });
  }, []);

  async function executeSaved(w: { id: number; name: string },
                               approvals?: Record<string, { approved: boolean; note?: string }>) {
    prevRawRef.current = {};
    retriedRef.current = new Set();
    setExecutingId(w.id);
    setExec({ workflowId: w.id, note: "执行中，状态来自后端任务节点轮询…", statuses: {}, nodeNames: [] });
    const timer = window.setInterval(() => {
      if (visibleRef.current && !document.hidden) void pollRun(w.id).catch(() => undefined);
    }, 1200);
    try {
      await api.executeWorkflow(w.id, approvals);
      await pollRun(w.id);
    } catch (e) {
      setMsg(`执行失败：${e instanceof Error ? e.message : String(e)}`);
    } finally {
      window.clearInterval(timer);
      setExecutingId(null);
      void runs.reload();
    }
  }

  /** Executing a workflow that contains human-approval nodes asks for
   * the decisions up front (the engine carries them into the run);
   * without approval nodes it starts immediately. */
  function requestExecute(w: { id: number; name: string; graph: { nodes: unknown[]; edges: unknown[] } }) {
    const graphNodes = (w.graph?.nodes ?? []) as Node[];
    const approvalNodes = graphNodes.filter((n) => kindOfLabel(String((n.data as Record<string, unknown>)?.label ?? "")) === "HumanApproval");
    if (approvalNodes.length === 0) { void executeSaved(w); return; }
    const choices: Record<string, boolean> = {};
    approvalNodes.forEach((n) => { choices[n.id] = true; });
    setApprovalChoices(choices);
    setApprovalFor({ id: w.id, name: w.name, graph: { nodes: graphNodes, edges: (w.graph?.edges ?? []) as Edge[] } });
  }

  function confirmExecute() {
    if (!approvalFor) return;
    const approvals: Record<string, { approved: boolean; note?: string }> = {};
    for (const [gid, ok] of Object.entries(approvalChoices)) {
      approvals[gid] = { approved: ok };
    }
    const w = { id: approvalFor.id, name: approvalFor.name };
    setApprovalFor(null);
    void executeSaved(w, approvals);
  }

  const onConnect = useCallback((c: Connection) => setEdges((eds) => addEdge(c, eds)), [setEdges]);

  function addNode(kind: string) {
    const id = String(seq++);
    setNodes((nds) => [...nds, { id, position: { x: 120 + (seq % 5) * 90, y: 60 + (seq % 4) * 80 }, data: { label: kind } }]);
  }

  function updateNode(id: string, patch: { detail?: string; config?: NodeConfig }) {
    setNodes((nds) => nds.map((n) => {
      if (n.id !== id) return n;
      const kind = kindOfLabel(String(n.data.label ?? ""));
      const detail = patch.detail ?? detailOfLabel(String(n.data.label ?? ""));
      const label = detail ? `${kind}：${detail}` : kind;
      const cfg = patch.config ?? configOf(n);
      return { ...n, data: { ...n.data, label, config: cfg } };
    }));
  }

  function deleteNode(id: string) {
    setNodes((nds) => nds.filter((n) => n.id !== id));
    setEdges((eds) => eds.filter((e) => e.source !== id && e.target !== id));
    if (selectedId === id) setSelectedId(null);
  }

  function focusIssue(issue: GraphIssue) {
    if (!issue.nodeId) return;
    setSelectedId(issue.nodeId);
    const n = nodes.find((x) => x.id === issue.nodeId);
    if (n) flowRef.current?.setCenter(n.position.x + 100, n.position.y + 40, { zoom: 1.1, duration: 400 });
  }

  function checkGraph(): boolean {
    const found = validateGraph(nodes, edges);
    setIssues(found);
    if (found.length) {
      setMsg(`校验未通过：${found[0].message}${found.length > 1 ? `，另有 ${found.length - 1} 处` : ""}`);
      return false;
    }
    return true;
  }

  async function save() {
    if (saving || !checkGraph()) return;
    setSaving(true);
    try {
      if (currentId) {
        await api.updateWorkflow(currentId, { name, graph: { nodes, edges } });
        setMsg("已保存修改。");
      } else {
        const created = await api.saveWorkflow({ name, graph: { nodes, edges } });
        setCurrentId(created.id);
        setMsg("已保存到后端数据库。");
      }
      void saved.reload();
    } catch (e) { setMsg(`保存失败：${e instanceof Error ? e.message : String(e)}`); }
    finally { setSaving(false); }
  }

  async function saveAsCopy() {
    if (saving || !checkGraph()) return;
    setSaving(true);
    try {
      const created = await api.saveWorkflow({ name: `${name} 副本`, graph: { nodes, edges } });
      setCurrentId(created.id);
      setName(`${name} 副本`);
      setMsg("已另存为副本，可以放心修改。");
      void saved.reload();
    } catch (e) { setMsg(`另存失败：${e instanceof Error ? e.message : String(e)}`); }
    finally { setSaving(false); }
  }

  function clearCanvas() {
    setNodes([]);
    setEdges([]);
    setSelectedId(null);
    setCurrentId(null);
    setExec(null);
    setIssues([]);
    setConfirmClear(false);
    setMsg("画布已清空，重新拖拽节点开始画。");
  }

  async function runCurrent() {
    if (!currentId) { setMsg("先保存工作流，再运行。"); return; }
    if (!checkGraph()) return;
    try {
      await api.updateWorkflow(currentId, { name, graph: { nodes, edges } });
    } catch (e) {
      // 之前这里无 try/catch：保存失败时静默丢弃，用户只看到什么都没发生。
      setMsg(`保存失败：${e instanceof Error ? e.message : String(e)}，请重试。`);
      return;
    }
    requestExecute({ id: currentId, name, graph: { nodes: nodes as unknown[], edges: edges as unknown[] } });
  }

  function load(w: { id: number; name: string; graph: { nodes: unknown[]; edges: unknown[] } }) {
    setName(w.name);
    setCurrentId(w.id);
    setSelectedId(null);
    setIssues([]);
    setExec(null);
    // Graphs saved via API may lack React Flow positions; synthesize a
    // cascade layout instead of crashing React Flow on missing position.
    const rawNodes = (w.graph?.nodes ?? []) as Array<Record<string, unknown>>;
    // A saved graph laid out as one very long single row (e.g. a linear
    // chain) fits the canvas only at a tiny zoom, leaving most of the
    // canvas empty. Re-wrap just the positions into the cascade grid in
    // that case; nodes, edges and the stored graph stay untouched until
    // the user saves again.
    const savedPos = rawNodes.map((n) => n.position as { x: number; y: number } | undefined);
    const allPos = savedPos.every((p) => p && typeof p.x === "number" && typeof p.y === "number");
    const xs = allPos ? savedPos.map((p) => p!.x) : [];
    const ys = allPos ? savedPos.map((p) => p!.y) : [];
    const degenerateRow = rawNodes.length >= 6 && allPos
      && Math.max(...ys) - Math.min(...ys) <= 40
      && Math.max(...xs) - Math.min(...xs) > 1500;
    const cascade = (i: number) => ({ x: 120 + (i % 4) * 220, y: 60 + Math.floor(i / 4) * 110 });
    const normalized = rawNodes.map((n, i) => ({
      ...n,
      position: degenerateRow ? cascade(i) : (savedPos[i] ?? cascade(i)),
      data: (n.data as Record<string, unknown> | undefined) ?? { label: String(n.id ?? `node-${i}`) },
    }));
    setNodes(normalized as Node[]); setEdges((w.graph?.edges ?? []) as Edge[]);
    // Refit after the new graph renders, so a wider saved workflow is
    // fully visible instead of clipped at the canvas edge.
    window.setTimeout(() => flowRef.current?.fitView({ duration: 0, padding: 0.12, maxZoom: 1 }), 80);
    setMsg(`已加载「${w.name}」。`);
    void pollRun(w.id).catch(() => undefined); // show last run's real statuses, if any
  }

  // First visit: open the most recently saved workflow instead of the
  // starter sketch, so the page leads with real work (and its last run's
  // real node statuses). Fires once per mount; manual edits afterwards
  // are never overridden.
  const autoLoadedRef = useRef(false);
  useEffect(() => {
    if (autoLoadedRef.current || !saved.data) return;
    autoLoadedRef.current = true;
    if (saved.data.length === 0) {
      // 没有已保存的工作流：摆出示例草图（只在画布还空着时摆，不覆盖
      // 用户在加载回来之前已经动手画的内容）。
      setNodes((cur) => (cur.length === 0 ? STARTER_NODES : cur));
      setEdges((cur) => (cur.length === 0 ? STARTER_EDGES : cur));
      return;
    }
    const latest = [...saved.data].sort((a, b) => b.id - a.id)[0];
    load(latest);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [saved.data]);

  // Decorated copies for rendering: base nodes/edges state stays untouched
  // (editing, saving and validation keep working on the raw graph).
  const displayNodes: Node[] = nodes.map((n) => {
    const rawLabel = String(n.data.label ?? "");
    return {
      ...n,
      type: "kindNode",
      data: {
        ...n.data,
        label: displayNodeLabel(rawLabel),
        kind: kindOfLabel(rawLabel),
        title: detailOfLabel(rawLabel),
        execStatus: exec?.statuses[n.id],
      },
    };
  });
  const displayEdges: Edge[] = edges.map((e) => {
    const s = exec?.statuses[e.source];
    const t = exec?.statuses[e.target];
    if (t === "running") {
      return { ...e, animated: true, style: { stroke: "var(--info)", strokeWidth: 2 } };
    }
    if (t === "verifying") {
      return { ...e, animated: true, style: { stroke: "var(--info)", strokeWidth: 2, strokeDasharray: "3 3" } };
    }
    if (t === "retrying") {
      // Self-Loop feedback path: the edge feeding a node that the loop
      // sent back for another attempt — warning color, dashed.
      return {
        ...e, animated: true, label: "修复重试",
        labelStyle: { fontSize: 11, fontWeight: 600, fill: "#C58A32" },
        labelBgStyle: { fill: "#FFFFFF" }, labelBgPadding: [6, 3] as [number, number], labelBgBorderRadius: 4,
        style: { stroke: "var(--warning)", strokeWidth: 2, strokeDasharray: "6 4" },
      };
    }
    if (t === "failed") return { ...e, animated: false, style: { stroke: "var(--destructive)" } };
    if (s === "success" && t === "success") return { ...e, animated: false, style: { stroke: "var(--success)" } };
    return { ...e, animated: false };
  });

  return (
    <div>
      <h1 className="page-title">工作流</h1>
      <p className="page-desc">把工作流程画出来：拖拽节点、连线成图，保存后可以随时打开继续编辑；执行时节点的颜色会跟着每一步的真实进度变化。</p>
      <div className="row" style={{ marginBottom: 10 }}>
        {NODE_KINDS.map((k) => <button key={k} className="btn secondary sm" onClick={() => addNode(k)}>+ {KIND_LABELS[k]}</button>)}
        <input className="input" style={{ maxWidth: 200 }} value={name} onChange={(e) => setName(e.target.value)} placeholder="工作流名称" aria-label="工作流名称" />
        <button className="btn sm" disabled={saving} onClick={() => void save()}>{saving ? "保存中…" : "保存工作流"}</button>
        <button className="btn secondary sm" disabled={currentId != null && executingId === currentId} onClick={() => void runCurrent()}>{currentId != null && executingId === currentId ? "执行中…" : "运行当前"}</button>
        <button className="btn secondary sm" disabled={saving} onClick={() => void saveAsCopy()}>另存为副本</button>
        {confirmClear
          ? <button className="btn sm" style={{ background: "var(--destructive)" }} onClick={clearCanvas}>确认清空画布</button>
          : <button className="btn secondary sm" onClick={() => setConfirmClear(true)}>清空画布</button>}
        {selectedEdgeId && (
          <button className="btn secondary sm" onClick={() => { setEdges((eds) => eds.filter((e) => e.id !== selectedEdgeId)); setSelectedEdgeId(null); }}>删除选中的连线</button>
        )}
      </div>
      {msg && <div className="small" style={{ marginBottom: 8, color: msg.startsWith("校验") || msg.includes("失败") ? "var(--destructive)" : "var(--success)" }}>{msg}</div>}
      {issues.length > 0 && (
        <div className="card" style={{ marginBottom: 10, borderColor: "var(--destructive)" }}>
          <div className="small" style={{ fontWeight: 600, color: "var(--destructive)" }}>这张图还有 {issues.length} 处问题，点任意一条可以定位到节点</div>
          {issues.map((it, i) => (
            <div key={i} className="small" style={{ marginTop: 4, cursor: it.nodeId ? "pointer" : undefined }} onClick={() => focusIssue(it)}>· {it.message}</div>
          ))}
        </div>
      )}
      <div className="row small" style={{ marginBottom: 8, gap: 6, alignItems: "center" }}>
        <span className="muted">节点类型</span>
        {NODE_KINDS.map((k) => (
          <span key={k} className={`badge ${KIND_META[k].tone}`}>{KIND_LABELS[k]}</span>
        ))}
      </div>
      <div className="card" style={{ height: 460, padding: 0, overflow: "hidden" }}>
        <ReactFlow nodes={displayNodes} edges={displayEdges} nodeTypes={NODE_TYPES}
          defaultEdgeOptions={{ type: "smoothstep", style: { stroke: "#C6C6BF", strokeWidth: 1.6 } }}
          onNodeClick={(_, n) => { setSelectedId(n.id); setSelectedEdgeId(null); }}
          onEdgeClick={(_, e) => { setSelectedEdgeId(e.id); setSelectedId(null); }}
          onPaneClick={() => { setSelectedId(null); setSelectedEdgeId(null); }}
          onNodesChange={onNodesChange} onEdgesChange={onEdgesChange} onConnect={onConnect} onInit={(inst) => { flowRef.current = inst; }} fitView fitViewOptions={{ padding: 0.15, maxZoom: 1 }}>
          <Background color="#E8E8E5" />
          <Controls />
          <MiniMap pannable zoomable nodeColor={(n) => KIND_META[String((n.data as Record<string, unknown>)?.kind ?? "Agent")]?.color ?? "#AAAAAA"} style={{ borderRadius: 8 }} />
        </ReactFlow>
      </div>
      {(() => {
        const sel = nodes.find((n) => n.id === selectedId);
        return sel ? (
          <NodeConfigPanel key={sel.id} node={sel}
            agents={agentsList.data ?? []} tools={toolsList.data ?? []}
            agentsError={agentsList.error} agentsLoading={agentsList.loading}
            onReloadAgents={() => void agentsList.reload()}
            toolsError={toolsList.error} toolsLoading={toolsList.loading}
            onReloadTools={() => void toolsList.reload()}
            onUpdate={(patch) => updateNode(sel.id, patch)}
            onDelete={() => deleteNode(sel.id)} />
        ) : null;
      })()}
      {exec && (
        <div className="card" style={{ marginTop: 12 }}>
          <div className="spread">
            <div className="small" style={{ fontWeight: 600 }}>{exec.note}</div>
            {exec.taskId && <button className="btn secondary sm" onClick={() => openTask(exec.taskId!)}>查看任务详情</button>}
          </div>
          <div className="row" style={{ marginTop: 8 }}>
            {exec.nodeNames.map((n, i) => (
              <span key={`${n.name}-${i}`} className={`badge motion-color ${STATUS_BADGE[n.status]}`}>{n.name}：{EXEC_LABELS[n.status]}</span>
            ))}
            {exec.nodeNames.length === 0 && <span className="muted small">等待后端创建任务节点…</span>}
          </div>
          <div className="muted small" style={{ marginTop: 6 }}>图例：排队 灰 · 执行中 蓝 · 验证中 蓝虚线边框 · 成功 绿 · 失败 红 · 重试中 黄虚线。执行中的连线会流动，重试表示自动修复后重跑。</div>
        </div>
      )}
      <h3 style={{ marginTop: 16 }}>运行历史{currentId ? ` · ${name}` : ""}</h3>
      {(runs.data?.length ?? 0) === 0 && <div className="muted small">还没有运行记录，运行一次后这里会留下时间、状态与耗时。</div>}
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        {(runs.data ?? []).slice(0, 8).map((r) => (
          <div className="card spread" key={r.id}>
            <div className="small">
              <b>运行 #{r.id}</b>
              <span className="muted" title={r.created_at ? new Date(r.created_at).toLocaleString("zh-CN", { hour12: false }) : ""}> · {r.created_at ? `${fmtAgo(r.created_at)}开始` : ""} · 耗时 {fmtDuration(r.created_at, r.finished_at)}</span>
              {r.error && <div style={{ color: "var(--destructive)", marginTop: 2 }}>{r.error}</div>}
            </div>
            <div className="row">
              <span className={`badge ${r.status === "completed" ? "success" : r.status === "failed" ? "error" : "info"}`}>
                {runStatusLabel(r.status)}
              </span>
              <button className="btn secondary sm" onClick={() => openTask(r.task_id)}>查看任务</button>
            </div>
          </div>
        ))}
      </div>
      {(runs.data ?? []).length > 8 && <div className="muted small" style={{ marginTop: 8 }}>仅显示最近 8 条运行记录。</div>}

      <AnimatedModal open={approvalFor !== null} onClose={() => setApprovalFor(null)} labelledBy="wf-approval-title">
        <div style={{ width: 460, maxWidth: "92vw" }}>
          <h3 id="wf-approval-title" style={{ marginTop: 0 }}>运行前确认审批</h3>
          <p className="small muted">「{approvalFor?.name}」里有人工审批节点。批准的会直接放行，拒绝的会让这次运行在审批处停下并报错。</p>
          {(approvalFor?.graph.nodes ?? []).filter((n) => kindOfLabel(String((n.data as Record<string, unknown>)?.label ?? "")) === "HumanApproval").map((n) => {
            const cfg = configOf(n);
            const gid = n.id;
            return (
              <div key={gid} className="card" style={{ marginTop: 10 }}>
                <div style={{ fontWeight: 600 }} className="small">{displayNodeLabel(String((n.data as Record<string, unknown>)?.label ?? ""))}</div>
                <div className="muted small" style={{ marginTop: 2 }}>{String(cfg.note ?? "没有填写审批说明")}</div>
                <div className="row" style={{ marginTop: 8 }}>
                  <button className={`btn sm ${approvalChoices[gid] ? "" : "secondary"}`} onClick={() => setApprovalChoices((c) => ({ ...c, [gid]: true }))}>批准</button>
                  <button className={`btn sm ${approvalChoices[gid] === false ? "" : "secondary"}`} onClick={() => setApprovalChoices((c) => ({ ...c, [gid]: false }))}>拒绝</button>
                </div>
              </div>
            );
          })}
          <div className="row" style={{ marginTop: 14, justifyContent: "flex-end" }}>
            <button className="btn secondary sm" onClick={() => setApprovalFor(null)}>取消</button>
            <button className="btn sm" onClick={confirmExecute}>确认并运行</button>
          </div>
        </div>
      </AnimatedModal>

      <h3 style={{ marginTop: 16 }}>已保存工作流</h3>
      {saved.loading && <div className="muted small">正在加载已保存工作流…</div>}
      {saved.error && (
        <div className="state-box error">
          没能加载已保存工作流。
          <div><button className="btn sm" style={{ marginTop: 10 }} onClick={() => void saved.reload()}>重新加载</button></div>
          <details className="fold" style={{ textAlign: "left", maxWidth: 560, margin: "8px auto 0" }}>
            <summary>排查详情</summary>
            <div className="small" style={{ marginTop: 4 }}>{saved.error}。请确认后端服务正在运行，然后重新加载再试一次。</div>
          </details>
        </div>
      )}
      {(saved.data?.length ?? 0) === 0 && !saved.loading && !saved.error && <div className="muted small">暂无已保存工作流。在上面拼好节点后点「保存工作流」，就能在这里打开继续编辑。</div>}
      <div className="grid grid-2">
        {(saved.data ?? []).map((w) => (
          <div className="card spread" key={w.id}>
            <div><div style={{ fontWeight: 600 }}>{w.name}</div><div className="muted small">节点 {((w.graph?.nodes as unknown[] | undefined) ?? []).length} · 连线 {((w.graph?.edges as unknown[] | undefined) ?? []).length}</div></div>
            <div className="row">
              <button className="btn secondary sm" onClick={() => load(w)}>加载</button>
              <button className="btn sm" disabled={executingId === w.id} onClick={() => requestExecute(w)}>{executingId === w.id ? "执行中…" : "执行"}</button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
