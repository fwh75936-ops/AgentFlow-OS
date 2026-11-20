import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "../services/api";
import { AgentStatusDot, AnimatedModal, Avatar, MoreMenu, StateBox, activityLabel, statusLabel, useAsync } from "../components/ui";
import emptyChatUrl from "../assets/empty-chat.png";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import Markdown from "../components/Markdown";
import { useAppStore } from "../stores/appStore";
import { usePageVisible } from "../motion/visibility";
import { showToast } from "../motion/Toast";
import { useStudioStore } from "../features/pixel-studio/store/studioStore";
import type { Agent, ChannelMessage, Team } from "../types";

/* Team identity: a deterministic colour + initial tile per team, so a
 * team reads as its own entity instead of a re-listing of its members
 * (the employee page owns member detail; this page owns teams). */
const TEAM_COLORS = ["#4E6E9E", "#69915F", "#A87F52", "#9C6470", "#57808C", "#7C8288", "#6E7D5C", "#42648C"];
const teamColor = (id: number) => TEAM_COLORS[Math.abs(id) % TEAM_COLORS.length];

type Mode = "direct" | "chief" | "discuss";
const MODE_HINTS: Record<Mode, string> = {
  direct: "直接发消息聊天；在消息里 @成员 发送，就会给对方生成真实任务，干完在群里回报。",
  chief: "把目标发给总管，总管负责组队规划、派活执行，最后在群里汇报结果。",
  discuss: "成员按顺序各抒己见，负责人先说，每人一轮，用的是各自绑定的模型。",
};

function excerpt(text: string, n = 42) {
  const t = (text || "").replace(/\s+/g, " ");
  return t.length > n ? t.slice(0, n) + "…" : t;
}

/* Task/result cards store the raw structured node output: often a JSON
   object whose field values are themselves fenced JSON strings. Unwrap
   those layers for display only, so people read the answer instead of
   the transport format. Anything unrecognized is returned untouched. */
function unwrapStructuredText(text: string, depth = 0): string | null {
  if (depth > 4) return null;
  const t = text.trim();
  const fenced = t.replace(/^```(?:json)?\s*/i, "").replace(/\s*```$/, "");
  try {
    const v: unknown = JSON.parse(fenced);
    if (typeof v === "string") return unwrapStructuredText(v, depth + 1) ?? v;
    if (v && typeof v === "object" && !Array.isArray(v)) {
      const rec = v as Record<string, unknown>;
      for (const key of ["result", "summary", "执行", "结论", "内容", "output", "text"]) {
        const val = rec[key];
        if (typeof val === "string" && val.trim()) return unwrapStructuredText(val, depth + 1) ?? val;
      }
      const parts = Object.values(rec).filter((x): x is string => typeof x === "string" && !!x.trim());
      if (parts.length) return parts.map((p) => unwrapStructuredText(p, depth + 1) ?? p).join("\n");
    }
  } catch { /* not JSON — leave as-is */ }
  return null;
}

function humanizeCardContent(raw: string): string {
  const i = raw.indexOf("{");
  if (i < 0) return raw;
  const inner = unwrapStructuredText(raw.slice(i));
  if (!inner) return raw;
  const prefix = raw.slice(0, i).trimEnd();
  return prefix ? `${prefix}${inner}` : inner;
}

function TeamTile({ team, size = 40 }: { team: Team; size?: number }) {
  return (
    <div style={{
      width: size, height: size, borderRadius: 10, background: teamColor(team.id),
      color: "#fff", display: "flex", alignItems: "center", justifyContent: "center",
      fontWeight: 700, fontSize: size * 0.42, flexShrink: 0,
    }}>{team.name.slice(0, 1)}</div>
  );
}

/* ------------------------------------------------------------- chat panel */

function ChatPanel({ team, agents, onSeen, savedDraft, onDraftSave }: {
  team: Team;
  agents: Agent[];
  onSeen: (teamId: number, lastMessageId: string) => void;
  /** 切走团队再回来时恢复的未发草稿（面板按团队 key 重挂载，草稿存在父组件）。 */
  savedDraft?: { draft: string; mentions: number[] };
  onDraftSave: (draft: string, mentions: number[]) => void;
}) {
  const messages = useAsync(() => api.channelMessages(team.id));
  const [draft, setDraft] = useState(savedDraft?.draft ?? "");
  const [mentions, setMentions] = useState<number[]>(savedDraft?.mentions ?? []);
  const [sending, setSending] = useState(false);
  const [pinningId, setPinningId] = useState<string | null>(null);
  const [mode, setMode] = useState<Mode>("direct");
  const [replyTo, setReplyTo] = useState<ChannelMessage | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [mentionQuery, setMentionQuery] = useState<string | null>(null);
  const listRef = useRef<HTMLDivElement>(null);

  // 草稿随打随存：切到别的团队再回来，未发的内容与 @ 的人都在。
  useEffect(() => { onDraftSave(draft, mentions); }, [draft, mentions, onDraftSave]);

  const agentById = useMemo(() => new Map(agents.map((a) => [a.id, a])), [agents]);
  const agentName = (id: number) => agentById.get(id)?.name ?? "已删除的员工";
  const members = useMemo(
    () => (team.members ?? []).map((m) => agentById.get(m.agent_id)).filter((a): a is Agent => !!a),
    [team, agentById]);

  // Poll for new messages (task cards and discussion replies arrive
  // asynchronously from background execution). Paused while this page is
  // hidden (keep-alive pages stay mounted) or the window is hidden.
  const pageVisible = usePageVisible();
  const pageVisibleRef = useRef(pageVisible);
  pageVisibleRef.current = pageVisible;
  useEffect(() => {
    const iv = setInterval(() => { if (pageVisibleRef.current && !document.hidden) void messages.refresh(); }, 3000);
    return () => clearInterval(iv);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [team.id]);

  const all = messages.data ?? [];
  useEffect(() => {
    if (all.length) onSeen(team.id, all[all.length - 1].message_id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [all.length, team.id]);

  useEffect(() => {
    const el = listRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [all.length]);

  const visible = query.trim()
    ? all.filter((m) => (m.content || "").includes(query.trim()))
    : all;
  const byId = useMemo(() => new Map(all.map((m) => [m.message_id, m])), [all]);

  const mentionCandidates = mentionQuery === null ? [] : members.filter(
    (a) => a.name.includes(mentionQuery) || mentionQuery === "");

  function onDraftChange(v: string) {
    setDraft(v);
    const m = /(^|\s)@([^\s@]*)$/.exec(v);
    setMentionQuery(m ? m[2] : null);
  }

  function pickMention(a: Agent) {
    setDraft((d) => d.replace(/(^|\s)@([^\s@]*)$/, (full, pre) => `${pre}@${a.name} `));
    setMentions((prev) => (prev.includes(a.id) ? prev : [...prev, a.id]));
    setMentionQuery(null);
  }

  async function send() {
    const content = draft.trim();
    if (!content || sending) return;
    setSending(true);
    try {
      const r = (await api.postChannelMessage(team.id, content, {
        mentions, mode, replyTo: replyTo?.message_id ?? null,
      })) as { dispatch?: { mode: string; task_ids?: number[] } };
      const d = r.dispatch;
      if (d?.mode === "chief") setNotice("总管已接单，正在组队规划，完成后会在群里汇报。");
      else if (d?.mode === "discuss") setNotice("讨论已开始，成员将依次发言。");
      else if (d?.task_ids?.length) setNotice(`已生成真实任务：${d.task_ids.map((i) => `#${i}`).join("、")}，执行结果会回到群里。`);
      else if (mentions.length) setNotice(`已定向通知：${mentions.map(agentName).join("、")}`);
      else setNotice(null);
      setDraft(""); setMentions([]); setReplyTo(null); setMentionQuery(null);
      void messages.reload();
    } catch (e) {
      setNotice(`发送失败：${e instanceof Error ? e.message : String(e)}`);
    } finally { setSending(false); }
  }

  async function togglePin(m: ChannelMessage) {
    // 置顶是开关式操作：不加守卫连点两下会置顶又取消，等于没点。
    if (pinningId !== null) return;
    setPinningId(m.message_id);
    try {
      await api.pinChannelMessage(team.id, m.message_id);
      void messages.reload();
    } finally { setPinningId(null); }
  }

  const openTask = useAppStore((s) => s.openTask);

  function timeDivider(m: ChannelMessage, prev: ChannelMessage | undefined) {
    if (!m.timestamp) return null;
    const t = new Date(m.timestamp);
    if (prev?.timestamp && t.getTime() - new Date(prev.timestamp).getTime() < 5 * 60 * 1000) return null;
    const label = (new Date().toDateString() === t.toDateString() ? "" : `${t.getMonth() + 1}月${t.getDate()}日 `)
      + t.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", hour12: false });
    return (
      <div key={m.message_id + "-time"} style={{ textAlign: "center" }}>
        <span className="small" style={{ background: "#E9E9E6", color: "#8A8A86", borderRadius: 999, padding: "2px 10px" }}>{label}</span>
      </div>
    );
  }

  function renderMessage(m: ChannelMessage, idx: number, list: ChannelMessage[]) {
    const prev = idx > 0 ? list[idx - 1] : undefined;
    const divider = timeDivider(m, prev);
    if (m.message_type === "SYSTEM") {
      return (
        <div key={m.message_id}>
          {divider}
          <div style={{ textAlign: "center", marginTop: divider ? 8 : 0 }}>
            <span className="small" style={{ background: "#E9E9E6", color: "#8A8A86", borderRadius: 999, padding: "3px 12px" }}>{m.content}</span>
          </div>
        </div>
      );
    }
    if (m.message_type === "TASK_CARD" || m.message_type === "RESULT_CARD") {
      const isResult = m.message_type === "RESULT_CARD";
      const status = m.meta.status;
      const accent = !isResult ? "#C58A32" : status === "completed" ? "#238B61" : "#D9534F";
      return (
        <div key={m.message_id}>
          {divider}
          <div className="msg-row" style={{
            border: "1px solid var(--border)", borderLeft: `3px solid ${accent}`,
            borderRadius: 10, padding: "9px 12px", background: "#fff",
            width: "min(88%, 560px)", margin: "8px auto 0", boxShadow: "0 1px 2px rgba(0,0,0,0.04)",
          }}>
            <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
              <Badge variant="secondary" className="border-transparent" style={{ background: accent + "1A", color: accent }}>
                {m.meta.kind === "mission_start" ? "总管接单" : m.meta.kind === "mission_card" ? "总管汇报" : isResult ? "执行结果" : "任务卡"}
              </Badge>
              {m.meta.task_id ? <span className="small muted">任务 #{m.meta.task_id}</span> : null}
              {isResult && m.meta.token_usage ? <span className="small muted">Token {m.meta.token_usage.toLocaleString("zh-CN")}</span> : null}
              {isResult && (m.meta.retry_count ?? 0) > 0 ? <span className="small muted">重试 {m.meta.retry_count} 次</span> : null}
              <span style={{ flex: 1 }} />
              <button className="small muted msg-actions" style={{ background: "none", border: "none", cursor: "pointer" }} disabled={pinningId === m.message_id} onClick={() => void togglePin(m)}>{m.meta.pinned ? "取消置顶" : "置顶"}</button>
            </div>
            <div style={{ marginTop: 5 }}><Markdown text={humanizeCardContent(m.content)} /></div>
            {m.meta.nodes?.length ? (
              <div style={{ marginTop: 6, display: "flex", flexDirection: "column", gap: 3 }}>
                {m.meta.nodes.map((n) => (
                  <div key={n.id} className="small muted">· {n.name} — {statusLabel(n.status)} · {n.assigned_agent_id ? agentName(n.assigned_agent_id) : "未分配"}</div>
                ))}
              </div>
            ) : null}
            {m.meta.task_id ? (
              <button className="small" style={{ marginTop: 6, background: "none", border: "none", color: "#3978C6", cursor: "pointer", padding: 0 }}
                onClick={() => openTask(m.meta.task_id!)}>查看任务详情 →</button>
            ) : null}
          </div>
        </div>
      );
    }
    const mine = m.sender_agent_id === null;
    const sender = mine ? null : agentById.get(m.sender_agent_id!);
    const quoted = m.meta.reply_to ? byId.get(m.meta.reply_to) : null;
    const grouped = !!prev && prev.message_type === "CHANNEL_MESSAGE"
      && prev.sender_agent_id === m.sender_agent_id
      && !!m.timestamp && !!prev.timestamp
      && new Date(m.timestamp).getTime() - new Date(prev.timestamp).getTime() < 2 * 60 * 1000;
    return (
      <div key={m.message_id}>
        {divider}
        <div className="msg-row" style={{
          display: "flex", flexDirection: mine ? "row-reverse" : "row",
          gap: 8, alignItems: "flex-start", marginTop: divider ? 8 : 0,
        }}>
          {grouped
            ? <div style={{ width: 34, flexShrink: 0 }} />
            : <Avatar name={sender ? sender.name : "我"} size={34} avatar={sender?.avatar} appearance={sender?.appearance} />}
          <div style={{ maxWidth: "72%", display: "flex", flexDirection: "column", alignItems: mine ? "flex-end" : "flex-start", minWidth: 0 }}>
            {!grouped && (
              <div style={{ display: "flex", gap: 6, alignItems: "center", marginBottom: 3, flexDirection: mine ? "row-reverse" : "row" }}>
                <span className="small" style={{ color: "#8A8A86" }}>{mine ? "我" : sender?.name ?? "已删除的员工"}</span>
                {m.meta.kind === "discussion" && <Badge variant="secondary">讨论</Badge>}
                {m.meta.pinned && <Badge variant="secondary" className="border-transparent bg-success-tint text-success hover:bg-success-tint">已置顶</Badge>}
              </div>
            )}
            <div style={{
              background: mine ? "#242424" : "#fff",
              color: mine ? "#fff" : "#202020",
              border: mine ? "none" : "1px solid var(--border)",
              borderRadius: mine ? "14px 4px 14px 14px" : "4px 14px 14px 14px",
              padding: "8px 12px", boxShadow: "0 1px 2px rgba(0,0,0,0.05)",
              wordBreak: "break-word",
            }}>
              {quoted && (
                <div className="small" style={{
                  background: mine ? "rgba(255,255,255,0.12)" : "#F7F7F5",
                  color: mine ? "rgba(255,255,255,0.8)" : "#777777",
                  borderRadius: 6, padding: "4px 8px", marginBottom: 6,
                }}>
                  {quoted.sender_agent_id ? agentName(quoted.sender_agent_id) : "我"}：{excerpt(quoted.content, 60)}
                </div>
              )}
              <Markdown text={m.content} tone={mine ? "dark" : "light"} />
            </div>
          </div>
          <div className="msg-actions" style={{ display: "flex", flexDirection: "column", gap: 1, paddingTop: 4 }}>
            <button className="small muted" style={{ background: "none", border: "none", cursor: "pointer", whiteSpace: "nowrap" }} onClick={() => setReplyTo(m)}>引用</button>
            <button className="small muted" style={{ background: "none", border: "none", cursor: "pointer", whiteSpace: "nowrap" }} disabled={pinningId === m.message_id} onClick={() => void togglePin(m)}>{m.meta.pinned ? "取消置顶" : "置顶"}</button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <Card style={{ display: "flex", flexDirection: "column", height: "100%", minHeight: 0, overflow: "hidden", padding: 0 }}>
      <div style={{ padding: "13px 16px 11px", borderBottom: "1px solid var(--border)", background: "#fff" }}>
        <div className="spread">
          <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
            <TeamTile team={team} size={36} />
            <div>
              <div style={{ fontWeight: 700 }}>{team.name}</div>
              <div className="small muted">{(team.members ?? []).length} 位成员 · 负责人：{team.leader_agent_id ? agentName(team.leader_agent_id) : "未设置"}</div>
            </div>
          </div>
          <span style={{ position: "relative", display: "inline-flex", maxWidth: 170, flex: 1 }}>
            <Input placeholder="搜索消息" value={query} onChange={(e) => setQuery(e.target.value)} style={{ width: "100%", paddingRight: 28 }} />
            {query && (
              <button type="button" aria-label="清空搜索" onClick={() => setQuery("")}
                style={{ position: "absolute", right: 4, top: "50%", transform: "translateY(-50%)", background: "none", border: "none", cursor: "pointer", color: "#8A8A86", fontSize: 16, lineHeight: 1, padding: "2px 6px" }}>×</button>
            )}
          </span>
        </div>
        <div style={{ display: "flex", gap: 6, marginTop: 10, alignItems: "center", flexWrap: "wrap" }}>
          {(["direct", "chief", "discuss"] as Mode[]).map((md) => (
            <Button key={md} size="sm" variant={mode === md ? "default" : "secondary"} onClick={() => setMode(md)}>
              {md === "direct" ? "指定模式" : md === "chief" ? "总管模式" : "讨论模式"}
            </Button>
          ))}
          <span className="small muted">{MODE_HINTS[mode]}</span>
        </div>
      </div>

      <div ref={listRef} style={{
        flex: 1, overflowY: "auto", background: "#F5F5F2", padding: "14px 16px",
        display: "flex", flexDirection: "column", gap: 9, minHeight: 0,
      }}>
        {messages.loading && <div className="muted small" style={{ textAlign: "center" }}>加载中…</div>}
        {!messages.loading && visible.length === 0 && (
          <div className="muted small" style={{ textAlign: "center", marginTop: 30 }}>
            {!query && <img src={emptyChatUrl} alt="" aria-hidden="true" style={{ display: "block", height: 64, width: "auto", margin: "0 auto 8px", imageRendering: "pixelated" }} />}
            {query ? "没有匹配的消息。" : "暂无消息，发第一条吧。"}
          </div>
        )}
        {visible.map((m, i) => renderMessage(m, i, visible))}
      </div>

      <div style={{ borderTop: "1px solid var(--border)", background: "#fff", padding: "10px 12px 12px" }}>
        {notice && <div className="small" style={{ marginBottom: 8, color: "#3978C6" }}>{notice}</div>}
        {replyTo && (
          <div className="small" style={{ marginBottom: 8, display: "flex", gap: 8, alignItems: "center", background: "#F7F7F5", borderRadius: 8, padding: "6px 10px" }}>
            <span className="muted">引用 {replyTo.sender_agent_id ? agentName(replyTo.sender_agent_id) : "我"}：{excerpt(replyTo.content, 50)}</span>
            <span style={{ flex: 1 }} />
            <button className="muted" title="取消引用" aria-label="取消引用" style={{ background: "none", border: "none", cursor: "pointer" }} onClick={() => setReplyTo(null)}>✕</button>
          </div>
        )}
        <div style={{ position: "relative" }}>
          {mentionQuery !== null && mentionCandidates.length > 0 && (
            <div style={{
              position: "absolute", bottom: "100%", left: 0, marginBottom: 6, zIndex: 5,
              background: "#fff", border: "1px solid var(--border)", borderRadius: 10,
              boxShadow: "0 8px 24px rgba(0,0,0,0.10)", padding: 4, minWidth: 210,
            }}>
              {mentionCandidates.map((a) => (
                <button key={a.id} style={{ display: "flex", gap: 8, alignItems: "center", width: "100%", padding: "6px 8px", background: "none", border: "none", cursor: "pointer", borderRadius: 6, textAlign: "left" }}
                  onClick={() => pickMention(a)}>
                  <Avatar name={a.name} size={24} avatar={a.avatar} appearance={a.appearance} />
                  <span className="small">{a.name}</span>
                  <span className="small muted">{activityLabel(a.activity_status)}</span>
                </button>
              ))}
            </div>
          )}
          <div style={{
            display: "flex", gap: 6, alignItems: "flex-end",
            background: "#F7F7F5", border: "1px solid var(--border)", borderRadius: 16, padding: "5px 5px 5px 8px",
          }}>
            <button title="选择成员派活"
              style={{ background: "none", border: "none", cursor: "pointer", fontSize: 17, color: "#777777", padding: "4px 6px", lineHeight: 1 }}
              onClick={() => onDraftChange(draft + "@")}>@</button>
            <textarea
              autoFocus
              value={draft}
              onChange={(e) => onDraftChange(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); void send(); } }}
              placeholder={mode === "chief" ? "把目标告诉总管，例如：完成用户管理 API 的分页改造" : mode === "discuss" ? "抛出一个话题，让成员依次发言" : "输入消息，@ 成员派活，Enter 发送"}
              rows={2}
              style={{ flex: 1, resize: "none", border: "none", outline: "none", padding: "6px 2px", font: "inherit", background: "transparent" }}
            />
            <Button onClick={() => void send()} disabled={!draft.trim() || sending} style={{ borderRadius: 999, paddingLeft: 18, paddingRight: 18 }}>{sending ? "发送中…" : "发送"}</Button>
          </div>
          {mentions.length > 0 && (
            <div className="small muted" style={{ marginTop: 6 }}>
              将派活给：{mentions.map(agentName).join("、")}
              <button className="muted" style={{ background: "none", border: "none", cursor: "pointer", marginLeft: 6 }} onClick={() => setMentions([])}>清除</button>
            </div>
          )}
        </div>
      </div>
    </Card>
  );
}

/* ----------------------------------------------------------- digest panel */

function SummaryPanel({ teamId, agentName }: { teamId: number; agentName: (id: number) => string }) {
  const summary = useAsync(() => api.channelSummary(teamId));
  const openTask = useAppStore((s) => s.openTask);
  // Paused while this page is hidden: the page stays mounted in the
  // keep-alive pool, so document.hidden alone would keep polling.
  const pageVisible = usePageVisible();
  const pageVisibleRef = useRef(pageVisible);
  pageVisibleRef.current = pageVisible;
  useEffect(() => {
    const iv = setInterval(() => { if (pageVisibleRef.current && !document.hidden) void summary.refresh(); }, 6000);
    return () => clearInterval(iv);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [teamId]);
  return (
    <Card className="p-4">
      <div style={{ fontWeight: 700, marginBottom: 10 }}>群聊纪要</div>
      <div className="small" style={{ fontWeight: 600, marginBottom: 6 }}>置顶消息</div>
      {(summary.data?.pinned ?? []).length === 0 && <div className="muted small">暂无置顶。把重要消息置顶，就沉淀成团队记忆。</div>}
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        {(summary.data?.pinned ?? []).map((m) => (
          <div key={m.message_id} className="small" style={{ borderLeft: "2px solid var(--border)", paddingLeft: 8 }}>
            <span className="muted">{m.sender_agent_id ? agentName(m.sender_agent_id) : "我"}：</span>{excerpt(humanizeCardContent(m.content), 60)}
          </div>
        ))}
      </div>
      <div className="small" style={{ fontWeight: 600, margin: "12px 0 6px" }}>最近的工作卡</div>
      {(summary.data?.cards ?? []).length === 0 && <div className="muted small">暂无任务卡与结果卡。任务完成后，任务卡与结果卡会回到群里。</div>}
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        {(summary.data?.cards ?? []).slice(-6).map((m) => (
          <button key={m.message_id} className="small" disabled={!m.meta.task_id}
            style={{ textAlign: "left", background: "none", border: "none", borderLeft: `2px solid ${m.message_type === "RESULT_CARD" ? "#238B61" : "#C58A32"}`, paddingLeft: 8, cursor: m.meta.task_id ? "pointer" : "default" }}
            onClick={() => m.meta.task_id && openTask(m.meta.task_id)}>
            {excerpt(humanizeCardContent(m.content), 60)}
          </button>
        ))}
      </div>
    </Card>
  );
}

/* -------------------------------------------------------------- main page */

export default function Teams() {
  const teams = useAsync(() => api.teams());
  const agents = useAsync(() => api.agents());
  const [activeId, setActiveId] = useState<number | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [members, setMembers] = useState<number[]>([]);
  const [leader, setLeader] = useState<number | "">("");
  const [previews, setPreviews] = useState<Record<number, ChannelMessage[]>>({});
  const [lastRead, setLastRead] = useState<Record<number, string>>(() => {
    try { return JSON.parse(localStorage.getItem("teamchat-last-read") || "{}"); } catch { return {}; }
  });

  // 各团队未发草稿（草稿 + @ 的人）：面板按团队重挂载，草稿放这里才不会切走就丢。
  const drafts = useRef(new Map<number, { draft: string; mentions: number[] }>());
  const saveDraft = useCallback((teamId: number, draft: string, mentions: number[]) => {
    drafts.current.set(teamId, { draft, mentions });
  }, []);

  const agentById = useMemo(() => new Map((agents.data ?? []).map((a) => [a.id, a])), [agents.data]);
  const agentName = (id: number) => agentById.get(id)?.name ?? "已删除的员工";
  const teamList = teams.data ?? [];
  // Default to the team with the most members (the working team with real
  // history) instead of the first row, which may be an empty demo shell.
  const active = teamList.find((t) => t.id === activeId)
    ?? teamList.reduce<Team | undefined>((best, t) => (!best || (t.members ?? []).length > (best.members ?? []).length ? t : best), undefined);

  // Latest-message previews + unread counts for the team list. The page
  // stays mounted in the keep-alive pool, so the 6s poll pauses while
  // hidden instead of fetching every team's messages forever.
  const pageVisible = usePageVisible();
  const pageVisibleRef = useRef(pageVisible);
  pageVisibleRef.current = pageVisible;
  const loadPreviewsRef = useRef<(() => void) | null>(null);
  // 切回本页立即刷新一次预览与未读数，不让用户对着旧数等满 6 秒。
  useEffect(() => { if (pageVisible) loadPreviewsRef.current?.(); }, [pageVisible]);
  useEffect(() => {
    if (!teamList.length) return;
    let stop = false;
    const load = async () => {
      const entries = await Promise.allSettled(teamList.map(async (t) => [t.id, await api.channelMessages(t.id)] as const));
      if (stop) return;
      const next: Record<number, ChannelMessage[]> = {};
      for (const e of entries) if (e.status === "fulfilled") next[e.value[0]] = e.value[1].slice(-40);
      setPreviews(next);
    };
    loadPreviewsRef.current = load;
    void load();
    const iv = setInterval(() => { if (pageVisibleRef.current && !document.hidden) void load(); }, 6000);
    return () => { stop = true; clearInterval(iv); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [teams.data]);

  function onSeen(teamId: number, lastMessageId: string) {
    setLastRead((prev) => {
      if (prev[teamId] === lastMessageId) return prev;
      const next = { ...prev, [teamId]: lastMessageId };
      try { localStorage.setItem("teamchat-last-read", JSON.stringify(next)); } catch { /* ignore */ }
      return next;
    });
  }

  function unreadOf(teamId: number) {
    const msgs = previews[teamId];
    if (!msgs?.length || teamId === active?.id) return 0;
    const lr = lastRead[teamId];
    if (!lr) return 0;
    const idx = msgs.findIndex((m) => m.message_id === lr);
    return idx === -1 ? msgs.length : msgs.length - idx - 1;
  }

  function teamStatusLine(t: Team) {
    let busy = 0, idle = 0, bad = 0;
    for (const m of t.members ?? []) {
      const a = agentById.get(m.agent_id);
      const s = a?.activity_status ?? "idle";
      if (s === "running" || s === "model_calling" || s === "tool_calling") busy++;
      else if (s === "failed" || s === "disabled") bad++;
      else idle++;
    }
    const parts = [];
    if (busy) parts.push(`在忙 ${busy}`);
    if (idle) parts.push(`空闲 ${idle}`);
    if (bad) parts.push(`异常 ${bad}`);
    return parts.join(" · ") || "暂无成员";
  }

  const goStudio = (agentId: number) => {
    useStudioStore.getState().selectAgent(agentId);
    useStudioStore.getState().requestFocus(agentId);
    useAppStore.getState().openStudio();
  };

  async function create() {
    if (!name.trim() || creating) return;
    setCreating(true);
    try {
      const created = (await api.createTeam({ name, description: "", leader_agent_id: leader === "" ? null : leader, member_agent_ids: members })) as { id?: number } | null;
      const createdName = name;
      setName(""); setMembers([]); setLeader(""); setShowForm(false);
      // 建完直接切进新团队：不然还停在旧团队的聊天里，用户得自己去
      // 左栏找新团队才能发第一条消息。
      if (created?.id) setActiveId(created.id);
      showToast("success", `已创建团队「${createdName}」，可以在群里发消息了。`);
      void teams.reload();
    } catch (e) {
      showToast("error", `创建失败：${e instanceof Error ? e.message : String(e)}，可以检查团队名称后重试。`);
    } finally { setCreating(false); }
  }

  return (
    <div>
      <div className="spread">
        <div>
          <h1 className="page-title">AI 团队</h1>
          <p className="page-desc">团队群聊协作中心：在群里 @成员直接派活、把目标交给总管，或发起一轮讨论。</p>
        </div>
        <Button onClick={() => setShowForm(true)}>+ 创建团队</Button>
      </div>

      <StateBox loading={teams.loading} error={teams.error} empty={teamList.length === 0}
        emptyText="还没有团队" emptyHint="把几位员工组成团队，就能在群聊里协作派活。" emptyActionLabel="创建第一支团队" onEmptyAction={() => setShowForm(true)} onRetry={() => void teams.reload()}>
        {/* 三栏整页定高、栏内各自滚动：聊天输入区钉在视口内，不再被压到页底。 */}
        <div style={{ display: "grid", gridTemplateColumns: "272px minmax(0, 1fr) 264px", gap: 14, alignItems: "stretch", marginTop: 14, height: "calc(100vh - 175px)", minHeight: 540 }}>
          {/* left: team list */}
          <div style={{ display: "flex", flexDirection: "column", gap: 8, minHeight: 0, overflowY: "auto", paddingRight: 2 }}>
            {teamList.map((t) => {
              const last = previews[t.id]?.[previews[t.id].length - 1];
              const unread = unreadOf(t.id);
              return (
                <Card key={t.id} className="p-3" style={{
                  cursor: "pointer", textAlign: "left",
                  borderColor: active?.id === t.id ? teamColor(t.id) : undefined,
                  background: active?.id === t.id ? "#FCFBF8" : undefined,
                }} onClick={() => setActiveId(t.id)}>
                  <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
                    <TeamTile team={t} size={38} />
                    <div style={{ minWidth: 0, flex: 1 }}>
                      <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                        <span style={{ fontWeight: 700, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{t.name}</span>
                        {unread > 0 && <Badge className="border-transparent" style={{ background: "#D9534F", color: "#fff" }}>{unread}</Badge>}
                      </div>
                      <div className="small muted">{(t.members ?? []).length} 位成员 · {teamStatusLine(t)}</div>
                    </div>
                    <MoreMenu items={[
                      { label: "删除团队", danger: true, confirmText: `删除团队「${t.name}」？成员与员工本身不会被删除。`, onClick: () => void api.deleteTeam(t.id).then(() => teams.reload()).catch((e) => showToast("error", `删除团队失败：${e instanceof Error ? e.message : String(e)}，可以稍后再试。`)) },
                    ]} />
                  </div>
                  {last && <div className="small muted" style={{ marginTop: 8, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                    {last.sender_agent_id ? agentName(last.sender_agent_id) : last.message_type === "CHANNEL_MESSAGE" ? "我" : "系统"}：{excerpt(humanizeCardContent(last.content), 30)}
                  </div>}
                </Card>
              );
            })}
          </div>

          {/* center: chat */}
          {active ? <ChatPanel key={active.id} team={active} agents={agents.data ?? []} onSeen={onSeen}
            savedDraft={drafts.current.get(active.id)}
            onDraftSave={(d, m) => saveDraft(active.id, d, m)} /> : <Card className="p-4"><span className="muted small">选择一个团队</span></Card>}

          {/* right: members + digest */}
          <div style={{ display: "flex", flexDirection: "column", gap: 14, minHeight: 0, overflowY: "auto", paddingRight: 2 }}>
            <Card className="p-4">
              <div style={{ fontWeight: 700, marginBottom: 10 }}>成员</div>
              <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                {(active?.members ?? []).map((m) => {
                  const a = agentById.get(m.agent_id);
                  if (!a) return null;
                  return (
                    <button key={m.agent_id} onClick={() => goStudio(a.id)} title="在 AI 工作室中定位这位成员"
                      style={{ display: "flex", gap: 9, alignItems: "center", background: "none", border: "none", cursor: "pointer", textAlign: "left", padding: 0 }}>
                      <Avatar name={a.name} size={30} avatar={a.avatar} appearance={a.appearance} />
                      <div style={{ minWidth: 0, flex: 1 }}>
                        <div className="small" style={{ fontWeight: 600, display: "flex", gap: 6, alignItems: "center" }}>
                          {a.name}
                          {(m.is_leader || active?.leader_agent_id === a.id) && <Badge variant="secondary" className="border-transparent bg-success-tint text-success hover:bg-success-tint">负责人</Badge>}
                        </div>
                        <div className="small muted" style={{ display: "flex", gap: 5, alignItems: "center" }}>
                          <AgentStatusDot runtimeStatus={a.runtime_status} status={a.status} activityStatus={a.activity_status} />
                          {activityLabel(a.activity_status)}
                        </div>
                      </div>
                    </button>
                  );
                })}
                {(active?.members ?? []).length === 0 && <span className="muted small">暂无成员。</span>}
              </div>
              <div className="small muted" style={{ marginTop: 10 }}>点成员可跳到 AI 工作室看他在哪儿干活。</div>
            </Card>

            {active ? <SummaryPanel key={active.id} teamId={active.id} agentName={agentName} /> : null}
          </div>
        </div>
      </StateBox>

      <AnimatedModal open={showForm} onClose={() => setShowForm(false)} labelledBy="create-team-title">
        <h3 id="create-team-title" style={{ marginTop: 0 }}>创建团队</h3>
        <Label className="block text-xs text-muted-foreground mt-3 mb-1.5" htmlFor="create-team-name">团队名称</Label>
        <Input id="create-team-name" autoFocus value={name} onChange={(e) => setName(e.target.value)} />
        <Label className="block text-xs text-muted-foreground mt-3 mb-1.5">负责人</Label>
        <Select value={leader === "" ? "none" : String(leader)} onValueChange={(v) => setLeader(v === "none" ? "" : Number(v))}>
          <SelectTrigger>
            <SelectValue placeholder="暂不指定" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="none">暂不指定</SelectItem>
            {(agents.data ?? []).map((a) => <SelectItem key={a.id} value={String(a.id)}>{a.name}</SelectItem>)}
          </SelectContent>
        </Select>
        <Label className="block text-xs text-muted-foreground mt-3 mb-1.5">选择成员</Label>
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {(agents.data ?? []).map((a) => (
            <label key={a.id} className="small" style={{ display: "flex", gap: 8, alignItems: "center", cursor: "pointer" }}>
              <input type="checkbox" checked={members.includes(a.id)} onChange={(e) => setMembers(e.target.checked ? [...members, a.id] : members.filter((x) => x !== a.id))} />
              <Avatar name={a.name} size={26} avatar={a.avatar} appearance={a.appearance} />{a.name}
            </label>
          ))}
          {(agents.data?.length ?? 0) === 0 && <span className="muted small">还没有员工可加入，请先去创建员工。</span>}
        </div>
        <div className="row" style={{ marginTop: 16 }}>
          <Button disabled={!name.trim() || creating} onClick={() => void create()}>{creating ? "创建中…" : "创建团队"}</Button>
          <Button variant="secondary" onClick={() => setShowForm(false)}>取消</Button>
        </div>
      </AnimatedModal>
    </div>
  );
}
