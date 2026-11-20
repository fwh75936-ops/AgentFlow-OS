import Phaser from "phaser";
import { TILE, ANCHORS, spriteScaleAt } from "./OfficeMap";
import { findPath, type TilePoint } from "./Pathfinding";
import {
  appearanceKey, charKey, ensureBubbleTexture, ensureCharacterTextures,
  ensureSelectRingTexture, ensureShadowTexture, ensureSparkTexture,
  type BubbleKind, type CharFrame,
} from "./textures";
import type { AgentVisual, StudioState } from "../types/studio";

export type DeskZone = "chief" | "dev" | "test" | "data" | "product";

const SPEED = 78; // px per second, uniform for every agent
const HIGH_PRIORITY: StudioState[] = ["failed", "completed", "retrying"];

/** Icon animation timing from the redesign spec (section 2): frame
 * sequence + step in ms. `once` kinds play a single pass on state entry
 * and then hold their last frame (plug flash, flag wave). */
const BUBBLE_ANIM: Partial<Record<BubbleKind, { seq: number[]; step: number; once?: boolean }>> = {
  cup: { seq: [0, 1, 2], step: 300 },
  dots: { seq: [0, 1, 2, 2], step: 160 },
  monitor: { seq: [0, 1], step: 450 },
  plug: { seq: [0, 1], step: 120, once: true },
  hourglass: { seq: [0, 1, 2], step: 500 },
  magnifier: { seq: [0, 1, 2, 2], step: 240 },
  wrench: { seq: [0, 1, 2, 3], step: 140 },
  flag: { seq: [0, 1, 1, 0], step: 90, once: true },
};

function bubbleFrame(kind: BubbleKind, elapsedMs: number): number {
  const a = BUBBLE_ANIM[kind];
  if (!a) return 0;
  const idx = Math.floor(Math.max(0, elapsedMs) / a.step);
  return a.once ? a.seq[Math.min(idx, a.seq.length - 1)] : a.seq[idx % a.seq.length];
}

function tileCenter(t: TilePoint): { x: number; y: number } {
  return { x: t.x * TILE + TILE / 2, y: t.y * TILE + TILE / 2 };
}

/** One pixel character bound to one real Agent. Position/animation live
 * entirely in the render layer (never written to React/Zustand); business
 * state arrives as AgentVisual snapshots and is translated here into a
 * destination + action. Movement follows A* paths — no teleporting.
 * The character carries NO name text: names are a DOM overlay (React)
 * shown on hover/selection; status is a drawn pixel icon bubble, and the
 * shared-computer lease gets its own independent bubble driven only by
 * the real lease field. */
export class AgentSprite {
  readonly agentId: number;
  container: Phaser.GameObjects.Container;
  private sprite: Phaser.GameObjects.Image;
  private bubble: Phaser.GameObjects.Image;
  private leaseBubble: Phaser.GameObjects.Image;
  private ring: Phaser.GameObjects.Image;
  private scene: Phaser.Scene;
  private visual: AgentVisual;
  private appearance: string;
  desk: TilePoint;
  deskZone: DeskZone;
  restSpot: TilePoint;
  isChief: boolean;
  tile: TilePoint;
  private path: TilePoint[] = [];
  private state: StudioState = "idle";
  private arrivedAction: StudioState = "idle";
  private lastRepathAt = 0;
  private animClock = 0;
  private stateStartClock = 0;
  private talkStartClock = 0;
  private forcedDestination: TilePoint | null = null;
  private onArriveForced: (() => void) | null = null;
  private talkUntil = 0;
  private completedUntil = 0;
  private lastNow = 0;
  /** Current depth scale (iso view): 1 in the flat view; in the iso
   * view it follows the feet y (OfficeMap.spriteScaleAt). Applied to
   * the whole container, so the figure, its shadow and both bubbles
   * grow/shrink together; the local hit area scales with it. */
  private depthScale = 1;

  constructor(
    scene: Phaser.Scene,
    visual: AgentVisual,
    desk: TilePoint,
    deskZone: DeskZone,
    restSpot: TilePoint,
    isChief: boolean,
    spawn: TilePoint,
    onSelect: (id: number) => void,
    onOpen: (id: number) => void,
    onHover: (id: number | null) => void,
  ) {
    this.scene = scene;
    this.agentId = visual.agentId;
    this.visual = visual;
    this.appearance = appearanceKey(visual.appearance);
    this.desk = desk;
    this.deskZone = deskZone;
    this.restSpot = restSpot;
    this.isChief = isChief;
    this.tile = spawn;
    ensureCharacterTextures(scene, visual.appearance);
    const c = tileCenter(spawn);
    this.ring = scene.add.image(0, 2, ensureSelectRingTexture(scene)).setOrigin(0.5, 0.5).setVisible(false);
    const shadow = scene.add.image(0, 1, ensureShadowTexture(scene)).setOrigin(0.5, 0.5);
    this.sprite = scene.add.image(0, 0, charKey(visual.appearance, "idle_d"))
      .setOrigin(0.5, 0.95);
    this.bubble = scene.add.image(-10, -41, "__DEFAULT").setOrigin(0.5, 1).setVisible(false);
    this.leaseBubble = scene.add.image(10, -41, "__DEFAULT").setOrigin(0.5, 1).setVisible(false);
    this.container = scene.add.container(c.x, c.y, [this.ring, shadow, this.sprite, this.bubble, this.leaseBubble]);
    this.container.setDepth(20 + c.y);
    this.applyDepthScale();
    this.container.setSize(30, 42);
    // Explicit hit area centred on the figure: a container's default hit
    // rectangle starts AT the origin (the feet point) and extends right /
    // down, which misses the body that stands above it.
    this.container.setInteractive({
      hitArea: new Phaser.Geom.Rectangle(-16, -42, 32, 46),
      hitAreaCallback: Phaser.Geom.Rectangle.Contains,
      useHandCursor: true,
    });
    let downAt = 0;
    let downPos = { x: 0, y: 0 };
    this.container.on("pointerdown", (p: Phaser.Input.Pointer) => {
      downAt = p.time;
      downPos = { x: p.x, y: p.y };
    });
    this.container.on("pointerup", (p: Phaser.Input.Pointer) => {
      const moved = Math.hypot(p.x - downPos.x, p.y - downPos.y);
      if (moved > 6) return; // it was a camera drag, not a click
      if (p.time - downAt < 350 && (p.event as MouseEvent)?.detail === 2) onOpen(this.agentId);
      else onSelect(this.agentId);
    });
    this.container.on("pointerover", () => onHover(this.agentId));
    this.container.on("pointerout", () => onHover(null));
    this.applyVisual(visual, true);
  }

  destroy() {
    this.container.destroy();
  }

  setSelected(v: boolean) {
    this.ring.setVisible(v);
  }

  /** Recompute the depth scale from the feet position (iso view). */
  private applyDepthScale() {
    const s = spriteScaleAt(this.container.y);
    if (Math.abs(s - this.depthScale) > 0.004) {
      this.depthScale = s;
      this.container.setScale(s);
    }
  }

  /** World-space anchor for the DOM name label: just above the bubbles. */
  labelAnchor(): { x: number; y: number } {
    return { x: this.container.x, y: this.container.y - 62 * this.depthScale };
  }
  feetAnchor(): { x: number; y: number } {
    return { x: this.container.x, y: this.container.y + 6 };
  }

  /** Destination tile for a business state. */
  private destinationFor(state: StudioState): TilePoint {
    switch (state) {
      case "planning":
        return this.isChief ? ANCHORS.boardChief : ANCHORS.boardMeeting;
      case "verifying":
        return this.deskZone === "test" ? this.desk : ANCHORS.testBoard;
      case "idle":
        return this.restSpot;
      default:
        return this.desk;
    }
  }

  applyVisual(visual: AgentVisual, force = false) {
    const prevState = this.state;
    this.visual = visual;
    this.state = visual.state;
    const key = appearanceKey(visual.appearance);
    if (key !== this.appearance) {
      this.appearance = key;
      ensureCharacterTextures(this.scene, visual.appearance);
    }
    if (visual.state !== prevState) this.stateStartClock = this.animClock;
    if (visual.state !== "disabled") this.sprite.clearTint();
    if (visual.state === "completed") {
      this.completedUntil = this.scene.time.now + 4200;
      if (prevState !== "completed") this.spawnSparks();
    }
    if (this.forcedDestination) return; // a handoff walk owns the character right now
    const dest = this.destinationFor(visual.state);
    const now = this.scene.time.now;
    const sameTarget = this.path.length > 0
      ? (this.path[this.path.length - 1].x === dest.x && this.path[this.path.length - 1].y === dest.y)
      : (this.tile.x === dest.x && this.tile.y === dest.y);
    if (sameTarget && !force) {
      this.arrivedAction = visual.state;
      return;
    }
    // Anti-thrash: non-urgent state flapping does not re-path more than
    // once per 1.5s; urgent states (failed/completed/retrying) always win.
    if (!force && prevState !== visual.state && !HIGH_PRIORITY.includes(visual.state)
        && now - this.lastRepathAt < 1500) {
      this.arrivedAction = visual.state;
      return;
    }
    this.goTo(dest);
    this.arrivedAction = visual.state;
  }

  private goTo(dest: TilePoint) {
    this.lastRepathAt = this.scene.time.now;
    this.path = findPath(this.tile, dest);
    if (this.path.length === 0 && (this.tile.x !== dest.x || this.tile.y !== dest.y)) {
      // Unreachable target: step to the closest reachable neighbour once.
      const alt = findPath(this.tile, { x: dest.x, y: dest.y + 1 });
      this.path = alt;
    }
  }

  /** Handoff walk: go to a tile, run a callback, then resume state. */
  walkToAndBack(dest: TilePoint, onArrive: () => void, onDone: () => void) {
    this.forcedDestination = dest;
    this.goTo(dest);
    if (this.path.length === 0) {
      // Already standing on the target tile: fire arrival asynchronously.
      this.scene.time.delayedCall(60, () => this.onArrived());
    }
    this.onArriveForced = () => {
      onArrive();
      this.scene.time.delayedCall(950, () => {
        this.forcedDestination = null;
        this.onArriveForced = null;
        this.applyVisual(this.visual, true);
        onDone();
      });
    };
  }

  showTalk(durationMs = 950) {
    this.talkUntil = this.scene.time.now + durationMs;
    this.talkStartClock = this.animClock;
  }

  /** Completion burst: four 1px-class sparkles expand and fade. Fired
   * only by the real completed state transition — never looped. */
  private spawnSparks() {
    const tex = ensureSparkTexture(this.scene);
    const colors = [0x238b61, 0xc58a32, 0x238b61, 0x3978c6];
    const dirs: [number, number][] = [[-9, -13], [9, -15], [-13, -5], [13, -6]];
    dirs.forEach(([dx, dy], i) => {
      const s = this.scene.add.image(this.container.x, this.container.y - 28, tex)
        .setTint(colors[i]).setDepth(600);
      this.scene.tweens.add({
        targets: s,
        x: this.container.x + dx, y: this.container.y - 28 + dy,
        alpha: 0, duration: 620, ease: "Quad.easeOut",
        onComplete: () => s.destroy(),
      });
    });
  }

  private bubbleKind(): BubbleKind | null {
    const now = this.scene.time.now;
    if (now < this.talkUntil) return "talk";
    if (this.state === "failed") return "error";
    if (this.state === "completed" || now < this.completedUntil) return "flag";
    switch (this.state) {
      case "retrying": return "wrench";
      case "waiting": return "hourglass";
      case "verifying": return "magnifier";
      case "planning": return "clipboard";
      case "thinking": return "dots";
      case "working": return "monitor";
      case "tool_running": return "plug";
      case "idle": return "cup";
      case "disabled": return "power";
      default: return null;
    }
  }

  private refreshBubbles() {
    const kind = this.bubbleKind();
    if (!kind) {
      this.bubble.setVisible(false);
    } else {
      const elapsed = kind === "talk" ? this.animClock - this.talkStartClock : this.animClock - this.stateStartClock;
      const key = ensureBubbleTexture(this.scene, kind, bubbleFrame(kind, elapsed));
      if (this.bubble.texture.key !== key) this.bubble.setTexture(key);
      this.bubble.setVisible(true);
    }
    // The lease bubble is independent of state: it follows ONLY the real
    // desktop-lease holder field, for as long as the lease is held.
    if (this.visual.holdsDesktop) {
      const key = ensureBubbleTexture(this.scene, "lease", 0);
      if (this.leaseBubble.texture.key !== key) this.leaseBubble.setTexture(key);
      this.leaseBubble.setVisible(true);
    } else {
      this.leaseBubble.setVisible(false);
    }
  }

  private setFrame(frame: CharFrame, flipX = false) {
    const key = charKey(this.visual.appearance, frame);
    if (this.sprite.texture.key !== key) this.sprite.setTexture(key);
    this.sprite.setFlipX(flipX);
  }

  private cycle(frames: CharFrame[], stepMs: number, seq?: number[]): CharFrame {
    const idx = Math.floor(this.animClock / stepMs);
    return frames[seq ? seq[idx % seq.length] : idx % frames.length];
  }

  update(_time: number, _deltaMs: number) {
    // Movement uses REAL elapsed time, not Phaser's smoothed step delta:
    // Phaser caps/smooths delta toward the target FPS, which would make
    // characters crawl at a fraction of the intended speed on any machine
    // that cannot hold 60 FPS. Clamp to 100ms so a hidden-tab pause does
    // not turn into a teleport on return.
    const nowMs = performance.now();
    const deltaMs = this.lastNow ? Math.min(nowMs - this.lastNow, 100) : 16;
    this.lastNow = nowMs;
    this.animClock += deltaMs;
    this.applyDepthScale();
    this.refreshBubbles();
    if (this.path.length > 0) {
      const target = tileCenter(this.path[0]);
      const dx = target.x - this.container.x;
      const dy = target.y - this.container.y;
      const dist = Math.hypot(dx, dy);
      const step = (SPEED * deltaMs) / 1000;
      const cycle = (["1", "2", "3", "2"] as const)[Math.floor(this.animClock / 130) % 4];
      if (Math.abs(dx) > Math.abs(dy)) this.setFrame(`side${cycle}` as CharFrame, dx < 0);
      else if (dy < 0) this.setFrame(`walk_u${cycle}` as CharFrame);
      else this.setFrame(`walk_d${cycle}` as CharFrame);
      if (dist <= step) {
        this.container.setPosition(target.x, target.y);
        this.tile = this.path.shift()!;
        if (this.path.length === 0) this.onArrived();
      } else {
        this.container.setPosition(
          this.container.x + (dx / dist) * step,
          this.container.y + (dy / dist) * step,
        );
      }
      this.container.setDepth(20 + this.container.y);
      return;
    }
    // Arrived: play the action animation for the current state. Poses are
    // differentiated per the redesign spec, on top of the SAME derived
    // states as before — no state is invented here.
    const atDesk = this.tile.x === this.desk.x && this.tile.y === this.desk.y;
    switch (this.arrivedAction) {
      case "working":
        if (atDesk) this.setFrame(this.cycle(["sit_type1", "sit_type2"], 170));
        else this.setFrame("stand_read");
        break;
      case "tool_running":
        if (atDesk) this.setFrame(this.cycle(["sit_type1", "sit_type2"], 200));
        else this.setFrame("stand_read");
        break;
      case "retrying": // faster input than working, plus the wrench bubble
        if (atDesk) this.setFrame(this.cycle(["sit_type1", "sit_type2"], 140));
        else this.setFrame("stand_read");
        break;
      case "thinking": // hand at the chin, hands OFF the keyboard
        if (atDesk) this.setFrame(this.cycle(["sit_think1", "sit_think2", "sit_think3", "sit_think2"], 400));
        else this.setFrame(this.cycle(["stand_think1", "stand_think2"], 400));
        break;
      case "planning": // back to the whiteboard, one arm raised
        this.setFrame(this.cycle(["board1", "board2", "board3", "board2"], 330));
        break;
      case "verifying": // leaning in, reading a sheet — no typing
        if (atDesk) this.setFrame(this.cycle(["sit_verify1", "sit_verify2", "sit_verify3", "sit_verify3"], 240));
        else this.setFrame("stand_read");
        break;
      case "waiting": // seated, hands on the lap
        if (atDesk) this.setFrame(this.cycle(["sit_wait1", "sit_wait2"], 500));
        else this.setFrame(this.cycle(["idle_d", "idle_d2"], 500));
        break;
      case "failed": // head down, input stopped
        this.setFrame(atDesk ? "sit_down" : "stand_down");
        break;
      case "disabled":
        if (atDesk) this.setFrame("sit_type1");
        else this.setFrame("idle_d");
        this.sprite.setTint(0xbdbdbd);
        break;
      case "completed":
        this.setFrame("idle_d");
        break;
      default: // idle: breathing at the current post — never relocated
        this.setFrame(this.cycle(["idle_d", "idle_d2"], 500));
    }
    if (this.arrivedAction !== "disabled") this.sprite.clearTint();
  }

  private onArrived() {
    this.tile = { ...this.tile };
    if (this.onArriveForced && this.forcedDestination
        && this.tile.x === this.forcedDestination.x && this.tile.y === this.forcedDestination.y) {
      const cb = this.onArriveForced;
      this.onArriveForced = null;
      cb();
    }
  }
}
