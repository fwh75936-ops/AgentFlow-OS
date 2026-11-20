import { useState } from "react";
import { api } from "../services/api";
import { fmtAgo } from "../lib/time";
import { showToast } from "../motion/Toast";
import { IconChip, MoreMenu, StateBox, useAsync } from "../components/ui";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";

const SOURCE_LABELS: Record<string, string> = {
  upload: "上传", text: "粘贴文本", workspace: "共享工作区", manual: "手动添加", seed: "预置",
};

type ImportResult = { filename: string; status: string; chunks?: number; error?: string };

export default function Knowledge() {
  const docs = useAsync(() => api.knowledgeDocuments());
  const wsFiles = useAsync(() => api.wsFiles());
  const [dragOver, setDragOver] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [results, setResults] = useState<ImportResult[]>([]);
  const [pasteTitle, setPasteTitle] = useState("");
  const [pasteText, setPasteText] = useState("");
  const [pasteMsg, setPasteMsg] = useState<string | null>(null);
  const [wsPath, setWsPath] = useState("");
  const [wsMsg, setWsMsg] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [searching, setSearching] = useState(false);
  const [pasting, setPasting] = useState(false);
  const [importingWs, setImportingWs] = useState(false);
  const [expandedChunks, setExpandedChunks] = useState<Set<number>>(new Set());
  const [searchData, setSearchData] = useState<{ method: string; results: Array<{ item_id: number; score: number; keyword_score: number; tfidf_score: number; chunk: string }> } | null>(null);
  const [searchError, setSearchError] = useState<string | null>(null);

  const docTitle = (itemId: number) => docs.data?.find((d) => d.id === itemId)?.title ?? `文档 #${itemId}`;
  const fileList = (wsFiles.data ?? []).filter((f) => !f.is_dir);

  async function upload(files: File[]) {
    if (!files.length || uploading) return;
    setUploading(true);
    setResults(files.map((f) => ({ filename: f.name, status: "parsing" })));
    try {
      const r = await api.knowledgeImport(files);
      setResults(r.results);
      void docs.reload();
    } catch (e) {
      setResults(files.map((f) => ({ filename: f.name, status: "failed", error: e instanceof Error ? e.message : String(e) })));
    } finally { setUploading(false); }
  }

  async function paste() {
    if (!pasteText.trim() || pasting) return;
    setPasting(true);
    try {
      const r = await api.knowledgeImportText(pasteTitle.trim() || "粘贴的文档", pasteText);
      setPasteMsg(`已建好文档，切成 ${r.chunks} 个分片进了索引。`);
      setPasteTitle(""); setPasteText("");
      void docs.reload();
    } catch (e) { setPasteMsg(`没建好：${e instanceof Error ? e.message : String(e)}。可以检查文字内容后重试。`); }
    finally { setPasting(false); }
  }

  async function importWorkspace() {
    if (!wsPath || importingWs) return;
    setImportingWs(true);
    try {
      const r = await api.knowledgeImportWorkspace(wsPath);
      setWsMsg(`已导入，切成 ${r.chunks} 个分片进了索引。`);
      void docs.reload();
    } catch (e) { setWsMsg(`没导进来：${e instanceof Error ? e.message : String(e)}。确认文件还在共享工作区里后重试。`); }
    finally { setImportingWs(false); }
  }

  async function search() {
    if (!query.trim() || searching) return;
    setSearching(true); setSearchError(null); setExpandedChunks(new Set());
    try {
      setSearchData(await api.knowledgeSearch(query.trim()));
    } catch (e) {
      setSearchError(e instanceof Error ? e.message : String(e));
      setSearchData(null);
    } finally { setSearching(false); }
  }

  return (
    <div>
      <div className="spread">
        <div>
          <h1 className="page-title">知识库</h1>
          <p className="page-desc">把文档放进来，员工执行任务时会自动查这里的内容，并在执行记录里注明引用了哪一篇。</p>
        </div>
      </div>

      <Card className="p-4" style={{ marginBottom: 14, borderStyle: dragOver ? "dashed" : undefined, borderColor: dragOver ? "var(--info)" : undefined }}
        onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
        onDragLeave={() => setDragOver(false)}
        onDrop={(e) => { e.preventDefault(); setDragOver(false); void upload(Array.from(e.dataTransfer.files ?? [])); }}>
        <div className="spread">
          <div>
            <div style={{ fontWeight: 600 }}>导入文档</div>
            <div className="muted small" style={{ marginTop: 2 }}>把 txt、md、pdf、docx 文件拖到这里，或者点按钮选文件，可以多选。导入后会自动切分并建立索引。</div>
          </div>
          <label className="btn sm" style={{ cursor: "pointer" }}>
            {uploading ? "正在解析…" : "选择文件"}
            <input type="file" accept=".txt,.md,.markdown,.pdf,.docx" multiple style={{ display: "none" }} disabled={uploading}
              onChange={(e) => { void upload(Array.from(e.target.files ?? [])); e.target.value = ""; }} />
          </label>
        </div>
        {results.length > 0 && (
          <div style={{ marginTop: 10 }}>
            {results.map((r, i) => (
              <div key={i} className="small" style={{ marginTop: 4 }}>
                {r.status === "indexed" && <span>✓ {r.filename}：已索引，共 {r.chunks} 个分片</span>}
                {r.status === "parsing" && <span className="muted">… {r.filename}：解析中</span>}
                {r.status === "failed" && <span style={{ color: "var(--destructive)" }}>✕ {r.filename}：{r.error}</span>}
              </div>
            ))}
          </div>
        )}
        <div className="grid grid-2" style={{ marginTop: 14, gap: 14 }}>
          <div>
            <div className="small" style={{ fontWeight: 600, marginBottom: 6 }}>粘贴文本建文档</div>
            <Input placeholder="文档标题" value={pasteTitle} onChange={(e) => setPasteTitle(e.target.value)} />
            <Textarea className="mt-2" rows={4} placeholder="把要存进知识库的文字粘贴到这里" value={pasteText} onChange={(e) => setPasteText(e.target.value)} />
            <div className="row mt-2">
              <Button size="sm" disabled={!pasteText.trim() || pasting} onClick={() => void paste()}>{pasting ? "保存中…" : "保存为文档"}</Button>
              {pasteMsg && <span className="small muted">{pasteMsg}</span>}
            </div>
          </div>
          <div style={{ display: "flex", flexDirection: "column" }}>
            <div className="small" style={{ fontWeight: 600, marginBottom: 6 }}>从共享工作区导入</div>
            <select className="input" value={wsPath} onChange={(e) => setWsPath(e.target.value)}>
              <option value="">{wsFiles.loading ? "正在加载文件列表…" : "选择共享工作区里的文件"}</option>
              {fileList.map((f) => <option key={f.path} value={f.path}>{f.path}</option>)}
            </select>
            {wsFiles.error && <div className="small" style={{ color: "var(--destructive)", marginTop: 6 }}>文件列表加载失败：{wsFiles.error}，可以刷新页面后重试。</div>}
            <div className="row mt-2">
              <Button size="sm" variant="secondary" disabled={!wsPath || importingWs} onClick={() => void importWorkspace()}>{importingWs ? "导入中…" : "导入这个文件"}</Button>
              {wsMsg && <span className="small muted">{wsMsg}</span>}
            </div>
            <div className="muted small" style={{ marginTop: "auto", paddingTop: 6 }}>只列共享工作区根目录与子目录里的文件。</div>
          </div>
        </div>
      </Card>

      <Card className="p-4" style={{ marginBottom: 14 }}>
        <div style={{ fontWeight: 600, marginBottom: 6 }}>检索自测</div>
        <div className="muted small" style={{ marginBottom: 8 }}>输入一个问题，看看知识库会命中哪些分片。分数是检索引擎真实算出来的综合相关度。</div>
        <div className="row">
          <Input placeholder="例如：发现文件冲突怎么办" value={query} onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") void search(); }} />
          <Button disabled={searching || !query.trim()} onClick={() => void search()}>{searching ? "检索中…" : "检索"}</Button>
        </div>
        {searchError && <div className="small" style={{ color: "var(--destructive)", marginTop: 8 }}>检索失败：{searchError}。可以换个问法再试一次。</div>}
        {searchData && (
          <div style={{ marginTop: 10 }}>
            <div className="muted small">检索方式：按关键词与词频混合打分。本次命中 {searchData.results.length} 条。</div>
            {searchData.results.map((r, i) => (
              <div key={i} className="card" style={{ marginTop: 8 }}>
                <div className="spread">
                  <b className="small">{docTitle(r.item_id)}</b>
                  <span className="small muted">综合 {r.score.toFixed(3)} · 关键词 {r.keyword_score.toFixed(3)} · 词频 {r.tfidf_score.toFixed(3)}</span>
                </div>
                <div className="small" style={{ marginTop: 4, lineHeight: 1.6 }}>{expandedChunks.has(i) ? r.chunk : r.chunk.slice(0, 220)}</div>
                {r.chunk.length > 220 && (
                  <button className="btn secondary sm" style={{ marginTop: 6 }} onClick={() => setExpandedChunks((prev) => {
                    const next = new Set(prev);
                    if (next.has(i)) next.delete(i); else next.add(i);
                    return next;
                  })}>{expandedChunks.has(i) ? "收起" : "展开全文"}</button>
                )}
              </div>
            ))}
            {searchData.results.length === 0 && <div className="muted small" style={{ marginTop: 6 }}>没有命中，换个问法或者先导入相关文档。</div>}
          </div>
        )}
      </Card>

      <div style={{ fontWeight: 600, marginBottom: 8 }}>文档列表</div>
      <StateBox loading={docs.loading} error={docs.error} empty={(docs.data?.length ?? 0) === 0}
        emptyText="知识库还是空的" emptyHint="先导入第一份文档，员工执行任务时就能自动查到里面的内容。" emptyArt="knowledge" onRetry={() => void docs.reload()}>
        <div className="grid">{(docs.data ?? []).map((d) => (
          <Card key={d.id}>
            <CardContent className="flex gap-3 p-4">
              <IconChip icon="file" tone="info" />
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-1.5">
                  <b className="text-sm">{d.title}</b>
                  <Badge variant="secondary" className="border-transparent bg-info-tint text-info hover:bg-info-tint">{SOURCE_LABELS[d.source] ?? "手动添加"}</Badge>
                  <Badge variant="secondary">{d.chunks} 个分片</Badge>
                  <Badge variant="secondary">{d.chars.toLocaleString("zh-CN")} 字</Badge>
                  {d.created_at && <span className="text-xs text-muted-foreground" title={new Date(d.created_at).toLocaleString("zh-CN", { hour12: false })}>{fmtAgo(d.created_at)}创建</span>}
                </div>
                <div className="small muted" style={{ marginTop: 4, lineHeight: 1.6 }}>{d.preview}</div>
              </div>
              <MoreMenu items={[
                { label: "删除这篇文档", danger: true, confirmText: `删除「${d.title}」？它的分片会同时从索引里清掉。`, onClick: () => void api.deleteMemory(d.id).then(() => docs.reload()).catch((e) => showToast("error", `删除文档失败：${e instanceof Error ? e.message : String(e)}，可以稍后再试。`)) },
              ]} />
            </CardContent>
          </Card>))}
        </div>
      </StateBox>
    </div>
  );
}
