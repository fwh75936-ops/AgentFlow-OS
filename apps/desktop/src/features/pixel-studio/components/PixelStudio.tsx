import { useEffect, useRef, useState } from "react";
import { usePageVisible } from "../../../motion/visibility";
import { useAppStore } from "../../../stores/appStore";
import { useStudioStore } from "../store/studioStore";
import { useStudioEvents, type StudioHandlers } from "../hooks/useStudioEvents";
import type { StudioBridge } from "../game/StudioGame";
import StudioHeader from "./StudioHeader";
import StudioToolbar from "./StudioToolbar";
import AgentDetailPanel from "./AgentDetailPanel";
import StudioTimeline from "./StudioTimeline";

/** DOM name label (redesign section 2): names are NOT drawn in the scene.
 * One label node, reused for hover and selection, positioned every frame
 * from the sprite's live render position via the bridge — so Chinese
 * glyphs rasterise at CSS resolution and never blur with scene scaling.
 * Dark plate per spec: rgba(26,37,54,0.94) on #526980, 13px/18px, square
 * corners, hard shadow; selection only swaps the border colour. */
function StudioNameLabel({ bridgeRef, wrapRef, agentId, name, selected }: {
  bridgeRef: React.RefObject<StudioBridge | null>;
  wrapRef: React.RefObject<HTMLDivElement | null>;
  agentId: number | null;
  name: string | null;
  selected: boolean;
}) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = ref.current;
    if (!el || agentId == null) return;
    let raf = 0;
    const loop = () => {
      const pos = bridgeRef.current?.getLabelAnchor(agentId) ?? null;
      const wrap = wrapRef.current;
      const canvas = wrap?.querySelector("canvas");
      if (pos && wrap && canvas) {
        const wr = wrap.getBoundingClientRect();
        const cr = canvas.getBoundingClientRect();
        el.style.opacity = "1";
        el.style.left = `${Math.round(pos.x + cr.left - wr.left)}px`;
        el.style.top = `${Math.round(pos.y + cr.top - wr.top)}px`;
        el.style.transform = pos.below ? "translate(-50%, 6px)" : "translate(-50%, calc(-100% - 4px))";
      } else {
        el.style.opacity = "0";
      }
      raf = requestAnimationFrame(loop);
    };
    raf = requestAnimationFrame(loop);
    return () => {
      cancelAnimationFrame(raf);
      if (ref.current) ref.current.style.opacity = "0";
    };
  }, [agentId, bridgeRef, wrapRef]);
  if (agentId == null || !name) return null;
  return <div ref={ref} className={`studio-name-label${selected ? " selected" : ""}`}>{name}</div>;
}

/** AI 工作室 (AI Studio) — the former 工作台 page, same route key
 * "dashboard". A Phaser-rendered pixel office whose characters are bound
 * 1:1 to real Agents and driven exclusively by real backend state. */
export default function PixelStudio() {
  const visible = usePageVisible();
  const setPage = useAppStore((s) => s.setPage);
  const openTask = useAppStore((s) => s.openTask);
  const selectedAgentId = useStudioStore((s) => s.selectedAgentId);
  const panelOpen = useStudioStore((s) => s.panelOpen);
  const timelineOpen = useStudioStore((s) => s.timelineOpen);
  const timeline = useStudioStore((s) => s.timeline);
  const selectAgent = useStudioStore((s) => s.selectAgent);
  const setPanelOpen = useStudioStore((s) => s.setPanelOpen);
  const setTimelineOpen = useStudioStore((s) => s.setTimelineOpen);
  const pushEvents = useStudioStore((s) => s.pushEvents);
  const requestFocus = useStudioStore((s) => s.requestFocus);
  const focusAgentId = useStudioStore((s) => s.focusAgentId);
  const focusNonce = useStudioStore((s) => s.focusNonce);

  const containerRef = useRef<HTMLDivElement>(null);
  const bridgeRef = useRef<StudioBridge | null>(null);
  const [hoveredId, setHoveredId] = useState<number | null>(null);
  const [hoverShownId, setHoverShownId] = useState<number | null>(null);

  // Spec: the hover label appears after the pointer rests 150ms on one
  // employee; leaving hides it at once (the 80ms fade is in CSS).
  useEffect(() => {
    if (hoveredId == null) {
      setHoverShownId(null);
      return;
    }
    const t = setTimeout(() => setHoverShownId(hoveredId), 150);
    return () => clearTimeout(t);
  }, [hoveredId]);
  const handlersRef = useRef<StudioHandlers>({
    onEvents: (events) => pushEvents(events),
    onAssignments: (assignments) => {
      for (const a of assignments) bridgeRef.current?.enqueueAssignment(a);
    },
  });
  handlersRef.current.onEvents = (events) => pushEvents(events);

  const { snapshot, visuals, error } = useStudioEvents(visible, handlersRef);

  // Game lifecycle: created once for this page instance, destroyed only
  // on a real unmount. The keep-alive pool hides the page instead —
  // the scene sleeps while hidden and never touches other pages' data.
  useEffect(() => {
    if (!containerRef.current || bridgeRef.current) return;
    let alive = true;
    // Phaser is loaded on demand: the studio chunk stays out of the main
    // bundle so other pages never pay its parse cost.
    void import("../game/StudioGame").then(({ createStudioGame }) => {
      if (!alive || !containerRef.current) return;
      const bridge = createStudioGame(containerRef.current, {
        onSelectAgent: (id) => selectAgent(id),
        onHoverAgent: (id) => setHoveredId(id),
        onOpenAgentPage: (id) => {
          selectAgent(id);
          setPage("agents");
        },
        onZoneClick: (zone) => {
          if (zone === "computer") setPage("linux");
          else if (zone === "test") setPage("verification");
          else if (zone === "board") setPage("workflow");
          else setPage("teams");
        },
      });
      bridgeRef.current = bridge;
      bridge.setVisible(visible);
    });
    return () => {
      alive = false;
      bridgeRef.current?.destroy();
      bridgeRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    bridgeRef.current?.setVisible(visible);
  }, [visible]);

  useEffect(() => {
    if (visuals.length > 0) bridgeRef.current?.syncAgents(visuals);
  }, [visuals]);

  useEffect(() => {
    bridgeRef.current?.setSelectedAgent(selectedAgentId);
  }, [selectedAgentId, visuals]);

  const labelAgentId = hoverShownId ?? selectedAgentId;
  const labelName = labelAgentId != null
    ? visuals.find((v) => v.agentId === labelAgentId)?.name ?? null
    : null;

  useEffect(() => {
    if (focusAgentId != null && focusNonce > 0) bridgeRef.current?.focusAgent(focusAgentId);
  }, [focusAgentId, focusNonce]);

  return (
    <div className="studio-root">
      <StudioHeader
        snapshot={snapshot}
        visuals={visuals}
        error={error}
        onNewTask={() => setPage("tasks")}
        onOpenSettings={() => setPage("settings")}
      />
      <div className="studio-main">
        <div className="studio-canvas-wrap" ref={containerRef}>
          <StudioToolbar
            onZoomIn={() => bridgeRef.current?.zoomBy(1.2)}
            onZoomOut={() => bridgeRef.current?.zoomBy(1 / 1.2)}
            onFit={() => bridgeRef.current?.fitCamera()}
            onFocus={() => selectedAgentId != null && bridgeRef.current?.focusAgent(selectedAgentId)}
            focusDisabled={selectedAgentId == null}
          />
          <StudioNameLabel
            bridgeRef={bridgeRef}
            wrapRef={containerRef}
            agentId={labelAgentId}
            name={labelName}
            selected={labelAgentId != null && labelAgentId === selectedAgentId}
          />
          {visuals.length === 0 && (
            <div className="studio-empty-hint">
              {error ? "后端未连接 — 请先启动后端服务，员工们会立刻回到办公室。" : "正在等待员工数据…"}
            </div>
          )}
        </div>
        {panelOpen ? (
          <AgentDetailPanel
            snapshot={snapshot}
            visuals={visuals}
            selectedId={selectedAgentId}
            onSelect={(id) => selectAgent(id)}
            onClose={() => setPanelOpen(false)}
            onOpenAgents={() => setPage("agents")}
            onOpenLinux={() => setPage("linux")}
            onOpenTask={(id) => openTask(id)}
            onFocus={(id) => requestFocus(id)}
          />
        ) : (
          <button className="studio-panel-reopen" onClick={() => setPanelOpen(true)} title="展开详情面板">‹</button>
        )}
      </div>
      <StudioTimeline
        open={timelineOpen}
        onToggle={() => setTimelineOpen(!timelineOpen)}
        events={timeline}
        snapshot={snapshot}
        onOpenTasks={() => setPage("tasks")}
        onOpenVerification={() => setPage("verification")}
        onSelectAgent={(id) => selectAgent(id)}
      />
    </div>
  );
}
