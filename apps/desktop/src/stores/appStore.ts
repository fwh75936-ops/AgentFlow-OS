import { create } from "zustand";

export type PageKey = "dashboard" | "agents" | "teams" | "tasks" | "workflow"
  | "linux" | "v3" | "skills" | "mcp" | "knowledge" | "verification" | "evaluation" | "settings";

interface AppState {
  page: PageKey;
  /** Pages visited at least once; the keep-alive pool mounts a page on
   * first visit and never unmounts it afterwards. */
  visited: PageKey[];
  setPage: (p: PageKey) => void;
  /** When set, the Tasks page opens this task's detail (dashboard and
   * workflow pages deep-link into a task). Cleared by the Tasks page. */
  focusTaskId: number | null;
  openTask: (id: number) => void;
  clearFocusTask: () => void;
  /** Dashboard's board/studio toggle, lifted into the store so other
   * pages (e.g. the team chat member list) can deep-link straight into
   * the pixel studio view. */
  dashboardView: "board" | "studio";
  setDashboardView: (v: "board" | "studio") => void;
  openStudio: () => void;
}
export const useAppStore = create<AppState>((set) => ({
  page: "dashboard",
  visited: ["dashboard"],
  setPage: (p) => set((s) => ({
    page: p,
    visited: s.visited.includes(p) ? s.visited : [...s.visited, p],
  })),
  focusTaskId: null,
  openTask: (id) => set((s) => ({
    focusTaskId: id, page: "tasks",
    visited: s.visited.includes("tasks") ? s.visited : [...s.visited, "tasks"],
  })),
  clearFocusTask: () => set({ focusTaskId: null }),
  dashboardView: "board",
  setDashboardView: (v) => set({ dashboardView: v }),
  openStudio: () => set((s) => ({
    dashboardView: "studio", page: "dashboard",
    visited: s.visited.includes("dashboard") ? s.visited : [...s.visited, "dashboard"],
  })),
}));
