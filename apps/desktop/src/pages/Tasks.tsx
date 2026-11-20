import { useEffect, useState } from "react";
import { api } from "../services/api";
import { AvatarStack, CandidateBars, ErrorNotice, StateBox, StatusBadge, capLabel, modeLabel, nodeLabel, toolLabel, translateFilterReason, useAsync, useFreshIds } from "../components/ui";
import { showToast } from "../motion/Toast";
import { useAppStore } from "../stores/appStore";
import type { Task, TaskPlan } from "../types";
import { Button } from "@/components/ui/button";
import { fmtAgo } from "../lib/time";
import { Card, CardContent, CardFooter, CardHeader } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import { Progress } from "@/components/ui/progress";
import { Separator } from "@/components/ui/separator";
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select";

/* 任务标题常直接取自用户在群聊里发的原始消息（含 @ 前缀与多句说明）；
   列表只显示去 @ 后的首句摘要，完整标题留在悬停提示里。 */
function displayTitle(title: string): string {
  const t = (title || "").replace(/@[^\s@]+/g, "").replace(/\s+/g, " ").trim();
  const first = (t.split(/[。！？\n]/)[0] || t).trim();
  return first.length > 40 ? `${first.slice(0, 40)}…` : first;
}

export default function Tasks() {
  const setPage = useAppStore((s) => s.setPage);
  const tasks = useAsync(() => api.tasks());
  const agents = useAsync(() => api.agents());
  const [showForm, setShowForm] = useState(false);
  const [title, setTitle] = useState("");
  const [desc, setDesc] = useState("");
  const [creating, setCreating] = useState(false);
  const [mode, setMode] = useState(() => {
    // 记住上次选的调度方式：新建任务默认沿用上一次的选择，不用每次重选。
    const saved = localStorage.getItem("aftask-last-mode");
    return saved === "fixed" || saved === "round_robin" || saved === "adaptive" ? saved : "adaptive";
  });
  const [busyId, setBusyId] = useState<number | null>(null);
  // 危险操作两步确认：点一次进入待确认态（按钮文案变「确认…？」），
  // 再点一次才真正执行；actingId 守卫防重复点击，失败给 Toast。
  const [confirmingAction, setConfirmingAction] = useState<{ id: number; kind: "cancel" | "retry" } | null>(null);
  const [actingId, setActingId] = useState<number | null>(null);
  const [openId, setOpenId] = useState<number | null>(null);
  const focusTaskId = useAppStore((s) => s.focusTaskId);
  const clearFocusTask = useAppStore((s) => s.clearFocusTask);
  useEffect(() => {
    if (focusTaskId) { setOpenId(focusTaskId); clearFocusTask(); }
  }, [focusTaskId, clearFocusTask]);
  const decisions = useAsync(() => (openId ? api.decisions(openId) : Promise.resolve([])));
  // 点开哪条任务的调度决策，就拉哪条的：不加这一步，上面那次取数永远是
  // 挂载时 openId=null 的空结果（useAsync 的取数函数已用最新闭包，见 ui.tsx）。
  useEffect(() => { if (openId) void decisions.reload(); }, [openId]);
  const freshTasks = useFreshIds((tasks.data ?? []).map((t) => t.id));
  const agentName = (id: number | null) => (id == null ? "无" : agents.data?.find((a) => a.id === id)?.name ?? `已删除的员工`);
  const agentFace = (id: number) => { const a = agents.data?.find((x) => x.id === id); return a ? { avatar: a.avatar, appearance: a.appearance } : undefined; };

  // 计划确认：仅待执行任务走此入口，打开前拉取后端计划预览。
  const [planTask, setPlanTask] = useState<Task | null>(null);
  const [plan, setPlan] = useState<TaskPlan | null>(null);
  const [planLoading, setPlanLoading] = useState(false);
  const [planError, setPlanError] = useState<string | null>(null);
  const [assigningNodeId, setAssigningNodeId] = useState<number | null>(null);
  const [confirming, setConfirming] = useState(false);

  async function create() {
    if (!title.trim() || creating) return;
    setCreating(true);
    try {
      await api.createTask({ title, description: desc, scheduler_mode: mode, priority: 3 });
      try { localStorage.setItem("aftask-last-mode", mode); } catch { /* ignore */ }
      setTitle(""); setDesc(""); setShowForm(false); void tasks.reload();
    } catch (e) {
      // 之前只有 try/finally：创建失败时表单停在原地、没有任何提示。
      showToast("error", `创建失败：${e instanceof Error ? e.message : String(e)}，可以稍后再试。`);
    } finally { setCreating(false); }
  }

  async function actTask(t: Task, kind: "cancel" | "retry") {
    if (actingId === t.id) return;
    setActingId(t.id);
    try {
      if (kind === "cancel") await api.cancelTask(t.id);
      else await api.retryTask(t.id);
      setConfirmingAction(null);
      void tasks.reload();
    } catch (e) {
      showToast("error", `${kind === "cancel" ? "取消失败" : "重置重试失败"}：${e instanceof Error ? e.message : String(e)}`);
    } finally { setActingId(null); }
  }

  /** 真正调执行接口并统一报错提示；返回是否执行成功。 */
  async function executeNow(t: Task): Promise<boolean> {
    setBusyId(t.id);
    try { await api.executeTask(t.id); return true; } catch (e) {
      const raw = e instanceof Error ? e.message : String(e);
      // The backend's 409 guard message is productized here; anything else
      // keeps the original message (it is already an honest error).
      showToast("error", /正在执行中/.test(raw) ? "这个任务正在执行中，等它完成或先取消后再试。" : `执行失败：${raw}`);
      return false;
    } finally { setBusyId(null); }
  }
  async function run(t: Task) {
    await executeNow(t);
    void tasks.reload();
  }

  async function openPlan(t: Task) {
    setPlanTask(t);
    setPlan(null);
    setPlanError(null);
    setPlanLoading(true);
    try { setPlan(await api.taskPlan(t.id)); } catch (e) {
      setPlanError(e instanceof Error ? e.message : String(e));
    } finally { setPlanLoading(false); }
  }
  function closePlan() {
    if (confirming) return;
    setPlanTask(null);
    setPlan(null);
    setPlanError(null);
  }
  async function assign(nodeId: number, value: string) {
    if (!planTask) return;
    const agentId = value === "auto" ? null : Number(value);
    setAssigningNodeId(nodeId);
    try {
      // 改派后以后端返回的最新计划整体刷新，候选人评分随之更新；
      // 改派给不合格员工不在前端拦截，其不合格原因由后端预览给出。
      setPlan(await api.assignNode(planTask.id, nodeId, agentId));
    } catch (e) {
      showToast("error", `改派失败：${e instanceof Error ? e.message : String(e)}`);
    } finally { setAssigningNodeId(null); }
  }
  async function confirmAndExecute() {
    if (!planTask || confirming) return;
    setConfirming(true);
    const ok = await executeNow(planTask);
    setConfirming(false);
    if (ok) { setPlanTask(null); setPlan(null); setPlanError(null); }
    void tasks.reload();
  }

  return (
    <div>
      <div className="spread">
        <div>
          <h1 className="page-title">任务中心</h1>
          <p className="page-desc">把工作交给团队：系统会自动拆解步骤、分配员工、验证结果，失败了还会尝试自动修复。</p>
        </div>
        <Button onClick={() => setShowForm(true)}>新建任务</Button>
      </div>
      <StateBox loading={tasks.loading} error={tasks.error} empty={(tasks.data?.length ?? 0) === 0}
        emptyText="还没有任务" emptyActionLabel="创建第一个任务" onEmptyAction={() => setShowForm(true)} emptyArt="tasks" onRetry={() => void tasks.reload()}>
        <div className="grid">
          {(tasks.data ?? []).map((t) => (
            <Card className={freshTasks.has(t.id) ? "motion-item-enter" : undefined} key={t.id}>
              <CardHeader className="p-5 pb-3">
                <div className="spread">
                  <div style={{ minWidth: 0 }}>
                    <div style={{ fontWeight: 700 }} className="truncate" title={t.title}>{displayTitle(t.title)}</div>
                    <div className="text-sm text-muted-foreground">{modeLabel(t.scheduler_mode)} · Token {t.token_usage.toLocaleString("zh-CN")} · 重试 {t.retry_count} 次{t.created_at ? ` · ${fmtAgo(t.created_at)}创建` : ""}</div>
                  </div>
                  <StatusBadge status={t.status} />
                </div>
              </CardHeader>
              <CardContent className="p-5 pt-0">
                {t.nodes.length > 0 && (
                  <div className="node-flow">
                    {t.nodes.map((n, i) => (
                      <span key={n.id} style={{ display: "contents" }}>
                        {i > 0 && <span className={`node-link ${t.nodes[i - 1].status === "completed" ? "done" : ""}`} />}
                        <span className={`node-step s-${n.display_status ?? n.status}`}>
                          <span className="node-dot">{n.status === "completed" ? "✓" : n.status === "failed" ? "✕" : (n.display_status ?? n.status) === "verifying" ? "◐" : i + 1}</span>
                          <span className="node-name">{nodeLabel(n.name)}</span>
                          <span className="node-agent">{n.assigned_agent_id ? agentName(n.assigned_agent_id) : "待分配"}</span>
                        </span>
                      </span>
                    ))}
                  </div>
                )}
                {t.nodes.length > 0 && (
                  <div style={{ display: "flex", alignItems: "center", gap: 12, marginTop: 12 }}>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <Progress value={(t.nodes.filter((n) => n.status === "completed").length / t.nodes.length) * 100} className="h-2 [&>div]:bg-success" />
                      <div className="text-xs text-muted-foreground" style={{ marginTop: 5 }}>已完成 {t.nodes.filter((n) => n.status === "completed").length} / {t.nodes.length} 步</div>
                    </div>
                    {(() => {
                      const ids = [...new Set(t.nodes.map((n) => n.assigned_agent_id).filter((x): x is number => x != null))];
                      const names = ids.map((id) => agentName(id));
                      const avatars = ids.map((id) => agents.data?.find((a) => a.id === id)?.avatar);
                      const appearances = ids.map((id) => agents.data?.find((a) => a.id === id)?.appearance);
                      return names.length > 0 ? <AvatarStack names={names} avatars={avatars} appearances={appearances} size={26} /> : null;
                    })()}
                  </div>
                )}
                {t.status === "failed" && (
                  <ErrorNotice errorType={t.error_type} errorMessage={t.error_message} onConfigure={() => setPage("settings")} />
                )}
                {openId === t.id && (
                  <div style={{ marginTop: 12 }}>
                    {decisions.loading ? <div className="text-xs text-muted-foreground">正在加载调度决策…</div>
                    : decisions.error ? <div className="text-xs" style={{ color: "var(--destructive)" }}>调度决策没能加载：{decisions.error}，可以收起再打开重试。</div>
                    : (decisions.data ?? []).length === 0 && <div className="text-xs text-muted-foreground">暂无调度决策记录，执行任务后会在这里说明每一步为什么选这位员工。</div>}
                    {(decisions.data ?? []).map((d) => (
                      <div key={d.id}>
                        <Separator style={{ margin: "10px 0" }} />
                        <div className="text-xs" style={{ marginBottom: 8 }}>
                          步骤「{nodeLabel(t.nodes.find((n) => n.id === d.node_id)?.name ?? "")}」· {modeLabel(d.mode)} · 选中 <b>{agentName(d.selected_agent_id)}</b>
                        </div>
                        <CandidateBars candidates={d.candidates} selectedAgentId={d.selected_agent_id} agentName={(id) => agentName(id)} agentFace={agentFace} />
                      </div>
                    ))}
                  </div>
                )}
              </CardContent>
              <CardFooter className="flex-wrap gap-2 p-5 pt-0">
                <Button size="sm" disabled={busyId === t.id || t.status === "running"}
                  onClick={() => { if (t.status === "pending") { void openPlan(t); } else { void run(t); } }}>
                  {busyId === t.id || t.status === "running" ? "执行中…" : t.status === "failed" || t.status === "completed" ? "重新执行" : "执行"}
                </Button>
                {(t.status === "pending" || t.status === "failed") && (
                <Button variant="secondary" size="sm" disabled={actingId === t.id}
                  onClick={() => {
                    if (confirmingAction?.id === t.id && confirmingAction.kind === "retry") { void actTask(t, "retry"); }
                    else { setConfirmingAction({ id: t.id, kind: "retry" }); }
                  }}>
                  {actingId === t.id ? "处理中…" : confirmingAction?.id === t.id && confirmingAction.kind === "retry" ? "确认重置重试？" : "重置重试"}
                </Button>
                )}
                {(t.status === "pending" || t.status === "running") && (
                <Button variant="secondary" size="sm" disabled={actingId === t.id}
                  onClick={() => {
                    if (confirmingAction?.id === t.id && confirmingAction.kind === "cancel") { void actTask(t, "cancel"); }
                    else { setConfirmingAction({ id: t.id, kind: "cancel" }); }
                  }}>
                  {actingId === t.id ? "处理中…" : confirmingAction?.id === t.id && confirmingAction.kind === "cancel" ? "确认取消任务？" : "取消"}
                </Button>
                )}
                <Button variant="secondary" size="sm" onClick={() => { setOpenId(openId === t.id ? null : t.id); }}>调度决策</Button>
              </CardFooter>
            </Card>
          ))}
        </div>
      </StateBox>

      <Dialog open={showForm} onOpenChange={(open) => { if (!open) setShowForm(false); }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>新建任务</DialogTitle>
            <DialogDescription>把目标和要求写清楚，系统会据此拆解步骤并分配员工。</DialogDescription>
          </DialogHeader>
          <div className="grid gap-2">
            <Label htmlFor="task-title">任务标题</Label>
            <Input id="task-title" autoFocus placeholder="如：开发用户管理接口并自动测试修复" value={title} onChange={(e) => setTitle(e.target.value)} />
          </div>
          <div className="grid gap-2">
            <Label htmlFor="task-desc">任务描述</Label>
            <Textarea id="task-desc" placeholder="把目标和要求写清楚，系统会据此拆解步骤。" value={desc} onChange={(e) => setDesc(e.target.value)} />
          </div>
          <div className="grid gap-2">
            <Label>调度方式</Label>
            <Select value={mode} onValueChange={setMode}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="adaptive">自适应调度</SelectItem>
                <SelectItem value="fixed">固定分配</SelectItem>
                <SelectItem value="round_robin">轮询分配</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <DialogFooter>
            <Button variant="secondary" onClick={() => setShowForm(false)}>取消</Button>
            <Button disabled={!title.trim() || creating} onClick={() => void create()}>{creating ? "创建中…" : "创建任务"}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={planTask !== null} onOpenChange={(open) => { if (!open) closePlan(); }}>
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle>确认执行计划</DialogTitle>
            <DialogDescription>
              {planTask ? `任务：${planTask.title}。先确认每一步派给谁，需要时可以改派，确认后才会开始执行。` : "先确认每一步派给谁，确认后才会开始执行。"}
            </DialogDescription>
          </DialogHeader>
          {planLoading && <div className="text-sm text-muted-foreground">正在生成执行计划…</div>}
          {planError && !planLoading && (
            <div className="grid gap-3">
              <div className="text-sm text-destructive">执行计划没能生成：{planError}</div>
              {planTask && <div><Button variant="secondary" size="sm" onClick={() => void openPlan(planTask)}>重新加载</Button></div>}
            </div>
          )}
          {plan && !planLoading && (
            <div className="grid gap-4 overflow-y-auto pr-1" style={{ maxHeight: "55vh" }}>
              {plan.nodes.length === 0 && <div className="text-sm text-muted-foreground">这个任务还没有拆解出步骤，确认后系统会按调度方式执行。</div>}
              {plan.nodes.map((node, idx) => {
                const pinnedCandidate = node.pinned_agent_id == null ? undefined : node.candidates.find((c) => c.agent_id === node.pinned_agent_id);
                const selectedCandidate = node.selected_agent_id == null ? undefined : node.candidates.find((c) => c.agent_id === node.selected_agent_id);
                const unqualified = pinnedCandidate && !pinnedCandidate.eligible ? pinnedCandidate
                  : selectedCandidate && !selectedCandidate.eligible ? selectedCandidate : undefined;
                return (
                  <div key={node.node_id} className="grid gap-3">
                    {idx > 0 && <Separator />}
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-sm font-semibold">第 {idx + 1} 步 · {nodeLabel(node.name)}</span>
                      {node.pinned_agent_id != null && <Badge variant="secondary">用户指派</Badge>}
                      <span className="text-sm text-muted-foreground">
                        拟派员工：{node.selected_agent_id == null ? "待分配" : agentName(node.selected_agent_id)}
                      </span>
                    </div>
                    {(node.required_capabilities ?? []).length > 0 && (
                      <div className="flex flex-wrap items-center gap-1.5">
                        <span className="text-xs text-muted-foreground">能力要求</span>
                        {(node.required_capabilities ?? []).map((cap) => (
                          <Badge key={cap} variant="outline">{capLabel(cap)}</Badge>
                        ))}
                      </div>
                    )}
                    <CandidateBars candidates={node.candidates} selectedAgentId={node.selected_agent_id} agentName={(id) => agentName(id)} agentFace={agentFace} />
                    {node.duty_match && ((node.duty_match.capabilities ?? []).length > 0 || (node.duty_match.tools ?? []).length > 0 || (node.duty_match.deliverables ?? []).length > 0) && (
                      <div className="flex flex-wrap items-center gap-1.5">
                        <span className="text-xs text-muted-foreground">职责匹配</span>
                        {(node.duty_match.capabilities ?? []).map((c) => (
                          <Badge key={`cap-${c}`} className="border-transparent bg-info-tint text-info hover:bg-info-tint">能力对口 · {capLabel(c)}</Badge>
                        ))}
                        {(node.duty_match.tools ?? []).map((t) => (
                          <Badge key={`tool-${t}`} className="border-transparent bg-success-tint text-success hover:bg-success-tint">工具齐备 · {toolLabel(t)}</Badge>
                        ))}
                        {(node.duty_match.deliverables ?? []).map((d) => (
                          <Badge key={`del-${d}`} className="border-transparent bg-warning-tint text-warning hover:bg-warning-tint">交付物对口 · {d}</Badge>
                        ))}
                      </div>
                    )}
                    <div className="grid gap-2">
                      <Label>改派给</Label>
                      <Select
                        value={node.pinned_agent_id == null ? "auto" : String(node.pinned_agent_id)}
                        onValueChange={(value) => void assign(node.node_id, value)}
                        disabled={assigningNodeId !== null || confirming}
                      >
                        <SelectTrigger>
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value="auto">跟随调度</SelectItem>
                          {(agents.data ?? []).map((a) => (
                            <SelectItem key={a.id} value={String(a.id)}>{a.name}</SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </div>
                    {assigningNodeId === node.node_id && <div className="text-xs text-muted-foreground">正在改派并重新计算评分…</div>}
                    {unqualified && (
                      <div className="text-xs text-destructive">
                        提示：{agentName(unqualified.agent_id)} 未通过这一步的资格检查，{translateFilterReason(unqualified.filter_reason)}，改派仍然保留，执行时可能无法由该员工完成。
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}
          <DialogFooter>
            <Button variant="secondary" disabled={confirming} onClick={closePlan}>取消</Button>
            <Button disabled={!plan || planLoading || confirming || assigningNodeId !== null} onClick={() => void confirmAndExecute()}>
              {confirming ? "执行中…" : "确认并执行"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
