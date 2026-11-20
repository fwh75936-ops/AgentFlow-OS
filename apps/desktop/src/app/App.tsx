import { lazy, Suspense, useEffect, useRef, useState, type ComponentType } from "react";
import {
  BookOpen, Bot, Gauge, LayoutDashboard, ListTodo, MonitorSmartphone, Plug,
  RefreshCw, Settings, ShieldCheck, Sparkles, Users, UsersRound, Workflow,
  type LucideIcon,
} from "lucide-react";
import { LazyMotion, MotionConfig, domMax, m } from "motion/react";
import { useAppStore, type PageKey } from "../stores/appStore";
import { SPRING } from "../motion/tokens";
import { PageVisibilityContext } from "../motion/visibility";
import { ToastViewport } from "../motion/Toast";
import { api } from "../services/api";
import Dashboard from "../pages/Dashboard";
import brandIconUrl from "../assets/brand-icon.png";
// 除首屏工作台外全部懒加载：冷启动只下发首屏代码，其余页首次点开时再加载，
// 保活池（visited）不变——页一旦挂载就常驻，切页不重复加载。
const Agents = lazy(() => import("../pages/Agents"));
const Teams = lazy(() => import("../pages/Teams"));
const Tasks = lazy(() => import("../pages/Tasks"));
const WorkflowPage = lazy(() => import("../pages/WorkflowPage"));
const LinuxWorkspace = lazy(() => import("../pages/LinuxWorkspace"));
const Skills = lazy(() => import("../pages/Skills"));
const Mcp = lazy(() => import("../pages/Mcp"));
const Knowledge = lazy(() => import("../pages/Knowledge"));
const Verification = lazy(() => import("../pages/Verification"));
const V3 = lazy(() => import("../pages/V3"));
const Evaluation = lazy(() => import("../pages/Evaluation"));
const SettingsPage = lazy(() => import("../pages/Settings"));

/** Grouped navigation （总览/智能体/能力/可信/系统）; page keys and
 * routing are the flat list's, only the grouping is visual. Nav icons
 * are the component library's lucide line glyphs, each on its own
 * category-color chip (the cat-* tones in globals.css). */
const NAV_GROUPS: Array<{ label: string; items: Array<{ key: PageKey; label: string; icon: LucideIcon; tone: string }> }> = [
  { label: "总览", items: [
    { key: "dashboard", label: "AI 工作室", icon: LayoutDashboard, tone: "cat-blue" },
  ] },
  { label: "智能体", items: [
    { key: "agents", label: "AI 员工", icon: Users, tone: "cat-violet" },
    { key: "teams", label: "AI 团队", icon: UsersRound, tone: "cat-rose" },
    { key: "tasks", label: "任务中心", icon: ListTodo, tone: "cat-orange" },
    { key: "workflow", label: "工作流", icon: Workflow, tone: "cat-teal" },
  ] },
  { label: "能力", items: [
    { key: "linux", label: "AI 电脑", icon: MonitorSmartphone, tone: "cat-gold" },
    { key: "skills", label: "技能", icon: Sparkles, tone: "cat-rose" },
    { key: "mcp", label: "MCP", icon: Plug, tone: "cat-slate" },
    { key: "knowledge", label: "知识库", icon: BookOpen, tone: "cat-green" },
  ] },
  { label: "质量把关", items: [
    { key: "verification", label: "验收记录", icon: ShieldCheck, tone: "cat-blue" },
    { key: "v3", label: "总管调度", icon: Bot, tone: "cat-violet" },
    { key: "evaluation", label: "对比实验", icon: Gauge, tone: "cat-orange" },
  ] },
  { label: "系统", items: [
    { key: "settings", label: "设置", icon: Settings, tone: "cat-slate" },
  ] },
];

const PAGE_LABELS: Record<PageKey, string> = Object.fromEntries(
  NAV_GROUPS.flatMap((g) => g.items.map((i) => [i.key, i.label])),
) as Record<PageKey, string>;

const PAGES: Record<PageKey, ComponentType> = {
  dashboard: Dashboard, agents: Agents, teams: Teams, tasks: Tasks,
  workflow: WorkflowPage, linux: LinuxWorkspace, skills: Skills, mcp: Mcp,
  knowledge: Knowledge, verification: Verification, v3: V3, evaluation: Evaluation,
  settings: SettingsPage,
};

/** System notifications when a task finishes or fails (toggle lives in
 * Settings, stored in localStorage as af_notify). Polls the real task
 * list and only fires on an observed status transition — the first
 * snapshot just establishes the baseline, so opening the app does not
 * replay history. */
function useTaskNotifications() {
  useEffect(() => {
    const prev = new Map<number, string>();
    let first = true;
    const check = async () => {
      if (localStorage.getItem("af_notify") === "0") return;
      try {
        const tasks = await api.tasks();
        for (const t of tasks) {
          const before = prev.get(t.id);
          prev.set(t.id, t.status);
          if (first || !before || before === t.status) continue;
          if (t.status === "completed" || t.status === "failed") {
            if ("Notification" in window) {
              if (Notification.permission === "default") void Notification.requestPermission();
              if (Notification.permission === "granted") {
                new Notification(t.status === "completed" ? "任务已完成" : "任务执行失败", { body: t.title });
              }
            }
          }
        }
        first = false;
      } catch { /* backend offline: stay quiet */ }
    };
    void check();
    const t = setInterval(() => void check(), 10000);
    return () => clearInterval(t);
  }, []);
}

function useBackendHealth() {
  const [state, setState] = useState<{ ok: boolean; at: string } | null>(null);
  useEffect(() => {
    let alive = true;
    const check = async () => {
      const at = new Date().toLocaleTimeString("zh-CN", { hour12: false });
      try { await api.health(); if (alive) setState({ ok: true, at }); }
      catch { if (alive) setState({ ok: false, at }); }
    };
    void check();
    const t = setInterval(() => void check(), 20000);
    return () => { alive = false; clearInterval(t); };
  }, []);
  return state;
}

export default function App() {
  // Narrow zustand selectors: App re-renders only when page/visited change.
  const page = useAppStore((s) => s.page);
  const visited = useAppStore((s) => s.visited);
  const setPage = useAppStore((s) => s.setPage);
  const health = useBackendHealth();
  useTaskNotifications();
  // 切页滚动位置按页保留：所有保活页共用一个 .main-scroll 滚动容器，
  // 切页前记下当前页的 scrollTop，切回来时恢复——否则回来看到的
  // 是上一页留下的滚动位置。保活页已挂载常驻，恢复时内容高度完整。
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const scrollPos = useRef(new Map<PageKey, number>());
  const pageRef = useRef(page);
  pageRef.current = page;
  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = scrollPos.current.get(page) ?? 0;
  }, [page]);
  return (
    <MotionConfig reducedMotion="user">
      <LazyMotion features={domMax} strict>
        <div className="layout">
          <aside className="sidebar">
            <div className="brand-row">
              <img className="brand-mark" src={brandIconUrl} width="28" height="28" alt="" aria-hidden="true" />
              <div>
                <div className="brand">AgentFlow OS</div>
                <div className="brand-sub">多智能体桌面工作空间</div>
              </div>
            </div>
            <nav className="sidebar-nav">
              {NAV_GROUPS.map((g) => (
                <div key={g.label}>
                  <div className="nav-group-label">{g.label}</div>
                  {g.items.map((n) => (
                    <button key={n.key} className={`nav-item ${page === n.key ? "active" : ""}`} onClick={() => setPage(n.key)}>
                      {page === n.key && (
                        <m.span
                          className="nav-pill"
                          layoutId="nav-active-pill"
                          transition={SPRING.layout}
                          aria-hidden="true"
                        />
                      )}
                      <span className={`icon-chip sm ${n.tone}`}><n.icon size={14} /></span>{n.label}
                    </button>
                  ))}
                </div>
              ))}
            </nav>
          </aside>
          <main className="main">
            <div className="topbar">
              <div className="topbar-crumb">AgentFlow OS / <b>{PAGE_LABELS[page]}</b></div>
              <div className="row" style={{ gap: 10 }}>
                <span className="small muted">
                  <span className={`conn-dot ${health?.ok ? "ok" : "down"}`} style={health == null ? { background: "var(--disabled)" } : undefined} />
                  {health == null ? "连接中" : health.ok ? "后端已连接" : "后端未连接"}
                </span>
                <button className="btn secondary sm" onClick={() => window.location.reload()}>
                  <RefreshCw size={13} />刷新
                </button>
              </div>
            </div>
            <div className="main-scroll" ref={scrollRef} onScroll={(e) => { scrollPos.current.set(pageRef.current, e.currentTarget.scrollTop); }}>
              {visited.map((key) => {
                const Page = PAGES[key];
                const active = key === page;
                return (
                  <div key={key} className="page-shell motion-page-enter" hidden={!active}>
                    <PageVisibilityContext.Provider value={active}>
                      <Suspense fallback={<div className="muted small" style={{ padding: 24 }}>加载中…</div>}>
                        <Page />
                      </Suspense>
                    </PageVisibilityContext.Provider>
                  </div>
                );
              })}
            </div>
          </main>
          <ToastViewport />
        </div>
      </LazyMotion>
    </MotionConfig>
  );
}
