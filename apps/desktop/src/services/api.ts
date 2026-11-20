// Thin client for the Agent Core API. In Electron the renderer talks to the
// local Python service over HTTP; API keys never pass through the renderer
// except when the user types them into the provider form (write-only).
// Dev (vite) uses relative paths via the dev-server proxy. The packaged
// Electron app loads dist/index.html over file://, where relative paths
// cannot reach the backend — fall back to the local Agent Core address
// (the same one the preload bridge reports and the CSP allows).
const BASE = (import.meta.env.VITE_API_BASE as string | undefined)
  ?? (window.location.protocol === "file:" ? "http://127.0.0.1:8000" : "");

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const body = (await res.json()) as { detail?: string };
      if (body.detail) detail = body.detail;
    } catch { /* keep status text */ }
    throw new Error(detail);
  }
  return (await res.json()) as T;
}

async function reqForm<T>(path: string, form: FormData): Promise<T> {
  const res = await fetch(`${BASE}${path}`, { method: "POST", body: form });
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const body = (await res.json()) as { detail?: string };
      if (body.detail) detail = body.detail;
    } catch { /* keep status text */ }
    throw new Error(detail);
  }
  return (await res.json()) as T;
}

export const api = {
  health: () => req<{ status: string; service?: string; version?: string; data_dir?: string; db_file?: string }>("/health"),
  stats: () => req<import("../types").Stats>("/api/stats"),
  providers: () => req<import("../types").Provider[]>("/api/providers"),
  createProvider: (b: unknown) => req<import("../types").Provider>("/api/providers", { method: "POST", body: JSON.stringify(b) }),
  updateProvider: (id: number, b: unknown) => req(`/api/providers/${id}`, { method: "PUT", body: JSON.stringify(b) }),
  deleteProvider: (id: number) => req(`/api/providers/${id}`, { method: "DELETE" }),
  fetchModels: (id: number) => req<{ models: string[] }>(`/api/providers/${id}/fetch-models`, { method: "POST" }),
  testProvider: (id: number) => req<{ ok: boolean; error?: string; model_count?: number }>(`/api/providers/${id}/test`, { method: "POST" }),
  providerModels: (id: number, models: string[]) => req<import("../types").Provider>(`/api/providers/${id}/models`, { method: "PUT", body: JSON.stringify({ models }) }),
  detectProvider: (id: number, model: string) => req<{ ok: boolean; model?: string; latency_ms?: number; reply_preview?: string; error?: string }>(`/api/providers/${id}/detect`, { method: "POST", body: JSON.stringify({ model }) }),
  activity: () => req<Array<{ at: string | null; kind: string; agent_id: number | null; agent_name: string | null; task_id: number | null; task_title: string | null; text: string }>>("/api/activity?limit=30"),
  systemSettings: () => req<{ proxy_mode: string; proxy_url: string; default_provider_id: number | null; default_model: string; data_dir: string; db_file: string }>("/api/system/settings"),
  updateSystemSettings: (b: unknown) => req("/api/system/settings", { method: "PUT", body: JSON.stringify(b) }),
  systemUsage: () => req<{ by_agent: Array<{ agent_id: number; name: string; tokens: number; calls: number }>; by_task: Array<{ task_id: number; title: string; tokens: number; calls: number }>; by_provider: Array<{ provider: string; tokens: number; calls: number }>; note: string }>("/api/system/usage"),
  systemRuntimes: () => req<{ runtimes: Array<{ key: string; label: string; available: boolean; path: string; version: string }> }>("/api/system/runtimes"),
  backupUrl: `${BASE}/api/system/backup`,
  restoreBackup: (file: File) => { const f = new FormData(); f.append("file", file); return reqForm<{ ok: boolean; restart_required: boolean; message: string }>("/api/system/restore", f); },
  clearData: () => req<{ cleared: Record<string, number> }>("/api/system/clear-data", { method: "POST", body: JSON.stringify({ confirm: "确认清除" }) }),
  importSkills: (file: File) => { const f = new FormData(); f.append("file", file); return reqForm<{ imported: Array<{ id: number; name: string; files: number }>; skipped: Array<{ where: string; reason: string }> }>("/api/skills/import", f); },
  knowledgeDocuments: () => req<Array<{ id: number; title: string; source: string; chunks: number; chars: number; created_at: string | null; preview: string }>>("/api/knowledge/documents"),
  knowledgeImport: (files: File[]) => { const f = new FormData(); files.forEach((x) => f.append("files", x)); return reqForm<{ results: Array<{ filename: string; status: string; chunks?: number; error?: string }> }>("/api/knowledge/import", f); },
  knowledgeImportText: (title: string, text: string) => req<{ item_id: number; chunks: number }>("/api/knowledge/import-text", { method: "POST", body: JSON.stringify({ title, text }) }),
  knowledgeImportWorkspace: (path: string) => req<{ item_id: number; chunks: number }>("/api/knowledge/import-workspace", { method: "POST", body: JSON.stringify({ path }) }),
  knowledgeSearch: (query: string) => req<{ method: string; results: Array<{ item_id: number; title?: string; score: number; keyword_score: number; tfidf_score: number; chunk: string }> }>("/api/knowledge/search", { method: "POST", body: JSON.stringify({ query, top_k: 5 }) }),
  mcpCatalog: () => req<{ catalog: Array<{ key: string; name: string; description: string; command: string; args: string[]; runtime: string; runtime_available: boolean; env: Array<{ key: string; label: string; secret: boolean }>; args_note?: string; extra_arg_placeholder?: string }> }>("/api/mcp/catalog"),
  mcpImportConfig: (text: string) => req<{ results: Array<{ name: string; ok: boolean; id?: number; reason?: string }> }>("/api/mcp/import-config", { method: "POST", body: JSON.stringify({ text }) }),
  mcpConnect: (id: number) => req<{ server_id: number; server_info: { name: string; version: string }; tools: Array<{ name: string; description: string }> }>(`/api/mcp/servers/${id}/connect`, { method: "POST" }),
  mcpTools: (id: number) => req<{ tools: Array<{ name: string; description: string }> }>(`/api/mcp/servers/${id}/tools`),
  mcpPrompts: (id: number) => req<{ prompts: Array<{ name: string; description?: string }> }>(`/api/mcp/servers/${id}/prompts`),
  mcpResources: (id: number) => req<{ resources: Array<{ uri: string; name?: string }> }>(`/api/mcp/servers/${id}/resources`),
  mcpLogs: (id: number) => req<{ status: string; stderr_tail: string; error: string | null }>(`/api/mcp/servers/${id}/logs`),
  updateMcp: (id: number, b: unknown) => req(`/api/mcp-servers/${id}`, { method: "PUT", body: JSON.stringify(b) }),
  browserStatus: () => req<{ installed: boolean; binary: string; desktop_running: boolean; running: boolean; page: { url?: string; title?: string }; profile_dir: string; config: { homepage: string; search_engine: string; search_engine_label: string; download_dir: string; persist_profile: boolean; profile_per_agent: boolean } }>("/api/browser/status"),
  browserEngines: () => req<Array<{ key: string; label: string }>>("/api/browser/search-engines"),
  browserUpdateConfig: (b: unknown) => req("/api/browser/config", { method: "PUT", body: JSON.stringify(b) }),
  browserOpen: (url: string) => req<{ url: string; title: string }>("/api/browser/open", { method: "POST", body: JSON.stringify({ url }) }),
  browserClearData: () => req<{ cleared_profiles: number }>("/api/browser/clear-data", { method: "POST" }),
  agents: () => req<import("../types").Agent[]>("/api/agents"),
  createAgent: (b: unknown) => req("/api/agents", { method: "POST", body: JSON.stringify(b) }),
  deleteAgent: (id: number) => req(`/api/agents/${id}`, { method: "DELETE" }),
  copyAgent: (id: number) => req(`/api/agents/${id}/copy`, { method: "POST" }),
  toggleAgent: (id: number) => req(`/api/agents/${id}/toggle`, { method: "POST" }),
  updateAgentTools: (id: number, tools: string[]) => req(`/api/agents/${id}/tools`, { method: "PUT", body: JSON.stringify({ tools }) }),
  updateAgent: (id: number, b: unknown) => req(`/api/agents/${id}`, { method: "PUT", body: JSON.stringify(b) }),
  agentDetail: (id: number) => req<import("../types").AgentDetail>(`/api/agents/${id}/detail`),
  teams: () => req<import("../types").Team[]>("/api/teams"),
  createTeam: (b: unknown) => req("/api/teams", { method: "POST", body: JSON.stringify(b) }),
  deleteTeam: (id: number) => req(`/api/teams/${id}`, { method: "DELETE" }),
  channelMessages: (teamId: number) => req<import("../types").ChannelMessage[]>(`/api/teams/${teamId}/channel/messages`),
  postChannelMessage: (teamId: number, content: string, opts?: { mentions?: number[]; mode?: string; replyTo?: string | null }) => req(`/api/teams/${teamId}/channel/messages`, { method: "POST", body: JSON.stringify({ content, mentions: opts?.mentions, mode: opts?.mode, reply_to: opts?.replyTo ?? null }) }),
  pinChannelMessage: (teamId: number, messageId: string) => req<import("../types").ChannelMessage>(`/api/teams/${teamId}/channel/messages/${messageId}/pin`, { method: "POST" }),
  channelSummary: (teamId: number) => req<{ pinned: import("../types").ChannelMessage[]; cards: import("../types").ChannelMessage[] }>(`/api/teams/${teamId}/channel/summary`),
  tasks: () => req<import("../types").Task[]>("/api/tasks"),
  createTask: (b: unknown) => req("/api/tasks", { method: "POST", body: JSON.stringify(b) }),
  executeTask: (id: number) => req<import("../types").Task>(`/api/tasks/${id}/execute`, { method: "POST" }),
  taskPlan: (id: number) => req<import("../types").TaskPlan>(`/api/tasks/${id}/plan`),
  assignNode: (taskId: number, nodeId: number, agentId: number | null) => req<import("../types").TaskPlan>(`/api/tasks/${taskId}/nodes/${nodeId}/assign`, { method: "PUT", body: JSON.stringify({ agent_id: agentId }) }),
  cancelTask: (id: number) => req(`/api/tasks/${id}/cancel`, { method: "POST" }),
  retryTask: (id: number) => req(`/api/tasks/${id}/retry`, { method: "POST" }),
  taskMessages: (id: number) => req<Array<{ message_id: string; message_type: string; content: string; sender_agent_id: number | null; receiver_agent_id: number | null }>>(`/api/tasks/${id}/messages`),
  decisions: (taskId?: number) => req<import("../types").Decision[]>(`/api/scheduler/decisions${taskId ? `?task_id=${taskId}` : ""}`),
  verifications: (taskId?: number) => req<import("../types").Verification[]>(`/api/verifications${taskId ? `?task_id=${taskId}` : ""}`),
  chiefPlan: (goal: string) => req<{ nodes: Array<{ name: string; node_type: string; required_capabilities: string[] }> }>("/api/chief/plan", { method: "POST", body: JSON.stringify({ goal }) }),
  chiefMission: (goal: string) => req<{ mission_id: number; status: string; report: unknown }>("/api/chief/missions", { method: "POST", body: JSON.stringify({ goal }) }),
  chiefReport: (id: number) => req<Record<string, unknown>>(`/api/chief/missions/${id}/report`),
  artifactVerifications: () => req<Array<{ id: number; artifact_path: string; artifact_version: number | null; passed: boolean; status: string; test_run_id: string | null }>>("/api/artifact-verifications"),
  resourceLeases: () => req<Array<{ id: number; resource_type: string; resource_key: string; holder_agent_id: number | null; status: string }>>("/api/resources/leases"),
  proactiveSuggestions: () => req<Array<{ id: number; event_type: string; level: string; title: string; status: string }>>("/api/proactive/suggestions"),
  proactiveScan: () => req<{ created: unknown[] }>("/api/proactive/scan", { method: "POST" }),
  approveSuggestion: (id: number) => req(`/api/proactive/suggestions/${id}/approve`, { method: "POST" }),
  rejectSuggestion: (id: number) => req(`/api/proactive/suggestions/${id}/reject`, { method: "POST" }),
  skills: () => req<import("../types").Skill[]>("/api/skills"),
  createSkill: (b: unknown) => req("/api/skills", { method: "POST", body: JSON.stringify(b) }),
  deleteSkill: (id: number) => req(`/api/skills/${id}`, { method: "DELETE" }),
  mcpServers: () => req<import("../types").McpServer[]>("/api/mcp-servers"),
  createMcp: (b: unknown) => req("/api/mcp-servers", { method: "POST", body: JSON.stringify(b) }),
  deleteMcp: (id: number) => req(`/api/mcp-servers/${id}`, { method: "DELETE" }),
  testMcp: (id: number) => req<{ ok: boolean; error?: string; status_code?: number }>(`/api/mcp-servers/${id}/test`, { method: "POST" }),
  memories: () => req<import("../types").MemoryItem[]>("/api/memories"),
  createMemory: (b: unknown) => req("/api/memories", { method: "POST", body: JSON.stringify(b) }),
  deleteMemory: (id: number) => req(`/api/memories/${id}`, { method: "DELETE" }),
  workflows: () => req<import("../types").Workflow[]>("/api/workflows"),
  saveWorkflow: (b: unknown) => req<import("../types").Workflow>("/api/workflows", { method: "POST", body: JSON.stringify(b) }),
  executeWorkflow: (id: number, approvals?: Record<string, { approved: boolean; note?: string }>, maxRetries?: number) => req<import("../types").WorkflowRun>(`/api/workflows/${id}/execute`, { method: "POST", body: JSON.stringify({ approvals: approvals ?? {}, max_retries: maxRetries ?? 2 }) }),
  updateWorkflow: (id: number, b: unknown) => req<import("../types").Workflow>(`/api/workflows/${id}`, { method: "PUT", body: JSON.stringify(b) }),
  deleteWorkflow: (id: number) => req(`/api/workflows/${id}`, { method: "DELETE" }),
  workflowRuns: (workflowId?: number) => req<import("../types").WorkflowRun[]>(`/api/workflows/runs/list${workflowId ? `?workflow_id=${workflowId}` : ""}`),
  tools: () => req<Array<{ id: string; name: string; description: string; high_risk: boolean; requires_approval: boolean }>>("/api/tools"),
  evaluations: () => req<Array<{ id: number; name: string; mode: string; status: string; metrics: { success_rate?: number; avg_latency?: number; recovery_rate?: number | null; error?: string; run_at?: string } | null }>>("/api/evaluations"),
  createEvaluation: (b: unknown) => req("/api/evaluations", { method: "POST", body: JSON.stringify(b) }),
  runEvaluation: (id: number) => req<{ status: string; metrics: { success_rate?: number; error?: string } | null }>(`/api/evaluations/${id}/run`, { method: "POST" }),
  deleteEvaluation: (id: number) => req(`/api/evaluations/${id}`, { method: "DELETE" }),
  wsStatus: () => req<import("../types").WsStatus>("/api/workspace/status"),
  wsSandbox: () => req<import("../types").SandboxInfo>("/api/workspace/sandbox"),
  wsSandboxSelect: (distro: string, version: string) => req<import("../types").SandboxInfo>("/api/workspace/sandbox", { method: "PUT", body: JSON.stringify({ distro, version }) }),
  wsSandboxInstall: (distro: string, version: string) => req<import("../types").SandboxInstall>("/api/workspace/sandbox/install", { method: "POST", body: JSON.stringify({ distro, version }) }),
  wsDeps: () => req<import("../types").DepsStatus>("/api/workspace/sandbox/dependencies"),
  wsDepsInstall: () => req<import("../types").DepsJob>("/api/workspace/sandbox/dependencies/install", { method: "POST", body: JSON.stringify({}) }),
  wsSessions: () => req<import("../types").WsSession[]>("/api/workspace/sessions"),
  wsCreateSession: (agentId: number) => req<import("../types").WsSession>("/api/workspace/sessions", { method: "POST", body: JSON.stringify({ agent_id: agentId }) }),
  wsCloseSession: (id: number) => req(`/api/workspace/sessions/${id}`, { method: "DELETE" }),
  wsExec: (sessionId: number, command: string, approved: boolean) => req<import("../types").WsExecResult>(`/api/workspace/sessions/${sessionId}/exec`, { method: "POST", body: JSON.stringify({ command, approved }) }),
  wsSessionLogs: (sessionId: number) => req<import("../types").WsCommandLog[]>(`/api/workspace/sessions/${sessionId}/logs?limit=30`),
  wsCommands: () => req<import("../types").WsCommandLog[]>("/api/workspace/commands?limit=30"),
  wsEvents: () => req<import("../types").WsEvent[]>("/api/workspace/events?limit=40"),
  wsFiles: () => req<import("../types").WsFile[]>("/api/workspace/files"),
  wsFileChanges: (status?: string) => req<import("../types").FileChange[]>(`/api/workspace/file-changes${status ? `?status=${status}` : ""}`),
  wsAcceptFileChange: (id: number) => req<{ change: import("../types").FileChange; write: { version: number } }>(`/api/workspace/file-changes/${id}/accept`, { method: "POST" }),
  wsRejectFileChange: (id: number) => req<{ change: import("../types").FileChange }>(`/api/workspace/file-changes/${id}/reject`, { method: "POST" }),
  wsFileContent: (path: string) => req<{ path: string; content: string; version: number | null }>(`/api/workspace/files/content?path=${encodeURIComponent(path)}`),
  wsDesktop: () => req<{ desktop: import("../types").WsDesktopStatus; lease: import("../types").WsLease }>("/api/workspace/desktop"),
  wsDesktopStart: () => req<{ started: boolean; missing?: string[]; error?: string }>("/api/workspace/desktop/start", { method: "POST" }),
  wsDesktopStop: () => req("/api/workspace/desktop/stop", { method: "POST" }),
  wsLeaseAcquire: (agentId: number) => req<{ state: string; position?: number }>("/api/workspace/desktop/lease/acquire", { method: "POST", body: JSON.stringify({ agent_id: agentId }) }),
  wsLeaseRenew: (agentId: number) => req("/api/workspace/desktop/lease/renew", { method: "POST", body: JSON.stringify({ agent_id: agentId }) }),
  wsLeaseRelease: (agentId: number) => req("/api/workspace/desktop/lease/release", { method: "POST", body: JSON.stringify({ agent_id: agentId, holder_type: "agent" }) }),
  wsLeaseForceTakeover: () => req("/api/workspace/desktop/lease/force-takeover", { method: "POST" }),
  wsLeaseUserRelease: () => req("/api/workspace/desktop/lease/release", { method: "POST", body: JSON.stringify({ holder_type: "user" }) }),
  wsSeedDemo: () => req<{ agents: Array<{ id: number; name: string; role: string }>; team_id: number }>("/api/workspace/seed-demo", { method: "POST" }),
};
