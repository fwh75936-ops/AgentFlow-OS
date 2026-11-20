import type { Agent, Decision, Stats, Task, Team, Verification, WsCommandLog, WsEvent, WsStatus } from "../../../types";
import type { AgentAppearance } from "../../../lib/appearance";

/** Studio visual states. Every one of these is DERIVED from real backend
 * data (agent activity_status, task node status/type, retry counters,
 * verification records, desktop lease) — never invented by a timer.
 * See events/AgentEventAdapter.ts for the exact mapping. */
export type StudioState =
  | "idle" | "planning" | "thinking" | "working" | "tool_running"
  | "waiting" | "verifying" | "failed" | "retrying" | "completed" | "disabled";

export interface AgentVisual {
  agentId: number;
  name: string;
  role: string;
  state: StudioState;
  /** Current real task/node this agent is bound to (null when idle). */
  taskId: number | null;
  taskTitle: string | null;
  nodeName: string | null;
  /** True when this agent currently holds the shared desktop lease. */
  holdsDesktop: boolean;
  /** Team leader or the AI总管 agent (drives handoff walks). */
  isChief: boolean;
  /** Work zone the agent's desk belongs to. Product roles sit in the
   * meeting room's workstation corner (next to meeting + chief). */
  zone: "chief" | "dev" | "test" | "data" | "product";
  /** The agent's appearance contract (gender/skin/hair/outfit), consumed
   * verbatim from the agents API — the studio renders the same four
   * fields the employee page edits. Never derived from the agent id. */
  appearance: AgentAppearance;
}

export interface AssignmentEvent {
  decisionId: number;
  taskId: number;
  fromAgentId: number | null; // team leader / chief, when one exists
  toAgentId: number;
  nodeName: string;
}

export type TimelineTone = "info" | "success" | "error" | "warning" | "neutral";

export interface TimelineEvent {
  id: string;
  at: string; // ISO or HH:MM:SS display string from real record timestamps
  kind: string; // task.assigned | task.started | verification.failed | ...
  text: string;
  tone: TimelineTone;
  agentId: number | null;
  taskId: number | null;
}

export interface StudioSnapshot {
  agents: Agent[];
  tasks: Task[];
  teams: Team[];
  stats: Stats | null;
  decisions: Decision[];
  verifications: Verification[];
  wsStatus: WsStatus | null;
  wsEvents: WsEvent[];
  wsCommands: WsCommandLog[];
}

export interface StudioCallbacks {
  onSelectAgent: (agentId: number) => void;
  onOpenAgentPage: (agentId: number) => void;
  onZoneClick: (zone: "board" | "test" | "computer" | "meeting") => void;
  /** Pointer hover over a character (null when the pointer leaves). Drives
   * the DOM name label — names are never drawn inside the scene. */
  onHoverAgent: (agentId: number | null) => void;
}
