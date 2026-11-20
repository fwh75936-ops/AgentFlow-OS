import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { StatusBadge, eventLabel } from "../../../components/ui";
import type { StudioSnapshot, TimelineEvent } from "../types/studio";

/* Event kinds are machine codes (task.assigned, verification.passed, …)
 * or raw workspace event types — never show them raw; workspace types
 * reuse the shared eventLabel vocabulary, unknown codes degrade to a
 * plain generic tag since the event text itself carries the detail. */
const KIND_LABELS: Record<string, string> = {
  "task.assigned": "任务分配", "task.started": "开始执行", "task.completed": "任务完成",
  "task.failed": "任务失败", "repair.started": "自动修复",
  "verification.passed": "验证通过", "verification.failed": "验证未通过",
  "desktop.lease.acquired": "取得桌面控制", "desktop.lease.released": "释放桌面控制",
  "file.changed": "文件变更",
};
function kindLabel(kind: string): string {
  if (KIND_LABELS[kind]) return KIND_LABELS[kind];
  const shared = eventLabel(kind);
  return shared !== kind ? shared : "动态";
}

const TONE_DOT: Record<string, string> = {
  success: "var(--success)", error: "var(--destructive)", warning: "var(--warning)",
  info: "var(--info)", neutral: "var(--disabled)",
};
const DIST_COLOR: Record<string, string> = { completed: "var(--success)", running: "var(--info)", failed: "var(--destructive)", pending: "var(--disabled)", cancelled: "var(--scrollbar)" };
const DIST_LABEL: Record<string, string> = { completed: "已完成", running: "进行中", failed: "失败", pending: "待执行", cancelled: "已取消" };

/** Bottom collapsible strip: the real-time event timeline, plus the
 * original dashboard's data overview (task distribution, recent tasks,
 * recent verifications) preserved under the second tab so no former
 * 工作台 capability is lost. */
export default function StudioTimeline({ open, onToggle, events, snapshot, onOpenTasks, onOpenVerification, onSelectAgent }: {
  open: boolean;
  onToggle: () => void;
  events: TimelineEvent[];
  snapshot: StudioSnapshot | null;
  onOpenTasks: () => void;
  onOpenVerification: () => void;
  onSelectAgent: (id: number) => void;
}) {
  const [tab, setTab] = useState<"events" | "overview">("events");
  const tasks = snapshot?.tasks ?? [];
  const hero = tasks.find((t) => t.status === "running") ?? tasks[0];
  const heroDone = hero ? hero.nodes.filter((n) => n.status === "completed").length : 0;
  const dist = ["completed", "running", "failed", "pending", "cancelled"]
    .map((k) => ({ key: k, count: tasks.filter((t) => t.status === k).length }))
    .filter((d) => d.count > 0);
  const failedCount = tasks.filter((t) => t.status === "failed").length;

  return (
    <div className="studio-timeline">
      <div className="spread" style={{ padding: "6px 12px" }}>
        <div className="row" style={{ gap: 6 }}>
          <button className={`studio-tab ${tab === "events" ? "active" : ""}`} onClick={() => { setTab("events"); if (!open) onToggle(); }}>实时事件</button>
          <button className={`studio-tab ${tab === "overview" ? "active" : ""}`} onClick={() => { setTab("overview"); if (!open) onToggle(); }}>数据概览</button>
          {failedCount > 0 && <span className="small" style={{ color: "var(--destructive)" }}>{failedCount} 个任务失败待处理</span>}
        </div>
        <button className="studio-tool-btn" title={open ? "收起" : "展开"} onClick={onToggle}>{open ? "▾" : "▸"}</button>
      </div>
      {open && (
        <div className="studio-timeline-body">
          {tab === "events" && (
            events.length > 0 ? events.map((e) => (
              <div key={e.id} className="studio-event-row" onClick={() => e.agentId != null && onSelectAgent(e.agentId)} style={e.agentId != null ? { cursor: "pointer" } : undefined}>
                <span className="studio-event-time">{e.at}</span>
                <span className="tl-dot" style={{ position: "static", background: TONE_DOT[e.tone] }} />
                <span className="truncate">{e.text}</span>
                <span className="muted small">{kindLabel(e.kind)}</span>
              </div>
            )) : <div className="muted small" style={{ padding: "4px 12px" }}>暂无事件。任务开始执行后，这里会实时记录分配、执行、验证与修复。</div>
          )}
          {tab === "overview" && (
            <div className="studio-overview">
              <div className="studio-overview-card">
                <div className="spread"><b className="small">{hero ? hero.title : "暂无任务"}</b>{hero && <StatusBadge status={hero.status} />}</div>
                {hero && hero.nodes.length > 0 && (
                  <>
                    <Progress value={(heroDone / hero.nodes.length) * 100} className="mt-2 h-2 [&>div]:bg-success" />
                    <div className="muted small" style={{ marginTop: 4 }}>已完成 {heroDone} / {hero.nodes.length} 步</div>
                  </>
                )}
                <Button variant="secondary" size="sm" className="mt-2" onClick={onOpenTasks}>任务中心 →</Button>
              </div>
              <div className="studio-overview-card">
                <b className="small">任务分布</b>
                {tasks.length > 0 ? (
                  <>
                    <div className="dist-bar" style={{ marginTop: 8 }}>
                      {dist.map((d) => <div key={d.key} style={{ width: `${(d.count / tasks.length) * 100}%`, background: DIST_COLOR[d.key] }} />)}
                    </div>
                    <div className="row small muted" style={{ marginTop: 6 }}>
                      {dist.map((d) => <span key={d.key}>● {DIST_LABEL[d.key]} {d.count}</span>)}
                    </div>
                  </>
                ) : <div className="muted small" style={{ marginTop: 6 }}>暂无任务记录。</div>}
              </div>
              <div className="studio-overview-card">
                <b className="small">最近任务</b>
                {tasks.slice(0, 3).map((t) => (
                  <div key={t.id} className="row small" style={{ justifyContent: "space-between", padding: "2px 0" }}>
                    <span className="truncate" style={{ maxWidth: 170 }}>{t.title}</span>
                    <StatusBadge status={t.status} />
                  </div>
                ))}
                {tasks.length === 0 && <div className="muted small" style={{ marginTop: 6 }}>暂无任务。</div>}
              </div>
              <div className="studio-overview-card">
                <b className="small">最近验证</b>
                {(snapshot?.verifications ?? []).slice(0, 3).map((v) => (
                  <div key={v.verification_id} className="row small" style={{ justifyContent: "space-between", padding: "2px 0" }}>
                    <span className="truncate" style={{ maxWidth: 170 }}>{tasks.find((t) => t.id === v.task_id)?.title ?? `任务 #${v.task_id}`}</span>
                    <span style={{ color: v.passed ? "var(--success)" : "var(--destructive)", fontWeight: 600 }}>{v.passed ? "通过" : "未通过"} {v.score.toFixed(2)}</span>
                  </div>
                ))}
                {(snapshot?.verifications.length ?? 0) === 0 && <div className="muted small" style={{ marginTop: 6 }}>暂无验证记录。</div>}
                <Button variant="secondary" size="sm" className="mt-2" onClick={onOpenVerification}>验收记录 →</Button>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
