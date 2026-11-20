export interface Provider { id: number; name: string; base_url: string; format: string; enabled: boolean; has_api_key: boolean; models?: string[]; }
export interface AgentDuties {
  responsible_for: string[]; not_responsible_for: string[]; deliverables: string[];
}
import type { AgentAppearance } from "../lib/appearance";
export type { AgentAppearance };

export interface Agent {
  id: number; name: string; description: string; role: string; system_prompt: string;
  responsibilities?: AgentDuties; avatar?: string; appearance?: AgentAppearance;
  model_provider_id: number | null; model_id: string | null; tools: string[]; skills?: Array<number | string>;
  capability_tags: string[]; capability_scores: Record<string, number>;
  status: string; runtime_status: string; activity_status?: string; historical_success_rate: number | null;
  average_latency: number; average_cost: number; current_load: number; execution_count: number;
  success_count?: number;
}
export interface AgentHistoryNode {
  node_id: number; name: string; node_type: string; status: string;
  started_at: string | null; finished_at: string | null; duration_seconds: number | null;
}
export interface AgentHistoryItem {
  task_id: number; title: string; task_status: string; nodes: AgentHistoryNode[];
  duration_seconds: number | null;
  verification: { passed: boolean; score: number; verification_type: string } | null;
}
export interface AgentDetailData {
  teams: Array<{ id: number; name: string; role_in_team: string; is_leader: boolean }>;
  current_tasks: Array<{ task_id: number; task_title: string; node_id: number; node_name: string; node_type: string; started_at: string | null }>;
  history: AgentHistoryItem[];
  skills_resolved: Array<{ id: number | null; name: string }>;
  performance: {
    execution_count: number; success_count: number; historical_success_rate: number | null;
    completed_nodes: number; failed_nodes: number; node_success_rate: number | null;
    average_duration_seconds: number | null; average_latency: number;
    total_tokens: number | null; current_load: number;
  };
}
export type AgentDetail = Agent & { detail: AgentDetailData };
export interface TaskNode {
  id: number; name: string; node_type: string; required_capabilities: string[];
  dependencies: number[]; status: string; display_status?: string; assigned_agent_id: number | null;
  output_data: string | null; error_type: string | null; error_message: string | null;
  started_at?: string | null; finished_at?: string | null;
}
export interface Task {
  id: number; title: string; description: string; status: string; scheduler_mode: string;
  priority: number; result: string | null; error_type: string | null; error_message: string | null;
  token_usage: number; retry_count: number; created_at: string | null; nodes: TaskNode[];
}
export interface TeamMember { agent_id: number; role_in_team: string; is_leader: boolean; }
export interface Team { id: number; name: string; description: string; leader_agent_id: number | null; members: TeamMember[]; }
export interface ChannelMessage {
  message_id: string; team_id?: number; task_id: number | null;
  message_type: string; content: string;
  sender_agent_id: number | null; receiver_agent_id: number | null;
  meta: {
    mentions?: number[]; mode?: string; reply_to?: string | null;
    pinned?: boolean; kind?: string; task_id?: number; agent_id?: number;
    status?: string; token_usage?: number; retry_count?: number;
    mission_id?: number; source_message_id?: string;
    nodes?: Array<{ id: number; name: string; status: string; assigned_agent_id: number | null }>;
  };
  timestamp: string | null;
}
export interface Skill { id: number; name: string; description: string; instructions: string; required_tools: string[]; version: string; enabled: boolean; trusted: boolean; source?: string; files?: Array<{ path: string; size: number }>; }
export interface McpServer { id: number; name: string; transport: string; command_or_url: string; args?: string[]; env?: Record<string, string>; enabled: boolean; tools: string[]; }
export interface Decision { id: number; task_id: number; node_id: number; mode: string; candidates: Array<{ agent_id: number; eligible: boolean; filter_reason: string; total: number | null; components: Record<string, number> }>; selected_agent_id: number | null; reason: string; created_at: string | null; }
export interface Verification { verification_id: string; task_id: number; verification_type: string; passed: boolean; score: number; evidence: string; errors: string[]; timestamp: string | null; }
export interface Stats { agent_count: number; team_count: number; task_count: number; running_tasks: number; success_rate: number | null; total_tokens: number; executed_steps?: number; avg_tool_latency_ms?: number | null; }
export interface PlanCandidate { agent_id: number; eligible: boolean; filter_reason: string; total: number | null; components: Record<string, number>; }
export interface DutyMatch { capabilities: string[]; tools: string[]; deliverables: string[]; }
export interface PlanNode {
  node_id: number; name: string; node_type: string; required_capabilities: string[];
  required_tools: string[]; dependencies: number[]; status: string;
  pinned_agent_id: number | null; selected_agent_id: number | null;
  candidates: PlanCandidate[]; reason: string; duty_match?: DutyMatch;
}
export interface TaskPlan { task_id: number; status: string; mode: string; nodes: PlanNode[]; }
export interface FileChange {
  id: number; path: string; agent_id: number | null; task_id: number | null;
  base_version: number | null; original_content: string | null; new_content: string;
  diff_summary: { added_lines?: number; removed_lines?: number };
  diff_text: string; status: string; created_at: string | null; decided_at: string | null;
}
export interface MemoryItem { id: number; agent_id: number | null; scope: string; content: string; }
export interface Workflow { id: number; name: string; graph: { nodes: unknown[]; edges: unknown[] }; }
export interface WorkflowRun {
  id: number; workflow_id: number; task_id: number; status: string;
  node_map: Record<string, number>; error: string | null;
  created_at: string | null; finished_at: string | null;
}
export interface WsDesktopStatus {
  state: string; display: string; desktop_environment: string; vnc_server: string;
  vnc_port: number; vnc_localhost_only: boolean; novnc_port: number;
  novnc_url: string | null; vnc_password?: string; websockify_running: boolean;
  components: Record<string, boolean>; missing: string[];
}
export interface WsLease {
  holder_type: string | null; holder_agent_id: number | null; lease_id: number | null;
  acquired_at: string | null; expires_at: string | null;
  queue: Array<{ queue_id: number; agent_id: number; requested_at: string | null }>;
}
export interface WsStatus {
  env_id: string; name: string; os: string; runtime_kind: string; hostname: string;
  process_user: string; is_root: boolean; logical_root: string; shared_root: string;
  disk_used_bytes: number; limits: Record<string, number>; active_sessions: number;
  desktop: WsDesktopStatus; desktop_lease: WsLease;
  security: { docker_socket_mounted: boolean; vnc_bind: string; high_risk_commands_require_approval: boolean };
}
export interface WsSession {
  id: number; env_id: string; agent_id: number; task_id: number | null;
  cwd: string; status: string; created_at: string | null; last_active_at: string | null;
}
export interface WsCommandLog {
  id: number; session_id: number | null; agent_id: number | null; task_id: number | null;
  command: string; cwd: string; exit_code: number | null; stdout: string; stderr: string;
  status: string; started_at: string | null; finished_at: string | null; duration_ms: number;
}
export interface WsEvent {
  id: number; event_type: string; agent_id: number | null; task_id: number | null;
  path: string | null; detail: string; created_at: string | null;
}
export interface WsFile {
  path: string; name: string; is_dir: boolean; size: number;
  version: number | null; content_hash: string | null; last_modified_by_agent_id: number | null;
}
export interface WsExecResult {
  log_id: number; blocked: boolean; exit_code: number | null;
  stdout: string; stderr: string; status: string; cwd: string; duration_ms?: number;
}
export interface SandboxVersion {
  id: string; label: string; maintenance_until: string;
  base_image: string; image: string;
  install_status: "not_installed" | "installing" | "installed" | "failed";
  install_method: "" | "docker" | "host-native";
  install_log: string; selected: boolean;
}
export interface SandboxDistro {
  id: string; name: string; description: string; recommended: boolean;
  dockerfile: string; selected: boolean; versions: SandboxVersion[];
}
export interface SandboxInfo {
  distros: SandboxDistro[]; selected_distro: string | null; selected_version: string | null;
  selected_method: string;
  isolation_mode: "container" | "host" | "host_sandbox";
  docker: { cli_available: boolean; daemon_reachable: boolean };
  note: string; message?: string;
}
export interface SandboxInstall {
  distro: string; version: string; status: string; method: string; log: string; updated_at: string | null;
}
export interface DepsPackage {
  key: string; label: string; packages: string[]; status: string; detail: string;
}
export interface DepsJob {
  id: number; status: string; package_manager: string; initiated_by: string;
  results: Array<{ key: string; label: string; packages: string[]; status: string; detail: string }>;
  log: string; created_at: string | null; finished_at: string | null;
}
export interface DepsStatus {
  family: "apt" | "dnf" | null; packages: DepsPackage[]; job: DepsJob | null;
  sandbox: { distro: string; version: string; label: string } | null;
}
