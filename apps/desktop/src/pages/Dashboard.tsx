import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "../services/api";
import type { Agent, Stats, Task } from "../types";
import {
  AgentStatusDot, Avatar, EmptyState, IconChip, StateBox, StatusBadge,
  activityLabel, nodeLabel, useAsync,
} from "../components/ui";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Progress } from "@/components/ui/progress";
import { useAppStore } from "../stores/appStore";
import PixelStudio from "../features/pixel-studio/components/PixelStudio";
import { usePageVisible } from "../motion/visibility";
import { fmtAgo } from "../lib/time";
import { Bot, Check, Clock, List, Users, Zap, type LucideIcon } from "lucide-react";

/** Metric-strip chip: a lucide glyph on the metric's category-color
 * chip (the cat-* family, same as the sidebar nav). The 执行步数 and
 * AI 员工 cells use lucide List / Bot directly — the old hand-drawn
 * icon set had no "list"/"bot" glyphs and silently fell back to a
 * generic file icon there. */
function MetricIcon({ icon: Icon, tone }: { icon: LucideIcon; tone: string }) {
  return <span className={`icon-chip ${tone}`}><Icon size={17} /></span>;
}

type FeedItem = {
  at: string | null; kind: string; agent_id: number | null;
  agent_name: string | null; task_id: number | null;
  task_title: string | null; text: string;
};

const KIND_LABELS: Record<string, string> = {
  KNOWLEDGE_REFERENCE: "引用知识库",
  browser_action: "浏览器操作",
  task_execution: "执行记录",
  tool_call: "工具调用",
  verification: "验证结果",
  file_change: "文件变更",
  handoff: "任务交接",
  system: "系统事件",
};

function fmtElapsed(iso?: string | null): string {
  if (!iso) return "—";
  const s = Math.max(0, Math.floor((Date.now() - new Date(iso).getTime()) / 1000));
  if (s < 60) return `${s} 秒`;
  if (s < 3600) return `${Math.floor(s / 60)} 分 ${s % 60} 秒`;
  return `${Math.floor(s / 3600)} 小时 ${Math.floor((s % 3600) / 60)} 分`;
}

const RUNNING_TASK_STATES = new Set(["running", "planning", "executing", "verifying", "in_progress"]);
const RUNNING_NODE_STATES = new Set(["running", "executing", "in_progress"]);
const DONE_NODE_STATES = new Set(["completed", "verified", "skipped"]);

/**
 * 工作台 = 实况看板. Everything on this board comes from live backend
 * sources: agent activity_status (backend-derived), task nodes (real
 * execution steps), /api/activity (execution + audit events) and
 * /api/stats. The pixel AI 工作室 stays one toggle away on the same
 * page; nothing here is decorative or invented.
 */
export default function Dashboard() {
  const view = useAppStore((s) => s.dashboardView);
  const setView = useAppStore((s) => s.setDashboardView);
  const openTask = useAppStore((s) => s.openTask);
  const setPage = useAppStore((s) => s.setPage);

  const statsQ = useAsync<Stats>(() => api.stats());
  const agentsQ = useAsync<Agent[]>(() => api.agents());
  const tasksQ = useAsync<Task[]>(() => api.tasks());
  const feedQ = useAsync<FeedItem[]>(() => api.activity() as Promise<FeedItem[]>);

  const reload = useCallback(() => {
    // 轮询用静默刷新：数据就地更新，不翻 loading，避免每 5 秒把员工
    // 实况卡闪成「正在加载…」。
    statsQ.refresh(); agentsQ.refresh(); tasksQ.refresh(); feedQ.refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 隐藏页休眠：切到别的页时工作台隐藏，5 秒轮询随之停掉（与像素
  // 工作室 2.5 秒轮询的休眠口径一致）；切回来立刻刷新一次再恢复轮询，
  // 不让用户看到一份旧数据等满 5 秒。首屏挂载不额外拉取，useAsync 已取过。
  const visible = usePageVisible();
  const wasHiddenRef = useRef(false);
  useEffect(() => {
    if (!visible) { wasHiddenRef.current = true; return; }
    if (wasHiddenRef.current) { wasHiddenRef.current = false; reload(); }
    if (view !== "board") return;
    const t = setInterval(reload, 5000);
    return () => clearInterval(t);
  }, [reload, view, visible]);

  const agents = agentsQ.data ?? [];
  const tasks = tasksQ.data ?? [];
  const feed = feedQ.data ?? [];
  const stats = statsQ.data;

  const runningTasks = useMemo(
    () => tasks.filter((t) => RUNNING_TASK_STATES.has(t.status)),
    [tasks],
  );

  /** agent_id -> {task, node} currently executing */
  const currentByAgent = useMemo(() => {
    const map = new Map<number, { task: Task; nodeName: string; since: string | null }>();
    for (const t of runningTasks) {
      for (const n of t.nodes ?? []) {
        if (n.assigned_agent_id && RUNNING_NODE_STATES.has(n.status)) {
          map.set(n.assigned_agent_id, { task: t, nodeName: nodeLabel(n.name), since: n.started_at ?? null });
        }
      }
    }
    return map;
  }, [runningTasks]);

  const lastEventByAgent = useMemo(() => {
    const map = new Map<number, string>();
    for (const item of feed) {
      if (item.agent_id && item.at && !map.has(item.agent_id)) map.set(item.agent_id, item.at);
    }
    return map;
  }, [feed]);

  return (
    <div className="page">
      <div className="page-head">
        {view === "board" ? (
          <div>
            <h1>AI 工作室</h1>
            <p className="page-sub">全队此刻在做什么，一眼看清。每 5 秒自动刷新，数据全部来自真实执行记录。</p>
          </div>
        ) : (
          // 工作室视图自带标题（StudioHeader 的「AI 工作室」+ 当前任务），
          // 这里不再重复标题与「每 5 秒」副标题——工作室是 2.5 秒轮询。
          <div />
        )}
        <div className="seg-control" role="tablist" aria-label="AI 工作室视图">
          <button className={view === "board" ? "active" : ""} onClick={() => setView("board")}>实况看板</button>
          <button className={view === "studio" ? "active" : ""} onClick={() => setView("studio")}>AI 工作室</button>
        </div>
      </div>

      {view === "studio" ? <PixelStudio /> : (
        <>
          {/* 指标带 */}
          <Card className="metric-strip">
            <CardContent>
              <div className="metric-strip-grid">
                <div className="metric-cell">
                  <MetricIcon icon={Users} tone="cat-violet" />
                  <div><strong>{stats ? `${runningTasks.length} 个` : "—"}</strong><span>进行中任务</span></div>
                </div>
                <div className="metric-cell">
                  <MetricIcon icon={Check} tone="cat-green" />
                  <div><strong>{stats?.success_rate != null ? `${Math.round(stats.success_rate * 100)}%` : "—"}</strong><span>任务成功率</span></div>
                </div>
                <div className="metric-cell">
                  <MetricIcon icon={Zap} tone="cat-orange" />
                  <div><strong>{stats ? stats.total_tokens.toLocaleString("zh-CN") : "—"}</strong><span>累计 Token</span></div>
                </div>
                <div className="metric-cell">
                  <MetricIcon icon={List} tone="cat-blue" />
                  <div><strong>{stats?.executed_steps != null ? stats.executed_steps.toLocaleString("zh-CN") : "—"}</strong><span>执行步数</span></div>
                </div>
                <div className="metric-cell">
                  <MetricIcon icon={Clock} tone="cat-teal" />
                  <div><strong>{stats?.avg_tool_latency_ms != null ? `${(stats.avg_tool_latency_ms / 1000).toFixed(1)} 秒` : "—"}</strong><span>工具平均延迟</span></div>
                </div>
                <div className="metric-cell">
                  <MetricIcon icon={Bot} tone="cat-rose" />
                  <div><strong>{stats ? `${stats.agent_count} 名` : "—"}</strong><span>AI 员工</span></div>
                </div>
              </div>
            </CardContent>
          </Card>

          {/* 员工实况 */}
          <Card>
            <CardHeader><CardTitle>员工实况</CardTitle></CardHeader>
            <CardContent>
              <StateBox loading={agentsQ.loading} error={agentsQ.error} empty={agents.length === 0} emptyText="还没有 AI 员工" onRetry={() => void agentsQ.reload()}>
                <div className="live-agent-grid">
                  {agents.map((a) => {
                    const cur = currentByAgent.get(a.id);
                    const activity = a.activity_status ?? "idle";
                    return (
                      <button
                        key={a.id}
                        className="live-agent-card"
                        onClick={() => (cur ? openTask(cur.task.id) : setPage("agents"))}
                      >
                        <span className="live-agent-top">
                          <Avatar name={a.name} size={38} avatar={a.avatar} appearance={a.appearance} />
                          <span className="live-agent-name">
                            <strong>{a.name.split(" · ")[0]}</strong>
                            <small>{a.name.split(" · ")[1] ?? ""}</small>
                          </span>
                          <span className="live-agent-state">
                            <AgentStatusDot runtimeStatus={a.runtime_status} activityStatus={activity} />
                            <Badge variant={activity === "idle" ? "secondary" : activity === "failed" ? "destructive" : "default"}>
                              {activityLabel(activity)}
                            </Badge>
                          </span>
                        </span>
                        {cur ? (
                          <span className="live-agent-current">
                            正在做：{cur.task.title}<br />
                            当前步骤：{cur.nodeName} · 已用时 {fmtElapsed(cur.since)}
                          </span>
                        ) : (
                          <span className="live-agent-current is-idle">
                            最近活动：{fmtAgo(lastEventByAgent.get(a.id))}
                          </span>
                        )}
                      </button>
                    );
                  })}
                </div>
              </StateBox>
            </CardContent>
          </Card>

          <div className="board-two-col">
            {/* 进行中任务 */}
            <Card>
              <CardHeader><CardTitle>进行中任务</CardTitle></CardHeader>
              <CardContent>
                {runningTasks.length === 0 ? (
                  <EmptyState title="现在没有进行中的任务" hint="新建任务或运行一条工作流，这里会实时显示进度。" art="tasks" />
                ) : (
                  <div className="live-task-list">
                    {runningTasks.map((t) => {
                      const nodes = t.nodes ?? [];
                      const done = nodes.filter((n) => DONE_NODE_STATES.has(n.status)).length;
                      const started = nodes.map((n) => n.started_at).filter(Boolean).sort()[0] ?? null;
                      return (
                        <button key={t.id} className="live-task-row" onClick={() => openTask(t.id)}>
                          <span className="live-task-main">
                            <strong>{t.title}</strong>
                            <StatusBadge status={t.status} />
                          </span>
                          <Progress value={nodes.length ? (done / nodes.length) * 100 : 0} />
                          <span className="live-task-meta">
                            已完成 {done} / {nodes.length} 步 · 已耗时 {fmtElapsed(started)}
                          </span>
                        </button>
                      );
                    })}
                  </div>
                )}
              </CardContent>
            </Card>

            {/* 实时动态流 */}
            <Card>
              <CardHeader><CardTitle>实时动态</CardTitle></CardHeader>
              <CardContent>
                {feed.length === 0 ? (
                  <EmptyState title="暂无动态" hint="员工开始执行任务后，这里会按时间倒序出现每一步记录。" />
                ) : (
                  <div className="live-feed">
                    {feed.slice(0, 20).map((item, i) => (
                      <button
                        key={`${item.at ?? ""}-${i}`}
                        className="live-feed-row"
                        disabled={!item.task_id}
                        onClick={() => item.task_id && openTask(item.task_id)}
                      >
                        {item.agent_name
                          ? <Avatar name={item.agent_name} size={26} avatar={agents.find((x) => x.name === item.agent_name)?.avatar} appearance={agents.find((x) => x.name === item.agent_name)?.appearance} />
                          : <IconChip icon="bot" tone="cat-slate" />}
                        <span className="live-feed-text">
                          <strong>{item.agent_name ?? "系统"}</strong>
                          {KIND_LABELS[item.kind] ? <Badge variant="outline">{KIND_LABELS[item.kind]}</Badge> : null}
                          <span>{item.text}</span>
                        </span>
                        <time>{fmtAgo(item.at)}</time>
                      </button>
                    ))}
                  </div>
                )}
              </CardContent>
            </Card>
          </div>
        </>
      )}
    </div>
  );
}
