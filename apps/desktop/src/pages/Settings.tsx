import { useRef, useState } from "react";
import { api } from "../services/api";
import { MoreMenu, StateBox, useAsync } from "../components/ui";
import { showToast } from "../motion/Toast";
import type { Provider } from "../types";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { cn } from "@/lib/utils";

/* Step 1 picks the upstream PROTOCOL (endpoint shape), not a vendor.
 * Vendor presets below a format are quick-fill only: they fill name +
 * base_url, both stay editable. */
type FormatKey = "chat_completions" | "responses" | "anthropic";
const FORMATS: Array<{ key: FormatKey; label: string; endpoint: string; desc: string }> = [
  { key: "chat_completions", label: "聊天补全", endpoint: "/v1/chat/completions", desc: "最常见的兼容协议，DeepSeek、OpenAI、通义千问、智谱、月之暗面、OpenRouter 和本地 Ollama 都用这种" },
  { key: "responses", label: "响应接口", endpoint: "/v1/responses", desc: "OpenAI 的新版协议，请求和返回结构与聊天补全不同，系统会自动翻译" },
  { key: "anthropic", label: "消息接口", endpoint: "/v1/messages", desc: "Claude 的官方协议，用单独的密钥请求头鉴权" },
];
const FORMAT_LABEL: Record<string, string> = {
  chat_completions: "聊天补全", responses: "响应接口", anthropic: "消息接口",
};
const FORMAT_TONE: Record<string, string> = {
  chat_completions: "bg-cat-blue-tint text-cat-blue", responses: "bg-cat-violet-tint text-cat-violet", anthropic: "bg-cat-orange-tint text-cat-orange",
};
const FORMAT_URL_HINT: Record<FormatKey, string> = {
  chat_completions: "填服务的基础地址，系统会自动在后面拼接 /chat/completions 和 /models",
  responses: "填服务的基础地址，系统会自动在后面拼接 /responses 和 /models",
  anthropic: "填服务的基础地址，系统会自动在后面拼接 /v1/messages 和 /v1/models",
};
type VendorPreset = { key: string; label: string; name: string; base_url: string };
const VENDOR_PRESETS: Record<FormatKey, VendorPreset[]> = {
  chat_completions: [
    { key: "deepseek", label: "DeepSeek", name: "DeepSeek", base_url: "https://api.deepseek.com" },
    { key: "openai", label: "OpenAI", name: "OpenAI", base_url: "https://api.openai.com/v1" },
    { key: "qwen", label: "通义千问", name: "通义千问", base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1" },
    { key: "zhipu", label: "智谱", name: "智谱", base_url: "https://open.bigmodel.cn/api/paas/v4" },
    { key: "moonshot", label: "月之暗面", name: "月之暗面", base_url: "https://api.moonshot.cn/v1" },
    { key: "openrouter", label: "OpenRouter", name: "OpenRouter", base_url: "https://openrouter.ai/api/v1" },
    { key: "ollama", label: "Ollama 本地", name: "Ollama 本地", base_url: "http://127.0.0.1:11434/v1" },
    { key: "custom", label: "自定义", name: "", base_url: "" },
  ],
  responses: [
    { key: "openai", label: "OpenAI", name: "OpenAI", base_url: "https://api.openai.com/v1" },
    { key: "custom", label: "自定义", name: "", base_url: "" },
  ],
  anthropic: [
    { key: "anthropic", label: "Anthropic 官方", name: "Anthropic", base_url: "https://api.anthropic.com" },
    { key: "custom", label: "自定义", name: "", base_url: "" },
  ],
};
const STEP_LABELS = ["选择格式", "地址与密钥", "获取模型", "启用"];
const ABOUT_KEYWORDS = "关于与系统信息 产品 版本 后端服务 数据目录 AgentFlow OS";

/** Mirror of the backend client's endpoint rule, for the live preview:
 * a base URL ending in # is used verbatim; otherwise the protocol
 * path is appended (…/v1 + leaf, or the leaf alone when the base
 * already ends with /v1 or with the leaf itself). */
function finalPath(base: string, format: string): string {
  const b = (base || "").replace(/\/+$/, "");
  if (!b) return "";
  if (b.endsWith("#")) return b.slice(0, -1);
  if (format === "anthropic") {
    if (b.endsWith("/messages")) return b;
    if (b.endsWith("/v1")) return `${b}/messages`;
    return `${b}/v1/messages`;
  }
  const leaf = format === "responses" ? "/responses" : "/chat/completions";
  if (b.endsWith(leaf)) return b;
  if (b.endsWith("/v1")) return `${b}${leaf}`;
  return `${b}/v1${leaf}`;
}

/** Per-provider model curation: only models added here appear in the
 * agent binding pickers. Candidates come from the provider's real
 * model list; each one is added with a click, manual IDs are allowed,
 * and 检测 sends one real chat request with the chosen model. */
function ProviderModelsPanel({ p, onChanged }: { p: Provider; onChanged: () => void }) {
  const [picked, setPicked] = useState<string[]>(p.models ?? []);
  const [candidates, setCandidates] = useState<string[] | null>(null);
  const [manual, setManual] = useState("");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [detectModel, setDetectModel] = useState("");
  const [detectMsg, setDetectMsg] = useState<string | null>(null);
  const [detecting, setDetecting] = useState(false);

  async function save(next: string[]) {
    if (busy) return;
    setPicked(next);
    setBusy(true); setNote(null);
    try {
      await api.providerModels(p.id, next);
      onChanged();
    } catch (e) {
      setNote(`保存模型清单失败：${e instanceof Error ? e.message : String(e)}`);
    } finally { setBusy(false); }
  }
  async function fetchCandidates() {
    setBusy(true); setNote(null);
    try {
      const r = await api.fetchModels(p.id);
      setCandidates(r.models);
    } catch (e) {
      setNote(`获取模型列表失败：${e instanceof Error ? e.message : String(e)}`);
    } finally { setBusy(false); }
  }
  async function detect() {
    const model = detectModel || picked[0] || "";
    if (!model) { setDetectMsg("先添加一个模型，再做检测。"); return; }
    // 检测会发一次真实模型请求：不加守卫连点会重复扣费。
    if (detecting) return;
    setDetecting(true);
    setDetectMsg("正在发一次真实请求验证…");
    try {
      const r = await api.detectProvider(p.id, model);
      setDetectMsg(r.ok ? `检测通过：${r.model} 回复正常，用时 ${r.latency_ms} 毫秒。` : `检测没通过：${r.error ?? "未知错误"}`);
    } catch (e) {
      setDetectMsg(`检测没通过：${e instanceof Error ? e.message : String(e)}`);
    } finally { setDetecting(false); }
  }

  return (
    <div className="mt-3 border-t pt-3">
      <div className="text-sm font-medium">已添加的模型</div>
      <div className="mt-1 text-sm text-muted-foreground">只有这里的模型会出现在员工绑定等选择里。模型名显示的是服务商真实返回的编号。</div>
      <div className="mt-2 flex flex-wrap gap-1.5">
        {picked.map((m) => (
          <span key={m} className="flex items-center gap-1 rounded-full border border-border bg-background py-1 pl-3 pr-1.5 text-sm">
            <span className="font-mono">{m}</span>
            <button type="button" aria-label={`删除模型 ${m}`} title={`移除模型 ${m}`} disabled={busy}
              className="rounded-full px-1 text-muted-foreground hover:text-destructive"
              onClick={() => void save(picked.filter((x) => x !== m))}>✕</button>
          </span>
        ))}
        {picked.length === 0 && <span className="text-sm text-muted-foreground">还没有添加任何模型。</span>}
      </div>
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <Button variant="secondary" size="sm" disabled={busy} onClick={() => void fetchCandidates()}>从服务商获取模型</Button>
        <Input className="h-8 w-56" placeholder="手动输入模型编号添加" value={manual}
          onChange={(e) => setManual(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && manual.trim()) { void save([...picked, manual.trim()]); setManual(""); } }} />
        <Button variant="secondary" size="sm" disabled={busy || !manual.trim()}
          onClick={() => { void save([...picked, manual.trim()]); setManual(""); }}>添加</Button>
      </div>
      {candidates && (
        <div className="mt-2">
          <div className="text-sm text-muted-foreground">点一下就把模型加进上面的清单：</div>
          <div className="mt-1.5 flex flex-wrap gap-1.5">
            {candidates.filter((m) => !picked.includes(m)).map((m) => (
              <button key={m} type="button" disabled={busy}
                className="rounded-full border border-border bg-background px-3 py-1 font-mono text-sm transition-colors hover:bg-hover"
                onClick={() => void save([...picked, m])}>+ {m}</button>
            ))}
            {candidates.every((m) => picked.includes(m)) && <span className="text-sm text-muted-foreground">服务商的模型都已经加完了。</span>}
          </div>
        </div>
      )}
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <select className="input h-8" style={{ maxWidth: 220 }} value={detectModel} onChange={(e) => setDetectModel(e.target.value)}>
          <option value="">用第一个已添加模型检测</option>
          {picked.map((m) => <option key={m} value={m}>{m}</option>)}
        </select>
        <Button variant="secondary" size="sm" disabled={detecting} onClick={() => void detect()}>{detecting ? "检测中…" : "检测"}</Button>
        {detectMsg && <span className="text-sm text-muted-foreground">{detectMsg}</span>}
      </div>
      <div className="mt-3 text-sm">
        <span className="text-muted-foreground">最终请求路径</span>　
        <span className="break-all font-mono">{finalPath(p.base_url, p.format)}</span>
      </div>
      <div className="text-sm text-muted-foreground">地址只填根地址就会自动补协议路径；地址以 # 结尾时视为完整地址，不再拼接。</div>
      {note && <div className="mt-2 text-sm" style={{ color: "var(--destructive)" }}>{note}</div>}
    </div>
  );
}

const SECTION_ALIASES: Record<string, string> = {
  defaults: "默认设置 新员工 默认供应商 默认模型 default",
  proxy: "网络代理 代理 proxy 网络",
  data: "数据管理 数据目录 备份 恢复 清除 导出 导入",
  usage: "用量统计 用量 统计 token 消耗 请求数",
  notify: "任务通知 通知 提醒 完成提醒",
};

function SystemSections({ providers, query }: { providers: Provider[]; query: string }) {
  const settings = useAsync(() => api.systemSettings());
  const usage = useAsync(() => api.systemUsage());
  const [proxyMode, setProxyMode] = useState("none");
  const [proxyUrl, setProxyUrl] = useState("");
  const [defProvider, setDefProvider] = useState<string>("");
  const [defModel, setDefModel] = useState("");
  const [note, setNote] = useState<string | null>(null);
  const [confirmClear, setConfirmClear] = useState(false);
  const [sysBusy, setSysBusy] = useState(false);
  const [clearing, setClearing] = useState(false);
  const [restoring, setRestoring] = useState(false);
  const [notify, setNotify] = useState(() => localStorage.getItem("af_notify") !== "0");
  const [loaded, setLoaded] = useState(false);

  if (settings.data && !loaded) {
    setLoaded(true);
    setProxyMode(settings.data.proxy_mode);
    setProxyUrl(settings.data.proxy_url);
    setDefProvider(settings.data.default_provider_id ? String(settings.data.default_provider_id) : "");
    setDefModel(settings.data.default_model);
  }

  const show = (key: string) => !query || SECTION_ALIASES[key].includes(query) || key.includes(query);
  const enabledProviders = providers.filter((p) => p.enabled);
  const defProviderObj = enabledProviders.find((p) => String(p.id) === defProvider);

  async function saveProxy() {
    if (sysBusy) return;
    setSysBusy(true);
    try {
      await api.updateSystemSettings({ proxy_mode: proxyMode, proxy_url: proxyMode === "custom" ? proxyUrl : "" });
      setNote("代理设置已保存，下一次模型调用就会按这个设置走。");
    } catch (e) { setNote(`保存失败：${e instanceof Error ? e.message : String(e)}`); }
    finally { setSysBusy(false); }
  }
  async function saveDefaults() {
    if (sysBusy) return;
    setSysBusy(true);
    try {
      await api.updateSystemSettings({
        default_provider_id: defProvider ? Number(defProvider) : null,
        default_model: defModel,
      });
      setNote("默认设置已保存，新建员工时会自动带出来。");
    } catch (e) { setNote(`保存失败：${e instanceof Error ? e.message : String(e)}`); }
    finally { setSysBusy(false); }
  }
  async function openDataDir() {
    const dir = settings.data?.data_dir;
    if (!dir) return;
    if (window.agentflow?.openPath) {
      const r = await window.agentflow.openPath(dir);
      if (!r.ok) setNote(`没能打开目录：${r.error ?? "未知错误"}`);
    } else {
      setNote("在浏览器里预览时打不开本机目录，请在桌面应用里操作。");
    }
  }
  async function restore(file: File) {
    if (restoring) return;
    setRestoring(true);
    setNote("正在恢复备份…");
    try {
      const r = await api.restoreBackup(file);
      setNote(`${r.message}。请重启应用让恢复生效。`);
    } catch (e) { setNote(`恢复失败：${e instanceof Error ? e.message : String(e)}`); }
    finally { setRestoring(false); }
  }
  async function clearAll() {
    if (clearing) return;
    setClearing(true);
    try {
      const r = await api.clearData();
      const total = Object.values(r.cleared).reduce((a, b) => a + b, 0);
      setNote(`已清除 ${total.toLocaleString("zh-CN")} 条业务数据，供应商与系统设置保留。`);
      setConfirmClear(false);
    } catch (e) { setNote(`清除失败：${e instanceof Error ? e.message : String(e)}`); }
    finally { setClearing(false); }
  }

  return (
    <div>
      {note && (
        <Card className="mb-3.5" style={{ marginTop: 14 }}>
          <CardContent className="p-4 text-sm">{note}</CardContent>
        </Card>
      )}

      {show("defaults") && (
        <section id="settings-section-defaults">
          <h3 className="section-title">默认设置</h3>
          <Card>
            <CardContent className="p-5">
              <div className="flex flex-col gap-3">
                <div className="flex flex-col gap-2">
                  <Label>新员工默认供应商</Label>
                  <select className="input" value={defProvider} onChange={(e) => { setDefProvider(e.target.value); setDefModel(""); }}>
                    <option value="">不预设</option>
                    {enabledProviders.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
                  </select>
                </div>
                <div className="flex flex-col gap-2">
                  <Label>新员工默认模型</Label>
                  <select className="input" value={defModel} onChange={(e) => setDefModel(e.target.value)}>
                    <option value="">不预设</option>
                    {(defProviderObj?.models ?? []).map((m) => <option key={m} value={m}>{m}</option>)}
                  </select>
                  <p className="text-sm text-muted-foreground">只能选这个供应商已经添加过的模型；先到上面的模型管理里添加。</p>
                </div>
                <div><Button size="sm" disabled={sysBusy} onClick={() => void saveDefaults()}>{sysBusy ? "保存中…" : "保存默认设置"}</Button></div>
              </div>
            </CardContent>
          </Card>
        </section>
      )}

      {show("proxy") && (
        <section id="settings-section-proxy">
          <h3 className="section-title">网络代理</h3>
          <Card>
            <CardContent className="p-5">
              <div className="flex flex-col gap-2.5">
                {([["system", "跟随系统代理"], ["custom", "使用下面的代理地址"], ["none", "不使用代理，直连"]] as const).map(([v, label]) => (
                  <label key={v} className="flex items-center gap-2 text-sm">
                    <input type="radio" name="proxy-mode" checked={proxyMode === v} onChange={() => setProxyMode(v)} />
                    {label}
                  </label>
                ))}
                {proxyMode === "custom" && (
                  <Input placeholder="如 http://127.0.0.1:7890 或 socks5://127.0.0.1:1080" value={proxyUrl} onChange={(e) => setProxyUrl(e.target.value)} />
                )}
                <p className="text-sm text-muted-foreground">这个设置只影响模型调用的网络请求，保存后下一次调用立即生效。</p>
                <div><Button size="sm" disabled={sysBusy} onClick={() => void saveProxy()}>{sysBusy ? "保存中…" : "保存代理设置"}</Button></div>
              </div>
            </CardContent>
          </Card>
        </section>
      )}

      {show("data") && (
        <section id="settings-section-data">
          <h3 className="section-title">数据管理</h3>
          <Card>
            <CardContent className="p-5">
              <div className="text-sm leading-8">
                <div><span className="text-muted-foreground">数据目录</span>　<span className="break-all font-mono">{settings.data?.data_dir ?? "读取中…"}</span></div>
                <div><span className="text-muted-foreground">数据库文件</span>　<span className="break-all font-mono">{settings.data?.db_file ?? "—"}</span></div>
              </div>
              <div className="mt-3 flex flex-wrap gap-2">
                <Button variant="secondary" size="sm" onClick={() => void openDataDir()}>打开数据目录</Button>
                <a className="btn secondary sm" href={api.backupUrl} download>导出备份</a>
                <label className="btn secondary sm" style={{ cursor: "pointer" }}>
                  从备份恢复
                  <input type="file" accept=".zip" style={{ display: "none" }}
                    onChange={(e) => { const f = e.target.files?.[0]; if (f) void restore(f); e.target.value = ""; }} />
                </label>
                {confirmClear
                  ? <Button size="sm" disabled={clearing} style={{ background: "var(--destructive)" }} onClick={() => void clearAll()}>{clearing ? "清除中…" : "确认清除全部业务数据"}</Button>
                  : <Button variant="secondary" size="sm" onClick={() => setConfirmClear(true)}>清除数据</Button>}
              </div>
              <p className="mt-2 text-sm" style={{ color: "var(--warning)" }}>注意：备份文件里包含模型供应商的 API Key，请像保管密码一样保管，不要随手转发。</p>
              <p className="text-sm text-muted-foreground">恢复备份会先校验文件完整性，再替换现有数据，并需要重启应用后才生效。清除数据会删掉任务、员工、团队等业务数据，供应商与系统设置会保留。</p>
            </CardContent>
          </Card>
        </section>
      )}

      {show("usage") && (
        <section id="settings-section-usage">
          <h3 className="section-title">用量统计</h3>
          <Card>
            <CardContent className="p-5">
              <p className="text-sm text-muted-foreground">{usage.data?.note ?? "正在读取…"}</p>
              {usage.data && (usage.data.by_agent ?? []).length === 0 && (usage.data.by_task ?? []).length === 0 && (usage.data.by_provider ?? []).length === 0 && (
                <p className="mt-2 text-sm text-muted-foreground">暂无数据。执行任务产生 Token 消耗后，用量会出现在这里。</p>
              )}
              <div className="grid grid-3" style={{ marginTop: 10, gap: 14 }}>
                <div>
                  <div className="text-sm font-medium" style={{ marginBottom: 6 }}>按员工</div>
                  {(usage.data?.by_agent ?? []).slice(0, 6).map((r) => (
                    <div key={r.agent_id} className="spread text-sm" style={{ padding: "3px 0" }}>
                      <span>{r.name}</span><span className="text-muted-foreground">{r.tokens.toLocaleString("zh-CN")} Token · {r.calls.toLocaleString("zh-CN")} 次</span>
                    </div>
                  ))}
                  {(usage.data?.by_agent ?? []).length === 0 && <div className="text-sm text-muted-foreground">暂无</div>}
                </div>
                <div>
                  <div className="text-sm font-medium" style={{ marginBottom: 6 }}>按任务</div>
                  {(usage.data?.by_task ?? []).slice(0, 6).map((r) => (
                    <div key={r.task_id} className="spread text-sm" style={{ padding: "3px 0" }}>
                      <span className="truncate" style={{ maxWidth: 130 }}>{r.title}</span><span className="text-muted-foreground">{r.tokens.toLocaleString("zh-CN")} Token · {r.calls.toLocaleString("zh-CN")} 次</span>
                    </div>
                  ))}
                  {(usage.data?.by_task ?? []).length === 0 && <div className="text-sm text-muted-foreground">暂无</div>}
                </div>
                <div>
                  <div className="text-sm font-medium" style={{ marginBottom: 6 }}>按供应商</div>
                  {(usage.data?.by_provider ?? []).slice(0, 6).map((r) => (
                    <div key={r.provider} className="spread text-sm" style={{ padding: "3px 0" }}>
                      <span>{r.provider}</span><span className="text-muted-foreground">{r.tokens.toLocaleString("zh-CN")} Token · {r.calls.toLocaleString("zh-CN")} 次</span>
                    </div>
                  ))}
                  {(usage.data?.by_provider ?? []).length === 0 && <div className="text-sm text-muted-foreground">暂无</div>}
                </div>
              </div>
            </CardContent>
          </Card>
        </section>
      )}

      {show("notify") && (
        <section id="settings-section-notify">
          <h3 className="section-title">任务通知</h3>
          <Card>
            <CardContent className="flex items-center justify-between p-5">
              <div>
                <div className="text-sm font-medium">任务完成或失败时弹系统通知</div>
                <div className="text-sm text-muted-foreground">在桌面应用里运行时生效，切到别的窗口也能第一时间知道结果。</div>
              </div>
              <Switch checked={notify} aria-label="任务通知"
                onCheckedChange={(v) => { setNotify(v); localStorage.setItem("af_notify", v ? "1" : "0"); }} />
            </CardContent>
          </Card>
        </section>
      )}
    </div>
  );
}

export default function Settings() {
  const providers = useAsync(() => api.providers());
  const health = useAsync(() => api.health());
  const [msg, setMsg] = useState<string | null>(null);

  /* ---------- 设置项搜索 ---------- */
  const [query, setQuery] = useState("");
  const [highlightKey, setHighlightKey] = useState<string | null>(null);
  const targetRefs = useRef<Record<string, HTMLElement | null>>({});
  const highlightTimer = useRef<number | null>(null);
  const q = query.trim().toLowerCase();
  const hit = (...texts: string[]) => !q || texts.some((t) => t.toLowerCase().includes(q));
  const providerList = providers.data ?? [];
  const providersTitleHit = hit("模型供应商");
  const shownProviders = !q || providersTitleHit
    ? providerList
    : providerList.filter((p) => hit(p.name, p.base_url));
  const aboutHit = hit(ABOUT_KEYWORDS, (providers.data ?? []).map((p) => p.name).join(" "));
  const addHit = hit("添加供应商");
  const searchItems: Array<{ key: string; label: string; hint: string }> = [];
  if (q) {
    if (providersTitleHit || shownProviders.length > 0) {
      searchItems.push({ key: "section-providers", label: "模型供应商", hint: "设置区块" });
    }
    for (const p of providerList) {
      if (hit(p.name, p.base_url)) searchItems.push({ key: `provider-${p.id}`, label: p.name, hint: p.base_url });
    }
    if (addHit) searchItems.push({ key: "add-provider", label: "添加供应商", hint: "设置区块" });
    for (const [key, aliases] of Object.entries(SECTION_ALIASES)) {
      if (aliases.toLowerCase().includes(q)) {
        searchItems.push({ key: `section-${key}`, label: { defaults: "默认设置", proxy: "网络代理", data: "数据管理", usage: "用量统计", notify: "任务通知" }[key] ?? key, hint: "设置区块" });
      }
    }
    if (aboutHit) searchItems.push({ key: "section-about", label: "关于与系统信息", hint: "设置区块" });
  }
  const [searchIdx, setSearchIdx] = useState(0);
  const showProviders = !q || providersTitleHit || shownProviders.length > 0;
  const showAbout = !q || aboutHit;
  const noMatch = q.length > 0 && searchItems.length === 0;

  function jumpTo(key: string) {
    const el = targetRefs.current[key] ?? document.getElementById(`settings-${key}`);
    if (!el) return;
    el.scrollIntoView({ behavior: "smooth", block: "center" });
    setHighlightKey(key);
    if (highlightTimer.current) window.clearTimeout(highlightTimer.current);
    highlightTimer.current = window.setTimeout(() => setHighlightKey(null), 1600);
  }
  const hl = (key: string) => (highlightKey === key ? "ring-2 ring-info ring-offset-2" : "");
  const setRef = (key: string) => (el: HTMLElement | null) => { targetRefs.current[key] = el; };

  /* ---------- 供应商四段式表单 ---------- */
  const [wizardOpen, setWizardOpen] = useState(false);
  const [step, setStep] = useState(1);
  const [wFormat, setWFormat] = useState<FormatKey>("chat_completions");
  const [vendor, setVendor] = useState("deepseek");
  const [wName, setWName] = useState("DeepSeek");
  const [wUrl, setWUrl] = useState("https://api.deepseek.com");
  const [wKey, setWKey] = useState("");
  const [created, setCreated] = useState<Provider | null>(null);
  const [models, setModels] = useState<string[]>([]);
  const [picked, setPicked] = useState<string[]>([]);
  const [manualModel, setManualModel] = useState("");
  const [modelsLoading, setModelsLoading] = useState(false);
  const [modelsError, setModelsError] = useState<string | null>(null);
  const [testMsg, setTestMsg] = useState<string | null>(null);
  const [wizardTesting, setWizardTesting] = useState(false);
  const [busy, setBusy] = useState(false);
  const [stepError, setStepError] = useState<string | null>(null);
  const [wEnabled, setWEnabled] = useState(true);

  function openWizard() {
    setStep(1); setWFormat("chat_completions"); setVendor("deepseek");
    setWName("DeepSeek"); setWUrl("https://api.deepseek.com"); setWKey("");
    setCreated(null); setModels([]); setPicked([]); setManualModel(""); setModelsError(null); setTestMsg(null);
    setStepError(null); setWEnabled(true); setBusy(false); setModelsLoading(false);
    setWizardOpen(true);
  }
  function closeWizard(open: boolean) {
    setWizardOpen(open);
    if (!open && created) void providers.reload();
  }
  function applyVendor(fmt: FormatKey, key: string) {
    setVendor(key);
    const p = VENDOR_PRESETS[fmt].find((x) => x.key === key);
    if (p) { setWName(p.name); setWUrl(p.base_url); }
  }
  function selectFormat(fmt: FormatKey) {
    setWFormat(fmt);
    applyVendor(fmt, VENDOR_PRESETS[fmt][0].key);
  }
  async function loadModels(providerId: number) {
    setModelsLoading(true); setModelsError(null);
    try {
      const r = await api.fetchModels(providerId);
      setModels(r.models);
    } catch (e) {
      setModels([]);
      setModelsError(`获取模型列表失败：${e instanceof Error ? e.message : String(e)}`);
    } finally { setModelsLoading(false); }
  }
  async function saveAndFetch() {
    if (!wName.trim() || !wUrl.trim() || busy) return;
    setBusy(true); setStepError(null); setTestMsg(null);
    try {
      let p = created;
      if (!p) {
        p = await api.createProvider({ name: wName, base_url: wUrl, api_key: wKey, format: wFormat, enabled: true });
        setCreated(p); setWEnabled(p.enabled);
      } else {
        await api.updateProvider(p.id, { name: wName, base_url: wUrl, api_key: wKey, format: wFormat, enabled: wEnabled });
        p = { ...p, name: wName, base_url: wUrl, format: wFormat };
        setCreated(p);
      }
      setStep(3);
      await loadModels(p.id);
    } catch (e) {
      setStepError(`保存失败：${e instanceof Error ? e.message : String(e)}`);
    } finally { setBusy(false); }
  }
  async function testInWizard() {
    if (!created || wizardTesting) return;
    const model = picked[0] ?? models[0];
    if (!model) { setTestMsg("先添加一个模型，再做检测。"); return; }
    // 检测会发一次真实模型请求：不加守卫连点会重复扣费。
    setWizardTesting(true);
    setTestMsg("正在发一次真实请求验证…");
    try {
      const r = await api.detectProvider(created.id, model);
      setTestMsg(r.ok ? `检测通过：${r.model} 回复正常，用时 ${r.latency_ms} 毫秒。` : `检测没通过：${r.error ?? "未知错误"}`);
    } catch (e) { setTestMsg(`检测没通过：${e instanceof Error ? e.message : String(e)}`); }
    finally { setWizardTesting(false); }
  }
  async function persistPicked(): Promise<boolean> {
    // 之前吞掉失败还照常推进向导：用户以为模型已保存，实际没存上。
    // 现在失败就返回 false、留在当前步并提示原因。
    if (!created) return true;
    try {
      await api.providerModels(created.id, picked);
      return true;
    } catch (e) {
      setStepError(`保存模型失败：${e instanceof Error ? e.message : String(e)}，可以稍后再试。`);
      return false;
    }
  }
  async function toggleWizardEnabled(v: boolean) {
    if (!created) { setWEnabled(v); return; }
    const prev = wEnabled;
    setWEnabled(v);
    try {
      await api.updateProvider(created.id, { name: created.name, base_url: created.base_url, format: created.format, enabled: v });
    } catch (e) {
      setWEnabled(prev);
      setStepError(`切换启用状态失败：${e instanceof Error ? e.message : String(e)}`);
    }
  }
  function finishWizard() {
    setWizardOpen(false);
    void providers.reload();
  }

  /* ---------- 已有供应商 ---------- */
  const [modelsFor, setModelsFor] = useState<number | null>(null);
  const [checkingId, setCheckingId] = useState<number | null>(null);
  const [togglingId, setTogglingId] = useState<number | null>(null);
  async function test(id: number) {
    if (checkingId !== null) return;
    setCheckingId(id);
    setMsg("正在连接供应商…");
    try {
      const r = await api.testProvider(id);
      setMsg(r.ok ? `连接成功，这个供应商提供 ${r.model_count} 个模型。` : `连接失败：${r.error}`);
    } finally { setCheckingId(null); }
  }
  async function fetchModels(id: number) {
    if (checkingId !== null) return;
    setCheckingId(id);
    setMsg("正在获取模型列表…");
    try {
      const r = await api.fetchModels(id);
      setMsg(`获取成功，共 ${r.models.length} 个：${r.models.slice(0, 12).join("、")}${r.models.length > 12 ? " …" : ""}`);
    } catch (e) { setMsg(`获取失败：${e instanceof Error ? e.message : String(e)}`); }
    finally { setCheckingId(null); }
  }
  async function toggleProvider(p: Provider, v: boolean) {
    if (togglingId !== null) return;
    setTogglingId(p.id);
    try {
      await api.updateProvider(p.id, { name: p.name, base_url: p.base_url, format: p.format, enabled: v });
      void providers.reload();
    } catch (e) { setMsg(`切换启用状态失败：${e instanceof Error ? e.message : String(e)}`); }
    finally { setTogglingId(null); }
  }

  return (
    <div>
      <div className="spread">
        <div>
          <h1 className="page-title">设置</h1>
          <p className="page-desc">模型供应商、默认模型、网络代理、数据备份、用量统计和任务通知，都在这里管理。</p>
        </div>
        <div ref={setRef("add-provider")} className={cn("rounded-lg", hl("add-provider"))}>
          <Button onClick={openWizard}>添加供应商</Button>
        </div>
      </div>

      <div className="relative mb-3.5">
        <Input
          className="pr-8"
          placeholder="搜索设置项，如供应商名称、代理、备份…"
          value={query}
          onChange={(e) => { setQuery(e.target.value); setSearchIdx(0); }}
          onKeyDown={(e) => {
            if (e.key === "ArrowDown" && searchItems.length > 0) { e.preventDefault(); setSearchIdx((i) => (i + 1) % searchItems.length); }
            else if (e.key === "ArrowUp" && searchItems.length > 0) { e.preventDefault(); setSearchIdx((i) => (i - 1 + searchItems.length) % searchItems.length); }
            else if (e.key === "Enter" && searchItems.length > 0) jumpTo(searchItems[Math.min(searchIdx, searchItems.length - 1)].key);
          }}
          aria-label="搜索设置项"
        />
        {query && (
          <button type="button" aria-label="清空搜索" onClick={() => { setQuery(""); setSearchIdx(0); }}
            className="absolute right-2 top-1/2 -translate-y-1/2 cursor-pointer border-none bg-transparent px-1.5 py-0.5 text-base leading-none text-muted-foreground">×</button>
        )}
        {q && searchItems.length > 0 && (
          <Card className="absolute inset-x-0 top-full z-20 mt-1.5 shadow-lg">
            <CardContent className="p-1.5">
              {searchItems.map((item, i) => (
                <button
                  key={item.key}
                  type="button"
                  className={cn("flex w-full items-center justify-between gap-3 rounded-md px-2.5 py-2 text-left text-sm hover:bg-hover", i === searchIdx && "bg-hover")}
                  onMouseEnter={() => setSearchIdx(i)}
                  onClick={() => jumpTo(item.key)}
                >
                  <span className="font-medium">{item.label}</span>
                  <span className="truncate text-muted-foreground">{item.hint}</span>
                </button>
              ))}
            </CardContent>
          </Card>
        )}
        {noMatch && <p className="mt-2 text-sm text-muted-foreground">没有找到相关设置项</p>}
      </div>

      {msg && (
        <Card className="mb-3.5">
          <CardContent className="break-words p-4 text-sm">{msg}</CardContent>
        </Card>
      )}

      {showProviders && !noMatch && (
        <section ref={setRef("section-providers")} className={cn("rounded-xl", hl("section-providers"))}>
          <h3 className="section-title" style={{ marginTop: 4 }}>模型供应商</h3>
          <StateBox loading={providers.loading} error={providers.error} empty={shownProviders.length === 0}
            emptyText={q ? "没有匹配的供应商" : "还没有模型供应商"}
            emptyHint={q ? undefined : "添加模型供应商后，员工才能选择模型开始执行任务。"}
            emptyActionLabel={q ? undefined : "添加第一个供应商"} onEmptyAction={openWizard} onRetry={() => void providers.reload()}>
            <div className="grid grid-2">{shownProviders.map((p) => (
              <Card key={p.id} ref={setRef(`provider-${p.id}`)} className={cn(hl(`provider-${p.id}`))}>
                <CardHeader className="p-5 pb-0">
                  <div className="flex items-start justify-between gap-2">
                    <CardTitle className="text-base">{p.name}</CardTitle>
                    <div className="flex shrink-0 items-center gap-1.5">
                      {p.enabled
                        ? <Badge variant="secondary" className="border-transparent bg-success-tint text-success hover:bg-success-tint">已启用</Badge>
                        : <Badge variant="secondary" className="border-transparent bg-warning-tint text-warning hover:bg-warning-tint">已禁用</Badge>}
                      <MoreMenu items={[
                        { label: "删除供应商", danger: true, confirmText: `删除供应商「${p.name}」？使用它的员工将无法执行任务。`, onClick: () => void api.deleteProvider(p.id).then(() => providers.reload()).catch((e) => showToast("error", `删除供应商失败：${e instanceof Error ? e.message : String(e)}，可以稍后再试。`)) },
                      ]} />
                    </div>
                  </div>
                </CardHeader>
                <CardContent className="p-5 pt-3">
                  <div className="mb-1.5">
                    <Badge variant="secondary" className={cn("border-transparent", FORMAT_TONE[p.format] ?? "")}>{FORMAT_LABEL[p.format] ?? p.format}</Badge>
                  </div>
                  <p className="break-all font-mono text-sm text-muted-foreground">{p.base_url}</p>
                  <p className="mt-1.5 text-sm">API Key：{p.has_api_key ? "已保存，不显示" : "未设置"}</p>
                  <div className="mt-2.5 flex flex-wrap items-center gap-2">
                    <Button variant="secondary" size="sm" disabled={checkingId === p.id} onClick={() => void test(p.id)}>{checkingId === p.id ? "测试中…" : "测试连接"}</Button>
                    <Button variant="secondary" size="sm" disabled={checkingId === p.id} onClick={() => void fetchModels(p.id)}>{checkingId === p.id ? "获取中…" : "获取模型列表"}</Button>
                    <Button variant="secondary" size="sm" onClick={() => setModelsFor(modelsFor === p.id ? null : p.id)}>
                      {modelsFor === p.id ? "收起模型管理" : `模型管理${(p.models?.length ?? 0) > 0 ? ` · 已添加 ${p.models!.length} 个` : ""}`}
                    </Button>
                    <span className="ml-auto flex items-center gap-2 text-sm">
                      <span className="text-muted-foreground">{p.enabled ? "已启用" : "已禁用"}</span>
                      <Switch checked={p.enabled} disabled={togglingId === p.id} onCheckedChange={(v) => void toggleProvider(p, v)} aria-label={`启用 ${p.name}`} />
                    </span>
                  </div>
                  {modelsFor === p.id && <ProviderModelsPanel key={`${p.id}-${(p.models ?? []).join(",")}`} p={p} onChanged={() => void providers.reload()} />}
                </CardContent>
              </Card>))}
            </div>
          </StateBox>
        </section>
      )}

      <SystemSections providers={providerList} query={q} />

      {showAbout && !noMatch && (
        <Card ref={setRef("section-about")} className={cn("mt-3.5", hl("section-about"))}>
          <CardHeader className="p-5 pb-0">
            <CardTitle className="text-base">关于与系统信息</CardTitle>
          </CardHeader>
          <CardContent className="p-5 pt-3">
            <div className="text-sm leading-8">
              <div><span className="text-muted-foreground">产品</span>　AgentFlow OS · 多智能体桌面工作空间</div>
              <div><span className="text-muted-foreground">版本</span>　{health.data?.version ?? "读取中…"}</div>
              <div><span className="text-muted-foreground">后端服务</span>　{health.error ? "未连接" : health.data ? `已连接 · ${health.data.service ?? "agentflow-agent-core"}` : "连接中…"}</div>
              <div><span className="text-muted-foreground">数据目录</span>　<span className="break-all font-mono">{health.data?.data_dir ?? "—"}</span></div>
              <div><span className="text-muted-foreground">模型供应商</span>　{(providers.data ?? []).length === 0 ? "未配置" : (providers.data ?? []).map((p) => `${p.name} · ${p.enabled ? "已启用" : "已禁用"}`).join("、")}</div>
            </div>
            <p className="mt-1.5 text-sm text-muted-foreground">让 AI 员工像一个团队一样协作的桌面工作空间。</p>
          </CardContent>
        </Card>
      )}

      <Dialog open={wizardOpen} onOpenChange={closeWizard}>
        <DialogContent className="max-w-xl">
          <DialogHeader>
            <DialogTitle>添加供应商</DialogTitle>
            <DialogDescription>按四步完成：选择接口格式、填写地址与密钥、获取真实模型列表、确认启用。</DialogDescription>
          </DialogHeader>

          <div className="flex items-center gap-1.5" aria-label="添加步骤">
            {STEP_LABELS.map((label, i) => {
              const n = i + 1;
              const state = n < step ? "done" : n === step ? "active" : "todo";
              return (
                <div key={label} className="flex flex-1 items-center gap-1.5">
                  <span className={cn(
                    "flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-xs font-semibold",
                    state === "todo" ? "bg-muted text-muted-foreground" : "bg-primary text-primary-foreground",
                  )}>{state === "done" ? "✓" : n}</span>
                  <span className={cn("whitespace-nowrap text-xs", state === "todo" ? "text-muted-foreground" : "font-medium")}>{label}</span>
                  {n < STEP_LABELS.length && <span className="h-px flex-1 bg-border" />}
                </div>
              );
            })}
          </div>

          {stepError && <p className="text-sm text-destructive">{stepError}</p>}

          {step === 1 && (
            <div className="flex flex-col gap-3">
              <div className="flex flex-col gap-2">
                {FORMATS.map((f) => (
                  <button
                    key={f.key}
                    type="button"
                    onClick={() => selectFormat(f.key)}
                    className={cn(
                      "rounded-lg border border-border bg-background px-4 py-3 text-left transition-colors hover:bg-hover",
                      wFormat === f.key && "border-info bg-info-tint/40 ring-1 ring-info",
                    )}
                  >
                    <span className="flex flex-wrap items-baseline gap-2">
                      <span className="text-sm font-semibold">{f.label}</span>
                      <span className="font-mono text-xs text-muted-foreground">{f.endpoint}</span>
                    </span>
                    <span className="mt-0.5 block text-sm text-muted-foreground">{f.desc}</span>
                  </button>
                ))}
              </div>
              <div>
                <div className="mb-1.5 text-sm text-muted-foreground">常用厂商快捷填充，选完还能改</div>
                <div className="flex flex-wrap gap-1.5">
                  {VENDOR_PRESETS[wFormat].map((v) => (
                    <button
                      key={v.key}
                      type="button"
                      onClick={() => applyVendor(wFormat, v.key)}
                      className={cn(
                        "rounded-full border border-border bg-background px-3 py-1 text-sm transition-colors hover:bg-hover",
                        vendor === v.key && "border-info bg-info-tint font-medium text-info",
                      )}
                    >{v.label}</button>
                  ))}
                </div>
              </div>
            </div>
          )}

          {step === 2 && (
            <div className="flex flex-col gap-3">
              <div className="flex flex-col gap-2">
                <Label htmlFor="wizard-name">供应商名称</Label>
                <Input id="wizard-name" value={wName} onChange={(e) => setWName(e.target.value)} />
              </div>
              <div className="flex flex-col gap-2">
                <Label htmlFor="wizard-url">服务地址</Label>
                <Input id="wizard-url" placeholder="如 https://api.openai.com/v1" value={wUrl} onChange={(e) => setWUrl(e.target.value)} />
                <p className="text-sm text-muted-foreground">{FORMAT_URL_HINT[wFormat]}</p>
              </div>
              <div className="flex flex-col gap-2">
                <Label htmlFor="wizard-key">API Key</Label>
                <Input id="wizard-key" type="password" value={wKey} onChange={(e) => setWKey(e.target.value)} />
              </div>
            </div>
          )}

          {step === 3 && (
            <div className="flex flex-col gap-3">
              {modelsLoading && <p className="text-sm text-muted-foreground">正在获取模型列表…</p>}
              {!modelsLoading && modelsError && (
                <div>
                  <p className="text-sm text-destructive">{modelsError}</p>
                  <p className="mt-1 text-sm text-muted-foreground">可以返回上一步修改地址或密钥后重试，也可以重新获取。有些上游不提供模型列表接口，这种情况可以先完成添加，再到 AI 员工页手动填写模型名。</p>
                </div>
              )}
              {!modelsLoading && !modelsError && (
                <div>
                  <p className="text-sm">获取成功，共 {models.length} 个模型。点一下把要用到的加进清单，只有加进清单的模型才会出现在员工绑定里。</p>
                  <ScrollArea className="mt-2 h-40 rounded-lg border border-border p-3">
                    <div className="flex flex-wrap gap-1.5">
                      {models.map((m) => (
                        <button key={m} type="button"
                          className={cn("rounded-full border px-3 py-1 font-mono text-sm transition-colors",
                            picked.includes(m) ? "border-info bg-info-tint font-medium text-info" : "border-border bg-background hover:bg-hover")}
                          onClick={() => setPicked((cur) => (cur.includes(m) ? cur.filter((x) => x !== m) : [...cur, m]))}>
                          {picked.includes(m) ? "✓ " : "+ "}{m}
                        </button>
                      ))}
                      {models.length === 0 && <span className="text-sm text-muted-foreground">这个供应商暂时没有返回可用模型，可以在下面手动输入模型编号添加。</span>}
                    </div>
                  </ScrollArea>
                  <div className="mt-2 flex flex-wrap items-center gap-2">
                    <Input className="h-8 w-56" placeholder="手动输入模型编号" value={manualModel} onChange={(e) => setManualModel(e.target.value)} />
                    <Button variant="secondary" size="sm" disabled={!manualModel.trim()}
                      onClick={() => { setPicked((cur) => [...new Set([...cur, manualModel.trim()])]); setManualModel(""); }}>添加</Button>
                    <span className="text-sm text-muted-foreground">已添加 {picked.length} 个</span>
                  </div>
                </div>
              )}
              <div className="flex flex-wrap items-center gap-2">
                <Button variant="secondary" size="sm" disabled={!created || wizardTesting} onClick={() => void testInWizard()}>{wizardTesting ? "检测中…" : "检测"}</Button>
                {created && (
                  <Button variant="secondary" size="sm" disabled={modelsLoading} onClick={() => void loadModels(created.id)}>重新获取</Button>
                )}
                {testMsg && <span className="text-sm text-muted-foreground">{testMsg}</span>}
              </div>
            </div>
          )}

          {step === 4 && created && (
            <div className="flex flex-col gap-3">
              <Card>
                <CardContent className="p-4 text-sm leading-7">
                  <div><span className="text-muted-foreground">供应商</span>　{created.name}</div>
                  <div><span className="text-muted-foreground">接口格式</span>　{FORMAT_LABEL[created.format] ?? created.format}</div>
                  <div><span className="text-muted-foreground">服务地址</span>　<span className="break-all font-mono">{created.base_url}</span></div>
                  <div><span className="text-muted-foreground">已添加模型</span>　{picked.length} 个{picked.length > 0 ? `：${picked.join("、")}` : ""}</div>
                  <div><span className="text-muted-foreground">API Key</span>　{created.has_api_key || wKey ? "已保存，不显示" : "未设置"}</div>
                </CardContent>
              </Card>
              <div className="flex items-center justify-between">
                <div>
                  <div className="text-sm font-medium">启用这个供应商</div>
                  <div className="text-sm text-muted-foreground">启用后，员工就能选择它的模型开始工作。</div>
                </div>
                <Switch checked={wEnabled} onCheckedChange={(v) => void toggleWizardEnabled(v)} aria-label="启用这个供应商" />
              </div>
            </div>
          )}

          <DialogFooter>
            {step === 1 && (
              <>
                <Button variant="secondary" onClick={() => closeWizard(false)}>取消</Button>
                <Button onClick={() => { setStepError(null); setStep(2); }}>下一步</Button>
              </>
            )}
            {step === 2 && (
              <>
                <Button variant="secondary" disabled={busy} onClick={() => setStep(1)}>上一步</Button>
                <Button disabled={!wName.trim() || !wUrl.trim() || busy} onClick={() => void saveAndFetch()}>
                  {busy ? "正在保存…" : "保存并获取模型"}
                </Button>
              </>
            )}
            {step === 3 && (
              <>
                <Button variant="secondary" onClick={() => setStep(2)}>返回上一步修改</Button>
                <Button disabled={modelsLoading} onClick={() => { setStepError(null); void persistPicked().then((ok) => { if (ok) setStep(4); }); }}>下一步</Button>
              </>
            )}
            {step === 4 && (
              <>
                <Button variant="secondary" onClick={() => setStep(3)}>上一步</Button>
                <Button onClick={() => { setStepError(null); void persistPicked().then((ok) => { if (ok) finishWizard(); }); }}>完成</Button>
              </>
            )}
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
