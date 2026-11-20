/** Unified pixel-art icon set (generated sheet, cut into per-glyph PNGs
 * under assets/icons). One family for nav, dashboard metrics and workflow
 * node kinds: charcoal #242424 silhouettes with mint accents, replacing
 * the mixed multicolor line glyphs that clashed with Pure Light. */
import dashboard from "../assets/icons/px-dashboard.png";
import agent from "../assets/icons/px-agent.png";
import teams from "../assets/icons/px-teams.png";
import tasks from "../assets/icons/px-tasks.png";
import workflow from "../assets/icons/px-workflow.png";
import computer from "../assets/icons/px-computer.png";
import skills from "../assets/icons/px-skills.png";
import plug from "../assets/icons/px-plug.png";
import book from "../assets/icons/px-book.png";
import shield from "../assets/icons/px-shield.png";
import robot from "../assets/icons/px-robot.png";
import gauge from "../assets/icons/px-gauge.png";
import gear from "../assets/icons/px-gear.png";
import clock from "../assets/icons/px-clock.png";
import bolt from "../assets/icons/px-bolt.png";
import seal from "../assets/icons/px-seal.png";
import steps from "../assets/icons/px-steps.png";
import wrench from "../assets/icons/px-wrench.png";
import play from "../assets/icons/px-play.png";
import flag from "../assets/icons/px-flag.png";
import fork from "../assets/icons/px-fork.png";
import usercheck from "../assets/icons/px-usercheck.png";

export const PIXEL_ICONS = {
  dashboard, agent, teams, tasks, workflow, computer, skills, plug, book,
  shield, robot, gauge, gear, clock, bolt, seal, steps, wrench, play,
  flag, fork, usercheck,
} as const;

export type PixelIconName = keyof typeof PIXEL_ICONS;
