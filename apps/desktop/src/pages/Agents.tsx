import { useMemo, useState } from "react";
import { api } from "../services/api";
import { AgentStatusDot, AnimatedModal, Avatar, MoreMenu, ScoreBar, StateBox, StatusBadge, activityLabel, capLabel, roleLabel, toolLabel, useAsync, useFreshIds } from "../components/ui";
import { AppearancePicker } from "../components/AgentAvatar";
import { DEFAULT_APPEARANCE, normalizeAppearance, type AgentAppearance } from "../lib/appearance";
import { showToast } from "../motion/Toast";
import { useAppStore } from "../stores/appStore";
import type { Agent, AgentDetail } from "../types";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";

const TOOL_OPTIONS = ["file_read", "file_write", "run_command", "run_pytest"];

interface DutiesShape { responsible_for: string[]; not_responsible_for: string[]; deliverables: string[] }
const EMPTY_DUTIES: DutiesShape = { responsible_for: [], not_responsible_for: [], deliverables: [] };

/* 存量数据里 responsibilities 可能是 {} 或缺子字段（总管自动建的员工
 * 就是空对象），?? 只能兜 null/undefined，兜不住缺失的子字段，直接读
 * responsible_for.length 会让整页白屏。渲染前一律补齐为完整三件套；
 * capability_tags / tools / capability_scores 等 JSON 字段同理可能为
 * null，凡 .length/.map/.join 处都先 ?? [] / ?? {}。 */
function normalizeDuties(d?: Partial<DutiesShape> | null): DutiesShape {
  return {
    responsible_for: d?.responsible_for ?? EMPTY_DUTIES.responsible_for,
    not_responsible_for: d?.not_responsible_for ?? EMPTY_DUTIES.not_responsible_for,
    deliverables: d?.deliverables ?? EMPTY_DUTIES.deliverables,
  };
}

/* 岗位模板：新建员工时带出角色、结构化职责、建议能力与工具、默认形象，
 * 全部可改；模板只提供起点，不写任何能力评分（评分来自真实执行）。 */
interface AgentTemplate {
  key: string; label: string; role: string; appearance: AgentAppearance;
  description: string; system_prompt: string;
  duties: DutiesShape; tags: string; tools: string[];
}
const TEMPLATES: AgentTemplate[] = [
  {
    key: "pm", label: "产品经理", role: "product_manager", appearance: { gender: "female", skin: 1, outfit: 1, hair: 1 },
    description: "负责需求分析、任务规划与验收标准制定",
    system_prompt: "先澄清需求与边界，再拆解任务；输出必须包含明确的验收标准。",
    duties: {
      responsible_for: ["需求分析与澄清", "任务拆解与排期", "验收标准制定", "调研与竞品分析"],
      not_responsible_for: ["代码实现", "测试执行", "环境与部署操作"],
      deliverables: ["需求文档", "任务规划清单", "验收标准"],
    },
    tags: "planning, research", tools: ["web_search"],
  },
  {
    key: "arch", label: "架构师", role: "architect", appearance: { gender: "male", skin: 1, outfit: 4, hair: 2 },
    description: "负责系统设计、技术选型与架构评审",
    system_prompt: "从整体结构出发做设计决策，说明取舍与风险，给出可落地的接口契约。",
    duties: {
      responsible_for: ["系统架构设计", "技术选型与评审", "接口与数据模型设计"],
      not_responsible_for: ["具体功能编码", "测试执行"],
      deliverables: ["架构设计文档", "技术选型说明", "接口契约"],
    },
    tags: "design, planning", tools: ["file_read"],
  },
  {
    key: "dev", label: "程序员", role: "developer", appearance: { gender: "male", skin: 2, outfit: 3, hair: 1 },
    description: "负责功能开发与代码实现",
    system_prompt: "按需求与接口契约实现功能，保持代码可运行、可测试，完成后说明变更点。",
    duties: {
      responsible_for: ["功能开发", "接口实现", "缺陷修复"],
      not_responsible_for: ["需求定义", "最终验收判定", "产品决策"],
      deliverables: ["可运行代码", "接口文档", "变更说明"],
    },
    tags: "coding, debugging", tools: ["file_read", "file_write", "run_command", "run_pytest"],
  },
  {
    key: "tester", label: "测试工程师", role: "tester", appearance: { gender: "female", skin: 2, outfit: 4, hair: 2 },
    description: "负责测试执行、代码审核与可信验证",
    system_prompt: "以验收标准为准绳执行测试与审核，只依据真实运行结果下结论。",
    duties: {
      responsible_for: ["测试执行", "代码审核", "可信验证与复验", "缺陷记录"],
      not_responsible_for: ["功能开发", "需求定义"],
      deliverables: ["测试结果", "验证记录", "缺陷清单"],
    },
    tags: "testing, review", tools: ["file_read", "run_pytest"],
  },
  {
    key: "writer", label: "文档工程师", role: "technical_writer", appearance: { gender: "female", skin: 1, outfit: 2, hair: 3 },
    description: "负责产品文档与使用说明编写",
    system_prompt: "面向读者写文档，结构清楚、用词准确，内容必须与实际功能一致。",
    duties: {
      responsible_for: ["产品文档编写", "使用说明与手册维护", "变更记录整理"],
      not_responsible_for: ["功能开发", "需求决策"],
      deliverables: ["产品文档", "使用手册", "变更记录"],
    },
    tags: "documentation, writing", tools: ["file_read", "file_write"],
  },
  {
    key: "analyst", label: "数据分析师", role: "data_analyst", appearance: { gender: "male", skin: 5, outfit: 7, hair: 4 },
    description: "负责数据整理、分析与结论输出",
    system_prompt: "先核对数据来源与口径，再做分析；结论必须标注依据，不确定的地方如实说明。",
    duties: {
      responsible_for: ["数据整理与清洗", "数据分析与可视化说明", "分析结论输出"],
      not_responsible_for: ["功能开发", "产品决策"],
      deliverables: ["分析报告", "数据说明"],
    },
    tags: "analysis, research", tools: ["file_read", "run_command"],
  },
];

function linesToList(text: string): string[] {
  return text.split("\n").map((x) => x.trim()).filter(Boolean);
}
function listToLines(list?: string[]): string {
  return (list ?? []).join("\n");
}
function fmtDuration(sec: number | null | undefined): string {
  if (sec == null) return "暂无数据";
  const s = Math.round(sec);
  if (s < 60) return `${s} 秒`;
  if (s < 3600) return `${Math.floor(s / 60)} 分 ${String(s % 60).padStart(2, "0")} 秒`;
  return `${Math.floor(s / 3600)} 小时 ${String(Math.floor((s % 3600) / 60)).padStart(2, "0")} 分`;
}
function fmtTime(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso.endsWith("Z") ? iso : iso + "Z");
  return Number.isNaN(d.getTime()) ? "" : d.toLocaleString("zh-CN", { hour12: false });
}

interface FormState {
  name: string; role: string; description: string; system_prompt: string; appearance: AgentAppearance;
  dutiesResp: string; dutiesNot: string; dutiesDeliv: string;
  model_provider_id: string; model_id: string; capability_tags: string;
  tools: string[]; skills: number[];
}
const BLANK_FORM: FormState = {
  name: "", role: "general", description: "", system_prompt: "", appearance: { ...DEFAULT_APPEARANCE },
  dutiesResp: "", dutiesNot: "", dutiesDeliv: "",
  model_provider_id: "", model_id: "", capability_tags: "general",
  tools: ["file_read"], skills: [],
};

export default function Agents() {
  const setPage = useAppStore((s) => s.setPage);
  const openTask = useAppStore((s) => s.openTask);
  const agents = useAsync(() => api.agents());
  const freshAgents = useFreshIds((agents.data ?? []).map((a) => a.id));
  const providers = useAsync(() => api.providers());
  const registry = useAsync(() => api.tools());
  const teams = useAsync(() => api.teams());
  const skillList = useAsync(() => api.skills());

  /* ---------- 工具权限（注册表全量） ---------- */
  const [toolsFor, setToolsFor] = useState<{ id: number; name: string; tools: string[] } | null>(null);
  const [toolSel, setToolSel] = useState<string[]>([]);
  const [savingTools, setSavingTools] = useState(false);
  const registryIds = (registry.data ?? []).map((t) => t.id);
  const legacyTools = toolsFor ? (toolsFor.tools ?? []).filter((t) => !registryIds.includes(t)) : [];
  function openTools(a: { id: number; name: string; tools: string[] }) {
    setToolsFor(a);
    setToolSel((a.tools ?? []).filter((t) => registryIds.includes(t)));
  }
  async function saveTools() {
    if (!toolsFor || savingTools) return;
    setSavingTools(true);
    try {
      await api.updateAgentTools(toolsFor.id, [...legacyTools, ...toolSel]);
      setToolsFor(null); void agents.reload();
      showToast("success", `员工「${toolsFor.name}」的工具权限已更新。`);
    } catch (e) { showToast("error", `保存失败：${e instanceof Error ? e.message : String(e)}`); }
    finally { setSavingTools(false); }
  }

  /* ---------- 筛选与搜索 ---------- */
  const [query, setQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");
  const [tagFilter, setTagFilter] = useState("all");
  const [teamFilter, setTeamFilter] = useState("all");
  const allTags = useMemo(
    () => [...new Set((agents.data ?? []).flatMap((a) => a.capability_tags ?? []))].sort(),
    [agents.data]);
  const teamMemberIds = useMemo(() => {
    if (teamFilter === "all") return null;
    const t = (teams.data ?? []).find((x) => x.id === Number(teamFilter));
    return new Set((t?.members ?? []).map((m) => m.agent_id));
  }, [teams.data, teamFilter]);
  const shownAgents = (agents.data ?? []).filter((a) => {
    const act = a.activity_status ?? "idle";
    if (statusFilter === "working" && !["running", "model_calling", "tool_calling"].includes(act)) return false;
    if (statusFilter !== "all" && statusFilter !== "working" && act !== statusFilter) return false;
    if (tagFilter !== "all" && !(a.capability_tags ?? []).includes(tagFilter)) return false;
    if (teamMemberIds && !teamMemberIds.has(a.id)) return false;
    const q = query.trim();
    if (q && !`${a.name} ${roleLabel(a.role)} ${a.description}`.includes(q)) return false;
    return true;
  });

  /* ---------- 创建 / 编辑 ---------- */
  const [showForm, setShowForm] = useState(false);
  const [editing, setEditing] = useState<Agent | null>(null);
  const [templateKey, setTemplateKey] = useState("");
  const [form, setForm] = useState<FormState>(BLANK_FORM);
  const [models, setModels] = useState<string[]>([]);
  const [modelError, setModelError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // Only models the user curated in Settings （模型管理） are offered
  // here — the pickers never show a provider's raw fetched list.
  function loadModels(providerId: string) {
    setModels([]); setModelError(null);
    if (!providerId) return;
    const p = (providers.data ?? []).find((x) => x.id === Number(providerId));
    const curated = p?.models ?? [];
    setModels(curated);
    if (curated.length === 0) {
      setModelError("这个供应商还没有添加模型，先去设置页的「模型管理」里添加。");
    }
  }

  const defaults = useAsync(() => api.systemSettings());
  function openCreate() {
    setEditing(null); setTemplateKey("");
    const f: FormState = { ...BLANK_FORM };
    const d = defaults.data;
    if (d?.default_provider_id) {
      f.model_provider_id = String(d.default_provider_id);
      f.model_id = d.default_model || "";
      loadModels(f.model_provider_id);
      if (d.default_model) {
        setModels((cur) => (cur.includes(d.default_model) ? cur : [...cur, d.default_model]));
      }
    }
    setForm(f);
    setShowForm(true);
  }
  function applyTemplate(key: string) {
    setTemplateKey(key);
    const t = TEMPLATES.find((x) => x.key === key);
    if (!t) return;
    setForm((f) => ({
      ...f,
      role: t.role, description: t.description, system_prompt: t.system_prompt,
      appearance: { ...t.appearance }, capability_tags: t.tags.replace(/,\s*/g, ", "),
      tools: [...t.tools],
      dutiesResp: listToLines(t.duties.responsible_for),
      dutiesNot: listToLines(t.duties.not_responsible_for),
      dutiesDeliv: listToLines(t.duties.deliverables),
    }));
  }
  function openEdit(a: Agent) {
    setEditing(a); setTemplateKey("");
    setForm({
      name: a.name, role: a.role, description: a.description, system_prompt: a.system_prompt,
      appearance: normalizeAppearance(a.appearance) ?? { ...DEFAULT_APPEARANCE },
      dutiesResp: listToLines(a.responsibilities?.responsible_for),
      dutiesNot: listToLines(a.responsibilities?.not_responsible_for),
      dutiesDeliv: listToLines(a.responsibilities?.deliverables),
      model_provider_id: a.model_provider_id ? String(a.model_provider_id) : "",
      model_id: a.model_id || "",
      capability_tags: (a.capability_tags ?? []).join(", "),
      tools: [...(a.tools ?? [])],
      skills: (a.skills ?? []).map((s) => Number(s)).filter((n) => Number.isFinite(n)),
    });
    if (a.model_provider_id) loadModels(String(a.model_provider_id));
    setShowForm(true);
  }

  async function submit() {
    if (busy || !form.name.trim()) return;
    setBusy(true);
    const duties: DutiesShape = {
      responsible_for: linesToList(form.dutiesResp),
      not_responsible_for: linesToList(form.dutiesNot),
      deliverables: linesToList(form.dutiesDeliv),
    };
    const payload = {
      name: form.name, role: form.role, description: form.description,
      system_prompt: form.system_prompt, responsibilities: duties,
      avatar: editing ? (editing.avatar ?? "") : "", appearance: form.appearance,
      model_provider_id: form.model_provider_id ? Number(form.model_provider_id) : null,
      model_id: form.model_id || null,
      capability_tags: form.capability_tags.split(",").map((x) => x.trim()).filter(Boolean),
      capability_scores: editing ? (editing.capability_scores ?? {}) : {},
      tools: form.tools, skills: form.skills,
      memory_config: editing ? (editing as unknown as { memory_config?: Record<string, unknown> }).memory_config ?? {} : {},
      execution_environment: "sandbox", permissions: {},
    };
    try {
      if (editing) {
        await api.updateAgent(editing.id, payload);
        showToast("success", `员工「${form.name}」已更新。`);
      } else {
        await api.createAgent(payload);
        showToast("success", `员工「${form.name}」已创建。`);
      }
      setShowForm(false); void agents.reload();
    } catch (e) { showToast("error", `保存失败：${e instanceof Error ? e.message : String(e)}`); }
    finally { setBusy(false); }
  }

  /* ---------- 详情 ---------- */
  const [detailFor, setDetailFor] = useState<Agent | null>(null);

  const formToolOptions = [...new Set([...TOOL_OPTIONS, ...form.tools])];

  return (
    <div>
      <div className="spread">
        <div><h1 className="page-title">AI 员工</h1><p className="page-desc">管理你的 AI 员工：岗位职责、能力、模型与工具权限。</p></div>
        <Button onClick={openCreate}>+ 创建员工</Button>
      </div>

      <div className="row" style={{ gap: 8, flexWrap: "wrap", marginBottom: 14 }}>
        <span style={{ position: "relative", display: "inline-flex", width: 220 }}>
          <Input placeholder="搜索姓名、角色或职责" value={query} onChange={(e) => setQuery(e.target.value)} style={{ width: "100%", paddingRight: 28 }} />
          {query && (
            <button type="button" aria-label="清空搜索" onClick={() => setQuery("")}
              style={{ position: "absolute", right: 4, top: "50%", transform: "translateY(-50%)", background: "none", border: "none", cursor: "pointer", color: "#8A8A86", fontSize: 16, lineHeight: 1, padding: "2px 6px" }}>×</button>
          )}
        </span>
        <Select value={statusFilter} onValueChange={setStatusFilter}>
          <SelectTrigger style={{ width: 140 }}><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="all">全部状态</SelectItem>
            <SelectItem value="idle">空闲</SelectItem>
            <SelectItem value="working">工作中</SelectItem>
            <SelectItem value="failed">执行失败</SelectItem>
            <SelectItem value="disabled">已禁用</SelectItem>
          </SelectContent>
        </Select>
        <Select value={tagFilter} onValueChange={setTagFilter}>
          <SelectTrigger style={{ width: 140 }}><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="all">全部技能</SelectItem>
            {allTags.map((t) => <SelectItem key={t} value={t}>{capLabel(t)}</SelectItem>)}
          </SelectContent>
        </Select>
        <Select value={teamFilter} onValueChange={setTeamFilter}>
          <SelectTrigger style={{ width: 160 }}><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="all">全部团队</SelectItem>
            {(teams.data ?? []).map((t) => <SelectItem key={t.id} value={String(t.id)}>{t.name}</SelectItem>)}
          </SelectContent>
        </Select>
        {(query || statusFilter !== "all" || tagFilter !== "all" || teamFilter !== "all") && (
          <span className="muted small">共 {shownAgents.length} 位</span>
        )}
      </div>

      <StateBox loading={agents.loading} error={agents.error} empty={(agents.data?.length ?? 0) === 0}
        emptyText="还没有 AI 员工" emptyHint="先创建一位员工，给它岗位职责、能力和模型，它就能开始接任务了。" emptyActionLabel="创建第一位员工" onEmptyAction={openCreate} onRetry={() => void agents.reload()}>
        {shownAgents.length === 0 ? (
          <div className="state-box"><div style={{ fontWeight: 600 }}>没有符合筛选条件的员工</div><div style={{ marginTop: 4 }}>换个关键词或清掉筛选条件再看。</div></div>
        ) : (
          <div className="grid grid-2">
            {shownAgents.map((a) => {
              const duties = normalizeDuties(a.responsibilities);
              return (
                <Card className={`flex flex-col p-4 ${freshAgents.has(a.id) ? "motion-item-enter" : ""}`} key={a.id}>
                  <div className="spread">
                    <div style={{ display: "flex", gap: 12, alignItems: "center", minWidth: 0 }}>
                      <Avatar name={a.name} size={44} avatar={a.avatar} appearance={a.appearance} />
                      <div style={{ minWidth: 0 }}>
                        <div style={{ fontWeight: 700, display: "flex", alignItems: "center", gap: 8 }} className="truncate">
                          {a.name}<AgentStatusDot runtimeStatus={a.runtime_status} status={a.status} activityStatus={a.activity_status} />
                        </div>
                        <div className="muted small">{a.name.includes("·") ? activityLabel(a.activity_status) : `${roleLabel(a.role)} · ${activityLabel(a.activity_status)}`}</div>
                      </div>
                    </div>
                    <div className="row" style={{ gap: 6, flexWrap: "nowrap" }}>
                      <StatusBadge status={a.status} />
                      <MoreMenu items={[
                        { label: "查看详情", onClick: () => setDetailFor(a) },
                        { label: "编辑员工", onClick: () => openEdit(a) },
                        { label: "工具权限", onClick: () => openTools(a) },
                        { label: "复制员工", onClick: () => void api.copyAgent(a.id).then(() => agents.reload()).catch((e) => showToast("error", `复制员工失败：${e instanceof Error ? e.message : String(e)}，可以稍后再试。`)) },
                        { label: a.status === "active" ? "禁用员工" : "启用员工", onClick: () => void api.toggleAgent(a.id).then(() => agents.reload()).catch((e) => showToast("error", `切换员工状态失败：${e instanceof Error ? e.message : String(e)}，可以稍后再试。`)) },
                        { label: "删除员工", danger: true, confirmText: `删除员工「${a.name}」？此操作不可恢复。`, onClick: () => void api.deleteAgent(a.id).then(() => agents.reload()).catch((e) => showToast("error", `删除员工失败：${e instanceof Error ? e.message : String(e)}，可以稍后再试。`)) },
                      ]} />
                    </div>
                  </div>
                  <div className="small wrap-anywhere" style={{ marginTop: 10, lineHeight: 1.55 }}>{a.description || "暂无职责描述。"}</div>
                  {duties.responsible_for.length > 0 && (
                    <div className="small" style={{ marginTop: 6, lineHeight: 1.55 }}>
                      <span className="muted">负责：</span>{duties.responsible_for.join("、")}
                    </div>
                  )}
                  {duties.deliverables.length > 0 && (
                    <div className="row" style={{ marginTop: 6, gap: 5, flexWrap: "wrap" }}>
                      <span className="muted small">交付物</span>
                      {duties.deliverables.map((d) => <Badge key={d} variant="secondary">{d}</Badge>)}
                    </div>
                  )}
                  {(() => {
                    const tags = a.capability_tags ?? [];
                    const scoresMap = a.capability_scores ?? {};
                    const scored = tags
                      .filter((t) => typeof scoresMap[t] === "number")
                      .sort((x, y) => (scoresMap[y] ?? 0) - (scoresMap[x] ?? 0))
                      .slice(0, 3);
                    if (scored.length === 0) {
                      return (
                        <div className="row" style={{ marginTop: 8 }}>
                          {tags.slice(0, 3).map((t) => <Badge key={t} className="border-transparent bg-info-tint text-info hover:bg-info-tint">{capLabel(t)}</Badge>)}
                          {tags.length > 3 && <Badge variant="secondary">+{tags.length - 3}</Badge>}
                        </div>
                      );
                    }
                    return (
                      <div style={{ marginTop: 10, display: "flex", flexDirection: "column", gap: 5, maxWidth: 340 }}>
                        {scored.map((t) => (
                          <div key={t} style={{ display: "flex", alignItems: "center", gap: 8 }}>
                            <span className="small muted" style={{ width: 64, flex: "none" }}>{capLabel(t)}</span>
                            <span style={{ flex: 1, minWidth: 0, display: "block" }}><ScoreBar value={scoresMap[t]} tone="info" format={(v) => v.toFixed(2)} /></span>
                          </div>
                        ))}
                      </div>
                    );
                  })()}
                  <div className="muted small" style={{ marginTop: 8 }}>可用工具：{(a.tools ?? []).map(toolLabel).join("、") || "无"}</div>
                  <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 8 }}>
                    <span className="muted small" style={{ flex: "none" }}>当前负载</span>
                    <span style={{ display: "inline-block", width: 120 }}><ScoreBar value={a.current_load} tone="info" format={(v) => `${Math.round(v * 100)}%`} /></span>
                  </div>
                  <div className="spread" style={{ marginTop: "auto", paddingTop: 10, alignItems: "center", flexWrap: "wrap", rowGap: 6 }}>
                    <span style={{ display: "flex", alignItems: "center", gap: 8, minWidth: 0, flexWrap: "wrap" }}>
                      <span className="muted small" style={{ flex: "none" }}>成功率</span>
                      {a.historical_success_rate == null
                        ? <span className="muted small">暂无数据</span>
                        : <span style={{ display: "inline-block", width: 150 }}><ScoreBar value={a.historical_success_rate} tone={a.historical_success_rate >= 0.8 ? "success" : "info"} format={(v) => `${Math.round(v * 100)}%`} /></span>}
                      <span className="muted small" style={{ whiteSpace: "nowrap" }}>· 执行 {a.execution_count} 次</span>
                    </span>
                    <span style={{ display: "flex", alignItems: "center", gap: 8 }}>
                      <Button variant="secondary" size="sm" onClick={() => setDetailFor(a)}>详情</Button>
                      {a.model_id
                        ? <Badge variant="secondary">模型：{a.model_id}</Badge>
                        : <Button variant="ghost" size="sm" className="h-5 rounded-full bg-warning-tint px-2 text-xs text-warning hover:bg-warning-tint hover:text-warning" onClick={() => setPage("settings")}>未配置模型 · 去配置</Button>}
                    </span>
                  </div>
                </Card>
              );
            })}
          </div>
        )}
      </StateBox>

      {/* ---------- 工具权限 ---------- */}
      <AnimatedModal open={toolsFor !== null} onClose={() => setToolsFor(null)} labelledBy="agent-tools-title">
        <h3 id="agent-tools-title" style={{ marginTop: 0 }}>工具权限 · {toolsFor?.name}</h3>
        <p className="muted small" style={{ marginTop: 0 }}>勾选这位员工可以调用的工具，清单来自工具注册表，保存后立即生效。</p>
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {(registry.data ?? []).map((t) => (
            <div key={t.id} className="small" style={{ display: "flex", gap: 8, alignItems: "flex-start" }}>
              <Switch className="mt-0.5 shrink-0" checked={toolSel.includes(t.id)}
                onCheckedChange={(checked) => setToolSel(checked ? [...toolSel, t.id] : toolSel.filter((x) => x !== t.id))} />
              <span>
                <span style={{ fontWeight: 500 }}>{toolLabel(t.id)}</span>
                {t.high_risk && <Badge className="ml-1.5 border-transparent bg-warning-tint text-warning hover:bg-warning-tint">高风险</Badge>}
                {t.requires_approval && <Badge variant="secondary" className="ml-1.5">需审批</Badge>}
                <span className="muted" style={{ display: "block" }}>{t.description}</span>
              </span>
            </div>))}
        </div>
        {legacyTools.length > 0 && (
          <div className="muted small" style={{ marginTop: 10 }}>历史工具，不在注册表中，保存时保留：{legacyTools.map(toolLabel).join("、")}</div>
        )}
        <div className="row" style={{ marginTop: 16 }}>
          <Button disabled={savingTools} onClick={() => void saveTools()}>{savingTools ? "保存中…" : "保存"}</Button>
          <Button variant="secondary" onClick={() => setToolsFor(null)}>取消</Button>
        </div>
      </AnimatedModal>

      {/* ---------- 创建 / 编辑 ---------- */}
      <AnimatedModal open={showForm} onClose={() => setShowForm(false)} labelledBy="agent-form-title">
        <h3 id="agent-form-title" style={{ marginTop: 0 }}>{editing ? `编辑员工 · ${editing.name}` : "创建员工"}</h3>
        {!editing && (
          <>
            <Label className="mb-1.5 mt-3 block">岗位模板</Label>
            <Select value={templateKey || "__blank__"} onValueChange={(v) => { if (v === "__blank__") { setTemplateKey(""); } else applyTemplate(v); }}>
              <SelectTrigger><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="__blank__">空白创建</SelectItem>
                {TEMPLATES.map((t) => <SelectItem key={t.key} value={t.key}>{t.label}</SelectItem>)}
              </SelectContent>
            </Select>
            <p className="muted small" style={{ marginTop: 4 }}>选模板会带出角色、岗位职责、建议能力与默认形象，带出后都能改。</p>
          </>
        )}
        <Label className="mb-1.5 mt-3 block">形象</Label>
        <AppearancePicker value={form.appearance} onChange={(a) => setForm({ ...form, appearance: a })} />
        <p className="muted small" style={{ marginTop: 4 }}>形象设置会同步到 AI 工作室，工作室里的人物形象和这里保持一致。</p>
        <Label className="mb-1.5 mt-3 block">名称</Label>
        <Input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
        <Label className="mb-1.5 mt-3 block">角色</Label>
        <Input value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })} placeholder="如：产品经理 / 程序员 / 测试工程师" />
        <Label className="mb-1.5 mt-3 block">一句话职责</Label>
        <Input value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} />
        <Label className="mb-1.5 mt-3 block">负责什么，每行一项</Label>
        <Textarea rows={3} value={form.dutiesResp} onChange={(e) => setForm({ ...form, dutiesResp: e.target.value })} placeholder={"需求分析与澄清\n任务拆解与排期"} />
        <Label className="mb-1.5 mt-3 block">不负责什么，每行一项</Label>
        <Textarea rows={2} value={form.dutiesNot} onChange={(e) => setForm({ ...form, dutiesNot: e.target.value })} placeholder={"代码实现\n测试执行"} />
        <Label className="mb-1.5 mt-3 block">标准交付物，每行一项</Label>
        <Textarea rows={2} value={form.dutiesDeliv} onChange={(e) => setForm({ ...form, dutiesDeliv: e.target.value })} placeholder={"需求文档\n验收标准"} />
        <Label className="mb-1.5 mt-3 block">工作要求</Label>
        <Textarea value={form.system_prompt} onChange={(e) => setForm({ ...form, system_prompt: e.target.value })} />
        <Label className="mb-1.5 mt-3 block">模型供应商</Label>
        <Select value={form.model_provider_id ? form.model_provider_id : "__none__"} onValueChange={(v) => { const pid = v === "__none__" ? "" : v; setForm({ ...form, model_provider_id: pid, model_id: "" }); void loadModels(pid); }}>
          <SelectTrigger><SelectValue placeholder="暂不配置" /></SelectTrigger>
          <SelectContent>
            <SelectItem value="__none__">暂不配置</SelectItem>
            {(providers.data ?? []).filter((p) => p.enabled).map((p) => <SelectItem key={p.id} value={String(p.id)}>{p.name} — {p.base_url}</SelectItem>)}
          </SelectContent>
        </Select>
        <Label className="mb-1.5 mt-3 block">模型</Label>
        <Select disabled={!form.model_provider_id} value={form.model_id ? form.model_id : "__none__"} onValueChange={(v) => setForm({ ...form, model_id: v === "__none__" ? "" : v })}>
          <SelectTrigger><SelectValue placeholder="请选择" /></SelectTrigger>
          <SelectContent>
            <SelectItem value="__none__">请选择</SelectItem>
            {models.map((m) => <SelectItem key={m} value={m}>{m}</SelectItem>)}
          </SelectContent>
        </Select>
        {modelError && <div className="small" style={{ color: "var(--warning)", marginTop: 4 }}>{modelError}</div>}
        <Label className="mb-1.5 mt-3 block">能力标签，逗号分隔，如 planning 规划、coding 编程、review 评审</Label>
        <Input value={form.capability_tags} onChange={(e) => setForm({ ...form, capability_tags: e.target.value })} />
        <Label className="mb-1.5 mt-3 block">绑定技能</Label>
        {(skillList.data ?? []).length === 0
          ? <div className="muted small">技能库还是空的，先去「技能」页添加或导入技能包。</div>
          : <div className="row" style={{ gap: 10, flexWrap: "wrap" }}>{(skillList.data ?? []).map((s) => (
            <span key={s.id} className="small" style={{ display: "flex", gap: 4, alignItems: "center" }}>
              <Switch checked={form.skills.includes(s.id)} onCheckedChange={(checked) => setForm({ ...form, skills: checked ? [...form.skills, s.id] : form.skills.filter((x) => x !== s.id) })} />{s.name}
            </span>))}
          </div>}
        <Label className="mb-1.5 mt-3 block">常用工具权限</Label>
        <div className="row" style={{ gap: 10, flexWrap: "wrap" }}>{formToolOptions.map((t) => (
          <span key={t} className="small" style={{ display: "flex", gap: 4, alignItems: "center" }}>
            <Switch checked={form.tools.includes(t)} onCheckedChange={(checked) => setForm({ ...form, tools: checked ? [...form.tools, t] : form.tools.filter((x) => x !== t) })} />{toolLabel(t)}
          </span>))}
        </div>
        <p className="muted small" style={{ marginTop: 4 }}>完整工具清单在员工卡片的「工具权限」里调整。</p>
        <div className="row" style={{ marginTop: 16 }}>
          <Button disabled={busy || !form.name.trim()} onClick={() => void submit()}>{busy ? "保存中…" : "保存"}</Button>
          <Button variant="secondary" onClick={() => setShowForm(false)}>取消</Button>
        </div>
      </AnimatedModal>

      {/* ---------- 详情 ---------- */}
      <AnimatedModal open={detailFor !== null} onClose={() => setDetailFor(null)} labelledBy="agent-detail-title">
        {detailFor && (
          <AgentDetailBody
            agent={detailFor}
            onClose={() => setDetailFor(null)}
            onOpenTask={(tid) => { setDetailFor(null); openTask(tid); }}
          />
        )}
      </AnimatedModal>
    </div>
  );
}

function AgentDetailBody({ agent, onClose, onOpenTask }: {
  agent: Agent; onClose: () => void; onOpenTask: (taskId: number) => void;
}) {
  const detail = useAsync(() => api.agentDetail(agent.id));
  const d: AgentDetail["detail"] | null = detail.data?.detail ?? null;
  const duties = normalizeDuties(detail.data?.responsibilities ?? agent.responsibilities);
  const scores = Object.entries(detail.data?.capability_scores ?? agent.capability_scores ?? {})
    .filter(([, v]) => typeof v === "number")
    .sort((x, y) => y[1] - x[1]);
  const perf = d?.performance;
  return (
    <div>
      <div style={{ display: "flex", gap: 14, alignItems: "center" }}>
        <Avatar name={agent.name} size={56} avatar={detail.data?.avatar ?? agent.avatar} appearance={detail.data?.appearance ?? agent.appearance} />
        <div style={{ minWidth: 0 }}>
          <h3 id="agent-detail-title" style={{ margin: 0, display: "flex", alignItems: "center", gap: 8 }}>
            {agent.name}
            <AgentStatusDot runtimeStatus={agent.runtime_status} status={agent.status} activityStatus={detail.data?.activity_status ?? agent.activity_status} />
          </h3>
          <div className="muted small" style={{ marginTop: 3 }}>
            {roleLabel(agent.role)} · {activityLabel(detail.data?.activity_status ?? agent.activity_status)}
            {agent.model_id ? ` · 模型：${agent.model_id}` : " · 未配置模型"}
          </div>
        </div>
        <span style={{ marginLeft: "auto" }}><StatusBadge status={agent.status} /></span>
      </div>

      <StateBox loading={detail.loading} error={detail.error} empty={false} emptyText="" onRetry={() => void detail.reload()}>
        {d && (
          <div style={{ display: "flex", flexDirection: "column", gap: 18, marginTop: 16 }}>
            <section>
              <h4 style={{ margin: "0 0 8px" }}>当前状态</h4>
              {(d.current_tasks ?? []).length === 0
                ? <div className="muted small">当前没有正在执行的任务。</div>
                : (d.current_tasks ?? []).map((c) => (
                  <div key={c.node_id} className="small" style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
                    <Badge className="border-transparent bg-info-tint text-info hover:bg-info-tint">执行中</Badge>
                    <button type="button" onClick={() => onOpenTask(c.task_id)} style={{ fontWeight: 600, background: "none", border: "none", padding: 0, cursor: "pointer", color: "var(--info)", font: "inherit" }}>{c.task_title}</button>
                    <span className="muted">步骤：{c.node_name}</span>
                    {c.started_at && <span className="muted">开始于 {fmtTime(c.started_at)}</span>}
                  </div>
                ))}
            </section>

            <section>
              <h4 style={{ margin: "0 0 8px" }}>岗位职责</h4>
              {duties.responsible_for.length + duties.not_responsible_for.length + duties.deliverables.length === 0
                ? <div className="muted small">暂未填写结构化职责，在编辑员工时可以补上。</div>
                : (
                  <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                    {duties.responsible_for.length > 0 && (
                      <div className="small"><span className="muted">负责：</span>{duties.responsible_for.join("、")}</div>
                    )}
                    {duties.not_responsible_for.length > 0 && (
                      <div className="small"><span className="muted">不负责：</span>{duties.not_responsible_for.join("、")}</div>
                    )}
                    {duties.deliverables.length > 0 && (
                      <div className="row" style={{ gap: 5, flexWrap: "wrap" }}>
                        <span className="muted small">标准交付物</span>
                        {duties.deliverables.map((x) => <Badge key={x} variant="secondary">{x}</Badge>)}
                      </div>
                    )}
                  </div>
                )}
            </section>

            <section>
              <h4 style={{ margin: "0 0 8px" }}>能力评分</h4>
              {scores.length === 0
                ? <div className="muted small">暂无能力评分，完成任务后会根据真实执行结果更新。</div>
                : (
                  <div style={{ display: "flex", flexDirection: "column", gap: 6, maxWidth: 420 }}>
                    {scores.map(([tag, v]) => (
                      <div key={tag} style={{ display: "flex", alignItems: "center", gap: 8 }}>
                        <span className="small muted" style={{ width: 64, flex: "none" }}>{capLabel(tag)}</span>
                        <span style={{ flex: 1, display: "block" }}><ScoreBar value={v} tone="info" format={(x) => x.toFixed(2)} /></span>
                      </div>
                    ))}
                  </div>
                )}
            </section>

            <section>
              <h4 style={{ margin: "0 0 8px" }}>绩效</h4>
              {perf && (
                <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 8 }}>
                  {[
                    { label: "执行次数", value: String(perf.execution_count) },
                    { label: "成功次数", value: String(perf.success_count) },
                    { label: "历史成功率", value: perf.historical_success_rate == null ? "暂无数据" : `${Math.round(perf.historical_success_rate * 100)}%` },
                    { label: "步骤成功率", value: perf.node_success_rate == null ? "暂无数据" : `${Math.round(perf.node_success_rate * 100)}%` },
                    { label: "单步平均耗时", value: fmtDuration(perf.average_duration_seconds) },
                    { label: "累计 Token", value: perf.total_tokens == null ? "暂无数据" : perf.total_tokens.toLocaleString("zh-CN") },
                  ].map((m) => (
                    <div key={m.label} style={{ border: "1px solid var(--border)", borderRadius: 10, padding: "8px 10px" }}>
                      <div className="muted small">{m.label}</div>
                      <div style={{ fontWeight: 700, marginTop: 2 }}>{m.value}</div>
                    </div>
                  ))}
                </div>
              )}
            </section>

            <section>
              <h4 style={{ margin: "0 0 8px" }}>历史任务</h4>
              {(d.history ?? []).length === 0
                ? <div className="muted small">暂无执行过的任务记录。</div>
                : (
                  <div style={{ display: "flex", flexDirection: "column" }}>
                    {(d.history ?? []).map((h) => (
                      <div key={h.task_id} style={{ display: "flex", gap: 8, alignItems: "center", padding: "7px 0", borderTop: "1px solid var(--border)", flexWrap: "wrap" }}>
                        <button type="button" onClick={() => onOpenTask(h.task_id)} style={{ fontWeight: 600, minWidth: 0, background: "none", border: "none", padding: 0, cursor: "pointer", color: "var(--info)", font: "inherit", textAlign: "left" }}>{h.title}</button>
                        <StatusBadge status={h.task_status} />
                        <span className="muted small">耗时 {fmtDuration(h.duration_seconds)}</span>
                        <span style={{ marginLeft: "auto" }}>
                          {h.verification
                            ? <Badge className={`border-transparent ${h.verification.passed ? "bg-success-tint text-success hover:bg-success-tint" : "bg-errortint text-destructive hover:bg-errortint"}`}>{h.verification.passed ? "验证通过" : "验证未通过"} · {h.verification.score.toFixed(2)}</Badge>
                            : <span className="muted small">暂无验证记录</span>}
                        </span>
                      </div>
                    ))}
                  </div>
                )}
            </section>

            <section>
              <h4 style={{ margin: "0 0 8px" }}>团队、技能与工具</h4>
              <div className="small" style={{ lineHeight: 1.9 }}>
                <div><span className="muted">所属团队：</span>
                  {(d.teams ?? []).length === 0 ? "暂未加入团队" : (d.teams ?? []).map((t, i) => (
                    <span key={t.name}>{i > 0 ? "、" : ""}{t.name}{t.is_leader ? <Badge variant="secondary" className="ml-1">负责人</Badge> : null}</span>
                  ))}
                </div>
                <div><span className="muted">绑定技能：</span>
                  {(d.skills_resolved ?? []).length === 0 ? "暂未绑定技能" : (d.skills_resolved ?? []).map((s) => s.name).join("、")}
                </div>
                <div><span className="muted">可用工具：</span>
                  {(agent.tools ?? []).length === 0 ? "无" : (agent.tools ?? []).map(toolLabel).join("、")}
                </div>
              </div>
            </section>
          </div>
        )}
      </StateBox>
      <div className="row" style={{ marginTop: 16 }}>
        <Button variant="secondary" onClick={onClose}>关闭</Button>
      </div>
    </div>
  );
}
