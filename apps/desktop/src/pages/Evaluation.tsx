import { useState } from "react";
import { api } from "../services/api";
import { MoreMenu, StateBox, modeLabel, statusLabel, useAsync } from "../components/ui";
import { showToast } from "../motion/Toast";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Progress } from "@/components/ui/progress";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { cn } from "@/lib/utils";

function fmtRate(v: number | null | undefined): string {
  return v === null || v === undefined ? "—" : `${Math.round(v * 100)}%`;
}
function fmtLatency(v: number | null | undefined): string {
  if (v === null || v === undefined) return "未测试";
  if (v < 0.001) return `${Math.round(v * 1_000_000)} 微秒`;
  if (v < 1) return `${(v * 1000).toFixed(1)} 毫秒`;
  return `${v.toFixed(2)} 秒`;
}

/* Real, already-run benchmark results (read-only display).
 * Source: agentflow-submission/benchmarks/summary.md (run 2026-11-17,
 * production scheduler/verification/self-loop engines, no LLM calls)
 * and v3_summary.md (V3.0 A/B/C/D, 6 tasks x 3 repeats). These numbers
 * are transcribed from those result files — never generated here. */
const ROUND1_GROUPS = [
  { name: "多员工自适应调度", rate: 1.0, note: "6/6 成功" },
  { name: "多员工轮询分配", rate: 0.5, note: "3/6 成功" },
  { name: "多员工固定分配", rate: 0.1667, note: "1/6 成功" },
  { name: "单员工执行", rate: 0.1667, note: "1/6 成功" },
];
const V3_GROUPS = [
  { name: "D · 共享环境感知调度 + 状态感知验证 + 自修复循环", rate: 0.8333, note: "冲突 0 · 过期检出 3/3 · 自动修复成功" },
  { name: "C · 共享环境感知调度", rate: 0.8333, note: "冲突 0 · 过期漏检 3" },
  { name: "A · 固定分配", rate: 0.3333, note: "冲突 6 · 人工介入 12" },
  { name: "B · 纯能力自适应调度", rate: 0.1667, note: "冲突 15 · 人工介入 15" },
];

function ResultCard({ title, subtitle, rows, footnote }: {
  title: string; subtitle: string;
  rows: Array<{ name: string; rate: number; note: string }>; footnote: string;
}) {
  return (
    <Card>
      <CardHeader className="p-5 pb-0">
        <CardTitle className="text-base">{title}</CardTitle>
        <CardDescription>{subtitle}</CardDescription>
      </CardHeader>
      <CardContent className="p-5">
        <div className="flex flex-col gap-3">
          {rows.map((r) => {
            const tone = r.rate >= 0.8 ? "success" : r.rate >= 0.4 ? "info" : "warning";
            return (
              <div key={r.name}>
                {/* Name and note stack vertically: side-by-side squeezed
                    long group names into the note text (screenshot bug). */}
                <div className="mb-1 text-sm">
                  <div className="font-medium leading-snug">{r.name}</div>
                  <div className="mt-0.5 text-[13px] text-muted-foreground">{r.note}</div>
                </div>
                <div className="flex items-center gap-2">
                  <Progress
                    value={Math.round(r.rate * 100)}
                    className={cn(
                      "h-2 flex-1 bg-muted",
                      tone === "success" && "[&>div]:bg-success",
                      tone === "info" && "[&>div]:bg-info",
                      tone === "warning" && "[&>div]:bg-warning",
                    )}
                  />
                  <span className="w-10 text-right text-sm tabular-nums">{Math.round(r.rate * 100)}%</span>
                </div>
              </div>
            );
          })}
        </div>
        <p className="mt-3 text-sm text-muted-foreground">{footnote}</p>
      </CardContent>
    </Card>
  );
}

function statusBadgeClass(status: string, running: boolean): string {
  if (running) return "border-transparent bg-info-tint text-info hover:bg-info-tint";
  if (status === "completed") return "border-transparent bg-success-tint text-success hover:bg-success-tint";
  if (status === "failed") return "border-transparent bg-errortint text-destructive hover:bg-errortint";
  return "";
}

export default function Evaluation() {
  const runs = useAsync(() => api.evaluations());
  const [showForm, setShowForm] = useState(false);
  const [name, setName] = useState("");
  const [mode, setMode] = useState("adaptive");
  const [runningId, setRunningId] = useState<number | null>(null);
  const [deletingId, setDeletingId] = useState<number | null>(null);
  const [creating, setCreating] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  async function create() {
    if (!name.trim() || creating) return;
    setCreating(true); setFormError(null);
    try {
      await api.createEvaluation({ name, mode, config: {} });
      setName(""); setShowForm(false); void runs.reload();
    } catch (e) {
      setFormError(`创建失败：${e instanceof Error ? e.message : String(e)}，可以检查名称后重试。`);
    } finally { setCreating(false); }
  }
  async function runOne(id: number, label: string) {
    if (runningId !== null) return;
    setRunningId(id);
    try {
      const r = await api.runEvaluation(id);
      void runs.reload();
      if (r.status === "completed") {
        showToast("success", `实验「${label}」运行完成，成功率 ${fmtRate(r.metrics?.success_rate)}。`);
      } else {
        showToast("error", `实验「${label}」运行失败：${r.metrics?.error ?? "未知错误"}`);
      }
    } catch (e) {
      showToast("error", `运行失败：${e instanceof Error ? e.message : String(e)}`);
    } finally { setRunningId(null); }
  }
  async function removeOne(id: number, label: string) {
    if (deletingId !== null) return;
    setDeletingId(id);
    try {
      await api.deleteEvaluation(id);
      void runs.reload();
      showToast("success", `实验配置「${label}」已删除。`);
    } catch (e) { showToast("error", `删除失败：${e instanceof Error ? e.message : String(e)}`); }
    finally { setDeletingId(null); }
  }
  return (
    <div>
      <div className="spread">
        <div>
          <h1 className="page-title">对比实验</h1>
          <p className="page-desc">用同一组任务对比不同调度方式，结论只来自真实运行。</p>
        </div>
        <Button onClick={() => setShowForm(true)}>新建实验配置</Button>
      </div>

      <h3 className="section-title">已完成的真实实验</h3>
      <div className="grid grid-2" style={{ alignItems: "start" }}>
        <ResultCard
          title="调度方式对比 + 自修复循环"
          subtitle="6 个调度任务 × 4 种方式；3 个缺陷任务 × 自修复循环开/关"
          rows={ROUND1_GROUPS}
          footnote="自修复循环：关闭时修复成功 0/3，开启后 3/3。数据来自 2026-11-17 真实运行，全程未调用大模型，模型用量为 0。"
        />
        <ResultCard
          title="共享环境协作实验 A/B/C/D"
          subtitle="6 个任务 × 3 次重复，四组使用同一成功标准"
          rows={V3_GROUPS}
          footnote="B 组低于 A 组是真实测量：该任务集里能力最强的员工常被共享资源占用，纯能力调度反复选到冲突的人。数据来自 2026-11-17 真实运行。"
        />
      </div>

      <h3 className="section-title">我的实验配置</h3>
      <StateBox loading={runs.loading} error={runs.error} empty={(runs.data?.length ?? 0) === 0}
        emptyText="还没有实验配置" emptyActionLabel="新建实验配置" onEmptyAction={() => setShowForm(true)} emptyArt="flask" onRetry={() => void runs.reload()}>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>实验</TableHead><TableHead>模式</TableHead><TableHead>状态</TableHead>
              <TableHead>成功率</TableHead><TableHead>平均延迟</TableHead><TableHead>恢复成功率</TableHead><TableHead>操作</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>{(runs.data ?? []).map((r) => (
            <TableRow key={r.id}>
              <TableCell className="font-medium">{r.name}</TableCell>
              <TableCell>{modeLabel(r.mode)}</TableCell>
              <TableCell>
                <Badge variant="secondary" className={statusBadgeClass(r.status, runningId === r.id)}>
                  {runningId === r.id ? "执行中" : statusLabel(r.status)}
                </Badge>
              </TableCell>
              <TableCell>{r.metrics ? fmtRate(r.metrics.success_rate) : "未测试"}</TableCell>
              <TableCell>{fmtLatency(r.metrics?.avg_latency)}</TableCell>
              <TableCell>{r.metrics ? fmtRate(r.metrics.recovery_rate) : "未测试"}</TableCell>
              <TableCell>
                <span className="flex items-center gap-1.5">
                  <Button variant="secondary" size="sm" disabled={runningId !== null || deletingId === r.id || r.status === "running"}
                    onClick={() => void runOne(r.id, r.name)}>{runningId === r.id ? "运行中…" : "运行"}</Button>
                  <MoreMenu items={[
                    { label: "删除实验配置", danger: true, confirmText: `删除实验配置「${r.name}」？其运行结果将一并删除，此操作不可恢复。`, onClick: () => void removeOne(r.id, r.name) },
                  ]} />
                </span>
              </TableCell>
            </TableRow>))}
          </TableBody>
        </Table>
      </StateBox>

      <Dialog open={showForm} onOpenChange={(open) => { if (open) setFormError(null); setShowForm(open); }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>新建实验配置</DialogTitle>
            <DialogDescription>实验名称必填。</DialogDescription>
          </DialogHeader>
          <div className="flex flex-col gap-2">
            <Label htmlFor="eval-name">实验名称</Label>
            <Input id="eval-name" autoFocus value={name} onChange={(e) => setName(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter" && name.trim() && !creating) void create(); }} />
          </div>
          <div className="flex flex-col gap-2">
            <Label>调度方式</Label>
            <Select value={mode} onValueChange={setMode}>
              <SelectTrigger><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="adaptive">自适应调度</SelectItem>
                <SelectItem value="fixed">固定分配</SelectItem>
                <SelectItem value="round_robin">轮询分配</SelectItem>
              </SelectContent>
            </Select>
          </div>
          {formError && <div className="text-sm" style={{ color: "var(--destructive)" }}>{formError}</div>}
          <DialogFooter>
            <Button variant="secondary" onClick={() => setShowForm(false)}>取消</Button>
            <Button disabled={!name.trim() || creating} onClick={() => void create()}>{creating ? "创建中…" : "创建"}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
