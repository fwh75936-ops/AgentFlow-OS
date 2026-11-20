import { COLS, ROWS, isWalkable, nearestWalkable } from "./OfficeMap";

export interface TilePoint { x: number; y: number }

/** Grid A* (4-directional) over the office collision grid.
 * Returns the path EXCLUDING the start tile, or [] when already there /
 * no route exists (callers fall back to the nearest walkable tile once —
 * never an unbounded retry loop). */
export function findPath(start: TilePoint, goal: TilePoint): TilePoint[] {
  const target = isWalkable(goal.x, goal.y) ? goal : nearestWalkable(goal.x, goal.y);
  if (start.x === target.x && start.y === target.y) return [];
  const k = (x: number, y: number) => y * COLS + x;
  const open = new Map<number, number>(); // key -> fScore
  const gScore = new Map<number, number>();
  const cameFrom = new Map<number, number>();
  const closed = new Set<number>();
  const h = (x: number, y: number) => Math.abs(x - target.x) + Math.abs(y - target.y);
  const sk = k(start.x, start.y);
  gScore.set(sk, 0);
  open.set(sk, h(start.x, start.y));
  const DIRS = [[1, 0], [-1, 0], [0, 1], [0, -1]] as const;
  let guard = 0;
  while (open.size > 0 && guard++ < 4000) {
    let current = -1;
    let best = Infinity;
    for (const [key, f] of open) if (f < best) { best = f; current = key; }
    if (current === k(target.x, target.y)) {
      const path: TilePoint[] = [];
      let cur = current;
      while (cur !== sk) {
        path.unshift({ x: cur % COLS, y: Math.floor(cur / COLS) });
        cur = cameFrom.get(cur)!;
      }
      return path;
    }
    open.delete(current);
    closed.add(current);
    const cx = current % COLS;
    const cy = Math.floor(current / COLS);
    for (const [dx, dy] of DIRS) {
      const nx = cx + dx;
      const ny = cy + dy;
      if (nx < 0 || ny < 0 || nx >= COLS || ny >= ROWS) continue;
      if (!isWalkable(nx, ny)) continue;
      const nk = k(nx, ny);
      if (closed.has(nk)) continue;
      const tentative = (gScore.get(current) ?? Infinity) + 1;
      if (tentative < (gScore.get(nk) ?? Infinity)) {
        cameFrom.set(nk, current);
        gScore.set(nk, tentative);
        open.set(nk, tentative + h(nx, ny));
      }
    }
  }
  return [];
}
