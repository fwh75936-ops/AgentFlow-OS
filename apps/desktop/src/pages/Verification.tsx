import { useState } from "react";
import { api } from "../services/api";
import { CandidateBars, ScoreBar, StateBox, modeLabel, nodeLabel, useAsync, vtypeLabel } from "../components/ui";
import { useAppStore } from "../stores/appStore";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

export default function Verification() {
  const setPage = useAppStore((s) => s.setPage);
  const [tab, setTab] = useState<"records" | "decisions">("records");
  const vers = useAsync(() => api.verifications());
  const decisions = useAsync(() => api.decisions());
  const tasks = useAsync(() => api.tasks());
  const agents = useAsync(() => api.agents());
  const taskOf = (id: number) => tasks.data?.find((t) => t.id === id);
  const taskTitle = (id: number | null) => (id == null ? "交叉评审记录" : taskOf(id)?.title ?? `任务 #${id}`);
  const nodeName = (taskId: number, nodeId: number) => nodeLabel(taskOf(taskId)?.nodes.find((n) => n.id === nodeId)?.name ?? "");
  const agentName = (id: number | null) => (id == null ? "无" : agents.data?.find((a) => a.id === id)?.name ?? `已删除的员工`);
  const agentFace = (id: number) => { const a = agents.data?.find((x) => x.id === id); return a ? { avatar: a.avatar, appearance: a.appearance } : undefined; };
  const fmtTime = (s: string | null) => (s ? new Date(s).toLocaleString("zh-CN", { hour12: false }) : "");

  return (
    <div>
      <h1 className="page-title">验收记录</h1>
      <p className="page-desc">每一步成果都要过验证才算完成；每一次派活，都能说清为什么选这位员工。</p>
      <Tabs value={tab} onValueChange={(v) => setTab(v as "records" | "decisions")}>
        <TabsList style={{ marginBottom: 16 }}>
          <TabsTrigger value="records">验证记录</TabsTrigger>
          <TabsTrigger value="decisions">调度决策</TabsTrigger>
        </TabsList>

        <TabsContent value="records">
          <StateBox loading={vers.loading} error={vers.error} empty={(vers.data?.length ?? 0) === 0}
            emptyText="暂无验证记录" emptyHint="任务执行并完成验证后，记录会出现在这里。" emptyActionLabel="去执行一个任务" onEmptyAction={() => setPage("tasks")} onRetry={() => void vers.reload()}>
            <div className="grid">
              {(vers.data ?? []).map((v) => (
                <Card className="p-4" key={v.verification_id}>
                  <div className="spread">
                    <div style={{ minWidth: 0 }}>
                      <span style={{ fontWeight: 600 }} className="truncate">{taskTitle(v.task_id)}</span>
                      <span className="muted small" style={{ marginLeft: 8 }}>{vtypeLabel(v.verification_type)}</span>
                    </div>
                    {v.passed
                      ? <Badge variant="secondary" className="border-transparent bg-success-tint text-success hover:bg-success-tint">验证通过</Badge>
                      : <Badge variant="secondary" className="border-transparent bg-errortint text-destructive hover:bg-errortint">未通过</Badge>}
                  </div>
                  <div style={{ marginTop: 10, maxWidth: 420 }}>
                    <ScoreBar value={v.score} tone={v.passed ? "success" : "error"} format={(x) => x.toFixed(2)} />
                  </div>
                  <div className="row" style={{ marginTop: 9, gap: 6 }}>
                    <span className="cred-chip">记录 {v.verification_id.slice(0, 8)}</span>
                    {v.timestamp && <span className="muted small">{fmtTime(v.timestamp)}</span>}
                  </div>
                  {(v.passed ? v.evidence : (v.errors.join("; ") || v.evidence)) && (
                    <details className="fold">
                      <summary>{v.passed ? "查看验证证据" : "查看未通过原因"}</summary>
                      <pre className="log" style={{ marginTop: 6 }}>{v.passed ? v.evidence : (v.errors.join("; ") || v.evidence)}</pre>
                    </details>
                  )}
                </Card>
              ))}
            </div>
          </StateBox>
        </TabsContent>

        <TabsContent value="decisions">
          <StateBox loading={decisions.loading} error={decisions.error} empty={(decisions.data?.length ?? 0) === 0}
            emptyText="暂无调度决策" emptyHint="每次派活的候选评分与过滤理由都会记录在这里。" emptyActionLabel="去执行一个任务" onEmptyAction={() => setPage("tasks")} onRetry={() => void decisions.reload()}>
            <div className="grid">
              {(decisions.data ?? []).slice(0, 10).map((d) => (
                <Card className="p-4" key={d.id}>
                  <div className="spread">
                    <div className="small" style={{ minWidth: 0 }}>
                      <b>{taskTitle(d.task_id)}</b> · 步骤「{nodeName(d.task_id, d.node_id)}」· {modeLabel(d.mode)}
                      {d.created_at && <span className="muted"> · {fmtTime(d.created_at)}</span>}
                    </div>
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
                </Card>
              ))}
            </div>
            {(decisions.data ?? []).length > 10 && <div className="muted small" style={{ marginTop: 10 }}>仅显示最近 10 条调度决策。</div>}
          </StateBox>
        </TabsContent>
      </Tabs>
    </div>
  );
}
