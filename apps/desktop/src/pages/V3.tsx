import { useState } from "react";
import { api } from "../services/api";
import { Avatar, CandidateBars, Icon, IconChip, StateBox, StatusBadge, eventLabel, statusLabel, useAsync } from "../components/ui";
import { showToast } from "../motion/Toast";
import { useAppStore } from "../stores/appStore";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";

// 智能协作 view: chief missions, SACAS decision components, state-aware
// verification (current/stale) and proactive approvals.
const RESOURCE_LABELS: Record<string, string> = {
  desktop: "桌面", file: "文件", port: "端口", browser: "浏览器", git: "Git 仓库", generic: "通用",
};

export default function V3() {
  const setPage = useAppStore((s) => s.setPage);
  const decisions = useAsync(() => api.decisions());
  const artVers = useAsync(() => api.artifactVerifications());
  const leases = useAsync(() => api.resourceLeases());
  const suggestions = useAsync(() => api.proactiveSuggestions());
  const agents = useAsync(() => api.agents());
  const tasks = useAsync(() => api.tasks());
  const [goal, setGoal] = useState("开发一个用户管理系统，完成后测试验证并汇报");
  const [mission, setMission] = useState<{ mission_id: number; status: string; report: unknown } | null>(null);
  const [busy, setBusy] = useState(false);
  const [scanning, setScanning] = useState(false);
  const [actingId, setActingId] = useState<number | null>(null);
  const [err, setErr] = useState("");
  const [errFromMission, setErrFromMission] = useState(false);
  const agentName = (id: number | null) => (id == null ? "无" : agents.data?.find((a) => a.id === id)?.name ?? `已删除的员工`);
  const agentFace = (id: number) => { const a = agents.data?.find((x) => x.id === id); return a ? { avatar: a.avatar, appearance: a.appearance } : undefined; };
  const taskTitle = (id: number) => tasks.data?.find((t) => t.id === id)?.title ?? `任务 #${id}`;

  async function runMission() {
    if (!goal.trim() || busy) return;
    setBusy(true); setErr(""); setErrFromMission(false);
    try { setMission(await api.chiefMission(goal)); }
    catch (e) { setErr(e instanceof Error ? e.message : String(e)); setErrFromMission(true); }
    finally { setBusy(false); }
  }
  async function scan() {
    if (scanning) return;
    setScanning(true); setErr(""); setErrFromMission(false);
    try {
      const r = await api.proactiveScan();
      suggestions.reload();
      showToast("success", r.created.length > 0 ? `扫描完成，发现 ${r.created.length} 条新建议。` : "扫描完成，没有发现新的建议。");
    }
    catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
    finally { setScanning(false); }
  }
  async function act(id: number, kind: "approve" | "reject") {
    if (actingId !== null) return;
    setActingId(id);
    try { if (kind === "approve") await api.approveSuggestion(id); else await api.rejectSuggestion(id); suggestions.reload(); }
    catch (e) { setErr(e instanceof Error ? e.message : String(e)); setErrFromMission(false); }
    finally { setActingId(null); }
  }

  const sacas = (decisions.data ?? []).filter((d) => d.mode === "sacas");
  return (
    <div>
      <h1 className="page-title">总管调度</h1>
      <p className="page-desc">AI 总管带队完成目标：自动组队、分配任务、盯着进度，最后向你汇报。</p>

      <h3 className="section-title">AI 总管</h3>
      <Card className="p-4">
        <div className="row" style={{ gap: 8 }}>
          <Input style={{ flex: 1 }} value={goal} onChange={(e) => setGoal(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") void runMission(); }} />
          <Button disabled={busy || !goal.trim()} onClick={runMission}>{busy ? "执行中…" : "创建并运行"}</Button>
        </div>
        {err && <div className="small" style={{ color: "var(--destructive)", marginTop: 6 }}>{err}</div>}
        {err && <div className="muted small" style={{ marginTop: 4 }}>{errFromMission ? "可以调整目标描述后重新创建，或稍后再试。" : "可以稍后再试一次。"}</div>}
        {mission && (
          <div style={{ marginTop: 10 }}>
            <div className="row" style={{ gap: 8 }}>
              <span className="small">本次总管任务状态</span>
              <StatusBadge status={mission.status} />
            </div>
            <details className="fold">
              <summary>查看总管汇报详情 · 原始数据</summary>
              <pre className="log" style={{ marginTop: 6 }}>{JSON.stringify(mission.report, null, 2)}</pre>
            </details>
          </div>
        )}
      </Card>

      <h3 className="section-title">共享环境感知调度决策</h3>
      <p className="muted small" style={{ margin: "-4px 0 10px" }}>派活时同时考虑能力、负载，以及共享电脑上的资源冲突、环境与依赖是否就绪。</p>
      <StateBox loading={decisions.loading} error={decisions.error} empty={sacas.length === 0}
        emptyText="暂无调度决策" emptyHint="运行一次总管任务或创建任务后，调度决策会出现在这里。" emptyActionLabel="先运行一次总管任务" onEmptyAction={() => document.querySelector(".main-scroll")?.scrollTo({ top: 0, behavior: "smooth" })} onRetry={() => void decisions.reload()}>
        <div className="grid">{sacas.slice(0, 8).map((d) => (
          <Card className="p-4" key={d.id}>
            <div className="spread">
              <div className="small" style={{ minWidth: 0 }}><b>{taskTitle(d.task_id)}</b></div>
              {d.selected_agent_id != null
                ? <Badge variant="secondary" className="border-transparent bg-info-tint text-info hover:bg-info-tint">选中：{agentName(d.selected_agent_id)}</Badge>
                : <Badge variant="secondary" className="border-transparent bg-warning-tint text-warning hover:bg-warning-tint">无人满足条件</Badge>}
            </div>
            <div style={{ marginTop: 12 }}>
              <CandidateBars candidates={d.candidates} selectedAgentId={d.selected_agent_id} agentName={(id) => agentName(id)} agentFace={agentFace} />
            </div>
            <details className="fold">
              <summary>调度说明 · 原始记录</summary>
              <div className="small muted wrap-anywhere" style={{ marginTop: 4 }}>{d.reason}</div>
            </details>
          </Card>))}
        </div>
        {sacas.length > 8 && <div className="muted small" style={{ marginTop: 10 }}>仅显示最近 8 条调度决策。</div>}
      </StateBox>

      <h3 className="section-title">状态感知成果验证</h3>
      <p className="muted small" style={{ margin: "-4px 0 10px" }}>成果一旦被改动，之前的验证自动标记为已过期，不会拿旧结论冒充新成果。</p>
      <StateBox loading={artVers.loading} error={artVers.error} empty={(artVers.data?.length ?? 0) === 0}
        emptyText="暂无成果验证记录" emptyHint="任务完成并验证成果后，记录会出现在这里。" onRetry={() => void artVers.reload()}>
        <div className="grid">{(artVers.data ?? []).map((v) => (
          <Card className={v.status === "current" ? "p-4" : "p-4 border-dashed opacity-[0.78]"} key={v.id}>
            <div className="spread">
              <span style={{ display: "flex", gap: 7, alignItems: "center", minWidth: 0 }}>
                <Icon name="file" size={16} />
                <span className="mono small truncate">{v.artifact_path}</span>
              </span>
              {v.status === "current"
                ? <Badge variant="secondary" className="border-transparent bg-success-tint text-success hover:bg-success-tint">最新</Badge>
                : <Badge variant="secondary" className="border-transparent bg-warning-tint text-warning hover:bg-warning-tint">已过期</Badge>}
            </div>
            <div className="row" style={{ marginTop: 9, gap: 6, flexWrap: "wrap" }}>
              <span className="cred-chip">第 {v.artifact_version ?? "-"} 版</span>
              {v.test_run_id && <span className="cred-chip">测试运行 {v.test_run_id}</span>}
              {v.passed
                ? <Badge variant="secondary" className="border-transparent bg-success-tint text-success hover:bg-success-tint">验证通过</Badge>
                : <Badge variant="secondary" className="border-transparent bg-errortint text-destructive hover:bg-errortint">未通过</Badge>}
            </div>
          </Card>))}
        </div>
      </StateBox>

      <h3 className="section-title">资源租约</h3>
      <StateBox loading={leases.loading} error={leases.error} empty={(leases.data?.length ?? 0) === 0}
        emptyText="暂无资源租约" emptyHint="任务执行占用共享资源时，租约会出现在这里。" onRetry={() => void leases.reload()}>
        <div className="grid">{(leases.data ?? []).map((l) => (
          <Card className="p-4" key={l.id}>
            <div className="spread">
              <span style={{ display: "flex", gap: 7, alignItems: "center", minWidth: 0 }}>
                <Icon name="key" size={16} />
                <span className="mono small truncate">{l.resource_key}</span>
              </span>
              {l.status === "held"
                ? <Badge variant="secondary" className="border-transparent bg-success-tint text-success hover:bg-success-tint">{statusLabel(l.status)}</Badge>
                : l.status === "waiting"
                  ? <Badge variant="secondary" className="border-transparent bg-warning-tint text-warning hover:bg-warning-tint">{statusLabel(l.status)}</Badge>
                  : <Badge variant="secondary">{statusLabel(l.status)}</Badge>}
            </div>
            <div className="small muted" style={{ marginTop: 8, display: "flex", gap: 7, alignItems: "center" }}>
              <Badge variant="secondary" className="border-transparent bg-info-tint text-info hover:bg-info-tint">{RESOURCE_LABELS[l.resource_type] ?? l.resource_type}</Badge>
              {l.holder_agent_id != null ? (
                <>
                  <Avatar name={agentName(l.holder_agent_id)} size={20} avatar={agents.data?.find((a) => a.id === l.holder_agent_id)?.avatar} appearance={agents.data?.find((a) => a.id === l.holder_agent_id)?.appearance} />
                  <span>{agentName(l.holder_agent_id)} 持有</span>
                </>
              ) : (
                <span>暂无持有者</span>
              )}
            </div>
          </Card>))}
        </div>
      </StateBox>

      <h3 className="section-title">待审批的主动建议</h3>
      <div className="row" style={{ marginBottom: 8 }}><Button variant="secondary" disabled={scanning} onClick={() => void scan()}>{scanning ? "扫描中…" : "扫描工作区动态"}</Button></div>
      <StateBox loading={suggestions.loading} error={suggestions.error} empty={(suggestions.data?.length ?? 0) === 0}
        emptyText="暂无主动建议" emptyHint="点「扫描工作区动态」后，系统发现值得提醒的变化会在这里等你审批。" onRetry={() => void suggestions.reload()}>
        <div className="grid">{(suggestions.data ?? []).map((s) => (
          <Card className="p-4" key={s.id}>
            <div style={{ display: "flex", gap: 10, alignItems: "flex-start" }}>
              <IconChip icon="bulb" tone={s.status === "pending" ? "warning" : ""} />
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontWeight: 600 }} className="wrap-anywhere">{s.title}</div>
                <div className="row" style={{ marginTop: 7, gap: 6, flexWrap: "wrap" }}>
                  <Badge variant="secondary" className="border-transparent bg-info-tint text-info hover:bg-info-tint">来源：{eventLabel(s.event_type)}</Badge>
                  <Badge variant="secondary">{s.level === "L2" ? "二级建议" : "一级建议"}</Badge>
                  {s.status === "pending"
                    ? <Badge variant="secondary" className="border-transparent bg-warning-tint text-warning hover:bg-warning-tint">{statusLabel(s.status)}</Badge>
                    : s.status === "approved"
                      ? <Badge variant="secondary" className="border-transparent bg-success-tint text-success hover:bg-success-tint">{statusLabel(s.status)}</Badge>
                      : <Badge variant="secondary">{statusLabel(s.status)}</Badge>}
                </div>
                {s.status === "pending" && (
                  <div className="row" style={{ marginTop: 9, gap: 6 }}>
                    <Button size="sm" disabled={actingId === s.id} onClick={() => act(s.id, "approve")}>{actingId === s.id ? "处理中…" : "批准"}</Button>
                    <Button variant="secondary" size="sm" disabled={actingId === s.id} onClick={() => act(s.id, "reject")}>拒绝</Button>
                  </div>
                )}
              </div>
            </div>
          </Card>))}
        </div>
      </StateBox>
      <Button variant="secondary" size="sm" style={{ marginTop: 6 }} onClick={() => setPage("verification")}>打开验收记录页</Button>
    </div>
  );
}
