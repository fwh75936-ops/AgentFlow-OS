import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../services/api";
import { Avatar, Icon, activityDotClass, agentTitle, eventLabel, statusLabel, useFreshIds } from "../components/ui";
import { usePageVisible } from "../motion/visibility";
import { showToast } from "../motion/Toast";
import { ToolsAndPermissions } from "./Computer";
import type { Agent, DepsStatus, FileChange, SandboxInfo, SandboxVersion, WsCommandLog, WsEvent, WsFile, WsSession, WsStatus } from "../types";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Progress } from "@/components/ui/progress";
import { Separator } from "@/components/ui/separator";
import { cn } from "@/lib/utils";

type ComputerTab = "workspace" | "desktop" | "tools" | "audit";

const CMD_STATUS_LABELS: Record<string, string> = { success: "成功", failed: "失败", blocked: "已拦截", timeout: "超时" };
const cmdStatusLabel = (s: string) => CMD_STATUS_LABELS[s] ?? s;

/** 后端运行环境标识是机器值（docker / local-process），直接显示就是英文。 */
const runtimeKindLabel = (k?: string | null) =>
  k === "docker" ? "容器运行" : k === "local-process" ? "本机进程" : k ?? "—";

/** 文件字节数按 B / KB / MB 分档显示，几 MB 的文件不再是一串难读的数字。 */
function fmtSize(bytes: number): string {
  if (bytes < 1024) return `${bytes.toLocaleString("zh-CN")} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/** 时间统一走 zh-CN 24 小时制：此前五处是 ISO 原串切片（2026-11-19 08:00:00 / 11-19 08:00），与其他页不一致。 */
function fmtTimeFull(iso?: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "" : d.toLocaleString("zh-CN", { hour12: false });
}
function fmtTimeShort(iso?: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "" : d.toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false });
}

const eventTone = (t: string) =>
  t.includes("conflict") || t.includes("lock") ? "warning"
  : t.startsWith("file_created") || t.startsWith("file_modified") ? "success"
  : t.includes("deleted") ? "error"
  : t.startsWith("session") || t.startsWith("desktop") ? "info" : "";

type BadgeTone = "success" | "warning" | "error" | "info" | "";

/** Map the old semantic badge tones onto the Pure Light tints via
 * Tailwind theme tokens; the shadcn Badge itself only ships
 * default/secondary/destructive/outline. */
const badgeToneClass = (tone: BadgeTone) =>
  tone === "success" ? "border-transparent bg-success-tint text-success hover:bg-success-tint"
  : tone === "warning" ? "border-transparent bg-warning-tint text-warning hover:bg-warning-tint"
  : tone === "error" ? "border-transparent bg-errortint text-destructive hover:bg-errortint"
  : tone === "info" ? "border-transparent bg-info-tint text-info hover:bg-info-tint"
  : "";

function ToneBadge({ tone, children }: { tone: BadgeTone; children: React.ReactNode }) {
  return (
    <Badge variant={tone ? "outline" : "secondary"} className={cn("rounded-full", badgeToneClass(tone))}>
      {children}
    </Badge>
  );
}

const diffLineClass = (line: string) =>
  line.startsWith("+") && !line.startsWith("+++") ? "bg-success-tint"
  : line.startsWith("-") && !line.startsWith("---") ? "bg-errortint"
  : line.startsWith("@@") ? "bg-info-tint text-info"
  : "";

// AI 电脑: ALL agents share ONE Linux environment (env shared-linux-1),
// one filesystem (/workspace) and one graphical desktop. Each agent owns
// an independent terminal session; desktop mouse/keyboard control is
// serialised by the lease.
export default function LinuxWorkspace() {
  const [status, setStatus] = useState<WsStatus | null>(null);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [sessions, setSessions] = useState<WsSession[]>([]);
  const [events, setEvents] = useState<WsEvent[]>([]);
  const [files, setFiles] = useState<WsFile[]>([]);
  const [commands, setCommands] = useState<WsCommandLog[]>([]);
  const [sessionLogs, setSessionLogs] = useState<WsCommandLog[]>([]);
  const [selectedSession, setSelectedSession] = useState<number | null>(null);
  const [selectedAgent, setSelectedAgent] = useState<number | null>(null);
  const [command, setCommand] = useState("");
  const [approved, setApproved] = useState(false);
  const [lastOutput, setLastOutput] = useState<string>("");
  const [filePreview, setFilePreview] = useState<string>("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  /** 首屏首次取数完成前，各列表不渲染「暂无…」空态，避免加载中伪装成空。 */
  const [dataLoaded, setDataLoaded] = useState(false);
  const [sessionLogsLoading, setSessionLogsLoading] = useState(false);
  const [tab, setTab] = useState<ComputerTab>("workspace");
  const [sandboxInfo, setSandboxInfo] = useState<SandboxInfo | null>(null);
  const [showEnv, setShowEnv] = useState(false);
  const [midView, setMidView] = useState<"terminal" | "desktop">("terminal");
  const [pendingChanges, setPendingChanges] = useState<FileChange[]>([]);
  const [decidedChanges, setDecidedChanges] = useState<FileChange[]>([]);
  const [changesLoaded, setChangesLoaded] = useState(false);
  const [changeBusyId, setChangeBusyId] = useState<number | null>(null);
  const [changeError, setChangeError] = useState<string | null>(null);
  const [expandedDiffs, setExpandedDiffs] = useState<ReadonlySet<number>>(new Set());
  const [showHistory, setShowHistory] = useState(false);
  // 安装失败日志默认只露两行，点一下再看全文：长报错不再把整行卡片撑高。
  const [expandedLogs, setExpandedLogs] = useState<ReadonlySet<string>>(new Set());

  const loadFileChanges = useCallback(async () => {
    try {
      const [pending, accepted, rejected] = await Promise.all([
        api.wsFileChanges("pending"), api.wsFileChanges("accepted"), api.wsFileChanges("rejected"),
      ]);
      setPendingChanges(pending);
      const decided = [...accepted, ...rejected].sort((a, b) => (b.decided_at ?? b.created_at ?? "").localeCompare(a.decided_at ?? a.created_at ?? ""));
      setDecidedChanges(decided.slice(0, 10));
      setChangesLoaded(true);
    } catch { /* card simply keeps its last state */ }
  }, []);

  const refresh = useCallback(async () => {
    try {
      const [st, ag, se, ev, fi, cm] = await Promise.all([
        api.wsStatus(), api.agents(), api.wsSessions(), api.wsEvents(), api.wsFiles(), api.wsCommands(),
      ]);
      setStatus(st); setAgents(ag); setSessions(se); setEvents(ev); setFiles(fi); setCommands(cm);
      setError(null);
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setDataLoaded(true); }
  }, []);

  // Keep-alive: this page stays mounted when hidden. Polling pauses while
  // hidden (or while the window itself is hidden) but no state is dropped
  // and neither the terminal nor the desktop area is ever remounted.
  const visible = usePageVisible();
  const visibleRef = useRef(visible);
  visibleRef.current = visible;
  useEffect(() => {
    void refresh();
    const t = setInterval(() => {
      if (visibleRef.current && !document.hidden) void refresh();
    }, 5000);
    return () => clearInterval(t);
  }, [refresh]);
  useEffect(() => { if (visible) void refresh(); }, [visible, refresh]);

  useEffect(() => {
    void loadFileChanges();
    const t = setInterval(() => {
      if (visibleRef.current && !document.hidden) void loadFileChanges();
    }, 5000);
    return () => clearInterval(t);
  }, [loadFileChanges]);
  useEffect(() => { if (visible) void loadFileChanges(); }, [visible, loadFileChanges]);

  const [deps, setDeps] = useState<DepsStatus | null>(null);

  const loadSandbox = useCallback(async () => {
    try { setSandboxInfo(await api.wsSandbox()); } catch { /* card simply stays hidden */ }
  }, []);
  const loadDeps = useCallback(async () => {
    try { setDeps(await api.wsDeps()); } catch { /* section simply stays hidden */ }
  }, []);
  useEffect(() => { void loadSandbox(); void loadDeps(); }, [loadSandbox, loadDeps]);

  const anyInstalling = sandboxInfo?.distros.some((d) => d.versions.some((v) => v.install_status === "installing")) ?? false;
  const depsRunning = deps?.job?.status === "running";
  useEffect(() => {
    if (!anyInstalling && !depsRunning) return;
    const t = setInterval(() => { if (visibleRef.current && !document.hidden) { void loadSandbox(); void loadDeps(); } }, 2500);
    return () => clearInterval(t);
  }, [anyInstalling, depsRunning, loadSandbox, loadDeps]);

  async function selectSandbox(distroId: string, versionId: string) {
    if (sandboxInfo?.selected_distro === distroId && sandboxInfo?.selected_version === versionId) return;
    if (busy) return;
    setBusy(true);
    try {
      const info = await api.wsSandboxSelect(distroId, versionId);
      setSandboxInfo(info);
      showToast("success", info.message ?? "已设为当前沙箱。");
    } catch (e) {
      showToast("error", `设置失败：${e instanceof Error ? e.message : String(e)}`);
    } finally { setBusy(false); }
  }

  async function installSandbox(distroId: string, v: SandboxVersion) {
    if (busy) return;
    setBusy(true);
    try {
      const rec = await api.wsSandboxInstall(distroId, v.id);
      if (rec.status === "failed") {
        showToast("error", `安装失败：${rec.log.split("\n")[0] ?? "请查看失败原因"}`);
      } else if (rec.status === "installing") {
        showToast("success", "已开始安装，进度会自动刷新。");
      } else if (rec.status === "installed") {
        showToast("success", "安装完成，可以设为当前沙箱了。");
      }
      await loadSandbox();
    } catch (e) {
      showToast("error", `安装失败：${e instanceof Error ? e.message : String(e)}`);
    } finally { setBusy(false); }
  }

  async function installDeps() {
    if (busy) return;
    setBusy(true);
    try {
      await api.wsDepsInstall();
      showToast("success", "已开始安装常用依赖，进度会自动刷新。");
      await loadDeps();
    } catch (e) {
      showToast("error", `启动失败：${e instanceof Error ? e.message : String(e)}`);
    } finally { setBusy(false); }
  }

  const freshEvents = useFreshIds(events.map((e) => e.id));

  useEffect(() => {
    if (selectedSession == null) { setSessionLogs([]); return; }
    setSessionLogsLoading(true);
    void api.wsSessionLogs(selectedSession).then(setSessionLogs).catch(() => setSessionLogs([])).finally(() => setSessionLogsLoading(false));
  }, [selectedSession, commands]);

  const agentName = (id: number | null) => agents.find((a) => a.id === id)?.name ?? (id == null ? "系统" : `已删除的员工`);
  const agentAvatar = (id: number | null) => agents.find((a) => a.id === id)?.avatar;
  const agentAppearance = (id: number | null) => agents.find((a) => a.id === id)?.appearance;
  const lease = status?.desktop_lease;
  const desktop = status?.desktop;
  const fileCount = files.filter((f) => !f.is_dir).length;
  const leaseText = lease?.holder_type === "user" ? "你"
    : lease?.holder_agent_id ? agentName(lease.holder_agent_id) : null;
  const leaseRemain = (() => {
    if (!lease?.expires_at || !leaseText) return "";
    const ms = new Date(lease.expires_at).getTime() - Date.now();
    if (Number.isNaN(ms)) return "";
    if (ms <= 0) return " · 租约已到期";
    const s = Math.floor(ms / 1000);
    return ` · 剩余 ${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
  })();

  /** after 返回 false 表示本次操作实际失败（只给失败反馈，不再补成功提示）。 */
  async function act(fn: () => Promise<unknown>, after?: (r: unknown) => boolean | void, okMsg?: string) {
    setBusy(true); setError(null);
    try {
      const r = await fn();
      const ok = after ? after(r) : true;
      if (ok !== false && okMsg) showToast("success", okMsg);
      await refresh();
    }
    catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  }

  async function openSession(agentId: number) {
    await act(() => api.wsCreateSession(agentId), (r) => setSelectedSession((r as WsSession).id), `已为 ${agentName(agentId)} 打开终端。`);
    setSelectedAgent(agentId);
  }

  async function runCommand() {
    // Enter 键直调这里，会绕过执行按钮的 disabled 守卫：忙时再按一次
    // 会把同一条命令执行两遍，入口必须自己拦。
    if (selectedSession == null || !command.trim() || busy) return;
    setBusy(true); setError(null);
    try {
      const r = await api.wsExec(selectedSession, command, approved);
      setLastOutput(`$ ${command}\n[退出码 ${r.exit_code ?? "—"} · ${cmdStatusLabel(r.status)}]\n${r.stdout}${r.stderr ? `\n[错误输出]\n${r.stderr}` : ""}`);
      setCommand("");
      await refresh();
    } catch (e) {
      setLastOutput(`$ ${command}\n[已被拦截] ${e instanceof Error ? e.message : String(e)}`);
    } finally { setBusy(false); }
  }

  function toggleDiff(id: number) {
    setExpandedDiffs((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  }

  async function acceptChange(change: FileChange) {
    setChangeBusyId(change.id); setChangeError(null);
    try {
      const r = await api.wsAcceptFileChange(change.id);
      showToast("success", `已接受并写入，${change.path} 更新为第 ${r.write.version} 版。`);
      await Promise.all([loadFileChanges(), refresh()]);
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setChangeError(msg);
      showToast("error", `接受失败：${msg}`);
    } finally { setChangeBusyId(null); }
  }

  async function rejectChange(change: FileChange) {
    setChangeBusyId(change.id); setChangeError(null);
    try {
      await api.wsRejectFileChange(change.id);
      showToast("success", `已拒绝 ${change.path} 的待审变更，文件未做改动。`);
      await loadFileChanges();
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setChangeError(msg);
      showToast("error", `拒绝失败：${msg}`);
    } finally { setChangeBusyId(null); }
  }

  const desktopCard = (
    <Card className="p-4">
      <div className="spread">
        <div style={{ fontWeight: 600 }}>共享桌面</div>
        <ToneBadge tone={desktop?.state === "running" ? "success" : desktop?.state === "unavailable" ? "error" : "warning"}>
          {desktop?.state === "running" ? "运行中" : desktop?.state === "degraded" ? "网页查看不可用" : desktop?.state === "unavailable" ? "不可用" : desktop?.state === "stopped" ? "已停止" : "…"}
        </ToneBadge>
      </div>
      <div
        key={`${lease?.holder_type ?? "none"}:${lease?.holder_agent_id ?? ""}`}
        className="small lease-banner motion-fade-enter"
        style={{ marginTop: 10, background: lease?.holder_type === "user" ? "var(--secondary)" : lease?.holder_agent_id ? "var(--info-tint)" : "transparent" }}
      >
        {leaseText
          ? <>桌面由 <b>{leaseText}</b> 控制中{leaseRemain}。其他员工可以观看，只有控制者能操作鼠标键盘。</>
          : <>桌面控制权空闲，员工可以申请控制。</>}
        {lease && lease.queue.length > 0 && <> 等待队列：{lease.queue.map((q) => agentName(q.agent_id)).join(" → ")}。</>}
      </div>
      <div className="row" style={{ marginTop: 10 }}>
        <Button size="sm" disabled={busy || desktop?.state !== "stopped"} onClick={() => void act(() => api.wsDesktopStart(), (r) => {
          // 启动接口失败时返回 HTTP 200 的 {started: false, error}，不抛
          // 异常：不检查返回值，点完就什么反馈都没有、状态还停在「已停止」。
          const res = r as { started?: boolean; missing?: string[]; error?: string } | null;
          if (res?.started === false) {
            const msg = res.error
              ?? (res.missing?.length ? `缺少桌面组件：${res.missing.join("、")}，先安装后再启动。` : "桌面启动失败，请查看桌面连接详情。");
            setError(msg);
            showToast("error", `启动桌面失败：${msg}`);
            return false;
          }
        }, "桌面已启动。")}>启动桌面</Button>
        <Button size="sm" variant="secondary" disabled={busy || (desktop?.state !== "running" && desktop?.state !== "degraded")} onClick={() => void act(() => api.wsDesktopStop(), undefined, "桌面已停止。")}>停止桌面</Button>
        {desktop?.novnc_url && <Button size="sm" variant="secondary" asChild><a href={desktop.novnc_url} target="_blank" rel="noreferrer">打开桌面画面</a></Button>}
        {desktop?.novnc_url && desktop?.vnc_password && <span className="small">桌面密码 {desktop.vnc_password}</span>}
      </div>
      <div className="row" style={{ marginTop: 8 }}>
        <Button size="sm" disabled={busy || selectedAgent == null} onClick={() => selectedAgent != null && void act(() => api.wsLeaseAcquire(selectedAgent), undefined, `已让 ${agentName(selectedAgent)} 申请桌面控制。`)}>让所选员工申请控制</Button>
        <Button size="sm" variant="secondary" disabled={busy || selectedAgent == null} onClick={() => selectedAgent != null && void act(() => api.wsLeaseRenew(selectedAgent), undefined, `已为 ${agentName(selectedAgent)} 续租桌面控制。`)}>续租</Button>
        <Button size="sm" variant="secondary" disabled={busy || selectedAgent == null} onClick={() => selectedAgent != null && void act(() => api.wsLeaseRelease(selectedAgent), undefined, "已释放桌面控制。")}>释放</Button>
        <Button size="sm" variant="destructive" disabled={busy} onClick={() => void act(() => api.wsLeaseForceTakeover(), undefined, "已接管桌面控制。")}>我来接管</Button>
        {lease?.holder_type === "user" && <Button size="sm" variant="secondary" disabled={busy} onClick={() => void act(() => api.wsLeaseUserRelease(), undefined, "已交还桌面控制。")}>交还控制</Button>}
      </div>
      <details className="fold">
        <summary>桌面连接详情</summary>
        <div className="small muted" style={{ marginTop: 4, lineHeight: 1.7 }}>
          桌面环境 {desktop?.desktop_environment} · 显示 {desktop?.display} · 远程桌面端口 {desktop?.vnc_port}，仅本机访问 · 网页桌面 {desktop?.novnc_url ?? "未启动"}
          {desktop && desktop.missing.length > 0 && <><br /><span style={{ color: "var(--destructive)" }}>这台机器缺少组件：{desktop.missing.join("、")}，桌面暂时无法启动；打包好的容器镜像里包含完整组件。</span></>}
        </div>
      </details>
    </Card>
  );

  const auditCard = (
    <Card className="p-4">
      <div style={{ fontWeight: 600, marginBottom: 4 }}>命令审计</div>
      <div className="small muted" style={{ marginBottom: 8 }}>这台电脑上执行过的每条命令都有记录：谁执行的、结果如何。</div>
      <ScrollArea className="h-[380px] pr-3">
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {commands.map((l) => (
            <div key={l.id} className="ws-feed-item">
              <div className="mono small">$ {l.command}</div>
              <div className="small muted">{agentName(l.agent_id)}{l.task_id ? ` · 任务 #${l.task_id}` : ""} · 退出码 {l.exit_code ?? "—"} · {cmdStatusLabel(l.status)} · {fmtTimeFull(l.started_at)}</div>
            </div>
          ))}
          {commands.length === 0 && <div className="small muted">{dataLoaded ? "暂无命令记录。" : "正在加载命令记录…"}</div>}
        </div>
      </ScrollArea>
    </Card>
  );

  const fileChangesCard = (
    <Card className="p-4">
      <div className="spread" style={{ alignItems: "center" }}>
        <div style={{ fontWeight: 600 }}>待审文件变更</div>
        {pendingChanges.length > 0 && <ToneBadge tone="warning">{pendingChanges.length} 条待审</ToneBadge>}
      </div>
      <div className="small muted" style={{ marginTop: 4, lineHeight: 1.7 }}>
        员工写共享文件会先暂存到这里，你接受后才真正写入；文件已被别人改过时接受会失败，原因会如实显示。
      </div>
      {changeError && (
        <div className="err-notice" style={{ marginTop: 10 }}>
          <span className="err-title">处理这条变更时出错</span>
          <div className="err-hint wrap-anywhere">{changeError}</div>
        </div>
      )}
      <div style={{ display: "flex", flexDirection: "column", gap: 10, marginTop: 10 }}>
        {!changesLoaded && <div className="small muted">正在加载待审变更…</div>}
        {changesLoaded && pendingChanges.length === 0 && (
          <div className="small muted">暂无待审变更。员工提交的文件变更会先出现在这里，接受后才会写入共享文件。</div>
        )}
        {pendingChanges.map((c) => {
          const expanded = expandedDiffs.has(c.id);
          const acting = changeBusyId === c.id;
          return (
            <div key={c.id} className="rounded-lg border border-border p-3">
              <div style={{ display: "flex", gap: 7, alignItems: "center", flexWrap: "wrap" }}>
                <Icon name="file" size={15} />
                <span className="mono small" style={{ fontWeight: 600 }}>{c.path}</span>
                {c.base_version == null
                  ? <ToneBadge tone="info">新文件</ToneBadge>
                  : <ToneBadge tone="">基于第 {c.base_version} 版</ToneBadge>}
              </div>
              <div className="small muted" style={{ marginTop: 6, display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
                <span style={{ display: "inline-flex", gap: 5, alignItems: "center" }}>
                  <Avatar name={agentName(c.agent_id)} size={18} avatar={agentAvatar(c.agent_id)} appearance={agentAppearance(c.agent_id)} />{agentName(c.agent_id)} 提交
                </span>
                {c.task_id != null && <span>任务 #{c.task_id}</span>}
                <span>{fmtTimeShort(c.created_at)}</span>
              </div>
              <div style={{ display: "flex", gap: 6, marginTop: 8, flexWrap: "wrap" }}>
                <ToneBadge tone="success">新增 {c.diff_summary?.added_lines ?? 0} 行</ToneBadge>
                <ToneBadge tone="error">删除 {c.diff_summary?.removed_lines ?? 0} 行</ToneBadge>
                <Button size="sm" variant="ghost" onClick={() => toggleDiff(c.id)}>{expanded ? "收起差异" : "查看差异"}</Button>
              </div>
              {expanded && (
                <div className="rounded-md border border-border font-mono text-xs" style={{ marginTop: 8, maxHeight: 260, overflow: "auto" }}>
                  {(c.diff_text || "").split("\n").map((line, i) => (
                    <div key={i} className={cn("whitespace-pre-wrap break-all px-2 py-0.5", diffLineClass(line))}>{line || " "}</div>
                  ))}
                  {!c.diff_text && <div className="px-2 py-1 text-muted-foreground">这条变更没有差异文本。</div>}
                </div>
              )}
              <div className="row" style={{ marginTop: 10 }}>
                <Button size="sm" disabled={acting || changeBusyId != null} onClick={() => void acceptChange(c)}>接受并写入</Button>
                <Button size="sm" variant="secondary" disabled={acting || changeBusyId != null} onClick={() => void rejectChange(c)}>拒绝</Button>
                {acting && <span className="small muted">正在处理…</span>}
              </div>
            </div>
          );
        })}
      </div>
      {decidedChanges.length > 0 && (
        <>
          <Separator className="my-3" />
          <Button size="sm" variant="ghost" onClick={() => setShowHistory(!showHistory)}>
            {showHistory ? "收起最近处理记录" : `查看最近处理记录 · ${decidedChanges.length} 条`}
          </Button>
          {showHistory && (
            <div style={{ display: "flex", flexDirection: "column", gap: 8, marginTop: 8 }}>
              {decidedChanges.map((c) => (
                <div key={c.id} className="ws-feed-item">
                  <div style={{ display: "flex", gap: 7, alignItems: "center", flexWrap: "wrap" }}>
                    <span className="mono small" style={{ fontWeight: 600 }}>{c.path}</span>
                    <ToneBadge tone={c.status === "accepted" ? "success" : "error"}>{statusLabel(c.status)}</ToneBadge>
                  </div>
                  <div className="small muted" style={{ marginTop: 3, display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
                    <span style={{ display: "inline-flex", gap: 5, alignItems: "center" }}>
                      <Avatar name={agentName(c.agent_id)} size={18} avatar={agentAvatar(c.agent_id)} appearance={agentAppearance(c.agent_id)} />{agentName(c.agent_id)}
                    </span>
                    <span>新增 {c.diff_summary?.added_lines ?? 0} 行 · 删除 {c.diff_summary?.removed_lines ?? 0} 行</span>
                    <span>{fmtTimeShort(c.decided_at ?? c.created_at)}</span>
                  </div>
                </div>
              ))}
            </div>
          )}
        </>
      )}
    </Card>
  );

  return (
    <div>
      <div className="spread">
        <div>
          <h1 className="page-title">AI 电脑</h1>
          <p className="page-desc">团队共用的那台 Linux 电脑 — 同一台电脑、同一套文件，像坐在同一间办公室里协作。</p>
        </div>
        <Button variant="secondary" onClick={() => setShowEnv(!showEnv)}>{showEnv ? "收起环境详情" : "环境详情"}</Button>
      </div>
      <Tabs value={tab} onValueChange={(v) => setTab(v as ComputerTab)} className="mb-[14px]">
        <TabsList>
          <TabsTrigger value="workspace">工作区</TabsTrigger>
          <TabsTrigger value="desktop">共享桌面</TabsTrigger>
          <TabsTrigger value="tools">工具与权限</TabsTrigger>
          <TabsTrigger value="audit">命令审计</TabsTrigger>
        </TabsList>
      </Tabs>
      {error && (
        <div className="err-notice" style={{ marginBottom: 14 }}>
          <span className="err-title">刚才的操作没能完成</span>
          <div className="err-hint">可能是权限不足、状态冲突，或后端服务暂时不可用。先刷新确认当前状态，再决定是否重试；具体原因在下方详情里。</div>
          <details><summary>原始错误详情</summary><div className="err-raw mono wrap-anywhere">{error}</div></details>
        </div>
      )}

      <Card className="mb-[14px] px-4 py-3">
        <div className="spread" style={{ alignItems: "center" }}>
          <div className="small" style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
            <span className={`conn-dot ${status ? "ok" : ""}`} style={{ marginRight: 0, ...(status ? {} : { background: "var(--disabled)" }) }} />
            <b>{status ? "共享电脑运行中" : "正在连接共享电脑…"}</b>
            <span className="muted">· {sessions.filter((x) => x.status === "active").length} 个终端会话 · {leaseText ? <>桌面由 {leaseText} 控制{leaseRemain}</> : "桌面空闲"} · 共享文件 {fileCount} 个</span>
          </div>
          <Button variant="secondary" size="sm" disabled={busy} onClick={() => void refresh()}>刷新</Button>
        </div>
      </Card>

      {sandboxInfo && (
        <Card className="mb-[14px] p-4">
          <div className="spread">
            <div style={{ fontWeight: 600 }}>沙箱系统</div>
            <ToneBadge tone={sandboxInfo.isolation_mode === "host" ? "warning" : "success"}>
              {sandboxInfo.isolation_mode === "container" ? "容器沙箱"
                : sandboxInfo.isolation_mode === "host_sandbox" ? "本机沙箱" : "本机模式"}
            </ToneBadge>
          </div>
          <div className="small muted" style={{ marginTop: 4, lineHeight: 1.7 }}>
            {sandboxInfo.selected_distro
              ? <>当前沙箱：<b style={{ color: "var(--foreground)" }}>{sandboxInfo.distros.find((d) => d.id === sandboxInfo.selected_distro)?.name} {sandboxInfo.distros.find((d) => d.id === sandboxInfo.selected_distro)?.versions.find((v) => v.id === sandboxInfo.selected_version)?.label}</b>。先安装一个系统，再把它设为当前沙箱。</>
              : <>当前<b style={{ color: "var(--foreground)" }}>尚未选择沙箱</b>。先选一个系统点安装，装好后才能设为当前沙箱。</>}
            只列用户量最大、兼容性最好的主流发行版，且都在维护期内。这台机器本身是本机环境，不算已安装的沙箱。{sandboxInfo.note}
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(2, 1fr)", gap: 10, marginTop: 12 }}>
            {sandboxInfo.distros.map((d) => (
              <div key={d.id} className={cn("rounded-lg border border-border bg-card p-3 transition-colors hover:bg-hover", d.selected && "border-primary bg-secondary")} style={{ display: "flex", flexDirection: "column" }}>
                <div className="spread">
                  <span style={{ fontWeight: 600 }}>{d.name}</span>
                  {d.recommended && <ToneBadge tone="info">推荐</ToneBadge>}
                </div>
                <div className="small muted" style={{ marginTop: 4 }}>{d.description}</div>
                <div style={{ display: "flex", gap: 8, marginTop: 10, flexWrap: "wrap", flex: 1 }}>
                  {d.versions.map((v) => (
                    <div key={v.id} style={{ border: "1px solid var(--border)", borderRadius: 8, padding: "8px 10px", minWidth: 168, flex: "1 1 168px", display: "flex", flexDirection: "column" }}>
                      <div className="spread">
                        <span style={{ fontWeight: 600 }}>{v.label}</span>
                        {v.install_status === "installed" && (v.selected
                          ? <ToneBadge tone="success">当前沙箱</ToneBadge>
                          : <ToneBadge tone="success">已安装</ToneBadge>)}
                        {v.install_status === "installing" && <ToneBadge tone="warning">安装中</ToneBadge>}
                        {v.install_status === "failed" && <ToneBadge tone="error">安装失败</ToneBadge>}
                        {v.install_status === "not_installed" && <ToneBadge tone="">未安装</ToneBadge>}
                      </div>
                      <div className="small muted" style={{ marginTop: 2 }}>维护至 {v.maintenance_until}</div>
                      {v.install_status === "installed" && v.install_method && (
                        <div className="small muted" style={{ marginTop: 2 }}>
                          安装方式：{v.install_method === "host-native" ? "本机直装，独立系统目录" : "容器镜像"}
                        </div>
                      )}
                      <div style={{ marginTop: "auto", paddingTop: 8 }}>
                        {v.install_status === "not_installed" && (
                          <Button size="sm" disabled={busy} onClick={() => void installSandbox(d.id, v)}>安装</Button>
                        )}
                        {v.install_status === "installing" && (
                          <Button size="sm" variant="secondary" disabled>安装中，进度自动刷新</Button>
                        )}
                        {v.install_status === "installed" && !v.selected && (
                          <Button size="sm" disabled={busy} onClick={() => void selectSandbox(d.id, v.id)}>设为当前沙箱</Button>
                        )}
                        {v.install_status === "failed" && (
                          <Button size="sm" disabled={busy} onClick={() => void installSandbox(d.id, v)}>重新安装</Button>
                        )}
                      </div>
                      {v.install_status === "installing" && v.install_log && (
                        <div className="small muted" style={{ marginTop: 6, lineHeight: 1.6 }}>
                          {v.install_log.split("\n").filter(Boolean).slice(-1)[0]}
                        </div>
                      )}
                      {v.install_status === "failed" && v.install_log && (() => {
                        const logKey = `${d.id}:${v.id}`;
                        const logLine = v.install_log.split("\n").filter(Boolean).slice(-1)[0] ?? "";
                        const expanded = expandedLogs.has(logKey);
                        return (
                          <div>
                            <div className="small" title={logLine} style={{
                              marginTop: 6, color: "var(--destructive)", lineHeight: 1.6,
                              ...(expanded ? {} : {
                                display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical",
                                overflow: "hidden", overflowWrap: "anywhere",
                              }),
                            }}>
                              {logLine}
                            </div>
                            {logLine.length > 90 && (
                              <button className="small muted" style={{ background: "none", border: "none", cursor: "pointer", padding: 0, marginTop: 2 }}
                                onClick={() => setExpandedLogs((prev) => {
                                  const next = new Set(prev);
                                  if (next.has(logKey)) next.delete(logKey); else next.add(logKey);
                                  return next;
                                })}>
                                {expanded ? "收起" : "展开全文"}
                              </button>
                            )}
                          </div>
                        );
                      })()}
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </Card>
      )}

      {deps && (
        <Card className="mb-[14px] p-4">
          <div className="spread">
            <div style={{ fontWeight: 600 }}>常用依赖</div>
            <ToneBadge tone="">{deps.family === "apt" ? "apt 系" : deps.family === "dnf" ? "dnf 系" : "未检测到包管理器"}</ToneBadge>
          </div>
          <div className="small muted" style={{ marginTop: 4, lineHeight: 1.7 }}>
            {deps.sandbox
              ? <>当前沙箱是 <b style={{ color: "var(--foreground)" }}>{deps.sandbox.label}</b>，依赖会装进这个沙箱系统内，不影响这台电脑本身。已经装好的会显示已存在，不会重复安装；每条安装命令都会记入命令审计。</>
              : <>一键把开发常用工具装到这台 AI 电脑上。已经装好的会显示已存在，不会重复安装；每条安装命令都会记入命令审计。</>}
          </div>
          <div style={{ display: "flex", flexDirection: "column", marginTop: 8 }}>
            {deps.packages.map((p) => {
              const dot = p.status === "exists" || p.status === "installed" ? "var(--success)"
                : p.status === "failed" ? "var(--destructive)"
                : p.status === "installing" ? "var(--warning)" : "var(--border)";
              const text = p.status === "exists" ? "已存在"
                : p.status === "installed" ? "新安装"
                : p.status === "failed" ? "安装失败"
                : p.status === "installing" ? "安装中"
                : p.status === "pending" ? "等待中" : p.status === "missing" ? "未安装" : "状态未知";
              return (
                <div key={p.key} className="person-row" style={{ cursor: "default" }}>
                  <span style={{ width: 9, height: 9, borderRadius: "50%", background: dot, flexShrink: 0 }} />
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontWeight: 500 }}>{p.label}</div>
                    <div className="muted small mono">{p.packages.join(" ")}</div>
                    {p.status === "failed" && p.detail && (
                      <div className="small" style={{ color: "var(--destructive)", lineHeight: 1.6 }}>{p.detail.slice(-160)}</div>
                    )}
                  </div>
                  <span className="small muted">{text}</span>
                </div>
              );
            })}
          </div>
          <div className="row" style={{ marginTop: 10, alignItems: "center" }}>
            <Button disabled={busy || depsRunning || !deps.family} onClick={() => void installDeps()}>
              {depsRunning ? "正在安装，进度自动刷新" : "一键安装常用依赖"}
            </Button>
            {deps.job && deps.job.status !== "running" && deps.job.log && (
              <span className="small muted">{deps.job.log}</span>
            )}
          </div>
        </Card>
      )}

      {showEnv && (
        <Card className="motion-item-enter mb-[14px] p-4">
          <div style={{ fontWeight: 600, marginBottom: 6 }}>环境详情</div>
          <div className="small muted" style={{ lineHeight: 1.8 }}>
            {status ? <>
              系统 {status.os} · 主机 {status.hostname} · 环境编号 {status.env_id} · {runtimeKindLabel(status.runtime_kind)}<br />
              共享目录 {status.logical_root} → {status.shared_root}<br />
              已用磁盘 {(status.disk_used_bytes / 1024).toFixed(1)} KB / 配额 {status.limits.disk_quota_mb} MB · 处理器 {status.limits.cpu_cores} 核 · 内存 {status.limits.memory_mb} MB · 进程上限 {status.limits.max_processes}<br />
              安全设置：{status.security.docker_socket_mounted ? "容器套接字已挂载" : "容器套接字未挂载"} · 远程桌面绑定 {status.security.vnc_bind} · 高风险命令{status.security.high_risk_commands_require_approval ? "需要人工审批" : "无需审批"}
              {status.is_root && <><br /><span style={{ color: "var(--warning)" }}>提示：当前开发机上的后端进程以管理员权限运行，属于本机环境限制；正式部署的镜像中为普通用户运行。</span></>}
            </> : "正在读取环境状态…"}
          </div>
          {status && status.limits.disk_quota_mb > 0 && (
            <div style={{ marginTop: 10, maxWidth: 420 }}>
              <div className="small muted">磁盘使用 {Math.min(100, Math.round((status.disk_used_bytes / (status.limits.disk_quota_mb * 1024 * 1024)) * 100))}%</div>
              <Progress className="mt-1" value={Math.min(100, (status.disk_used_bytes / (status.limits.disk_quota_mb * 1024 * 1024)) * 100)} />
            </div>
          )}
          <div className="row" style={{ marginTop: 10 }}>
            <Button variant="secondary" size="sm" disabled={busy} onClick={() => void act(() => api.wsSeedDemo(), undefined, "演示团队已初始化。")}>初始化演示团队 · 3 人</Button>
          </div>
        </Card>
      )}

      {tab === "tools" ? <ToolsAndPermissions /> : tab === "audit" ? auditCard : tab === "desktop" ? desktopCard : (
      <div className="ws-grid">
        {/* LEFT: agents + sessions */}
        <div className="ws-col">
          <Card className="p-4">
            <div style={{ fontWeight: 600, marginBottom: 6 }}>员工与会话</div>
            {agents.length === 0 && <div className="small muted">{dataLoaded ? "暂无员工。在「环境详情」里初始化演示团队，或去 AI 员工页创建。" : "正在加载员工与会话…"}</div>}
            <div style={{ display: "flex", flexDirection: "column" }}>
              {agents.map((a) => {
                const mySessions = sessions.filter((x) => x.agent_id === a.id && x.status === "active");
                return (
                  <div key={a.id} className="person-row" style={{ cursor: "pointer", ...(selectedAgent === a.id ? { background: "var(--secondary)", borderRadius: 8 } : {}) }} onClick={() => setSelectedAgent(a.id)}>
                    <Avatar name={a.name} size={32} avatar={a.avatar} appearance={a.appearance} />
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div className="truncate" style={{ fontWeight: 500 }}>{agentTitle(a.name, a.role)}</div>
                      <div className="muted small">{mySessions.length > 0 ? `会话 #${mySessions[0].id} · ${a.runtime_status === "Running" ? "正在看这个终端" : "已连接"}` : "尚未打开终端"}</div>
                    </div>
                    <span className={activityDotClass(a.activity_status)} />
                    <Button size="sm" variant="secondary" disabled={busy} onClick={(e) => { e.stopPropagation(); void openSession(a.id); }}>打开终端</Button>
                  </div>
                );
              })}
            </div>
            {sessions.length > 0 && <>
              <div style={{ fontWeight: 600, margin: "12px 0 6px" }}>终端会话</div>
              <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                {sessions.map((s) => (
                  <div key={s.id} className={cn("cursor-pointer rounded-lg border border-border bg-card p-2 transition-colors hover:bg-hover", selectedSession === s.id && "border-primary bg-secondary")} onClick={() => setSelectedSession(s.id)}>
                    <div className="spread"><span className="small" style={{ fontWeight: 600 }}>{agentName(s.agent_id)} 的终端</span><ToneBadge tone={s.status === "active" ? "success" : "warning"}>{s.status === "active" ? "进行中" : "已关闭"}</ToneBadge></div>
                    <div className="small muted" style={{ marginTop: 2 }}>目录 /workspace/{s.cwd}</div>
                    {s.status === "active" && <Button size="sm" variant="secondary" style={{ marginTop: 6 }} disabled={busy} onClick={(e) => { e.stopPropagation(); void act(() => api.wsCloseSession(s.id), undefined, "已关闭终端会话。").then(() => { if (selectedSession === s.id) setSelectedSession(null); }); }}>关闭会话</Button>}
                  </div>
                ))}
              </div>
            </>}
          </Card>
        </div>

        {/* MIDDLE: terminal (default) / desktop, focused */}
        <div className="ws-col">
          <Tabs value={midView} onValueChange={(v) => setMidView(v as "terminal" | "desktop")}>
            <TabsList>
              <TabsTrigger value="terminal">终端{selectedSession != null ? ` · ${agentName(sessions.find((x) => x.id === selectedSession)?.agent_id ?? null)}` : ""}</TabsTrigger>
              <TabsTrigger value="desktop">共享桌面</TabsTrigger>
            </TabsList>
          </Tabs>
          {midView === "desktop" ? desktopCard : (
          <Card className="p-4">
            {selectedSession == null && (
              <div style={{ padding: "18px 6px", textAlign: "center" }}>
                <div style={{ fontWeight: 600 }}>先选一位员工，打开他的终端</div>
                <div className="muted small" style={{ marginTop: 4 }}>每位员工在这台电脑上都有自己独立的终端，互不干扰。</div>
              </div>
            )}
            {selectedSession != null && <>
              <div className="row" style={{ flexWrap: "nowrap" }}>
                <Input className="mono flex-1" value={command} placeholder="输入命令，如 ls -la 或 python3 -m pytest -q"
                  onChange={(e) => setCommand(e.target.value)}
                  onKeyDown={(e) => { if (e.key === "Enter") void runCommand(); }} />
                <Button disabled={busy} onClick={() => void runCommand()}>执行</Button>
              </div>
              <label className="small muted" style={{ display: "flex", gap: 6, alignItems: "center", marginTop: 8 }}>
                <input type="checkbox" checked={approved} onChange={(e) => setApproved(e.target.checked)} /> 已人工审批，高风险命令如 sudo、docker 需要勾选才放行
              </label>
              {lastOutput
                ? <pre className="term" style={{ marginTop: 10 }}>{lastOutput}</pre>
                : <pre className="term" style={{ marginTop: 10 }}>{`# ${agentName(sessions.find((x) => x.id === selectedSession)?.agent_id ?? null)}的终端会话 · /workspace\n# 在上方输入命令开始，全部命令都会记入审计`}</pre>}
              <details className="fold">
                <summary>本会话的历史命令 <ToneBadge tone="">{sessionLogs.length}</ToneBadge></summary>
                <ScrollArea className="mt-[6px] h-[220px] pr-3">
                  <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                    {sessionLogs.map((l) => (
                      <div key={l.id} className="ws-feed-item">
                        <div className="mono small">$ {l.command}</div>
                        <div className="small muted">退出码 {l.exit_code ?? "—"} · {cmdStatusLabel(l.status)} · {l.duration_ms} 毫秒 · {fmtTimeFull(l.started_at)}</div>
                      </div>
                    ))}
                    {sessionLogs.length === 0 && <div className="small muted">{sessionLogsLoading ? "正在加载执行记录…" : "暂无执行记录。"}</div>}
                  </div>
                </ScrollArea>
              </details>
            </>}
          </Card>)}
        </div>

        {/* RIGHT: collaboration feed + files + pending file changes */}
        <div className="ws-col">
          <Card className="p-4">
            <div style={{ fontWeight: 600, marginBottom: 6 }}>协作动态</div>
            <div className="ws-feed timeline">
              {events.map((e) => (
                <div key={e.id} className={`ws-feed-item ${freshEvents.has(e.id) ? "motion-item-enter" : ""}`}>
                  <span className={`tl-dot ${eventTone(e.event_type)}`} />
                  <div style={{ display: "flex", gap: 8, alignItems: "flex-start" }}>
                    <Avatar name={agentName(e.agent_id)} size={26} avatar={agentAvatar(e.agent_id)} appearance={agentAppearance(e.agent_id)} />
                    <div style={{ minWidth: 0 }}>
                      <div className="small"><b>{agentName(e.agent_id)}</b> {eventLabel(e.event_type)}{e.path ? <span className="mono"> {e.path}</span> : ""}</div>
                      <div className="small muted" style={{ marginTop: 2 }}>{e.detail ? `${e.detail} · ` : ""}{fmtTimeShort(e.created_at)}</div>
                    </div>
                  </div>
                </div>
              ))}
              {events.length === 0 && <div className="small muted">{dataLoaded ? "暂无动态。员工在这台电脑上的操作会实时出现在这里。" : "正在加载协作动态…"}</div>}
            </div>
          </Card>
          <Card className="p-4">
            <div style={{ fontWeight: 600, marginBottom: 6 }}>共享文件</div>
            <ScrollArea className="h-[260px] pr-3">
              <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                {files.filter((f) => !f.is_dir).map((f) => (
                  <div key={f.path} className="ws-feed-item" style={{ cursor: "pointer" }}
                    onClick={() => void api.wsFileContent(f.path).then((r) => setFilePreview(`# ${r.path} · 第 ${r.version ?? "?"} 版\n${r.content.slice(0, 2000)}`)).catch((e) => setFilePreview(`读取失败：${e instanceof Error ? e.message : String(e)}，可以稍后再试。`))}>
                    <div style={{ display: "flex", gap: 7, alignItems: "center" }}>
                      <Icon name="file" size={15} />
                      <span className="mono small" style={{ fontWeight: 600 }}>{f.path}</span>
                      <ToneBadge tone="info">第 {f.version ?? "—"} 版</ToneBadge>
                    </div>
                    <div className="small muted" style={{ marginTop: 4, display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
                      <span>{fmtSize(f.size)}</span>
                      {f.content_hash && <span className="cred-chip">校验和 {f.content_hash.slice(0, 8)}</span>}
                      <span style={{ display: "inline-flex", gap: 5, alignItems: "center" }}><Avatar name={agentName(f.last_modified_by_agent_id)} size={18} avatar={agentAvatar(f.last_modified_by_agent_id)} appearance={agentAppearance(f.last_modified_by_agent_id)} />{agentName(f.last_modified_by_agent_id)} 修改</span>
                    </div>
                  </div>
                ))}
                {fileCount === 0 && <div className="small muted">{dataLoaded ? "共享目录里还没有文件。" : "正在加载共享文件…"}</div>}
              </div>
            </ScrollArea>
            {filePreview && <pre className="log motion-item-enter" style={{ marginTop: 10 }}>{filePreview}</pre>}
          </Card>
          {fileChangesCard}
        </div>
      </div>)}
    </div>
  );
}
