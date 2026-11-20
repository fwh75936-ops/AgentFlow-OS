import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Avatar, activityDotClass, activityLabel, agentTitle, eventLabel, toolLabel } from "../../../components/ui";
import { STATE_LABELS } from "../events/AgentEventAdapter";
import type { AgentVisual, StudioSnapshot } from "../types/studio";

const STATE_TONE: Record<string, string> = {
  failed: "border-transparent bg-errortint text-destructive hover:bg-errortint",
  completed: "border-transparent bg-success-tint text-success hover:bg-success-tint",
  retrying: "border-transparent bg-warning-tint text-warning hover:bg-warning-tint",
  verifying: "border-transparent bg-info-tint text-info hover:bg-info-tint",
  tool_running: "border-transparent bg-info-tint text-info hover:bg-info-tint",
};

/** 时间统一走 zh-CN 24 小时制：此前是 ISO 原串切片（11-19 08:00），与其他页不一致。 */
function fmtTimeShort(iso?: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "" : d.toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false });
}

export default function AgentDetailPanel({ snapshot, visuals, selectedId, onSelect, onClose, onOpenAgents, onOpenLinux, onOpenTask, onFocus }: {
  snapshot: StudioSnapshot | null;
  visuals: AgentVisual[];
  selectedId: number | null;
  onSelect: (id: number) => void;
  onClose: () => void;
  onOpenAgents: () => void;
  onOpenLinux: () => void;
  onOpenTask: (id: number) => void;
  onFocus: (id: number) => void;
}) {
  const agents = snapshot?.agents ?? [];
  const agent = agents.find((a) => a.id === selectedId) ?? null;
  const visual = visuals.find((v) => v.agentId === selectedId) ?? null;
  const lease = snapshot?.wsStatus?.desktop_lease;
  const leaseHolderName = lease?.holder_type === "user" ? "你"
    : lease?.holder_agent_id ? agents.find((a) => a.id === lease.holder_agent_id)?.name ?? "某位员工" : null;

  if (!agent || !visual) {
    return (
      <div className="studio-panel">
        <div className="spread" style={{ marginBottom: 8 }}>
          <div style={{ fontWeight: 600 }}>团队总览</div>
          <span className="muted small">点击办公室里的员工查看详情</span>
        </div>
        {visuals.map((v) => {
          const a = agents.find((x) => x.id === v.agentId);
          return (
            <div key={v.agentId} className="person-row" style={{ cursor: "pointer" }} onClick={() => onSelect(v.agentId)}>
              <Avatar name={v.name} size={30} avatar={a?.avatar} appearance={a?.appearance ?? v.appearance} />
              <div style={{ flex: 1, minWidth: 0 }}>
                <div className="truncate" style={{ fontWeight: 500 }}>{agentTitle(v.name, v.role)}</div>
                <div className="muted small truncate">{v.taskTitle ? `${v.taskTitle}${v.nodeName ? ` · ${v.nodeName}` : ""}` : STATE_LABELS[v.state]}</div>
              </div>
              <Badge className={STATE_TONE[v.state] ?? "border-transparent bg-secondary text-foreground hover:bg-secondary"}>{STATE_LABELS[v.state]}</Badge>
              {a && <span className={activityDotClass(a.activity_status)} />}
            </div>
          );
        })}
        {visuals.length === 0 && <div className="muted small">暂无员工数据。</div>}
        <div style={{ borderTop: "1px solid var(--border)", marginTop: 10, paddingTop: 10 }}>
          <div style={{ fontWeight: 600, marginBottom: 6 }}>共享 Linux 电脑</div>
          <div className="small" style={{ lineHeight: 1.9 }}>
            <div>环境：仅一台，全员共用</div>
            <div>终端会话：{snapshot?.wsStatus?.active_sessions ?? "—"} 个</div>
            <div>共享桌面：{snapshot?.wsStatus?.desktop.state === "running" ? "运行中" : snapshot?.wsStatus?.desktop.state === "degraded" ? "网页查看不可用" : snapshot?.wsStatus?.desktop.state === "unavailable" ? "不可用" : "未启动"}</div>
            <div>当前控制：<b>{leaseHolderName ?? "空闲"}</b></div>
          </div>
          <Button variant="secondary" size="sm" className="mt-2" onClick={onOpenLinux}>进入 AI 电脑</Button>
        </div>
      </div>
    );
  }

  const team = snapshot?.teams.find((t) => t.members.some((m) => m.agent_id === agent.id));
  const myTask = visual.taskId != null ? snapshot?.tasks.find((t) => t.id === visual.taskId) : null;
  const doneSteps = myTask ? myTask.nodes.filter((n) => n.status === "completed").length : 0;
  const commands = (snapshot?.wsCommands ?? []).filter((c) => c.agent_id === agent.id).slice(0, 5);
  const events = (snapshot?.wsEvents ?? []).filter((e) => e.agent_id === agent.id).slice(0, 5);

  return (
    <div className="studio-panel">
      <div className="spread" style={{ marginBottom: 8 }}>
        <div className="row" style={{ gap: 8, alignItems: "center", minWidth: 0 }}>
          <Avatar name={agent.name} size={34} avatar={agent.avatar} appearance={agent.appearance} />
          <div style={{ minWidth: 0 }}>
            <div className="truncate" style={{ fontWeight: 600 }}>{agentTitle(agent.name, agent.role)}</div>
            <div className="muted small">{team ? `团队：${team.name}` : "暂未加入团队"}</div>
          </div>
        </div>
        <button className="studio-tool-btn" title="关闭详情" onClick={onClose}>✕</button>
      </div>
      <div className="row" style={{ gap: 6, flexWrap: "wrap" }}>
        <Badge className={STATE_TONE[visual.state] ?? "border-transparent bg-secondary text-foreground hover:bg-secondary"}>{STATE_LABELS[visual.state]}</Badge>
        <Badge variant="secondary">{activityLabel(agent.activity_status)}</Badge>
        {visual.holdsDesktop && <Badge className="border-transparent bg-info-tint text-info hover:bg-info-tint">正在控制共享 Linux 桌面</Badge>}
      </div>

      <div style={{ fontWeight: 600, marginTop: 12, marginBottom: 4 }}>当前任务</div>
      {myTask ? (
        <div>
          <div className="small" style={{ fontWeight: 500 }}>{myTask.title}</div>
          <div className="muted small">当前步骤：{visual.nodeName ?? "—"} · 已完成 {doneSteps} / {myTask.nodes.length} 步</div>
          <Button variant="secondary" size="sm" className="mt-2" onClick={() => onOpenTask(myTask.id)}>查看任务详情</Button>
        </div>
      ) : (
        <div className="muted small">当前没有进行中的任务。</div>
      )}

      <div style={{ fontWeight: 600, marginTop: 12, marginBottom: 4 }}>执行工具</div>
      <div className="row" style={{ gap: 6, flexWrap: "wrap" }}>
        {(agent.tools ?? []).length > 0
          ? agent.tools.map((t) => <Badge key={t} variant="secondary">{toolLabel(t)}</Badge>)
          : <span className="muted small">未配置工具</span>}
      </div>

      <div style={{ fontWeight: 600, marginTop: 12, marginBottom: 4 }}>最近终端命令</div>
      {commands.length > 0 ? commands.map((c) => (
        <div key={c.id} className="studio-log-row">
          <code className="truncate" style={{ flex: 1 }}>{c.command}</code>
          <Badge className={c.exit_code === 0 ? "border-transparent bg-success-tint text-success hover:bg-success-tint" : "border-transparent bg-errortint text-destructive hover:bg-errortint"}>
            {c.exit_code === null ? "运行中" : `退出码 ${c.exit_code}`}
          </Badge>
        </div>
      )) : <div className="muted small">暂无该员工的命令记录。</div>}

      <div style={{ fontWeight: 600, marginTop: 12, marginBottom: 4 }}>最近动态</div>
      {events.length > 0 ? events.map((e) => (
        <div key={e.id} className="small muted" style={{ padding: "2px 0" }}>
          {fmtTimeShort(e.created_at)} · {e.detail || eventLabel(e.event_type)}
        </div>
      )) : <div className="muted small">暂无动态。</div>}

      {!visual.holdsDesktop && leaseHolderName && (
        <div className="muted small" style={{ marginTop: 10 }}>共享桌面当前由 {leaseHolderName} 控制，这位员工没有控制权。</div>
      )}
      <div className="row" style={{ gap: 8, marginTop: 12 }}>
        <Button variant="secondary" size="sm" onClick={() => onFocus(agent.id)}>聚焦镜头</Button>
        <Button variant="secondary" size="sm" onClick={onOpenAgents}>员工详情页</Button>
        <Button variant="secondary" size="sm" onClick={onOpenLinux}>AI 电脑</Button>
      </div>
    </div>
  );
}
