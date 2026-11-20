import { useState } from "react";
import { api } from "../services/api";
import { StateBox, toolLabel, useAsync } from "../components/ui";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { cn } from "@/lib/utils";

/** Browser configuration for the shared desktop: one real Chrome
 * window that employees and the user share. Settings here steer how
 * the backend launches it (homepage, search engine, download folder,
 * profile persistence) — saving takes effect on the next launch. */
export function BrowserCard() {
  const status = useAsync(() => api.browserStatus());
  const engines = useAsync(() => api.browserEngines());
  const [form, setForm] = useState<{ homepage: string; search_engine: string; download_dir: string; persist_profile: boolean; profile_per_agent: boolean } | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [confirmClear, setConfirmClear] = useState(false);
  const [opening, setOpening] = useState(false);
  const [saving, setSaving] = useState(false);
  const [clearing, setClearing] = useState(false);
  const cfg = status.data?.config;
  const shown = form ?? (cfg ? { homepage: cfg.homepage, search_engine: cfg.search_engine, download_dir: cfg.download_dir, persist_profile: cfg.persist_profile, profile_per_agent: cfg.profile_per_agent } : null);

  async function save() {
    if (!shown || saving) return;
    setSaving(true);
    try {
      await api.browserUpdateConfig(shown);
      setNote("已保存，下次打开浏览器时生效。");
      void status.reload();
    } catch (e) { setNote(`保存失败：${e instanceof Error ? e.message : String(e)}`); }
    finally { setSaving(false); }
  }
  async function openHome() {
    if (!shown || opening) return;
    setOpening(true);
    try {
      const r = await api.browserOpen(shown.homepage);
      setNote(`已在共享桌面上打开：${r.title || r.url}。切到共享桌面页签就能亲眼看到。`);
      void status.reload();
    } catch (e) { setNote(`没打开：${e instanceof Error ? e.message : String(e)}`); }
    finally { setOpening(false); }
  }
  async function clearData() {
    if (clearing) return;
    setClearing(true);
    try {
      const r = await api.browserClearData();
      setNote(`已清除 ${r.cleared_profiles} 份浏览器资料，历史、Cookie 与登录状态都清掉了。`);
      setConfirmClear(false);
      void status.reload();
    } catch (e) { setNote(`清除失败：${e instanceof Error ? e.message : String(e)}`); }
    finally { setClearing(false); }
  }

  return (
    <Card className="mb-[14px] p-4">
      <div className="spread">
        <div style={{ fontWeight: 600 }}>浏览器</div>
        {status.data && (
          <Badge variant="secondary" className={status.data.installed ? "border-transparent bg-success-tint text-success hover:bg-success-tint" : "border-transparent bg-warning-tint text-warning hover:bg-warning-tint"}>
            {status.data.installed ? "已安装 Chrome" : "还没安装浏览器"}
          </Badge>
        )}
      </div>
      <div className="small muted" style={{ marginTop: 4, lineHeight: 1.65 }}>
        员工和你共用共享桌面上的同一个浏览器窗口，谁在操作都能在远程桌面里亲眼看到。员工通过「浏览器打开网址、浏览器搜索、读取浏览器页面、浏览器截图」四个工具使用它，操作前要先拿到桌面控制权，全程有审计记录。
      </div>
      <StateBox loading={status.loading} error={status.error} onRetry={() => void status.reload()}>
        {status.data && shown && (
          <div style={{ marginTop: 10 }}>
            <div className="small" style={{ lineHeight: 1.9 }}>
              <div>桌面状态：{status.data.desktop_running ? "运行中" : "未启动，打开浏览器时会自动启动"}</div>
              <div>浏览器状态：{status.data.running ? "已打开" : "未打开"}{status.data.page?.title ? ` · 当前页面 ${status.data.page.title}` : ""}</div>
              <div className="break-all">资料目录：<span className="mono">{status.data.profile_dir}</span></div>
            </div>
            <div className="grid grid-2" style={{ marginTop: 10, gap: 12 }}>
              <label className="small" style={{ display: "block" }}>
                <span className="muted">主页</span>
                <Input style={{ marginTop: 4 }} value={shown.homepage} onChange={(e) => setForm({ ...shown, homepage: e.target.value })} />
              </label>
              <label className="small" style={{ display: "block" }}>
                <span className="muted">默认搜索引擎</span>
                <select className="input" style={{ marginTop: 4 }} value={shown.search_engine} onChange={(e) => setForm({ ...shown, search_engine: e.target.value })}>
                  {(engines.data ?? []).map((en) => <option key={en.key} value={en.key}>{en.label}</option>)}
                  {(engines.data ?? []).length === 0 && <option value={shown.search_engine}>{shown.search_engine}</option>}
                </select>
                {engines.error && <div className="small" style={{ color: "var(--destructive)", marginTop: 4 }}>搜索引擎列表加载失败：{engines.error}，可以点「刷新状态」重试。</div>}
              </label>
              <label className="small" style={{ display: "block" }}>
                <span className="muted">下载目录，留空就是共享工作区</span>
                <Input style={{ marginTop: 4 }} value={shown.download_dir} onChange={(e) => setForm({ ...shown, download_dir: e.target.value })} />
              </label>
              <div className="small" style={{ display: "flex", flexDirection: "column", gap: 8, justifyContent: "center" }}>
                <span className="flex items-center gap-2">
                  <Switch checked={shown.persist_profile} onCheckedChange={(v) => setForm({ ...shown, persist_profile: v })} aria-label="保存登录状态与浏览记录" />
                  保存登录状态与浏览记录
                </span>
                <span className="flex items-center gap-2">
                  <Switch checked={shown.profile_per_agent} disabled={!shown.persist_profile} onCheckedChange={(v) => setForm({ ...shown, profile_per_agent: v })} aria-label="按员工分开保存" />
                  按员工分开保存，关掉就是全员共用一份
                </span>
              </div>
            </div>
            <div className="row" style={{ marginTop: 12 }}>
              <Button size="sm" disabled={saving} onClick={() => void save()}>{saving ? "保存中…" : "保存设置"}</Button>
              <Button size="sm" variant="secondary" disabled={opening} onClick={() => void openHome()}>{opening ? "打开中…" : "打开浏览器看一眼"}</Button>
              {confirmClear
                ? <Button size="sm" disabled={clearing} style={{ background: "var(--destructive)" }} onClick={() => void clearData()}>{clearing ? "清除中…" : "确认清除全部浏览数据"}</Button>
                : <Button size="sm" variant="secondary" onClick={() => setConfirmClear(true)}>清除浏览数据</Button>}
              <Button size="sm" variant="secondary" onClick={() => void status.reload()}>刷新状态</Button>
            </div>
            {note && <div className="small" style={{ marginTop: 8 }}>{note}</div>}
          </div>
        )}
      </StateBox>
    </Card>
  );
}

// Tool Registry content, rendered as the 工具与权限 tab of the AI 电脑
// page (the single computer entry) rather than a standalone page.
export function ToolsAndPermissions() {
  const tools = useAsync(() => api.tools());
  return (
    <div>
      <BrowserCard />
      <Card className="mb-[14px] p-4">
        <details className="fold">
          <summary style={{ fontWeight: 600, color: "var(--foreground)", fontSize: 13 }}>运行环境说明</summary>
          <div className="small" style={{ marginTop: 6, lineHeight: 1.65 }}>
            员工的命令与文件操作运行在受限环境中：文件工具只能访问工作区目录，越界访问会被拒绝，高风险工具需要人工审批后才执行。
            桌面控制已在共享桌面上可用，可用鼠标、键盘与截图。容器沙箱镜像尚未构建成功，开发机网络受限，当前不作为可用选项提供。
          </div>
        </details>
      </Card>
      <StateBox loading={tools.loading} error={tools.error} empty={(tools.data?.length ?? 0) === 0} onRetry={() => void tools.reload()}>
        <Card className="p-2">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>工具</TableHead>
                <TableHead>说明</TableHead>
                <TableHead>高风险</TableHead>
                <TableHead>需审批</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {(tools.data ?? []).map((t) => (
                <TableRow key={t.id}>
                  <TableCell>{toolLabel(t.id)}</TableCell>
                  <TableCell>{t.description}</TableCell>
                  <TableCell>{t.high_risk ? <Badge variant="outline" className={cn("rounded-full border-transparent bg-warning-tint text-warning hover:bg-warning-tint")}>是</Badge> : <Badge variant="secondary">否</Badge>}</TableCell>
                  <TableCell>{t.requires_approval ? <Badge variant="outline" className={cn("rounded-full border-transparent bg-warning-tint text-warning hover:bg-warning-tint")}>是</Badge> : <Badge variant="secondary">否</Badge>}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Card>
      </StateBox>
    </div>
  );
}

export default function Computer() {
  return (
    <div>
      <h1 className="page-title">AI 电脑 · 工具与权限</h1>
      <p className="page-desc">员工能使用的全部工具，以及每项工具的风险与审批要求。</p>
      <ToolsAndPermissions />
    </div>
  );
}
