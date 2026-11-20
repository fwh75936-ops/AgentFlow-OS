import Phaser from "phaser";
import { OfficeScene } from "./OfficeScene";
import { WORLD_W, WORLD_H } from "./OfficeMap";
import type { AgentVisual, AssignmentEvent, StudioCallbacks } from "../types/studio";

export interface StudioBridge {
  syncAgents: (visuals: AgentVisual[]) => void;
  enqueueAssignment: (a: AssignmentEvent) => void;
  focusAgent: (agentId: number) => void;
  setSelectedAgent: (agentId: number | null) => void;
  getLabelAnchor: (agentId: number) => { x: number; y: number; below: boolean } | null;
  resetCamera: () => void;
  fitCamera: () => void;
  zoomBy: (factor: number) => void;
  setVisible: (visible: boolean) => void;
  getFps: () => number;
  destroy: () => void;
}

/** Creates the single Phaser game instance for the studio page. Phaser
 * owns all per-frame rendering; React only pushes business snapshots.
 * The game is destroyed when the page component unmounts for real
 * (keep-alive pool: hidden pages merely sleep the scene). */
export function createStudioGame(parent: HTMLElement, callbacks: StudioCallbacks): StudioBridge {
  const scene = new OfficeScene();
  scene.setCallbacks(callbacks);
  const game = new Phaser.Game({
    type: Phaser.AUTO,
    parent,
    width: WORLD_W,
    height: WORLD_H,
    transparent: false,
    backgroundColor: "#F7F7F5",
    render: { pixelArt: true, antialias: false, roundPixels: true },
    // The painted backdrop is the game's only external image; Phaser's
    // default XHR loader cannot read file:// URLs in Electron, so load
    // images through plain Image elements instead.
    loader: { imageLoadType: "HTMLImageElement" },
    scale: { mode: Phaser.Scale.RESIZE },
    fps: { target: 60 },
    scene: [scene],
  });
  const getScene = () => game.scene.keys["office"] as OfficeScene | undefined;
  return {
    syncAgents: (visuals) => getScene()?.syncAgents(visuals),
    enqueueAssignment: (a) => getScene()?.enqueueAssignment(a),
    focusAgent: (id) => getScene()?.focusAgent(id),
    setSelectedAgent: (id) => getScene()?.setSelectedAgent(id),
    getLabelAnchor: (id) => getScene()?.getLabelAnchor(id) ?? null,
    resetCamera: () => getScene()?.resetCamera(),
    fitCamera: () => getScene()?.fitCamera(),
    zoomBy: (f) => getScene()?.zoomBy(f),
    setVisible: (visible) => {
      const s = getScene();
      if (!s) return;
      if (visible) {
        if (!s.scene.isActive()) s.scene.wake();
        s.fitCamera();
      } else if (s.scene.isActive()) {
        s.scene.sleep();
      }
    },
    getFps: () => Math.round(game.loop.actualFps),
    destroy: () => game.destroy(true),
  };
}
