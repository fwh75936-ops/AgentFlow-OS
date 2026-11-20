import { useState } from "react";
import { api } from "../services/api";
import { MoreMenu, StateBox, useAsync } from "../components/ui";
import { showToast } from "../motion/Toast";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";

export default function Skills() {
  const skills = useAsync(() => api.skills());
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState({ name: "", description: "", instructions: "" });
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);
  const [importing, setImporting] = useState(false);
  const [importResult, setImportResult] = useState<{ imported: Array<{ id: number; name: string; files: number }>; skipped: Array<{ where: string; reason: string }> } | null>(null);
  const [importError, setImportError] = useState<string | null>(null);
  const [dragOver, setDragOver] = useState(false);
  async function create() {
    if (!form.name.trim() || creating) return;
    setCreating(true); setCreateError(null);
    try {
      await api.createSkill({ ...form, required_tools: [], permissions: {}, version: "1.0.0", enabled: true, trusted: false });
      setForm({ name: "", description: "", instructions: "" }); setShowForm(false); void skills.reload();
    } catch (e) {
      setCreateError(`没创建成：${e instanceof Error ? e.message : String(e)}。可以检查输入后重试。`);
    } finally { setCreating(false); }
  }
  async function importZip(file: File) {
    if (importing) return;
    setImporting(true); setImportError(null); setImportResult(null);
    try {
      const r = await api.importSkills(file);
      setImportResult(r);
      void skills.reload();
    } catch (e) {
      setImportError(e instanceof Error ? e.message : String(e));
    } finally { setImporting(false); }
  }
  return (
    <div>
      <div className="spread">
        <div>
          <h1 className="page-title">技能</h1>
          <p className="page-desc">可复用的工作技能包：把成熟的做法沉淀下来，分配给员工使用。</p>
        </div>
        <Button onClick={() => setShowForm(true)}>创建技能</Button>
      </div>
      <Card className="p-4" style={{ marginBottom: 14, borderStyle: dragOver ? "dashed" : undefined, borderColor: dragOver ? "var(--info)" : undefined }}
        onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
        onDragLeave={() => setDragOver(false)}
        onDrop={(e) => { e.preventDefault(); setDragOver(false); const f = e.dataTransfer.files?.[0]; if (f) void importZip(f); }}>
        <div className="spread">
          <div>
            <div style={{ fontWeight: 600 }}>上传技能包</div>
            <div className="muted small" style={{ marginTop: 2 }}>把技能 zip 包拖到这里，或者点按钮选文件。包里每个技能一个目录，目录里要有 SKILL.md；一个包可以装多个技能。附属文件只保存展示，不会自动执行。</div>
          </div>
          <label className="btn sm" style={{ cursor: "pointer" }}>
            {importing ? "正在导入…" : "选择 zip 文件"}
            <input type="file" accept=".zip" style={{ display: "none" }} disabled={importing}
              onChange={(e) => { const f = e.target.files?.[0]; if (f) void importZip(f); e.target.value = ""; }} />
          </label>
        </div>
        {importError && <div className="small" style={{ color: "var(--destructive)", marginTop: 8 }}>导入失败：{importError}</div>}
        {importResult && (
          <div style={{ marginTop: 10 }}>
            <div className="small" style={{ fontWeight: 600 }}>导入结果：成功 {importResult.imported.length} 个，跳过 {importResult.skipped.length} 个</div>
            {importResult.imported.map((s) => (
              <div key={s.id} className="small" style={{ marginTop: 4 }}>✓ 已导入「{s.name}」{s.files > 0 ? `，附带 ${s.files} 个文件` : ""}</div>
            ))}
            {importResult.skipped.map((s, i) => (
              <div key={i} className="small" style={{ marginTop: 4, color: "var(--warning)" }}>跳过「{s.where}」：{s.reason}</div>
            ))}
          </div>
        )}
      </Card>
      <StateBox loading={skills.loading} error={skills.error} empty={(skills.data?.length ?? 0) === 0}
        emptyText="还没有技能" emptyActionLabel="创建第一个技能" onEmptyAction={() => setShowForm(true)} onRetry={() => void skills.reload()}>
        <div className="grid grid-2">{(skills.data ?? []).map((s) => (
          <Card key={s.id} className="flex flex-col">
            <CardHeader className="p-5 pb-0">
              <div className="flex items-start justify-between gap-2">
                <CardTitle className="text-base">{s.name}</CardTitle>
                <div className="flex shrink-0 items-center gap-1.5">
                  <Badge variant="secondary">v{s.version}</Badge>
                  <MoreMenu items={[
                    { label: "删除技能", danger: true, confirmText: `删除技能「${s.name}」？`, onClick: () => void api.deleteSkill(s.id).then(() => skills.reload()).catch((e) => showToast("error", `删除技能失败：${e instanceof Error ? e.message : String(e)}，可以稍后再试。`)) },
                  ]} />
                </div>
              </div>
            </CardHeader>
            <CardContent className="flex flex-1 flex-col p-5 pt-3">
              <p className="text-sm leading-relaxed text-muted-foreground">{s.description || "暂无描述。"}</p>
              <div className="mt-auto pt-2">
                <div className="flex flex-wrap gap-1.5">
                  {s.trusted
                    ? <Badge variant="secondary" className="border-transparent bg-success-tint text-success hover:bg-success-tint">已验证可信</Badge>
                    : <Badge variant="secondary" className="border-transparent bg-warning-tint text-warning hover:bg-warning-tint">来源待验证</Badge>}
                  <Badge variant="secondary">{s.enabled ? "已启用" : "已禁用"}</Badge>
                  {s.source === "import"
                    ? <Badge variant="secondary" className="border-transparent bg-info-tint text-info hover:bg-info-tint">导入</Badge>
                    : <Badge variant="secondary">手动创建</Badge>}
                </div>
                {(s.files?.length ?? 0) > 0 && (
                  <div className="mt-2">
                    {s.files!.map((f) => (
                      <div key={f.path} className="text-xs text-muted-foreground">· {f.path} · {f.size.toLocaleString("zh-CN")} 字节</div>
                    ))}
                  </div>
                )}
              </div>
            </CardContent>
          </Card>))}
        </div>
      </StateBox>
      <Dialog open={showForm} onOpenChange={setShowForm}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>创建技能</DialogTitle>
            <DialogDescription>技能名称必填。</DialogDescription>
          </DialogHeader>
          <div className="flex flex-col gap-2">
            <Label htmlFor="skill-name">技能名称</Label>
            <Input id="skill-name" autoFocus value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
          </div>
          <div className="flex flex-col gap-2">
            <Label htmlFor="skill-desc">描述</Label>
            <Input id="skill-desc" value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} />
          </div>
          <div className="flex flex-col gap-2">
            <Label htmlFor="skill-instructions">技能指令</Label>
            <Textarea id="skill-instructions" placeholder="写清这个技能的做法步骤与要求。" value={form.instructions} onChange={(e) => setForm({ ...form, instructions: e.target.value })} />
          </div>
          <DialogFooter>
            <Button variant="secondary" onClick={() => setShowForm(false)}>取消</Button>
            <Button disabled={!form.name.trim() || creating} onClick={() => void create()}>{creating ? "创建中…" : "创建技能"}</Button>
          </DialogFooter>
          {createError && <div className="small" style={{ color: "var(--destructive)" }}>{createError}</div>}
        </DialogContent>
      </Dialog>
    </div>
  );
}
