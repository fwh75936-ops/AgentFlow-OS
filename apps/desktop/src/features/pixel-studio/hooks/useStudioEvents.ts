import { useEffect, useRef, useState } from "react";
import { api } from "../../../services/api";
import { deriveVisuals, diffSnapshots } from "../events/AgentEventAdapter";
import type { AgentVisual, AssignmentEvent, StudioSnapshot, TimelineEvent } from "../types/studio";

export interface StudioHandlers {
  onEvents: (events: TimelineEvent[]) => void;
  onAssignments: (assignments: AssignmentEvent[]) => void;
}

const POLL_MS = 2500;

/** Data engine for the studio: polls the real REST APIs (agents, tasks,
 * teams, stats, scheduler decisions, verifications, shared-computer
 * status/events/commands), diffs consecutive snapshots into timeline
 * events + assignment animations, and subscribes to the existing
 * per-task WebSocket purely as a "refresh sooner" signal. No state is
 * ever fabricated locally — if the backend is down, the last real
 * snapshot stays and an error is reported. */
export function useStudioEvents(visible: boolean, handlers: React.MutableRefObject<StudioHandlers>) {
  const [snapshot, setSnapshot] = useState<StudioSnapshot | null>(null);
  const [visuals, setVisuals] = useState<AgentVisual[]>([]);
  const [error, setError] = useState<string | null>(null);
  const prevRef = useRef<StudioSnapshot | null>(null);
  const flashRef = useRef<Map<number, number>>(new Map());
  const refreshRef = useRef<() => void>(() => {});
  const runningTaskIdRef = useRef<number | null>(null);
  const [runningTaskId, setRunningTaskId] = useState<number | null>(null);

  useEffect(() => {
    if (!visible) return;
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | null = null;

    const fetchSnapshot = async (): Promise<StudioSnapshot> => {
      const prev = prevRef.current;
      const [agents, tasks, teams, stats, decisions, vers, wsStatus, wsEvents, wsCommands] =
        await Promise.allSettled([
          api.agents(), api.tasks(), api.teams(), api.stats(), api.decisions(),
          api.verifications(), api.wsStatus(), api.wsEvents(), api.wsCommands(),
        ]);
      const val = <T,>(r: PromiseSettledResult<T>, fallback: T): T =>
        r.status === "fulfilled" ? r.value : fallback;
      return {
        agents: val(agents, prev?.agents ?? []),
        tasks: val(tasks, prev?.tasks ?? []),
        teams: val(teams, prev?.teams ?? []),
        stats: val(stats, prev?.stats ?? null),
        decisions: val(decisions, prev?.decisions ?? []),
        verifications: val(vers, prev?.verifications ?? []),
        wsStatus: val(wsStatus, prev?.wsStatus ?? null),
        wsEvents: val(wsEvents, prev?.wsEvents ?? []),
        wsCommands: val(wsCommands, prev?.wsCommands ?? []),
      };
    };

    const tick = async () => {
      try {
        const next = await fetchSnapshot();
        if (!alive) return;
        const diff = diffSnapshots(prevRef.current, next);
        if (diff.events.length > 0) handlers.current.onEvents(diff.events);
        if (diff.assignments.length > 0) handlers.current.onAssignments(diff.assignments);
        const now = Date.now();
        for (const id of diff.newlyCompletedAgents) flashRef.current.set(id, now + 5000);
        for (const [id, expiry] of flashRef.current) if (expiry < now) flashRef.current.delete(id);
        prevRef.current = next;
        setSnapshot(next);
        setVisuals(deriveVisuals(next, new Set(flashRef.current.keys())));
        setError(null);
        const running = next.tasks.find((t) => t.status === "running");
        const rid = running?.id ?? null;
        if (rid !== runningTaskIdRef.current) {
          runningTaskIdRef.current = rid;
          setRunningTaskId(rid);
        }
      } catch (e) {
        if (alive) setError(e instanceof Error ? e.message : "读取后端数据失败");
      }
      if (alive) timer = setTimeout(() => void tick(), POLL_MS);
    };
    refreshRef.current = () => {
      if (timer) clearTimeout(timer);
      void tick();
    };
    void tick();
    return () => {
      alive = false;
      if (timer) clearTimeout(timer);
    };
  }, [visible, handlers]);

  // Existing per-task WebSocket as a low-latency refresh trigger only.
  useEffect(() => {
    if (!visible || runningTaskId == null) return;
    let ws: WebSocket | null = null;
    let debounce: ReturnType<typeof setTimeout> | null = null;
    try {
      const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
      const host = window.location.host || "127.0.0.1:8000";
      ws = new WebSocket(`${proto}//${host}/ws/tasks/${runningTaskId}`);
      ws.onmessage = () => {
        if (debounce) clearTimeout(debounce);
        debounce = setTimeout(() => refreshRef.current(), 300);
      };
    } catch {
      ws = null; // polling alone still keeps the studio truthful
    }
    return () => {
      if (debounce) clearTimeout(debounce);
      ws?.close();
    };
  }, [visible, runningTaskId]);

  return { snapshot, visuals, error };
}
