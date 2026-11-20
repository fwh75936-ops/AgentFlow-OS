import { useEffect, useRef, type ReactNode } from "react";
import { AnimatePresence, m } from "motion/react";
import { ENTER_OFFSET, TRANSITION } from "../motion/tokens";
import { AgentAvatarFigure, AppearanceFigure, isAvatarPreset } from "./AgentAvatar";
import { normalizeAppearance, type AgentAppearance } from "../lib/appearance";
import { avatarImageFor } from "../lib/avatarImages";
import emptyTasksUrl from "../assets/empty-tasks.png";
import emptyKnowledgeUrl from "../assets/empty-knowledge.png";
import emptyChatUrl from "../assets/empty-chat.png";
import emptyFlaskUrl from "../assets/empty-flask.png";

/* One shared Chinese vocabulary for statuses, roles, capabilities,
 * tools and scheduler modes, so machine enums never reach the user
 * raw. Unknown values fall back to the raw text in detail areas only. */

const STATUS_LABELS: Record<string, string> = {
  pending: "待执行", queued: "排队中", running: "执行中", completed: "已完成",
  failed: "失败", cancelled: "已取消", active: "已启用", disabled: "已禁用",
  idle: "空闲", Idle: "空闲", Running: "工作中", Failed: "执行失败",
  stopped: "已停止", unavailable: "不可用", current: "最新", stale: "已过期",
  approved: "已批准", rejected: "已拒绝", executed: "已执行",
  pending_review: "待审阅", superseded: "已被新变更替代", accepted: "已接受",
  success: "成功", blocked: "已拦截", closed: "已关闭",
  draft: "草稿", granted: "已授予", waiting: "等待中", expired: "已到期", released: "已释放",
  created: "已创建",
};
export function statusLabel(status: string): string {
  return STATUS_LABELS[status] ?? status;
}

const MODE_LABELS: Record<string, string> = {
  adaptive: "自适应调度", fixed: "固定分配", round_robin: "轮询分配",
  sacas: "共享环境感知调度", single: "单员工",
};
export function modeLabel(mode: string): string {
  return MODE_LABELS[mode] ?? mode;
}

const ROLE_LABELS: Record<string, string> = {
  product_manager: "产品经理", product: "产品经理",
  developer: "开发工程师", tester: "测试工程师",
  planner: "规划师", reviewer: "评审员", researcher: "研究员",
  architect: "架构师", general: "通用员工", manager: "负责人", chief: "AI 总管",
  technical_writer: "文档工程师", data_analyst: "数据分析师",
};
export function roleLabel(role: string): string {
  return ROLE_LABELS[role] ?? role;
}

const CAP_LABELS: Record<string, string> = {
  planning: "规划", coding: "编程", research: "调研", review: "评审",
  testing: "测试", writing: "写作", analysis: "分析", general: "通用",
  design: "设计", debugging: "调试", documentation: "文档", management: "管理",
  product: "产品", verification: "验证",
};
export function capLabel(tag: string): string {
  return CAP_LABELS[tag] ?? tag;
}

const TOOL_LABELS: Record<string, string> = {
  file_read: "读文件", file_write: "写文件", run_command: "执行命令",
  run_pytest: "运行测试", linux_exec: "Linux 命令", linux_file_read: "读共享文件",
  linux_file_write: "写共享文件", linux_file_list: "列共享文件", desktop_control: "桌面控制",
  web_search: "网页搜索", terminal: "终端", file_editor: "文件编辑", pytest: "运行测试",
};
export function toolLabel(tool: string): string {
  return TOOL_LABELS[tool] ?? tool;
}

const NODE_LABELS: Record<string, string> = {
  plan: "规划", research: "调研", execute: "执行", verify: "验证",
  report: "汇报", review: "评审",
};
const NODE_TYPE_LABELS: Record<string, string> = {
  Tool: "工具", Agent: "员工", Condition: "条件判断", Verification: "验证", HumanApproval: "人工审批",
};
export function nodeLabel(name: string): string {
  const m = /^(Tool|Agent|Condition|Verification|HumanApproval)[：:]\s*(.*)$/.exec(name);
  if (m) return `${NODE_TYPE_LABELS[m[1]]}：${nodeLabel(m[2])}`;
  return NODE_LABELS[name] ?? name;
}

const VTYPE_LABELS: Record<string, string> = {
  structural: "结构验证", rule: "规则验证", tool: "工具验证", reviewer: "交叉评审",
};
export function vtypeLabel(t: string): string {
  return VTYPE_LABELS[t] ?? t;
}

const EVENT_LABELS: Record<string, string> = {
  session_created: "打开终端会话", session_closed: "关闭终端会话",
  command_executed: "执行命令", command_blocked: "命令被拦截",
  file_created: "创建文件", file_modified: "修改文件", file_deleted: "删除文件",
  file_conflict: "文件冲突", file_locked: "锁定文件", file_unlocked: "解锁文件",
  file_change_staged: "提交待审变更", file_change_accepted: "变更已接受并写入",
  file_change_rejected: "变更被拒绝",
  desktop_lease_acquired: "取得桌面控制", desktop_lease_granted: "授予桌面控制",
  desktop_lease_released: "释放桌面控制", desktop_lease_expired: "桌面租约到期",
  desktop_lease_waiting: "排队等待桌面", desktop_force_takeover: "用户强制接管",
  demo_seeded: "初始化演示团队",
};
export function eventLabel(t: string): string {
  return EVENT_LABELS[t] ?? t;
}

/** Translate the scheduler's hard-filter reasons into short Chinese.
 * Patterns verified against app/scheduler/engine.py. */
export function translateFilterReason(reason: string): string {
  if (!reason) return "";
  if (reason === "agent unavailable") return "员工当前不可用";
  if (reason === "agent disabled") return "员工已禁用";
  const cap = /^missing required capabilities: \[(.*)\]$/.exec(reason);
  if (cap) {
    const items = cap[1].split(",").map((x) => capLabel(x.trim().replace(/^['"]|['"]$/g, ""))).filter(Boolean);
    return `缺少能力：${items.join("、")}`;
  }
  const tool = /^missing required tools: \[(.*)\]$/.exec(reason);
  if (tool) {
    const items = tool[1].split(",").map((x) => toolLabel(x.trim().replace(/^['"]|['"]$/g, ""))).filter(Boolean);
    return `缺少工具：${items.join("、")}`;
  }
  return reason;
}

/** Friendly Chinese explanation for a backend error_type. The raw code
 * and message stay available inside the folded detail area. */
export function errorInfo(errorType: string | null, errorMessage: string | null): {
  title: string; hint: string; needsModel: boolean;
} {
  switch (errorType) {
    case "MODEL_ERROR":
      return {
        title: "未配置可用模型",
        hint: "这位员工还没有绑定模型，任务无法开始。去「设置」添加模型供应商，再到「AI 员工」里为员工选择模型，然后重新执行。",
        needsModel: true,
      };
    case "PERMISSION_ERROR":
      return { title: "权限不足", hint: "执行者没有这项操作的权限，已被系统拒绝。如确需执行，请调整该员工的工具权限。", needsModel: false };
    case "DEPENDENCY_ERROR":
      return { title: "前置步骤未完成", hint: "依赖的上一步没有成功完成，这一步无法继续。先处理前一步的失败，再重新执行。", needsModel: false };
    case "TIMEOUT":
      return { title: "执行超时", hint: "这一步运行太久被中止。可以重试，或把任务拆小后再执行。", needsModel: false };
    default:
      return { title: "执行失败", hint: errorMessage ? "执行过程中出现问题，原始信息已收在下方详情里。" : "执行过程中出现问题。", needsModel: false };
  }
}

/* Avatar hash colors come from the shared category palette (--cat-* in
   globals.css) so a person's color is the same family everywhere. */
const AVATAR_COLORS = ["#3978C6", "#238B61", "#D97B29", "#7C5CBF", "#1E8A7A", "#C6507B", "#C58A32", "#C2703D"];
export function avatarColor(seed: string): string {
  let h = 0;
  for (let i = 0; i < seed.length; i++) h = (h * 31 + seed.charCodeAt(i)) >>> 0;
  return AVATAR_COLORS[h % AVATAR_COLORS.length];
}

export function Avatar({ name, size = 34, avatar, appearance }: { name: string; size?: number; avatar?: string | null; appearance?: AgentAppearance | Record<string, unknown> | null }) {
  // A configured appearance renders the layered vector figure (the same
  // config drives the pixel studio sprite); a stored avatar preset id
  // renders the legacy drawn figure; anything else (legacy rows, names
  // without a profile) keeps the monogram block.
  const appearanceConfig = normalizeAppearance(appearance);
  if (appearanceConfig) {
    // Generated pixel portrait for this {gender, skin, outfit} combo
    // (src/lib/avatarImages.ts); if the set has no image for it, fall
    // back to the layered vector figure — the fallback chain must not
    // break.
    const photo = avatarImageFor(appearanceConfig);
    if (photo) {
      return (
        <span className="avatar" style={{ width: size, height: size, background: "transparent", overflow: "hidden", padding: 0 }} aria-hidden="true">
          <img src={photo} width={size} height={size} alt="" draggable={false}
            style={{ display: "block", objectFit: "cover" }} />
        </span>
      );
    }
    return (
      <span className="avatar" style={{ width: size, height: size, background: "transparent", overflow: "hidden", padding: 0 }} aria-hidden="true">
        <AppearanceFigure appearance={appearanceConfig} size={size} />
      </span>
    );
  }
  if (isAvatarPreset(avatar)) {
    return (
      <span className="avatar" style={{ width: size, height: size, background: "transparent", overflow: "hidden", padding: 0 }} aria-hidden="true">
        <AgentAvatarFigure presetId={avatar as string} size={size} />
      </span>
    );
  }
  const trimmed = (name || "?").trim();
  // Names like "AI总管" start with Latin letters but the person's name is
  // the CJK part — prefer the first CJK character so the monogram reads
  // as the name （总管 → 总）, falling back to the first character.
  const ch = trimmed.match(/[一-鿿]/)?.[0] ?? trimmed.slice(0, 1) ?? "?";
  return (
    <span className="avatar" style={{ width: size, height: size, background: avatarColor(name), fontSize: Math.round(size * 0.42) }} aria-hidden="true">
      {ch}
    </span>
  );
}

/** Display title for an agent. Seed names already embed a role suffix
 * ("林晓 · 产品经理"), so appending roleLabel again renders the role
 * twice; only append it when the name does not already carry one. */
export function agentTitle(name: string, role: string): string {
  if (name.includes("·")) return name;
  const rl = roleLabel(role);
  if (!rl || name.replace(/\s+/g, "") === rl.replace(/\s+/g, "")) return name;
  return `${name} · ${rl}`;
}

export function ScoreBar({ value, tone = "", format }: {
  value: number; tone?: string; format?: (v: number) => string;
}) {
  const pct = Math.max(0, Math.min(100, value * 100));
  return (
    <span className="scorebar">
      <span className="scorebar-track"><span className={`scorebar-fill ${tone}`} style={{ width: `${pct}%`, display: "block" }} /></span>
      <span className="scorebar-num">{format ? format(value) : value.toFixed(2)}</span>
    </span>
  );
}

const ICON_PATHS: Record<string, ReactNode> = {
  users: (<><circle cx="9" cy="8" r="3.2" /><path d="M3.5 19c.6-3 2.8-4.6 5.5-4.6s4.9 1.6 5.5 4.6" /><circle cx="17" cy="9" r="2.4" /><path d="M16.2 14.6c2.2.3 3.7 1.7 4.2 4.4" /></>),
  play: (<><circle cx="12" cy="12" r="8.6" /><path d="M10 8.6l5.8 3.4-5.8 3.4z" /></>),
  chart: (<><path d="M3 20.5h18" /><path d="M6 20.5v-6" /><path d="M11 20.5V6" /><path d="M16 20.5v-9" /><path d="M21 20.5V9" /></>),
  token: (<><circle cx="12" cy="12" r="8.6" /><path d="M12 6.8v10.4M15 9.1c-.6-1-1.7-1.5-3-1.5-1.7 0-3 .9-3 2.2 0 2.9 6 1.5 6 4 0 1.3-1.3 2.2-3 2.2-1.3 0-2.4-.5-3-1.5" /></>),
  file: (<><path d="M6 3.5h7.5L18 8v12.5H6z" /><path d="M13.5 3.5V8H18" /></>),
  terminal: (<><rect x="3" y="4.5" width="18" height="15" rx="2" /><path d="M7 9.5l3 3-3 3M12.5 15.5H17" /></>),
  monitor: (<><rect x="3" y="4.5" width="18" height="11.5" rx="2" /><path d="M9.5 20h5M12 16v4" /></>),
  check: (<path d="M4.5 12.5l5 5L19.5 7" />),
  alert: (<><path d="M12 4L2.8 20h18.4L12 4z" /><path d="M12 10v4.5M12 17.4v.3" /></>),
  clock: (<><circle cx="12" cy="12" r="8.6" /><path d="M12 7.5V12l3 2" /></>),
  chat: (<path d="M4 4.5h16V16H9.5L4 20z" />),
  sliders: (<><path d="M4 7h16M4 12h16M4 17h16" /><circle cx="9" cy="7" r="2.1" fill="var(--card)" /><circle cx="15" cy="12" r="2.1" fill="var(--card)" /><circle cx="8" cy="17" r="2.1" fill="var(--card)" /></>),
  shield: (<><path d="M12 3l7 2.5V11c0 4.6-3 7.7-7 9.5C8 18.7 5 15.6 5 11V5.5L12 3z" /><path d="M9 11.5l2.2 2.2 4.3-4.2" /></>),
  key: (<><circle cx="8.5" cy="14.5" r="4.3" /><path d="M11.8 11.2L20 3M17 6l3 3M14.6 8.6l2.4 2.4" /></>),
  bulb: (<><path d="M12 3a6 6 0 0 1 3.6 10.8c-.5.4-.6.8-.6 1.2h-6c0-.4-.1-.8-.6-1.2A6 6 0 0 1 12 3z" /><path d="M9.5 18.5h5M10.5 21h3" /></>),
  zap: (<path d="M13 2.5L4.5 13.5H11l-1.2 8 8.7-11H12l1-8z" />),
  refresh: (<><path d="M20 12a8 8 0 1 1-2.3-5.6" /><path d="M20 3.5V8h-4.5" /></>),
  layers: (<><path d="M12 3l9 5-9 5-9-5 9-5z" /><path d="M3.6 13.5L12 18l8.4-4.5" /></>),
};

export function Icon({ name, size = 18 }: { name: string; size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      {ICON_PATHS[name] ?? ICON_PATHS.file}
    </svg>
  );
}

export function IconChip({ icon, tone = "" }: { icon: string; tone?: string }) {
  return <span className={`icon-chip ${tone}`}><Icon name={icon} size={17} /></span>;
}

export function AvatarStack({ names, size = 26, max = 5, avatars, appearances }: { names: string[]; size?: number; max?: number; avatars?: Array<string | null | undefined>; appearances?: Array<AgentAppearance | Record<string, unknown> | null | undefined> }) {
  const shown = names.slice(0, max);
  return (
    <span className="avatar-stack">
      {shown.map((n, i) => <Avatar key={`${n}-${i}`} name={n} size={size} avatar={avatars?.[i]} appearance={appearances?.[i]} />)}
      {names.length > max && <span className="avatar avatar-more" style={{ width: size, height: size, fontSize: 11 }}>+{names.length - max}</span>}
    </span>
  );
}

/** Pixel-art illustrations for the highest-visibility empty states.
 * Opt-in per call site: minor empty states keep the small line motif. */
export type EmptyArt = "tasks" | "knowledge" | "chat" | "flask";
const EMPTY_ART: Record<EmptyArt, string> = {
  tasks: emptyTasksUrl, knowledge: emptyKnowledgeUrl,
  chat: emptyChatUrl, flask: emptyFlaskUrl,
};

export function EmptyState({ title, hint, actionLabel, onAction, art }: {
  title: string; hint?: string; actionLabel?: string; onAction?: () => void;
  art?: EmptyArt;
}) {
  return (
    <div className="state-box">
      {art ? (
        <img src={EMPTY_ART[art]} alt="" aria-hidden="true"
          style={{ display: "block", height: 92, width: "auto", margin: "2px auto 12px", imageRendering: "pixelated" }} />
      ) : (
      <svg width="34" height="34" viewBox="0 0 28 28" aria-hidden="true" style={{ display: "block", margin: "0 auto 10px" }}>
        <rect x="1.6" y="1.6" width="24.8" height="24.8" rx="7" fill="none" stroke="var(--disabled)" strokeWidth="1.6" strokeDasharray="4 3" />
        <circle cx="9.6" cy="10.2" r="2.1" fill="none" stroke="var(--disabled)" strokeWidth="1.6" />
        <circle cx="18.4" cy="10.2" r="2.1" fill="none" stroke="var(--disabled)" strokeWidth="1.6" />
        <circle cx="14" cy="18.4" r="2.1" fill="none" stroke="var(--disabled)" strokeWidth="1.6" />
      </svg>
      )}
      <div style={{ fontWeight: 600, color: "var(--foreground)" }}>{title}</div>
      {hint && <div style={{ marginTop: 4 }}>{hint}</div>}
      {actionLabel && onAction && (
        <button className="btn" style={{ marginTop: 12 }} onClick={onAction}>{actionLabel}</button>
      )}
    </div>
  );
}

/** Overflow "···" menu. Destructive actions live here (not on the card
 * face) and ask for an in-menu confirmation before firing. */
export function MoreMenu({ items }: {
  items: Array<{ label: string; danger?: boolean; confirmText?: string; onClick: () => void }>;
}) {
  const [open, setOpen] = useStateSafe(false);
  const [confirming, setConfirming] = useStateSafe<number | null>(null);
  const wrapRef = useRef<HTMLSpanElement>(null);
  useEffectSafe(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) { setOpen(false); setConfirming(null); }
    };
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") { setOpen(false); setConfirming(null); } };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("mousedown", onDoc); document.removeEventListener("keydown", onKey); };
  }, [open ]);
  return (
    <span className="menu-wrap" ref={wrapRef}>
      <button className="menu-btn" aria-label="更多操作" aria-haspopup="menu" onClick={() => { setOpen(!open); setConfirming(null); }}>···</button>
      {open && (
        <span className="menu-pop motion-menu-enter" role="menu">
          {confirming === null ? items.map((it, i) => (
            <button key={i} className={`menu-item ${it.danger ? "danger" : ""}`} role="menuitem"
              onClick={() => { if (it.confirmText) { setConfirming(i); } else { setOpen(false); it.onClick(); } }}>
              {it.label}
            </button>
          )) : (
            <span className="menu-confirm">
              <div>{items[confirming].confirmText}</div>
              <span className="row">
                <button className="btn danger sm" onClick={() => { setOpen(false); setConfirming(null); items[confirming].onClick(); }}>确认</button>
                <button className="btn secondary sm" onClick={() => setConfirming(null)}>取消</button>
              </span>
            </span>
          )}
        </span>
      )}
    </span>
  );
}

/** Friendly failure block: Chinese cause + next step first, raw backend
 * error folded away underneath. `onConfigure` jumps to Settings. */
export function ErrorNotice({ errorType, errorMessage, onConfigure, compact }: {
  errorType: string | null; errorMessage: string | null;
  onConfigure?: () => void; compact?: boolean;
}) {
  const info = errorInfo(errorType, errorMessage);
  return (
    <div className="err-notice" style={compact ? { marginTop: 8 } : { marginTop: 10 }}>
      <span className="err-title">{info.title}</span>
      <div className="err-hint">{info.hint}</div>
      {info.needsModel && onConfigure && (
        <button className="btn sm" style={{ marginTop: 8 }} onClick={onConfigure}>去配置模型</button>
      )}
      {(errorType || errorMessage) && (
        <details>
          <summary>原始错误详情</summary>
          <div className="err-raw mono wrap-anywhere">{errorType}{errorType && errorMessage ? "：" : ""}{errorMessage}</div>
        </details>
      )}
    </div>
  );
}

const COMPONENT_LABELS: Record<string, string> = {
  capability_match: "能力", capability: "能力", historical_success: "成功率", success: "成功率",
  current_load: "负载", load: "负载", availability: "可用性", priority_fit: "优先级",
  priority: "优先级", cost: "成本", latency: "时延",
  resource_conflict_risk: "资源冲突风险", env_readiness: "环境就绪", dependency_readiness: "依赖就绪",
};

/** Scheduler candidate comparison: one score bar per eligible agent,
 * the selected one highlighted; filtered-out agents get a short
 * Chinese reason instead of the raw engine string. */
export function CandidateBars({ candidates, selectedAgentId, agentName, agentFace }: {
  candidates: Array<{ agent_id: number; eligible: boolean; filter_reason: string; total: number | null; components: Record<string, number> }>;
  selectedAgentId: number | null;
  agentName: (id: number) => string;
  /** Real avatar/appearance lookup so candidates show the same face as
   * everywhere else instead of the monogram fallback. */
  agentFace?: (id: number) => { avatar?: string | null; appearance?: AgentAppearance | Record<string, unknown> | null } | undefined;
}) {
  const eligible = candidates.filter((c) => c.eligible).sort((a, b) => (b.total ?? 0) - (a.total ?? 0));
  const filtered = candidates.filter((c) => !c.eligible);
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 7 }}>
      {eligible.map((c) => (
        <div key={c.agent_id} className="row" style={{ gap: 10, flexWrap: "nowrap" }}>
          <span style={{ display: "inline-flex", alignItems: "center", gap: 7, minWidth: 128 }}>
            <Avatar name={agentName(c.agent_id)} size={24} avatar={agentFace?.(c.agent_id)?.avatar} appearance={agentFace?.(c.agent_id)?.appearance} />
            <span className="small" style={{ fontWeight: c.agent_id === selectedAgentId ? 700 : 400 }}>{agentName(c.agent_id)}</span>
            {c.agent_id === selectedAgentId && <span className="badge success">已选中</span>}
          </span>
          <span style={{ flex: 1, minWidth: 90 }}>
            <ScoreBar value={c.total ?? 0} tone={c.agent_id === selectedAgentId ? "success" : ""} />
          </span>
          <span className="muted small truncate" style={{ minWidth: 0 }}>
            {Object.entries(c.components).slice(0, 4).map(([k, v]) => `${COMPONENT_LABELS[k] ?? k} ${typeof v === "number" ? v.toFixed(2) : v}`).join(" · ")}
          </span>
        </div>
      ))}
      {filtered.map((c) => (
        <div key={c.agent_id} className="row small" style={{ gap: 8, color: "var(--disabled)", flexWrap: "nowrap" }}>
          <Avatar name={agentName(c.agent_id)} size={24} avatar={agentFace?.(c.agent_id)?.avatar} appearance={agentFace?.(c.agent_id)?.appearance} />
          <span>{agentName(c.agent_id)}</span>
          <span>未参与评分 — {translateFilterReason(c.filter_reason)}</span>
        </div>
      ))}
    </div>
  );
}

export function StateBox({ loading, error, empty, emptyText, emptyHint, emptyActionLabel, onEmptyAction, emptyArt, onRetry, children }: {
  loading: boolean; error: string | null; empty?: boolean; emptyText?: string;
  emptyHint?: string;
  emptyActionLabel?: string; onEmptyAction?: () => void; emptyArt?: EmptyArt;
  /** 报错时显示「重新加载」按钮：没有它用户只能整页刷新。 */
  onRetry?: () => void; children: ReactNode;
}) {
  if (loading) return <div className="state-box">正在加载…</div>;
  if (error) return (
    <div className="state-box error">
      暂时连不上服务，没能加载这一部分。
      {onRetry && (
        <div><button className="btn sm" style={{ marginTop: 10 }} onClick={onRetry}>重新加载</button></div>
      )}
      <details className="fold" style={{ textAlign: "left", maxWidth: 560, margin: "8px auto 0" }}>
        <summary>排查详情</summary>
        <div className="small" style={{ marginTop: 4 }}>{error}。请确认后端服务正在运行，然后重新加载再试一次。</div>
      </details>
    </div>
  );
  if (empty) return <EmptyState title={emptyText ?? "这里还没有内容"} hint={emptyHint} actionLabel={emptyActionLabel} onAction={onEmptyAction} art={emptyArt} />;
  return <>{children}</>;
}

export function StatusBadge({ status }: { status: string }) {
  const cls = status === "completed" || status === "active" || status === "current" ? "success"
    : status === "failed" || status === "Failed" || status === "stale" ? "error"
    : status === "running" || status === "Running" ? "info"
    : status === "disabled" || status === "cancelled" || status === "pending" ? "warning" : "";
  return <span className={`badge motion-color ${cls}`}>{statusLabel(status)}</span>;
}

/**
 * Agent activity states. The fine-grained values come from the backend's
 * derived `activity_status` field (api/routes.py `_agent_activity`),
 * which is computed at read time from real records only:
 *   disabled     <- agent.status == "disabled"
 *   failed       <- runner-set runtime_status == "Failed"
 *   tool_calling <- runtime_status == "Running" and the agent's running
 *                   TaskNode has node_type == "tool" (runner is inside
 *                   the Tool Registry call)
 *   model_calling<- runtime_status == "Running" and the running node is
 *                   an LLM node (runner is inside chat_completion)
 *   running      <- Running with no running node found (teardown race)
 *   idle         <- anything else
 * States with no backend source (thinking/planning/…) are NOT faked.
 */
export const ACTIVITY_LABELS: Record<string, string> = {
  idle: "空闲", running: "工作中", model_calling: "调用模型中",
  tool_calling: "调用工具中", failed: "上次执行失败", disabled: "已禁用",
};
export function activityLabel(activity?: string | null): string {
  return ACTIVITY_LABELS[activity ?? "idle"] ?? "空闲";
}
export function activityDotClass(activity?: string | null): string {
  switch (activity) {
    case "model_calling": return "agent-status-dot is-model motion-status-dot";
    case "tool_calling": return "agent-status-dot is-tool motion-status-dot";
    case "running": return "agent-status-dot is-working motion-status-dot";
    case "failed": return "agent-status-dot is-failed";
    case "disabled": return "agent-status-dot is-waiting";
    default: return "agent-status-dot is-idle";
  }
}

export function AgentStatusDot({ runtimeStatus, status, activityStatus }: { runtimeStatus: string; status?: string; activityStatus?: string }) {
  // Prefer the backend-derived activity state; fall back to the raw
  // runtime_status mapping when the field is absent (older payloads).
  const activity = activityStatus ?? (status === "disabled" ? "disabled" : runtimeStatus === "Running" ? "running" : runtimeStatus === "Failed" ? "failed" : "idle");
  const label = activityLabel(activity);
  return <span className={activityDotClass(activity)} title={label} aria-label={label} />;
}

/**
 * Returns the subset of `ids` that appeared after the first render —
 * used to animate ONLY genuinely new list items (chat messages, tasks,
 * agents, workspace events) instead of replaying entrance animations
 * for the whole list on every reload/poll.
 */
export function useFreshIds<T extends string | number>(ids: readonly T[]): ReadonlySet<T> {
  const seen = useRef<Set<T> | null>(null);
  const fresh = new Set<T>();
  if (seen.current === null) {
    // Not primed yet: an empty first render (data still loading) does not
    // count — the first real batch becomes the baseline, unanimated.
    if (ids.length === 0) return fresh;
    seen.current = new Set(ids);
    return fresh;
  }
  for (const id of ids) {
    if (!seen.current.has(id)) fresh.add(id);
  }
  seen.current = new Set(ids);
  return fresh;
}

/**
 * Animated modal with the accessibility basics the old static .modal
 * lacked: ESC closes, initial focus moves into the dialog, and focus
 * returns to the previously focused element on close. Enter/exit use
 * the modalEnter token preset (opacity + scale 0.98->1 + translateY 4px).
 */
export function AnimatedModal({ open, onClose, children, labelledBy }: {
  open: boolean;
  onClose: () => void;
  children: ReactNode;
  labelledBy?: string;
}) {
  const panelRef = useRef<HTMLDivElement>(null);
  const restoreRef = useRef<Element | null>(null);

  useEffect(() => {
    if (!open) return;
    restoreRef.current = document.activeElement;
    const panel = panelRef.current;
    const first = panel?.querySelector<HTMLElement>(
      "input, select, textarea, button:not([disabled])",
    );
    (first ?? panel)?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      if (restoreRef.current instanceof HTMLElement) restoreRef.current.focus();
    };
  }, [open, onClose]);

  return (
    <AnimatePresence>
      {open && (
        <m.div
          className="modal-backdrop"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0, transition: TRANSITION.exit }}
          transition={TRANSITION.menuEnter}
        >
          <m.div
            ref={panelRef}
            className="modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby={labelledBy}
            tabIndex={-1}
            initial={{ opacity: 0, y: ENTER_OFFSET.modalY, scale: ENTER_OFFSET.modalScale }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, transition: TRANSITION.exit }}
            transition={TRANSITION.modalEnter}
          >
            {children}
          </m.div>
        </m.div>
      )}
    </AnimatePresence>
  );
}

export function useAsync<T>(fn: () => Promise<T>) {
  const [data, setData] = useStateSafe<T | null>(null);
  const [loading, setLoading] = useStateSafe(true);
  const [error, setError] = useStateSafe<string | null>(null);
  // reload/refresh 是稳定的回调，不能直接闭包首轮渲染的 `fn`：有些调用方
  // 用挂载后才会变的状态拼 `fn`（例如当前打开的任务 id），闭包过期会
  // 让它永远按挂载时的参数取数——任务中心「调度决策」面板一直空白，
  // 根源就是这个。
  const fnRef = useRef(fn);
  fnRef.current = fn;
  const reload = useCallbackSafe(async () => {
    setLoading(true); setError(null);
    try { setData(await fnRef.current()); } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setLoading(false); }
  }, []);
  /** Silent re-fetch for polling: updates data in place without flipping
   * `loading`, so a 3–5s poll never flashes the loading state over live
   * content (dashboard board, team chat). Initial and user-triggered
   * loads keep using `reload` so their loading state still shows. */
  const refresh = useCallbackSafe(async () => {
    try { setData(await fnRef.current()); setError(null); } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
  }, []);
  useEffectSafe(() => { void reload(); }, [reload]);
  return { data, loading, error, reload, refresh };
}

// Local aliases keep this helper file self-contained.
import { useState as useStateSafe, useCallback as useCallbackSafe, useEffect as useEffectSafe } from "react";
