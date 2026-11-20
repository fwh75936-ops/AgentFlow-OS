import type { Agent, Task } from "../../../types";
import { DEFAULT_APPEARANCE, normalizeAppearance } from "../../../lib/appearance";
import type { AgentVisual, AssignmentEvent, StudioSnapshot, StudioState, TimelineEvent } from "../types/studio";

/** State mapping — every visual state traces to a real backend field:
 *  - disabled:    agent.activity_status === "disabled" (set from agent.status)
 *  - failed:      activity_status === "failed", or the agent owns a failed
 *                 node inside a still-running task (verification rejected it)
 *  - planning:    running node with node_type "plan"
 *  - verifying:   running node with node_type "verify" / display_status "verifying"
 *  - tool_running: running node while activity_status === "tool_calling"
 *  - thinking:    running node while activity_status === "model_calling"
 *  - retrying:    running execute node in a task whose retry_count > 0
 *                 (the Self-Loop is genuinely on a repair pass)
 *  - working:     any other running node / activity_status "running"
 *  - waiting:     assigned to a pending node of a running task
 *  - completed:   transient flash when a task the agent worked on completes
 *                 (event-driven, expires; the business state stays idle)
 *  - idle:        none of the above
 */
export const STATE_LABELS: Record<StudioState, string> = {
  idle: "休息中", planning: "规划中", thinking: "思考中", working: "工作中",
  tool_running: "调用工具", waiting: "等待中", verifying: "验证中",
  failed: "出错", retrying: "修复中", completed: "刚完成", disabled: "已停用",
};

export function isChiefAgent(agent: Agent): boolean {
  return agent.name.includes("总管") || /chief/i.test(agent.role);
}

export function zoneFor(agent: Agent): AgentVisual["zone"] {
  const text = `${agent.name} ${agent.role} ${(agent.capability_tags ?? []).join(" ")}`;
  if (isChiefAgent(agent)) return "chief";
  if (/测试|验证|test|qa/i.test(text)) return "test";
  if (/数据|分析|data|analyst/i.test(text)) return "data";
  if (/产品|product/i.test(text)) return "product";
  return "dev";
}

function agentState(agent: Agent, tasks: Task[], completedFlash: Set<number>): StudioState {
  const activity = agent.activity_status ?? "idle";
  if (activity === "disabled") return "disabled";
  if (activity === "failed") return "failed";
  const running = tasks.filter((t) => t.status === "running");
  for (const task of running) {
    const node = task.nodes.find((n) => n.assigned_agent_id === agent.id && n.status === "running");
    if (node) {
      if (node.display_status === "verifying" || node.node_type === "verify") return "verifying";
      if (node.node_type === "plan") return "planning";
      if (task.retry_count > 0 && node.node_type === "agent") return "retrying";
      if (activity === "tool_calling" || node.node_type === "tool") return "tool_running";
      if (activity === "model_calling") return "thinking";
      return "working";
    }
  }
  for (const task of running) {
    const failedNode = task.nodes.find((n) => n.assigned_agent_id === agent.id && n.status === "failed");
    if (failedNode) return "failed";
  }
  for (const task of running) {
    const pending = task.nodes.find((n) => n.assigned_agent_id === agent.id && n.status === "pending");
    if (pending) return "waiting";
  }
  if (activity === "running") return "working";
  if (completedFlash.has(agent.id)) return "completed";
  return "idle";
}

export function deriveVisuals(snap: StudioSnapshot, completedFlash: Set<number>): AgentVisual[] {
  const leaseHolder = snap.wsStatus?.desktop_lease?.holder_agent_id ?? null;
  return snap.agents.map((agent) => {
    const running = snap.tasks.filter((t) => t.status === "running");
    let taskId: number | null = null;
    let taskTitle: string | null = null;
    let nodeName: string | null = null;
    for (const task of running) {
      const node = task.nodes.find((n) => n.assigned_agent_id === agent.id
        && (n.status === "running" || n.status === "pending" || n.status === "failed"));
      if (node) {
        taskId = task.id;
        taskTitle = task.title;
        nodeName = node.name;
        break;
      }
    }
    return {
      agentId: agent.id,
      name: agent.name,
      role: agent.role,
      state: agentState(agent, snap.tasks, completedFlash),
      taskId, taskTitle, nodeName,
      holdsDesktop: leaseHolder === agent.id,
      isChief: isChiefAgent(agent),
      zone: zoneFor(agent),
      // Appearance comes from the shared contract only (agents.appearance).
      // Agents without a configured appearance get the neutral default —
      // never an id-derived look (docs/APPEARANCE.md).
      appearance: normalizeAppearance(agent.appearance) ?? DEFAULT_APPEARANCE,
    };
  });
}

/* ------------------------------------------------------------ event diff -- */

function fmtTime(iso: string | null | undefined): string {
  if (!iso) return new Date().toLocaleTimeString("zh-CN", { hour12: false });
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso.slice(11, 19) : d.toLocaleTimeString("zh-CN", { hour12: false });
}

export interface DiffResult {
  events: TimelineEvent[];
  assignments: AssignmentEvent[];
  newlyCompletedAgents: number[];
}

export function diffSnapshots(prev: StudioSnapshot | null, next: StudioSnapshot): DiffResult {
  const events: TimelineEvent[] = [];
  const assignments: AssignmentEvent[] = [];
  const newlyCompletedAgents: number[] = [];
  if (!prev) return { events, assignments, newlyCompletedAgents };
  const name = (id: number | null) => next.agents.find((a) => a.id === id)?.name ?? "某位员工";
  const chief = next.agents.find((a) => isChiefAgent(a));
  const nodeOf = (taskId: number, nodeId: number) =>
    next.tasks.find((t) => t.id === taskId)?.nodes.find((n) => n.id === nodeId);

  // Scheduler decisions -> assignments (drives the chief handoff walk)
  const prevDecisions = new Set(prev.decisions.map((d) => d.id));
  for (const d of next.decisions) {
    if (prevDecisions.has(d.id) || d.selected_agent_id == null) continue;
    const node = nodeOf(d.task_id, d.node_id);
    const task = next.tasks.find((t) => t.id === d.task_id);
    assignments.push({
      decisionId: d.id, taskId: d.task_id,
      fromAgentId: chief && chief.id !== d.selected_agent_id ? chief.id : null,
      toAgentId: d.selected_agent_id,
      nodeName: node?.name ?? "步骤",
    });
    events.push({
      id: `decision-${d.id}`, at: fmtTime(undefined), kind: "task.assigned",
      text: `调度将「${node?.name ?? "步骤"}」分配给 ${name(d.selected_agent_id)}${task ? `，任务：${task.title}` : ""}`,
      tone: "info", agentId: d.selected_agent_id, taskId: d.task_id,
    });
  }

  // Task & node transitions
  const prevTasks = new Map(prev.tasks.map((t) => [t.id, t]));
  for (const task of next.tasks) {
    const before = prevTasks.get(task.id);
    if (!before) continue;
    if (before.status !== task.status) {
      if (task.status === "running") {
        events.push({ id: `task-${task.id}-running`, at: fmtTime(task.created_at), kind: "task.started", text: `任务开始执行：${task.title}`, tone: "info", agentId: null, taskId: task.id });
      } else if (task.status === "completed") {
        events.push({ id: `task-${task.id}-completed`, at: fmtTime(undefined), kind: "task.completed", text: `任务完成：${task.title}`, tone: "success", agentId: null, taskId: task.id });
        for (const n of task.nodes) if (n.assigned_agent_id != null) newlyCompletedAgents.push(n.assigned_agent_id);
      } else if (task.status === "failed") {
        events.push({ id: `task-${task.id}-failed`, at: fmtTime(undefined), kind: "task.failed", text: `任务失败：${task.title}${task.error_message ? ` — ${task.error_message.slice(0, 60)}` : ""}`, tone: "error", agentId: null, taskId: task.id });
      }
    }
    if (task.retry_count > before.retry_count) {
      events.push({ id: `task-${task.id}-retry-${task.retry_count}`, at: fmtTime(undefined), kind: "repair.started", text: `自动修复已触发 · 第 ${task.retry_count} 次重试：${task.title}`, tone: "warning", agentId: null, taskId: task.id });
    }
    const prevNodes = new Map(before.nodes.map((n) => [n.id, n]));
    for (const node of task.nodes) {
      const pn = prevNodes.get(node.id);
      if (!pn || pn.status === node.status) continue;
      if (node.status === "running") {
        events.push({ id: `node-${node.id}-running-${node.started_at ?? ""}`, at: fmtTime(node.started_at), kind: "task.started", text: `${name(node.assigned_agent_id)} 开始执行「${node.name}」`, tone: "info", agentId: node.assigned_agent_id, taskId: task.id });
      } else if (node.status === "failed") {
        events.push({ id: `node-${node.id}-failed`, at: fmtTime(node.finished_at), kind: "verification.failed", text: `「${node.name}」执行失败${node.error_message ? `：${node.error_message.slice(0, 60)}` : ""}`, tone: "error", agentId: node.assigned_agent_id, taskId: task.id });
      } else if (node.status === "completed") {
        events.push({ id: `node-${node.id}-completed`, at: fmtTime(node.finished_at), kind: "task.completed", text: `${name(node.assigned_agent_id)} 完成了「${node.name}」`, tone: "success", agentId: node.assigned_agent_id, taskId: task.id });
      }
    }
  }

  // Verification records
  const prevVers = new Set(prev.verifications.map((v) => v.verification_id));
  for (const v of next.verifications) {
    if (prevVers.has(v.verification_id)) continue;
    const task = next.tasks.find((t) => t.id === v.task_id);
    events.push({
      id: `ver-${v.verification_id}`, at: fmtTime(undefined),
      kind: v.passed ? "verification.passed" : "verification.failed",
      text: v.passed
        ? `验证通过 · 评分 ${v.score.toFixed(2)}：${task?.title ?? `任务 #${v.task_id}`}`
        : `验证未通过 · 评分 ${v.score.toFixed(2)}：${task?.title ?? `任务 #${v.task_id}`}`,
      tone: v.passed ? "success" : "error", agentId: null, taskId: v.task_id,
    });
  }

  // Shared-computer events (lease / files / sessions)
  const prevWs = new Set(prev.wsEvents.map((e) => e.id));
  for (const e of next.wsEvents) {
    if (prevWs.has(e.id)) continue;
    const kind = e.event_type === "desktop_lease_acquired" ? "desktop.lease.acquired"
      : e.event_type === "desktop_lease_released" ? "desktop.lease.released"
      : e.event_type.startsWith("file_") ? "file.changed" : e.event_type;
    events.push({
      id: `ws-${e.id}`, at: fmtTime(e.created_at), kind,
      text: e.detail || e.event_type,
      tone: kind === "desktop.lease.acquired" ? "info" : "neutral",
      agentId: e.agent_id, taskId: e.task_id,
    });
  }

  return { events, assignments, newlyCompletedAgents };
}
