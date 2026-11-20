import { create } from "zustand";
import type { TimelineEvent } from "../types/studio";

/** UI-level studio state only: selection, panel visibility, the event
 * timeline and one-shot camera focus requests. Character positions and
 * per-frame animation NEVER pass through this store (render layer only),
 * and business truth lives in the API snapshots, not here. */
interface StudioStore {
  selectedAgentId: number | null;
  panelOpen: boolean;
  timelineOpen: boolean;
  timeline: TimelineEvent[];
  focusAgentId: number | null;
  focusNonce: number;
  selectAgent: (id: number | null) => void;
  setPanelOpen: (open: boolean) => void;
  setTimelineOpen: (open: boolean) => void;
  pushEvents: (events: TimelineEvent[]) => void;
  requestFocus: (agentId: number) => void;
}

export const useStudioStore = create<StudioStore>((set) => ({
  selectedAgentId: null,
  panelOpen: true,
  timelineOpen: true,
  timeline: [],
  focusAgentId: null,
  focusNonce: 0,
  selectAgent: (id) => set({ selectedAgentId: id, panelOpen: id != null ? true : undefined }),
  setPanelOpen: (open) => set({ panelOpen: open }),
  setTimelineOpen: (open) => set({ timelineOpen: open }),
  pushEvents: (events) => set((s) => {
    if (events.length === 0) return s;
    const known = new Set(s.timeline.map((e) => e.id));
    const fresh = events.filter((e) => !known.has(e.id));
    if (fresh.length === 0) return s;
    return { timeline: [...fresh.reverse(), ...s.timeline].slice(0, 90) };
  }),
  requestFocus: (agentId) => set((s) => ({ focusAgentId: agentId, focusNonce: s.focusNonce + 1 })),
}));
