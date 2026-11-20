/** Pixel office map: one compact 36x24 tile grid (16px tiles -> 576x384
 * world) with TWO interchangeable calibrations behind one switch:
 *
 * - "flat": the previous GPT floor plan (src/assets/office-bg-gpt.png),
 *   a strict top-down painting; grid calibration measured off it with a
 *   tile overlay (walls, glass partitions, door gaps, painted-furniture
 *   collision footprints, workstation slots).
 * - "iso": the user's preferred slanted oblique plate
 *   (src/assets/office-bg-iso.png, same GPT model, same reference, kept
 *   in its original slanted composition recomposed to the 3:2 frame).
 *   The grid no longer matches the painting geometrically, so the
 *   walkable area is a calibrated floor mask (a trapezoid of row spans
 *   over the painted floor, minus painted-furniture footprints), seats
 *   are the painted chairs, and characters scale with depth (smaller
 *   near the north wall, larger near the south edge). Pathfinding is
 *   the same grid A*, so routes are approximations of the painted
 *   space — visual fidelity first, as the user chose.
 *
 * In BOTH views everything environmental is painted; nothing is drawn
 * procedurally. What stays dynamic is only the roster binding:
 * OfficeScene assigns each real agent one of the painted workstations
 * (per zone, in floor-plan order) and the agent's desk anchor is that
 * desk's painted chair tile.
 *
 * Flip STUDIO_VIEW to switch the whole studio (backdrop + calibration
 * + depth scaling) between the two plates.
 */

export const TILE = 16;
export const COLS = 36;
export const ROWS = 24;
export const WORLD_W = COLS * TILE;
export const WORLD_H = ROWS * TILE;

/** The one switch: "iso" = slanted oblique plate (current choice),
 * "flat" = the previous top-down floor plan. Everything below —
 * backdrop, zones, furniture, workstations, blocked grid, sprite
 * depth scaling — follows this constant. */
export const STUDIO_VIEW: "flat" | "iso" = "iso";

export type ZoneId = "chief" | "dev" | "test" | "data" | "meeting" | "rest" | "hall";

export interface Zone { id: ZoneId; label: string; x: number; y: number; w: number; h: number }

export type FurnitureType =
  | "desk" | "desk_test" | "desk_data" | "chief_desk" | "whiteboard"
  | "meeting_table" | "cabinet" | "shelf" | "sofa" | "coffee" | "plant"
  | "data_board" | "test_screen" | "computer_station"
  | "bookshelf" | "server" | "beanbag" | "coffee_table" | "stickyboard";

/** Collision footprints of the PAINTED furniture (the backdrop draws
 * them; nothing here is rendered as a sprite). Entries for the boards,
 * the test bench and the meeting table also carry the invisible click
 * zones OfficeScene hangs on them. Chair tiles are deliberately NOT
 * furniture: they are walkable anchors an agent stands on. */
export interface Furniture { type: FurnitureType; x: number; y: number; w: number; h: number; v?: number }

/** Workstation slots — the desks and chairs are PAINTED; a slot is the
 * binding between one painted desk and one real agent. OfficeScene
 * binds slots from the roster (per zone, agents sorted by id take the
 * slots in floor-plan order, stable across polls) and the slot's chair
 * tile becomes the agent's desk anchor: working agents sit on the
 * painted chair, aligned with the painted desk. `desk` stays in
 * BLOCKED as permanent floor plan whether the slot is occupied or not.
 * Agents beyond the slot count fall back to meeting seats. */
export interface Workstation {
  type: FurnitureType;
  desk: { x: number; y: number; w: number; h: number };
  chair: { x: number; y: number };
}
const ws = (type: FurnitureType, dx: number, dy: number, dw: number, dh: number, cx: number, cy: number): Workstation =>
  ({ type, desk: { x: dx, y: dy, w: dw, h: dh }, chair: { x: cx, y: cy } });

const key = (x: number, y: number) => `${x},${y}`;

/* ================================================================ flat ==
 * Calibration of the top-down floor plan (office-bg-gpt.png), measured
 * with a tile overlay. Layout:
 *   top row    chief office | dev area    | test area   (open plan)
 *   bottom row meeting room | rest lounge | data area   (glass rooms)
 * The top row is one open space. The meeting and data rooms are glass
 * enclosures; the painting draws the glass continuous, so the two door
 * gaps (meeting east wall row 20, data west wall row 18) are
 * calibration choices — a one-tile opening in an otherwise solid line.
 */

const FLAT_ZONES: Zone[] = [
  { id: "chief", label: "总管办公室", x: 1, y: 3, w: 11, h: 9 },
  { id: "dev", label: "开发工作区", x: 13, y: 3, w: 10, h: 8 },
  { id: "test", label: "测试工作区", x: 24, y: 3, w: 11, h: 8 },
  { id: "meeting", label: "会议区", x: 1, y: 12, w: 11, h: 11 },
  { id: "rest", label: "休息区", x: 13, y: 11, w: 10, h: 12 },
  { id: "data", label: "数据分析区", x: 24, y: 12, w: 11, h: 11 },
];

const FLAT_FURNITURE: Furniture[] = [
  // Chief office: big desk, flowchart board, bookcase, plants, planter.
  // The bookcase footprint is one tile narrower than painted (col 2
  // only): the painted desk, board and bookcase otherwise seal the
  // chief's chair pocket (cols 4-8, rows 3-4) on all four sides, and
  // col 3 is its only possible entry gap.
  { type: "chief_desk", x: 4, y: 5, w: 5, h: 3 },
  { type: "whiteboard", x: 8, y: 3, w: 3, h: 3 },
  { type: "bookshelf", x: 2, y: 3, w: 1, h: 3 },
  { type: "plant", x: 1, y: 2, w: 1, h: 3 },
  { type: "plant", x: 0, y: 5, w: 1, h: 6 },
  { type: "plant", x: 11, y: 3, w: 1, h: 3 },
  // Dev area: the two plants at the dev/test seam
  { type: "plant", x: 23, y: 3, w: 2, h: 3 },
  { type: "plant", x: 23, y: 6, w: 1, h: 3 },
  // Test area: server rack on the east wall, plants
  { type: "server", x: 34, y: 1, w: 1, h: 6 },
  { type: "plant", x: 33, y: 3, w: 1, h: 4 },
  { type: "plant", x: 34, y: 7, w: 1, h: 4 },
  // Click footprint for the test bench (its green-check monitor); the
  // same tiles are already blocked as workstation slots.
  { type: "test_screen", x: 29, y: 3, w: 4, h: 4 },
  // Meeting room: long table for eight, boards on the north glass face,
  // the west storage cabinet, one plant
  { type: "meeting_table", x: 4, y: 17, w: 6, h: 3 },
  { type: "whiteboard", x: 5, y: 13, w: 7, h: 3 },
  { type: "cabinet", x: 0, y: 12, w: 2, h: 5 },
  { type: "plant", x: 3, y: 13, w: 1, h: 3 },
  // Rest lounge: sofa + console shelf + side table, coffee table on the
  // rug, beanbag, plants
  { type: "sofa", x: 15, y: 15, w: 6, h: 3 },
  { type: "shelf", x: 20, y: 12, w: 2, h: 5 },
  { type: "shelf", x: 15, y: 13, w: 2, h: 2 },
  { type: "coffee_table", x: 16, y: 18, w: 4, h: 3 },
  { type: "beanbag", x: 20, y: 17, w: 2, h: 2 },
  { type: "plant", x: 14, y: 12, w: 1, h: 4 },
  { type: "plant", x: 22, y: 13, w: 1, h: 4 },
  { type: "plant", x: 15, y: 22, w: 1, h: 1 },
  { type: "plant", x: 21, y: 21, w: 1, h: 2 },
  // Data area: monitor row above the painted desks, bookcase, the
  // plant + filing-cabinet column at the room's west side, SE plant
  { type: "data_board", x: 26, y: 13, w: 7, h: 2 },
  { type: "bookshelf", x: 34, y: 12, w: 1, h: 6 },
  { type: "plant", x: 33, y: 13, w: 1, h: 2 },
  { type: "plant", x: 25, y: 13, w: 1, h: 7 },
  { type: "plant", x: 34, y: 19, w: 1, h: 4 },
];

/** Product roles use the two small painted desks in the chief office's
 * open south end, next to the chief and one doorway away from the
 * meeting room. */
const FLAT_WORKSTATIONS: Record<"dev" | "test" | "data" | "product", Workstation[]> = {
  dev: [
    ws("desk", 13, 3, 4, 4, 15, 7), ws("desk", 17, 3, 4, 4, 19, 7),
    ws("desk", 21, 3, 3, 4, 22, 7),
  ],
  test: [
    ws("desk_test", 25, 3, 4, 4, 27, 7), ws("desk_test", 29, 3, 4, 4, 30, 7),
  ],
  data: [
    ws("desk_data", 26, 15, 4, 3, 27, 18), ws("desk_data", 30, 15, 4, 3, 30, 18),
  ],
  product: [
    ws("desk", 3, 9, 3, 2, 4, 11), ws("desk", 7, 9, 3, 2, 8, 11),
  ],
};

/** Chair/standing anchors (walkable) for the painted fixed seats: the
 * chief's executive chair, the meeting table's eight painted chairs
 * and the rest lounge's standing spots. */
const FLAT_DESKS = {
  chief: { x: 5, y: 4 },
  meeting: [
    { x: 4, y: 16 }, { x: 6, y: 16 }, { x: 8, y: 16 },
    { x: 4, y: 20 }, { x: 6, y: 20 }, { x: 8, y: 20 },
    { x: 3, y: 18 }, { x: 10, y: 18 },
  ],
  rest: [
    { x: 15, y: 19 }, { x: 21, y: 20 }, { x: 18, y: 21 }, { x: 19, y: 12 },
    { x: 14, y: 20 }, { x: 22, y: 18 },
  ],
};

const FLAT_ANCHORS = {
  boardChief: { x: 9, y: 6 },
  boardMeeting: { x: 8, y: 16 },
  testBoard: { x: 30, y: 8 },
  dataBoard: { x: 29, y: 19 },
  computer: { x: 18, y: 9 },
};

/** The painted dev workstations double as the shared-computer click
 * target (tile rect). */
const FLAT_COMPUTER_ZONE = { x: 16, y: 6, w: 6, h: 3 };

function buildFlatWalls(): Set<string> {
  const walls = new Set<string>();
  const hLine = (x0: number, x1: number, y: number, gaps: number[] = []) => {
    for (let x = x0; x <= x1; x++) if (!gaps.includes(x)) walls.add(key(x, y));
  };
  const vLine = (x: number, y0: number, y1: number, gaps: number[] = []) => {
    for (let y = y0; y <= y1; y++) if (!gaps.includes(y)) walls.add(key(x, y));
  };
  // Outer border + the painting's north wall / window band (rows 0-2)
  hLine(0, COLS - 1, 0); hLine(0, COLS - 1, 1); hLine(0, COLS - 1, 2);
  hLine(0, COLS - 1, ROWS - 1);
  vLine(0, 0, ROWS - 1); vLine(COLS - 1, 0, ROWS - 1);
  // Meeting room glass box: north face (row 12), east face (col 12)
  // with the door gap at row 20
  hLine(1, 12, 12);
  vLine(12, 12, 22, [20]);
  // Data room glass box: north face (row 12), west face (col 23)
  // with the door gap at row 18
  hLine(23, 34, 12);
  vLine(23, 12, 22, [18]);
  return walls;
}

const FLAT_WALLS = buildFlatWalls();

/** Wall tiles that are glass partitions in the painting: the meeting
 * and data room enclosures. Still solid. */
const FLAT_GLASS: Set<string> = (() => {
  const glass = new Set<string>();
  for (let x = 1; x <= 12; x++) glass.add(key(x, 12));
  for (let y = 12; y <= 22; y++) if (y !== 20) glass.add(key(12, y));
  for (let x = 23; x <= 34; x++) glass.add(key(x, 12));
  for (let y = 12; y <= 22; y++) if (y !== 18) glass.add(key(23, y));
  return glass;
})();

function buildFlatBlocked(): Set<string> {
  const blocked = new Set<string>(FLAT_WALLS);
  for (const f of FLAT_FURNITURE) {
    for (let x = f.x; x < f.x + f.w; x++)
      for (let y = f.y; y < f.y + f.h; y++) blocked.add(key(x, y));
  }
  for (const list of Object.values(FLAT_WORKSTATIONS)) {
    for (const s of list) {
      for (let x = s.desk.x; x < s.desk.x + s.desk.w; x++)
        for (let y = s.desk.y; y < s.desk.y + s.desk.h; y++) blocked.add(key(x, y));
    }
  }
  return blocked;
}

const FLAT_BLOCKED = buildFlatBlocked();

/* ================================================================= iso ==
 * Calibration of the slanted oblique plate (office-bg-iso.png — the
 * first GPT draft recomposed to the 3:2 frame, slanted view kept).
 * Measured with a tile overlay on the painting plus dark-pixel
 * clustering for the chair centres. The painting's interior is nearly
 * rectangular (the recomposition straightened the trapezoid), so the
 * walkable mask is: the interior floor (rows 5-22, the south row only
 * at the entrance strip) minus the wall/glass lines below and the
 * painted-furniture footprints. Known calibration compromises:
 * - The chief's chair pocket (row 5, cols 5-10) is entered from the
 *   east through col 10: the whiteboard footprint is one tile narrower
 *   than painted (cols 11-12) so its south-west leg tile stays floor.
 *   The small plant painted ON the chief's desk (col 8, row 5) shares
 *   its tile with the pocket's walkway — the desk surface overhangs
 *   the corridor there; an agent passing behind the desk overlaps the
 *   pot slightly.
 * - The glass of both bottom rooms is painted continuous; door tiles
 *   are calibration choices, as in the flat plan: meeting east face
 *   row 19, data north face col 27 (the practical entrance) and data
 *   west face row 18 (opens onto the landing behind the sofa, which
 *   connects through the room). Inside the data room, the bench seals
 *   the north strip from the chairs except at the col-24 passage
 *   between the west glass and the plant (see ISO_FURNITURE).
 * - Product roles have no painted desks in this plate (the two small
 *   chief-office desks exist only in the flat painting), so product
 *   agents take meeting seats through the normal overflow binding.
 */

const ISO_ZONES: Zone[] = [
  { id: "chief", label: "总管办公室", x: 1, y: 4, w: 12, h: 8 },
  { id: "dev", label: "开发工作区", x: 13, y: 4, w: 11, h: 7 },
  { id: "test", label: "测试工作区", x: 24, y: 4, w: 11, h: 7 },
  { id: "meeting", label: "会议区", x: 1, y: 15, w: 12, h: 8 },
  { id: "rest", label: "休息区", x: 13, y: 14, w: 10, h: 9 },
  { id: "data", label: "数据分析区", x: 23, y: 15, w: 12, h: 8 },
];

const ISO_FURNITURE: Furniture[] = [
  // Chief office: bookcase, west plant + planter strip, big desk,
  // flowchart board (narrowed, see header), plant at the dev seam.
  { type: "bookshelf", x: 3, y: 4, w: 2, h: 3 },
  { type: "plant", x: 1, y: 3, w: 2, h: 4 },
  { type: "plant", x: 0, y: 5, w: 2, h: 5 },
  { type: "chief_desk", x: 4, y: 6, w: 6, h: 2 },
  { type: "whiteboard", x: 11, y: 4, w: 2, h: 2 },
  { type: "plant", x: 13, y: 4, w: 1, h: 3 },
  // Dev/test seam plant (its cells overlap the bench's east end).
  { type: "plant", x: 23, y: 4, w: 2, h: 2 },
  // Test area: small plant in front of the bench's west end, the
  // green-check bench click footprint, plant + server rack east.
  { type: "plant", x: 25, y: 5, w: 1, h: 3 },
  { type: "test_screen", x: 29, y: 4, w: 4, h: 3 },
  { type: "plant", x: 33, y: 5, w: 1, h: 2 },
  { type: "server", x: 34, y: 3, w: 2, h: 5 },
  { type: "plant", x: 34, y: 8, w: 1, h: 3 },
  // Meeting room: board wall (whiteboard + sticky board), west plant,
  // the long table (chairs are anchors, not furniture).
  { type: "whiteboard", x: 5, y: 15, w: 8, h: 2 },
  { type: "plant", x: 3, y: 15, w: 2, h: 3 },
  { type: "meeting_table", x: 4, y: 18, w: 8, h: 3 },
  // Rest lounge: west plant + side table (cols 15-16; col 14 stays a
  // walkable lane between the meeting glass and the plant — the only
  // route into the lounge's west strip and the meeting door), sofa,
  // coffee-machine shelf, east plant, coffee table, two round stools,
  // beanbag, and the two entrance plants flanking the south doors.
  { type: "plant", x: 15, y: 15, w: 2, h: 3 },
  { type: "sofa", x: 16, y: 17, w: 6, h: 3 },
  // One-tile buffer directly behind the sofa's back (row 16, centre
  // span): characters are depth-sorted only among themselves over the
  // single painted backdrop, so feet on the sofa's back edge would
  // draw over it. The strip is a dead-end spur off the west lane —
  // no anchor needs it — so it is closed (risk mitigation suggested
  // by gpt-6.2-sol's review of the mask compromises, verified against
  // the reachability check).
  { type: "sofa", x: 17, y: 16, w: 3, h: 1 },
  { type: "shelf", x: 20, y: 15, w: 2, h: 3 },
  { type: "plant", x: 22, y: 15, w: 1, h: 3 },
  { type: "coffee_table", x: 17, y: 20, w: 4, h: 2 },
  { type: "coffee", x: 15, y: 20, w: 1, h: 1 },
  { type: "coffee", x: 21, y: 20, w: 1, h: 1 },
  { type: "beanbag", x: 22, y: 20, w: 2, h: 2 },
  // The single floor tile south-east of the beanbag (22,22) is a
  // one-cell pocket enclosed by the beanbag, the east entrance plant
  // and the south glass: walkable on paper but unreachable, so block
  // it outright — otherwise nearestWalkable could snap a target in.
  { type: "plant", x: 22, y: 22, w: 1, h: 1 },
  { type: "plant", x: 13, y: 21, w: 1, h: 2 },
  { type: "plant", x: 21, y: 21, w: 1, h: 2 },
  // Data area: west plant (blocked only where its pot meets the
  // bench, cols 25 rows 17-18 — already desk cells; the foliage row
  // 16 stays walkable so the room's north strip connects west into
  // the col-24 passage between the glass and the plant, the only
  // route from the strip down to the chairs; agents overlap foliage
  // and the pot's west edge there), the small cabinet under it
  // (rows 20-21, so the passage reaches the chair row at row 19),
  // the bench's east end (slots cover cols 25-32), east plant,
  // bookcase, SE plant.
  { type: "plant", x: 25, y: 17, w: 1, h: 2 },
  { type: "cabinet", x: 24, y: 20, w: 1, h: 2 },
  { type: "desk_data", x: 33, y: 17, w: 2, h: 2 },
  { type: "plant", x: 34, y: 16, w: 1, h: 2 },
  { type: "bookshelf", x: 35, y: 15, w: 1, h: 6 },
  { type: "plant", x: 34, y: 20, w: 2, h: 3 },
];

/** Chair tiles measured off the painting (dark-pixel cluster centres):
 * dev (15,7) (18,7) (21,7), test (27,7) (29,7), data (27,19) (30,19).
 * Product has no painted desks in this plate — the slot list is empty
 * and product agents fall back to meeting seats (see header). */
const ISO_WORKSTATIONS: Record<"dev" | "test" | "data" | "product", Workstation[]> = {
  dev: [
    ws("desk", 14, 5, 3, 2, 15, 7), ws("desk", 17, 5, 3, 2, 18, 7),
    ws("desk", 20, 5, 5, 2, 21, 7),
  ],
  test: [
    ws("desk_test", 26, 5, 3, 2, 27, 7), ws("desk_test", 29, 5, 4, 2, 29, 7),
  ],
  data: [
    ws("desk_data", 25, 17, 4, 2, 27, 19), ws("desk_data", 29, 17, 4, 2, 30, 19),
  ],
  product: [],
};

const ISO_DESKS = {
  chief: { x: 6, y: 5 },
  meeting: [
    { x: 5, y: 17 }, { x: 7, y: 17 }, { x: 9, y: 17 },
    { x: 5, y: 21 }, { x: 7, y: 21 }, { x: 9, y: 21 },
    { x: 3, y: 19 }, { x: 12, y: 19 },
  ],
  rest: [
    { x: 14, y: 19 }, { x: 18, y: 22 }, { x: 20, y: 22 }, { x: 22, y: 19 },
    { x: 15, y: 22 }, { x: 19, y: 15 },
  ],
};

const ISO_ANCHORS = {
  boardChief: { x: 11, y: 6 },
  boardMeeting: { x: 7, y: 17 },
  testBoard: { x: 29, y: 8 },
  dataBoard: { x: 29, y: 20 },
  computer: { x: 19, y: 9 },
};

const ISO_COMPUTER_ZONE = { x: 14, y: 5, w: 11, h: 2 };

function buildIsoWalls(): Set<string> {
  const walls = new Set<string>();
  const hLine = (x0: number, x1: number, y: number, gaps: number[] = []) => {
    for (let x = x0; x <= x1; x++) if (!gaps.includes(x)) walls.add(key(x, y));
  };
  const vLine = (x: number, y0: number, y1: number, gaps: number[] = []) => {
    for (let y = y0; y <= y1; y++) if (!gaps.includes(y)) walls.add(key(x, y));
  };
  // North wall + window band (rows 0-4) and the south wall (row 23,
  // plus row 22 outside the entrance strip).
  for (let y = 0; y <= 4; y++) hLine(0, COLS - 1, y);
  hLine(0, COLS - 1, ROWS - 1);
  hLine(0, 12, 22); hLine(23, COLS - 1, 22);
  vLine(0, 5, 22); vLine(COLS - 1, 5, 22);
  // Meeting room's glass vestibule on the west (cols 1-2).
  vLine(1, 15, 22); vLine(2, 15, 22);
  // Glass north faces of the two bottom rooms (row 15); the data
  // room's north face has the door gap at col 27 (its west door at
  // (23,18) opens onto a one-cell landing behind the sofa, so the
  // north door is the room's practical entrance from the hall).
  hLine(3, 12, 15); hLine(23, 34, 15, [27]);
  // Meeting east glass (col 13) with the door gap at row 19; data west
  // glass (col 23) with the door gap at row 18.
  vLine(13, 15, 21, [19]);
  vLine(23, 15, 21, [18]);
  return walls;
}

const ISO_WALLS = buildIsoWalls();

/** Glass partition tiles in the painting (subset of the walls). */
const ISO_GLASS: Set<string> = (() => {
  const glass = new Set<string>();
  for (let x = 3; x <= 12; x++) glass.add(key(x, 15));
  for (let x = 23; x <= 34; x++) if (x !== 27) glass.add(key(x, 15));
  for (let y = 15; y <= 21; y++) if (y !== 19) glass.add(key(13, y));
  for (let y = 15; y <= 21; y++) if (y !== 18) glass.add(key(23, y));
  for (let x = 1; x <= 12; x++) glass.add(key(x, 22));
  for (let x = 23; x <= 34; x++) glass.add(key(x, 22));
  return glass;
})();

function buildIsoBlocked(): Set<string> {
  const blocked = new Set<string>(ISO_WALLS);
  for (const f of ISO_FURNITURE) {
    for (let x = f.x; x < f.x + f.w; x++)
      for (let y = f.y; y < f.y + f.h; y++) blocked.add(key(x, y));
  }
  for (const list of Object.values(ISO_WORKSTATIONS)) {
    for (const s of list) {
      for (let x = s.desk.x; x < s.desk.x + s.desk.w; x++)
        for (let y = s.desk.y; y < s.desk.y + s.desk.h; y++) blocked.add(key(x, y));
    }
  }
  return blocked;
}

const ISO_BLOCKED = buildIsoBlocked();

/** Depth scaling for the oblique plate (world px): characters near the
 * north wall (small y) render smaller, near the south edge larger.
 * Coefficients derived from the painting's own furniture: painted
 * chair widths are 57 img px at the north (dev) depth vs 60-63 at the
 * south (data) depth, a ratio of 1.10. With the linear map anchored
 * at the floor's feet range (row 5 = 88 to row 22 = 360), sN = 0.90
 * and sS = 1.03 make the character scale ratio between the data
 * chairs' depth (feet y 312, s = 1.007) and the dev chairs' depth
 * (feet y 120, s = 0.915) exactly 1.10, and the scaled character
 * width at the north depth (29.3 world px) is 1.37x the painted
 * chair width there (21.4 world px) — inside the 1.35-1.55 band the
 * flat view's proportions sit in. (Derivation cross-checked with
 * gpt-6.2-sol high-reasoning from the measured cluster data, then
 * re-verified arithmetically; see iso/ check notes.) */
const ISO_DEPTH = { yNorth: 88, ySouth: 360, sNorth: 0.9, sSouth: 1.03 };

/* ============================================================ unified == */

export const ZONES: Zone[] = STUDIO_VIEW === "iso" ? ISO_ZONES : FLAT_ZONES;
export const FURNITURE: Furniture[] = STUDIO_VIEW === "iso" ? ISO_FURNITURE : FLAT_FURNITURE;
export const WORKSTATIONS: Record<"dev" | "test" | "data" | "product", Workstation[]> =
  STUDIO_VIEW === "iso" ? ISO_WORKSTATIONS : FLAT_WORKSTATIONS;
export const DESKS = STUDIO_VIEW === "iso" ? ISO_DESKS : FLAT_DESKS;
export const ANCHORS = STUDIO_VIEW === "iso" ? ISO_ANCHORS : FLAT_ANCHORS;
export const COMPUTER_ZONE = STUDIO_VIEW === "iso" ? ISO_COMPUTER_ZONE : FLAT_COMPUTER_ZONE;
export const WALLS: Set<string> = STUDIO_VIEW === "iso" ? ISO_WALLS : FLAT_WALLS;
export const GLASS: Set<string> = STUDIO_VIEW === "iso" ? ISO_GLASS : FLAT_GLASS;
export const BLOCKED: Set<string> = STUDIO_VIEW === "iso" ? ISO_BLOCKED : FLAT_BLOCKED;

export function isWalkable(x: number, y: number): boolean {
  return x >= 0 && y >= 0 && x < COLS && y < ROWS && !BLOCKED.has(key(x, y));
}

/** Nearest walkable tile to (x,y) by expanding ring search (bounded). */
export function nearestWalkable(x: number, y: number): { x: number; y: number } {
  if (isWalkable(x, y)) return { x, y };
  for (let r = 1; r <= 6; r++) {
    for (let dx = -r; dx <= r; dx++) {
      for (let dy = -r; dy <= r; dy++) {
        if (Math.max(Math.abs(dx), Math.abs(dy)) !== r) continue;
        if (isWalkable(x + dx, y + dy)) return { x: x + dx, y: y + dy };
      }
    }
  }
  return { x: 18, y: 12 }; // the lounge's open north strip, always walkable
}

export function zoneAt(x: number, y: number): ZoneId {
  for (const z of ZONES) {
    if (x >= z.x && x < z.x + z.w && y >= z.y && y < z.y + z.h) return z.id;
  }
  return "hall";
}

/** Render scale for a character whose feet stand at world y. Flat view:
 * always 1. Iso view: linear depth scaling between the floor's north
 * and south edges (clamped outside), so agents near the back wall read
 * as farther away, matching the plate's perspective. */
export function spriteScaleAt(worldY: number): number {
  if (STUDIO_VIEW !== "iso") return 1;
  const t = (worldY - ISO_DEPTH.yNorth) / (ISO_DEPTH.ySouth - ISO_DEPTH.yNorth);
  const c = Math.max(0, Math.min(1, t));
  return ISO_DEPTH.sNorth + (ISO_DEPTH.sSouth - ISO_DEPTH.sNorth) * c;
}
