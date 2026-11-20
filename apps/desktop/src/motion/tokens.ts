/**
 * AgentFlow OS — Motion Design Tokens (AgentFlow Pure Light)
 *
 * Single source of truth for animation timing in the desktop app.
 * Framework-agnostic on purpose: these constants are consumed by the CSS
 * token layer (src/styles/motion.css mirrors the same values as CSS
 * variables) and by the `motion` package (motion/react) once it is
 * installed as the app's only JS animation engine.
 *
 * Rules:
 * - Animate only `transform` and `opacity` (plus color transitions).
 * - Ordinary interactions never exceed 400ms.
 * - Springs are critically / near-critically damped: no visible overshoot.
 * - Every value here must stay in sync with motion.css.
 */

/** Duration scale in milliseconds. */
export const DURATION = {
  instant: 80,
  fast: 120,
  normal: 180,
  medium: 240,
  slow: 320,
} as const;

export type DurationToken = keyof typeof DURATION;

/** Hard ceiling for any ordinary interaction animation, in ms. */
export const MAX_INTERACTION_MS = 400;

/**
 * Easing curves as cubic-bezier control points (motion-style arrays).
 * `standard` is the default for enter/state transitions; `exit` is a
 * slightly faster-in curve for dismissals; `layout` is reserved for
 * shared-layout movement (sidebar indicator, list reorder).
 */
export const EASING = {
  standard: [0.2, 0, 0, 1],
  exit: [0.4, 0, 1, 1],
  layout: [0.32, 0.72, 0, 1],
} as const satisfies Record<string, readonly [number, number, number, number]>;

export type EasingToken = keyof typeof EASING;

/** Same curves as CSS cubic-bezier() strings, for the CSS token layer. */
export const EASING_CSS: Record<EasingToken, string> = {
  standard: "cubic-bezier(0.2, 0, 0, 1)",
  exit: "cubic-bezier(0.4, 0, 1, 1)",
  layout: "cubic-bezier(0.32, 0.72, 0, 1)",
};

/**
 * Spring presets (motion spring transition shape). Damping is chosen at
 * or above the critical-damping ratio for each stiffness so animations
 * settle without a bounce: this product's motion language is calm.
 */
export interface SpringPreset {
  type: "spring";
  stiffness: number;
  damping: number;
  mass: number;
}

export const SPRING: Record<"snappy" | "gentle" | "layout", SpringPreset> = {
  /** Small, direct feedback: menus, popovers, press recovery. */
  snappy: { type: "spring", stiffness: 550, damping: 42, mass: 0.9 },
  /** Default entrances: pages, modals, cards. */
  gentle: { type: "spring", stiffness: 320, damping: 34, mass: 1 },
  /** Shared-layout movement: nav indicator slide, list reorder. */
  layout: { type: "spring", stiffness: 420, damping: 40, mass: 1 },
};

/** Tween transition presets (motion `Transition` shape), in seconds. */
export interface TweenPreset {
  duration: number; // seconds — the unit motion expects
  ease: readonly [number, number, number, number];
}

const toTween = (ms: number, ease: EasingToken): TweenPreset => ({
  duration: ms / 1000,
  ease: EASING[ease],
});

export const TRANSITION = {
  /** Page content entrance: opacity 0→1, translateY 6px→0, 180–220ms. */
  pageEnter: toTween(DURATION.normal, "standard"),
  /** Modal entrance: opacity + scale 0.98→1 + translateY 4px, ~200ms. */
  modalEnter: toTween(200, "standard"),
  /** Menu/popover entrance: opacity + translateY -4px, 120–160ms. */
  menuEnter: toTween(140, "standard"),
  /** Shared-layout moves (sidebar pill, list reorder). */
  layout: SPRING.layout,
  /** Press feedback recovery (scale 0.985→1). */
  press: SPRING.snappy,
  /** Generic state change (badge color, status swap). */
  state: toTween(DURATION.normal, "standard"),
  /** Exits are a touch quicker than entrances. */
  exit: toTween(DURATION.fast, "exit"),
} as const;

/** Enter/exit variant values shared by page and overlay components. */
export const ENTER_OFFSET = {
  pageY: 6, // px, page content rises 6px on enter
  itemY: 4, // px, list items / messages rise 4px on enter
  menuY: -4, // px, menus drop from 4px above
  modalY: 4, // px, modal rises 4px while scaling 0.98→1
  modalScale: 0.98,
  pressScale: 0.985, // button :active / whileTap scale
} as const;

/** Media query string for reduced-motion checks in JS, mirrors motion.css. */
export const REDUCED_MOTION_QUERY = "(prefers-reduced-motion: reduce)";

/**
 * Durations collapsed for reduced motion: state changes stay (they carry
 * meaning) but movement becomes effectively instant.
 */
export const REDUCED_DURATION_MS = 0;
