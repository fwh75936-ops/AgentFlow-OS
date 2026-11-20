import { useRef, useState } from "react";
import { api } from "../services/api";
import { MoreMenu, StateBox, useAsync } from "../components/ui";
import { showToast } from "../motion/Toast";
import type { McpServer } from "../types";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";

/* Backend MCP session states (app/mcp/client.py): new -> handshaking
 * -> ready -> closed | dead. The logs endpoint returns them as `state`. */
const MCP_STATE_LABELS: Record<string, string> = {
  new: "尚未连接", handshaking: "正在连接", ready: "已连接", closed: "已关闭", dead: "连接已断开",
};

type CatalogItem = {
  key: string; name: string; description: string; command: string; args: string[];
  runtime: string; runtime_available: boolean;
  env: Array<{ key: string; label: string; secret: boolean }>;
  args_note?: string; extra_arg_placeholder?: string;
};

function ServerDetail({ server }: { server: McpServer }) {
  const [tab, setTab] = useState<"tools" | "prompts" | "resources" | "logs">("tools");
  const [data, setData] = useState<unknown>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // 快速连点页签时旧请求可能后到：带序号，只认最新一次请求的结果，
  // 否则会短暂把别的页签的数据显示在当前页签下。
  const seqRef = useRef(0);

  async function load(t: typeof tab) {
    const seq = ++seqRef.current;
    setTab(t); setLoading(true); setError(null); setData(null);
    try {
      let d: unknown;
      if (t === "tools") d = await api.mcpTools(server.id);
      else if (t === "prompts") d = await api.mcpPrompts(server.id);
      else if (t === "resources") d = await api.mcpResources(server.id);
      else d = await api.mcpLogs(server.id);
      if (seqRef.current !== seq) return;
      setData(d);
    } catch (e) {
      if (seqRef.current !== seq) return;
      setError(e instanceof Error ? e.message : String(e));
    } finally { if (seqRef.current === seq) setLoading(false); }
  }

  const d = (data ?? {}) as Record<string, unknown>;
  return (
    <div className="mt-3 border-t pt-3">
      <div className="flex gap-1.5">
        {([["tools", "工具"], ["prompts", "提示词"], ["resources", "资源"], ["logs", "服务器日志"]] as const).map(([t, label]) => (
          <Button key={t} size="sm" variant={tab === t ? "default" : "secondary"} onClick={() => void load(t)}>{label}</Button>
        ))}
      </div>
      {loading && <div className="mt-2 text-sm text-muted-foreground">正在向服务器询问…</div>}
      {error && <div className="mt-2 text-sm" style={{ color: "var(--destructive)" }}>没拿到：{error}。先点一次「测试连接」建立连接再看。</div>}
      {data !== null && tab === "tools" && (
        <div className="mt-2 flex flex-col gap-1.5">
          {((d.tools as Array<{ name: string; description: string }>) ?? []).map((t) => (
            <div key={t.name} className="text-sm"><b className="font-mono">{t.name}</b><span className="text-muted-foreground"> · {t.description}</span></div>
          ))}
          {((d.tools as unknown[]) ?? []).length === 0 && <div className="text-sm text-muted-foreground">这台服务器没有提供工具。</div>}
        </div>
      )}
      {data !== null && tab === "prompts" && (
        <div className="mt-2 flex flex-col gap-1.5">
          {((d.prompts as Array<{ name: string; description?: string }>) ?? []).map((p) => (
            <div key={p.name} className="text-sm"><b className="font-mono">{p.name}</b><span className="text-muted-foreground"> · {p.description ?? ""}</span></div>
          ))}
          {((d.prompts as unknown[]) ?? []).length === 0 && <div className="text-sm text-muted-foreground">这台服务器没有提供提示词。</div>}
        </div>
      )}
      {data !== null && tab === "resources" && (
        <div className="mt-2 flex flex-col gap-1.5">
          {((d.resources as Array<{ uri: string; name?: string }>) ?? []).map((r) => (
            <div key={r.uri} className="text-sm font-mono">{r.uri}</div>
          ))}
          {((d.resources as unknown[]) ?? []).length === 0 && <div className="text-sm text-muted-foreground">这台服务器没有提供资源。</div>}
        </div>
      )}
      {data !== null && tab === "logs" && (
        <div className="mt-2">
          <div className="text-sm">连接状态：{MCP_STATE_LABELS[String(d.state ?? "")] ?? "未知"}</div>
          <pre className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap rounded bg-muted p-2 font-mono text-xs">{(Array.isArray(d.stderr_tail) ? (d.stderr_tail as string[]).join("\n") : String(d.stderr_tail ?? "")) || "暂无日志输出"}</pre>
        </div>
      )}
    </div>
  );
}

function CatalogCard({ item, onAdded }: { item: CatalogItem; onAdded: () => void }) {
  const [envVals, setEnvVals] = useState<Record<string, string>>({});
  const [extraArg, setExtraArg] = useState("");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);

  async function add() {
    setBusy(true); setNote(null);
    try {
      const args = [...item.args];
      if (item.extra_arg_placeholder) {
        if (!extraArg.trim()) { setNote(`先填：${item.extra_arg_placeholder}`); setBusy(false); return; }
        args.push(extraArg.trim());
      }
      await api.createMcp({
        name: item.name, transport: "stdio", command_or_url: item.command,
        args, env: envVals, enabled: true, tools: [],
      });
      setNote("已添加，去下面的服务器列表里点「测试连接」验证。");
      onAdded();
    } catch (e) {
      setNote(`添加失败：${e instanceof Error ? e.message : String(e)}`);
    } finally { setBusy(false); }
  }

  return (
    <Card className="flex h-full flex-col">
      <CardContent className="flex flex-1 flex-col p-4">
        <div className="flex items-start justify-between gap-2">
          <b className="text-sm">{item.name}</b>
          {item.runtime_available
            ? <Badge variant="secondary" className="border-transparent bg-success-tint text-success hover:bg-success-tint">可直接添加</Badge>
            : <Badge variant="secondary" className="border-transparent bg-warning-tint text-warning hover:bg-warning-tint">缺运行时 {item.runtime}</Badge>}
        </div>
        <p className="mt-1 text-sm leading-relaxed text-muted-foreground">{item.description}</p>
        <p className="mt-1 break-words font-mono text-xs text-muted-foreground">{item.command} {item.args.join(" ")}</p>
        {!item.runtime_available && (
          <p className="mt-1 text-sm" style={{ color: "var(--warning)" }}>这台电脑还没装 {item.runtime}，可以先添加，但要装好运行时才能真正连上。</p>
        )}
        {item.args_note && <p className="mt-1 text-sm text-muted-foreground">{item.args_note}</p>}
        {item.extra_arg_placeholder && (
          <Input className="mt-2" placeholder={item.extra_arg_placeholder} value={extraArg} onChange={(e) => setExtraArg(e.target.value)} />
        )}
        {item.env.map((f) => (
          <div key={f.key} className="mt-2 flex flex-col gap-1">
            <Label>{f.label}</Label>
            <Input type={f.secret ? "password" : "text"} autoComplete="off"
              value={envVals[f.key] ?? ""} onChange={(e) => setEnvVals({ ...envVals, [f.key]: e.target.value })} />
          </div>
        ))}
        <div className="mt-auto flex items-center gap-2 pt-3">
          <Button size="sm" disabled={busy} onClick={() => void add()}>{busy ? "添加中…" : "一键添加"}</Button>
          {note && <span className="text-sm text-muted-foreground">{note}</span>}
        </div>
      </CardContent>
    </Card>
  );
}

export default function Mcp() {
  const servers = useAsync(() => api.mcpServers());
  const catalog = useAsync(() => api.mcpCatalog());
  const runtimes = useAsync(() => api.systemRuntimes());
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState({ name: "", transport: "stdio", command_or_url: "", args: "" });
  const [creating, setCreating] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<Record<number, string>>({});
  const [testingId, setTestingId] = useState<number | null>(null);
  const [togglingId, setTogglingId] = useState<number | null>(null);
  const [detailId, setDetailId] = useState<number | null>(null);
  const [jsonText, setJsonText] = useState("");
  const [jsonResults, setJsonResults] = useState<Array<{ name: string; ok: boolean; reason?: string }> | null>(null);
  const [jsonError, setJsonError] = useState<string | null>(null);
  const [importingJson, setImportingJson] = useState(false);

  async function create() {
    if (!form.name.trim() || creating) return;
    setCreating(true); setFormError(null);
    try {
      await api.createMcp({
        ...form,
        args: form.args.split(/\s+/).filter(Boolean),
        enabled: true, tools: [],
      });
      setForm({ name: "", transport: "stdio", command_or_url: "", args: "" }); setShowForm(false); void servers.reload();
    } catch (e) {
      setFormError(`添加失败：${e instanceof Error ? e.message : String(e)}，可以检查名称与命令后重试。`);
    } finally { setCreating(false); }
  }

  async function test(s: McpServer) {
    if (testingId !== null) return;
    setTestingId(s.id);
    setTestResult((m) => ({ ...m, [s.id]: "正在真实连接…" }));
    try {
      if (s.transport === "stdio") {
        const r = await api.mcpConnect(s.id);
        setTestResult((m) => ({ ...m, [s.id]: `连接成功：${r.server_info.name} ${r.server_info.version}，提供 ${r.tools.length} 个工具` }));
      } else {
        const r = await api.testMcp(s.id);
        setTestResult((m) => ({ ...m, [s.id]: r.ok ? `连接可达，状态码 ${r.status_code}` : `连接未通过：${r.error}` }));
      }
    } catch (e) {
      setTestResult((m) => ({ ...m, [s.id]: `连接失败：${e instanceof Error ? e.message : String(e)}` }));
    } finally { setTestingId(null); }
  }

  async function toggleEnabled(s: McpServer) {
    // 之前直接调接口无 catch、无 busy 守卫：失败时静默、连点会重复提交。
    if (togglingId !== null) return;
    setTogglingId(s.id);
    try {
      await api.updateMcp(s.id, { enabled: !s.enabled });
      void servers.reload();
    } catch (e) {
      showToast("error", `切换失败：${e instanceof Error ? e.message : String(e)}，可以稍后再试。`);
    } finally { setTogglingId(null); }
  }

  async function importJson() {
    if (importingJson) return;
    setImportingJson(true);
    setJsonError(null); setJsonResults(null);
    try {
      const r = await api.mcpImportConfig(jsonText);
      setJsonResults(r.results);
      void servers.reload();
    } catch (e) {
      setJsonError(e instanceof Error ? e.message : String(e));
    } finally {
      setImportingJson(false);
    }
  }

  const runtimeKeys = ["node", "npx", "python3", "uvx"];
  const runtimeRows = (runtimes.data?.runtimes ?? []).filter((r) => runtimeKeys.includes(r.key));

  return (
    <div>
      <div className="spread">
        <div>
          <h1 className="page-title">MCP</h1>
          <p className="page-desc">MCP 是模型上下文协议，用来给员工接入外部工具。从下面的目录里点一个就能加上，不用手写配置。</p>
        </div>
        <Button variant="secondary" onClick={() => setShowForm(true)}>高级：手动添加</Button>
      </div>

      {runtimeRows.length > 0 && (
        <Card className="mb-3.5">
          <CardContent className="flex flex-wrap items-center gap-2 p-4">
            <span className="text-sm font-medium">本机运行时</span>
            {runtimeRows.map((r) => (
              <Badge key={r.key} variant="secondary" className={r.available ? "border-transparent bg-success-tint text-success hover:bg-success-tint" : "border-transparent bg-warning-tint text-warning hover:bg-warning-tint"}>
                {r.label}：{r.available ? `可用 ${r.version}` : "未安装"}
              </Badge>
            ))}
            <span className="text-sm text-muted-foreground">只检测展示，缺的需要自行安装。</span>
          </CardContent>
        </Card>
      )}

      <div style={{ fontWeight: 600, marginBottom: 8 }}>预置目录</div>
      <div className="grid grid-2" style={{ marginBottom: 16 }}>
        {(catalog.data?.catalog ?? []).map((item) => (
          <CatalogCard key={item.key} item={item} onAdded={() => void servers.reload()} />
        ))}
      </div>
      {(catalog.data?.catalog ?? []).length === 0 && !catalog.loading && (
        <div className="muted small" style={{ marginBottom: 16 }}>目录没有加载出来，请确认后端服务在运行。</div>
      )}

      <Card className="p-4" style={{ marginBottom: 16 }}>
        <div style={{ fontWeight: 600 }}>粘贴配置添加</div>
        <div className="muted small" style={{ marginTop: 2 }}>把别处复制来的 mcpServers 配置整段粘贴进来，一次可以加多台，逐条告诉你结果。</div>
        <Textarea className="mt-2 font-mono" placeholder={'{\n  "mcpServers": {\n    "名字": { "command": "npx", "args": ["-y", "包名"] }\n  }\n}'} value={jsonText} onChange={(e) => setJsonText(e.target.value)} />
        <div className="row mt-2">
          <Button size="sm" disabled={!jsonText.trim() || importingJson} onClick={() => void importJson()}>{importingJson ? "添加中…" : "解析并添加"}</Button>
          {jsonError && <span className="small" style={{ color: "var(--destructive)" }}>{jsonError}</span>}
        </div>
        {jsonResults && (
          <div style={{ marginTop: 8 }}>
            {jsonResults.map((r, i) => (
              <div key={i} className="small" style={{ marginTop: 3 }}>
                {r.ok ? `✓ 已添加「${r.name}」` : <span style={{ color: "var(--warning)" }}>跳过「{r.name}」：{r.reason}</span>}
              </div>
            ))}
          </div>
        )}
      </Card>

      <div style={{ fontWeight: 600, marginBottom: 8 }}>已添加的服务器</div>
      <StateBox loading={servers.loading} error={servers.error} empty={(servers.data?.length ?? 0) === 0}
        emptyText="还没有 MCP 服务器" emptyHint="从上面的预置目录里点一个添加，或者粘贴一段现成配置。" onRetry={() => void servers.reload()}>
        <div className="grid grid-2">{(servers.data ?? []).map((s) => (
          <Card key={s.id} className="flex flex-col">
            <CardHeader className="p-5 pb-0">
              <div className="flex items-start justify-between gap-2">
                <CardTitle className="text-base">{s.name}</CardTitle>
                <MoreMenu items={[
                  { label: "删除服务器", danger: true, confirmText: `删除 MCP 服务器「${s.name}」？`, onClick: () => void api.deleteMcp(s.id).then(() => servers.reload()).catch((e) => showToast("error", `删除服务器失败：${e instanceof Error ? e.message : String(e)}，可以稍后再试。`)) },
                ]} />
              </div>
            </CardHeader>
            <CardContent className="flex flex-1 flex-col p-5 pt-3">
              <div className="flex flex-wrap items-center gap-1.5">
                <Badge variant="secondary">{s.transport === "stdio" ? "本地命令" : "网页服务"}</Badge>
                <Badge variant="secondary" className={s.enabled ? "border-transparent bg-success-tint text-success hover:bg-success-tint" : ""}>{s.enabled ? "已启用" : "已停用"}</Badge>
              </div>
              <p className="mt-2 break-words font-mono text-sm text-muted-foreground">{s.command_or_url} {(s.args ?? []).join(" ")}</p>
              <div className="mt-auto flex flex-wrap gap-1.5 pt-2.5">
                <Button variant="secondary" size="sm" disabled={testingId === s.id} onClick={() => void test(s)}>{testingId === s.id ? "测试中…" : "测试连接"}</Button>
                <Button variant="secondary" size="sm" onClick={() => setDetailId(detailId === s.id ? null : s.id)}>{detailId === s.id ? "收起详情" : "查看详情"}</Button>
                <Button variant="secondary" size="sm" disabled={togglingId === s.id} onClick={() => void toggleEnabled(s)}>{togglingId === s.id ? "处理中…" : s.enabled ? "停用" : "启用"}</Button>
              </div>
              {testResult[s.id] && <div className="mt-2 text-sm">{testResult[s.id]}</div>}
              {detailId === s.id && <ServerDetail server={s} />}
            </CardContent>
          </Card>))}
        </div>
      </StateBox>

      <Dialog open={showForm} onOpenChange={(open) => { if (open) setFormError(null); setShowForm(open); }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>高级：手动添加 MCP 服务器</DialogTitle>
            <DialogDescription>目录里没有的服务器可以在这里手填。目前真实支持本地命令方式连接；网页服务方式可以登记，连接测试只做可达性检查。</DialogDescription>
          </DialogHeader>
          <div className="flex flex-col gap-2">
            <Label htmlFor="mcp-name">服务器名称</Label>
            <Input id="mcp-name" autoFocus value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
          </div>
          <div className="flex flex-col gap-2">
            <Label>连接方式</Label>
            <Select value={form.transport} onValueChange={(v) => setForm({ ...form, transport: v })}>
              <SelectTrigger><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="stdio">本地命令</SelectItem>
                <SelectItem value="streamable_http">流式网页服务</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className="flex flex-col gap-2">
            <Label htmlFor="mcp-target">命令或地址</Label>
            <Input id="mcp-target" value={form.command_or_url} onChange={(e) => setForm({ ...form, command_or_url: e.target.value })} />
          </div>
          <div className="flex flex-col gap-2">
            <Label htmlFor="mcp-args">参数，多个参数用空格隔开</Label>
            <Input id="mcp-args" value={form.args} onChange={(e) => setForm({ ...form, args: e.target.value })} />
          </div>
          {formError && <div className="text-sm" style={{ color: "var(--destructive)" }}>{formError}</div>}
          <DialogFooter>
            <Button variant="secondary" onClick={() => setShowForm(false)}>取消</Button>
            <Button disabled={!form.name.trim() || creating} onClick={() => void create()}>{creating ? "添加中…" : "添加"}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
