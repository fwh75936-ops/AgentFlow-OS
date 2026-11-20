import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { StatusBadge } from "../../../components/ui";
import type { StudioSnapshot, AgentVisual } from "../types/studio";

const BUSY = new Set(["planning", "thinking", "working", "tool_running", "verifying", "retrying"]);

function formatLatency(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return "暂无数据";
  if (ms >= 1000) return `${(ms / 1000).toFixed(1)} 秒`;
  return `${Math.round(ms)} 毫秒`;
}

export default function StudioHeader({ snapshot, visuals, error, onNewTask, onOpenSettings }: {
  snapshot: StudioSnapshot | null;
  visuals: AgentVisual[];
  error: string | null;
  onNewTask: () => void;
  onOpenSettings: () => void;
}) {
  const s = snapshot?.stats;
  const tasks = snapshot?.tasks ?? [];
  const current = tasks.find((t) => t.status === "running") ?? tasks[0];
  const busyCount = visuals.filter((v) => BUSY.has(v.state)).length;
  return (
    <div className="studio-header">
      <div style={{ minWidth: 0 }}>
        <h1 className="page-title" style={{ fontSize: 20 }}>AI 工作室</h1>
        <div className="muted small truncate" style={{ maxWidth: 460 }}>
          {error && !snapshot
            ? "后端未连接，工作室等待数据…"
            : current
              ? <>当前任务：<b style={{ color: "var(--foreground)" }}>{current.title}</b></>
              : "暂无任务，员工们在休息区待命。"}
        </div>
      </div>
      {current && <StatusBadge status={current.status} />}
      <div className="studio-chips">
        <span className="studio-chip">运行中员工 <b>{busyCount}</b> / {visuals.length}</span>
        <span className="studio-chip">任务成功率 <b>{s?.success_rate == null ? "暂无数据" : `${Math.round(s.success_rate * 100)}%`}</b></span>
        <span className="studio-chip">累计 Token <b>{s?.total_tokens == null ? "—" : s.total_tokens.toLocaleString("zh-CN")}</b></span>
        <span className="studio-chip">执行步数 <b>{s?.executed_steps != null ? s.executed_steps.toLocaleString("zh-CN") : "—"}</b></span>
        <span className="studio-chip">工具平均延迟 <b>{formatLatency(s?.avg_tool_latency_ms)}</b></span>
        {error && snapshot && <Badge className="border-transparent bg-errortint text-destructive hover:bg-errortint">数据刷新失败，显示上次结果</Badge>}
      </div>
      <div className="row" style={{ gap: 8 }}>
        <Button variant="secondary" size="sm" onClick={onOpenSettings}>工作室设置</Button>
        <Button size="sm" onClick={onNewTask}>+ 新建任务</Button>
      </div>
    </div>
  );
}
