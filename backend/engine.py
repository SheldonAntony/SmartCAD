"""Deterministic geometry engine.

Pipeline:
  envelope -> room programme -> footprint sizing -> structural grid ->
  circulation core -> grid-snapped recursive split -> hard-constraint search ->
  circulation graph -> openings, columns, parking, balconies

Conventions (violating these is how bugs get in):
  * Rule A -- metres everywhere internally. Feet appear only at the input edge.
  * Rule B -- all geometry math lives in this file. The renderers only draw.
  * Rule C -- y = 0 is the STREET side of the plot; y grows toward the rear.
              "front" always means smaller y. The engine, the validator and both
              renderers all depend on this, so it is stated once, here.
  * Rule D -- ONE structural grid for the whole building. Columns sit at grid
              intersections, so every column is continuous from foundation to
              roof by construction. Upper floors are free to arrange rooms
              differently; they are not free to move the grid.
"""
import math
import random

from .models import ProjectSpec
from .rooms import ACCESS_PARENTS, ADJACENCY, HUB_TYPES, ROOM_SPECS, normalize_type

FT_TO_M = 0.3048
RNG_SEED = 42
N_CANDIDATES = 500
ASPECT_LIMIT = 3.0            # single source of truth; validator imports this

MIN_BAY = 2.4                 # m -- below this a bay is not a usable room width
MAX_BAY = 4.5                 # m -- past this an ordinary RCC beam has to deepen
CIRCULATION_ALLOWANCE = 0.12  # fraction of programme area lost to walls/passage
DOOR_CLEAR = 0.9              # m -- a shorter shared wall cannot hold a door
PARKING_LEN = 5.0             # m -- standard car
PARKING_WID = 2.5
EPS = 0.02


def ft_to_m(x: float) -> float:
    return x * FT_TO_M


class Rect:
    __slots__ = ("x", "y", "w", "h")

    def __init__(self, x, y, w, h):
        self.x, self.y, self.w, self.h = x, y, w, h

    @property
    def area(self):
        return self.w * self.h

    def key(self):
        return (round(self.x, 3), round(self.y, 3), round(self.w, 3), round(self.h, 3))

    def __repr__(self):
        return f"Rect({self.x:.2f},{self.y:.2f},{self.w:.2f},{self.h:.2f})"


def adjacency_weight(t1: str, t2: str) -> float:
    a, b = normalize_type(t1), normalize_type(t2)
    return ADJACENCY.get((a, b), ADJACENCY.get((b, a), 0))


def shared_wall(a: Rect, b: Rect) -> float:
    """Length of wall shared between two rects. 0 if they do not touch."""
    if abs(a.x + a.w - b.x) < 0.01 or abs(b.x + b.w - a.x) < 0.01:
        return max(0.0, min(a.y + a.h, b.y + b.h) - max(a.y, b.y))
    if abs(a.y + a.h - b.y) < 0.01 or abs(b.y + b.h - a.y) < 0.01:
        return max(0.0, min(a.x + a.w, b.x + b.w) - max(a.x, b.x))
    return 0.0


# ---------------------------------------------------------------------------
# Rule D -- the structural grid
# ---------------------------------------------------------------------------

def make_grid(fp: Rect, min_cells: int = 1):
    """Column grid over the footprint. Bays are held inside [MIN_BAY, MAX_BAY]
    where the footprint allows, and subdivided further only when a floor needs
    more cells than the coarse grid offers.

    Returns (gx, gy): absolute coordinates of the grid lines, the outermost
    lines being the footprint edges. Computed ONCE for the whole building."""
    def axis(length):
        n = max(1, math.ceil(length / MAX_BAY - 1e-9))
        while n > 1 and length / n < MIN_BAY:
            n -= 1
        return n

    nx, ny = axis(fp.w), axis(fp.h)
    _ = min_cells   # deliberately unused: bay size is a structural property,
                    # not a function of how many rooms this floor happens to hold
    gx = [fp.x + fp.w * i / nx for i in range(nx + 1)]
    gy = [fp.y + fp.h * j / ny for j in range(ny + 1)]
    return gx, gy


def _snap(target, lines, lo, hi, margin=0.35):
    """Nearest grid line strictly inside (lo, hi); None if the span is too
    narrow to be cut on the grid at all."""
    inner = [v for v in lines if lo + margin < v < hi - margin]
    if not inner:
        return None
    return min(inner, key=lambda v: abs(v - target))


def split(rect: Rect, rooms: list, gx: list, gy: list) -> list:
    """Recursive rectangle tiling. Every cut snaps to a structural grid line
    when one is available in that axis, so partition walls land on beam lines.
    A span narrower than one bay falls back to a free cut -- that wall is a
    light partition inside a bay, carried by the slab, needing no column under
    it. Either way the COLUMNS never move, which is the property that matters.

    rooms = [{"id":..., "type":..., "area":...}, ...] -> [(room, Rect), ...]
    """
    if len(rooms) == 1:
        return [(rooms[0], Rect(rect.x, rect.y, rect.w, rect.h))]

    total = sum(r["area"] for r in rooms)
    acc, i = 0.0, 0
    for i in range(len(rooms)):
        if acc + rooms[i]["area"] > total / 2:
            break
        acc += rooms[i]["area"]
    i = max(1, min(i, len(rooms) - 1))
    A, B = rooms[:i], rooms[i:]
    frac = sum(r["area"] for r in A) / total
    x, y, w, h = rect.x, rect.y, rect.w, rect.h

    order = ["x", "y"] if w >= h else ["y", "x"]
    cut_axis, cut = None, None
    for axis_name in order:
        c = (_snap(x + w * frac, gx, x, x + w) if axis_name == "x"
             else _snap(y + h * frac, gy, y, y + h))
        if c is not None:
            cut_axis, cut = axis_name, c
            break
    if cut_axis is None:                       # no grid line fits -> partition
        cut_axis = order[0]
        cut = (x + w * frac) if cut_axis == "x" else (y + h * frac)

    if cut_axis == "x":
        return (split(Rect(x, y, cut - x, h), A, gx, gy)
                + split(Rect(cut, y, x + w - cut, h), B, gx, gy))
    return (split(Rect(x, y, w, cut - y), A, gx, gy)
            + split(Rect(x, cut, w, y + h - cut), B, gx, gy))


# ---------------------------------------------------------------------------
# Area budget -- bounded on BOTH sides
# ---------------------------------------------------------------------------

def compute_budget(room_types: list, plate_area: float) -> list:
    """Scale ideal areas to fill plate_area, then clamp each room into its
    [min, max] band and redistribute among the unclamped rooms. The max clamp
    is what stops a bathroom reaching 36 m^2 on a large plot; sizing the
    footprint from the programme (see compute_footprint) is what makes the
    clamp bite, since split() itself only honours ratios."""
    n = len(room_types)
    if n == 0:
        return []
    ideal = [ROOM_SPECS[t]["ideal"] for t in room_types]
    mins = [ROOM_SPECS[t]["min"] for t in room_types]
    maxs = [ROOM_SPECS[t]["max"] for t in room_types]
    total_ideal = sum(ideal)
    scale = plate_area / total_ideal if total_ideal > 0 else 1.0
    areas = [a * scale for a in ideal]

    clamped = [False] * n
    for _ in range(10):
        delta = 0.0
        for k in range(n):
            if clamped[k]:
                continue
            if areas[k] < mins[k]:
                delta += mins[k] - areas[k]
                areas[k], clamped[k] = mins[k], True
            elif areas[k] > maxs[k]:
                delta -= areas[k] - maxs[k]
                areas[k], clamped[k] = maxs[k], True
        if abs(delta) < 1e-9:
            break
        pool = [k for k in range(n) if not clamped[k]]
        pool_total = sum(areas[k] for k in pool)
        if not pool or pool_total <= 1e-9:
            break
        for k in pool:
            areas[k] -= delta * (areas[k] / pool_total)
    return areas


def programme_area(types: list) -> float:
    """Target built area for a set of rooms, including circulation."""
    return sum(ROOM_SPECS[t]["ideal"] for t in types) * (1.0 + CIRCULATION_ALLOWANCE)


# ---------------------------------------------------------------------------
# Hard constraints
# ---------------------------------------------------------------------------

def touches_boundary(rect: Rect, fp: Rect) -> bool:
    return (abs(rect.x - fp.x) < EPS
            or abs(rect.x + rect.w - (fp.x + fp.w)) < EPS
            or abs(rect.y - fp.y) < EPS
            or abs(rect.y + rect.h - (fp.y + fp.h)) < EPS)


def exterior_violations(items: list, fp: Rect) -> int:
    """Rooms failing their ventilation / daylight / position requirement. A
    HARD constraint in the search, not a scoring preference -- this is what
    stops a bathroom or a balcony landing in the middle of the plan."""
    bad = 0
    for room, rect in items:
        need = ROOM_SPECS[room["type"]]["exterior"]
        if need in ("required", "cantilever"):
            if not touches_boundary(rect, fp):
                bad += 1
        elif need == "front" and abs(rect.y - fp.y) > EPS:
            bad += 1
    return bad


def build_access_graph(items: list, root_types: tuple):
    """Fixpoint reachability from the front door (ground floor) or the landing
    (upper floors), walking only edges a room is ALLOWED to be entered through.
    Terminal rooms are never passed through, so no route crosses a bedroom.

    Returns (reachable_ids, parent_of) where parent_of[child] = parent, which
    is also the edge the door gets cut in."""
    neigh = {}
    for a, (ra, rect_a) in enumerate(items):
        neigh[ra["id"]] = [items[b][0] for b in range(len(items))
                           if b != a and shared_wall(rect_a, items[b][1]) >= DOOR_CLEAR]

    reachable, parent_of = set(), {}
    for room, _ in items:
        if room["type"] in root_types:
            reachable.add(room["id"])
            break                                   # exactly one root

    changed = True
    while changed:
        changed = False
        for room, _ in items:
            rid = room["id"]
            if rid in reachable:
                continue
            allowed = ACCESS_PARENTS.get(room["type"], set())
            for cand in neigh[rid]:
                if cand["id"] not in reachable or cand["type"] not in allowed:
                    continue
                # you may ENTER a terminal room, never route onward through it
                if cand["type"] not in HUB_TYPES and cand["id"] not in reachable:
                    continue
                reachable.add(rid)
                parent_of[rid] = cand["id"]
                changed = True
                break
    return reachable, parent_of


def _indoor(items: list) -> list:
    return [(r, rc) for r, rc in items if ROOM_SPECS[r["type"]]["access"] != "outside"]


def access_violations(items: list, root_types: tuple) -> int:
    live = _indoor(items)
    reachable, _ = build_access_graph(live, root_types)
    return sum(1 for r, _ in live if r["id"] not in reachable)


def aspect_penalty(rect: Rect) -> float:
    long_side, short_side = max(rect.w, rect.h), min(rect.w, rect.h)
    if short_side <= 0:
        return 1000.0
    return max(0.0, long_side / short_side - ASPECT_LIMIT) * 10.0


def min_width_penalty(room: dict, rect: Rect) -> float:
    return max(0.0, ROOM_SPECS[room["type"]]["min_w"] - min(rect.w, rect.h)) * 20.0


def evaluate_layout(placed: list, fixed: list, fp: Rect, root_types: tuple) -> tuple:
    """Lexicographic rank key, lowest wins:
        (unreachable rooms, ventilation/position failures, size failures,
         -adjacency score, penalty magnitude)
    Order matters: a plan you cannot walk through, or one with a windowless
    bathroom, is worse than one that merely scores poorly on adjacency."""
    all_items = placed + fixed
    adjacency_score = 0.0
    for i in range(len(all_items)):
        room_a, rect_a = all_items[i]
        for j in range(i + 1, len(all_items)):
            room_b, rect_b = all_items[j]
            wall = shared_wall(rect_a, rect_b)
            if wall > 0:
                adjacency_score += adjacency_weight(room_a["type"], room_b["type"]) * wall

    size_bad, penalty_total = 0, 0.0
    for room, rect in placed:
        ap, wp = aspect_penalty(rect), min_width_penalty(room, rect)
        row = ROOM_SPECS[room["type"]]
        under = max(0.0, row["min"] - rect.area) * 15.0
        over = max(0.0, rect.area - row["max"]) * 3.0
        penalty_total += ap + wp + under + over
        if ap > 0 or wp > 0 or under > 0:
            size_bad += 1

    vent_bad = exterior_violations(all_items, fp)
    unreachable = access_violations(all_items, root_types)
    return unreachable, vent_bad, size_bad, adjacency_score, penalty_total


def deal_to_regions(rooms: list, regions: list) -> list:
    """Deal rooms into regions, each room going to whichever region is
    currently furthest from full as a fraction of its area. The landing is
    pinned to the core spine so it sits directly off the stair."""
    groups = [[] for _ in regions]
    used = [0.0] * len(regions)
    rest = []
    for r in rooms:
        if r["type"] == "corridor" and len(regions) > 1:
            groups[-1].append(r)
            used[-1] += ROOM_SPECS[r["type"]]["ideal"]
        else:
            rest.append(r)

    for r in rest:
        need = ROOM_SPECS[r["type"]]["ideal"]
        k = min(range(len(regions)),
                key=lambda i: (used[i] + need) / regions[i].area
                if regions[i].area > 1e-6 else float("inf"))
        groups[k].append(r)
        used[k] += need

    # a region with no rooms would be an untiled void inside the footprint
    for i in range(len(groups)):
        if groups[i]:
            continue
        donor = max(range(len(groups)), key=lambda j: len(groups[j]))
        if len(groups[donor]) > 1:
            groups[i].append(groups[donor].pop())
    return groups


def best_split(regions, rooms, fixed, fp, gx, gy, root_types, rng, tag="F"):
    """Search shuffles of the room order. The order drives BOTH which region a
    room lands in and how the region is subdivided, so one shuffle explores
    both decisions at once. Regions are fixed rectangles shared by every floor,
    so cross-floor alignment survives whatever the search picks."""
    if not rooms:
        return [], 0.0
    best_key, best_placed, best_adj = None, None, 0.0
    for _ in range(N_CANDIDATES):
        order = rooms[:]
        rng.shuffle(order)
        groups = deal_to_regions(order, regions)
        placed = []
        for ri, (region, group) in enumerate(zip(regions, groups)):
            if not group:
                continue
            areas = compute_budget([r["type"] for r in group], region.area)
            candidate = [{**group[k], "area": areas[k]} for k in range(len(group))]
            # Every room is at its maximum and the plate still is not full: the
            # surplus becomes a landing or an open terrace rather than being
            # smeared back over the rooms.
            slack = region.area - sum(areas)
            if slack > 0.75:
                filler = "corridor" if slack <= ROOM_SPECS["corridor"]["max"] else "open_terrace"
                candidate.append({"id": f"{tag}_{filler}_r{ri}", "type": filler, "area": slack})
            placed += split(region, candidate, gx, gy)
        unreach, vent, size_bad, adj, penalty = evaluate_layout(placed, fixed, fp, root_types)
        key = (unreach, vent, size_bad, -adj, penalty)
        if best_key is None or key < best_key:
            best_key, best_placed, best_adj = key, placed, adj
    return best_placed, best_adj


# ---------------------------------------------------------------------------
# Room programme
# ---------------------------------------------------------------------------

def build_room_requests(spec: ProjectSpec):
    """floor index -> list of room types. Returns (floor_rooms, has_stair, notes).

    Bedrooms are dealt ROUND-ROBIN across upper floors, not by ceil(), so no
    floor is starved and no floor is left holding nothing but a staircase.
    Any upper floor that still ends up with no habitable room is dropped and
    reported, rather than being built and billed as an empty storey."""
    notes = []
    floors = spec.floors
    floor_rooms = {i: [] for i in range(floors)}
    floor_rooms[0] += ["entrance", "living", "dining", "kitchen", "utility"]

    beds = (["master_bedroom"] + ["bedroom"] * (spec.bedrooms - 1)) if spec.bedrooms > 0 else []

    if floors == 1:
        floor_rooms[0] += beds
        floor_rooms[0] += ["bathroom"] * spec.bathrooms
        if spec.balcony and beds:
            floor_rooms[0].append("balcony")
        return floor_rooms, False, notes

    ground_bath = 1 if spec.bathrooms >= 2 else 0
    floor_rooms[0] += ["bathroom"] * ground_bath

    upper = list(range(1, floors))
    for k, b in enumerate(beds):
        floor_rooms[upper[k % len(upper)]].append(b)
    for k in range(spec.bathrooms - ground_bath):
        floor_rooms[upper[k % len(upper)]].append("bathroom")

    empty = [f for f in upper
             if not any(t in ("bedroom", "master_bedroom") for t in floor_rooms[f])]
    if empty:
        for f in empty:                       # push any stragglers down a floor
            keep = [f2 for f2 in sorted(floor_rooms) if f2 not in empty]
            target = keep[-1] if keep else 0
            floor_rooms[target] += floor_rooms[f]
            del floor_rooms[f]
        notes.append(
            f"{len(empty)} upper floor(s) would have held no bedroom and were dropped -- "
            f"a storey containing only a stair and a balcony is not a storey. "
            f"{spec.bedrooms} bedroom(s) do not need {spec.floors} floors; "
            f"building {len(floor_rooms)} instead.")

    for f in sorted(floor_rooms):
        if f == 0:
            continue
        floor_rooms[f].append("corridor")       # landing, keeps front rooms reachable
        if spec.balcony:
            floor_rooms[f].append("balcony")

    if spec.balcony and len(floor_rooms) == 1:
        floor_rooms[0].append("balcony")
    return floor_rooms, True, notes


# ---------------------------------------------------------------------------
# Footprint, core, parking
# ---------------------------------------------------------------------------

def compute_footprint(spec: ProjectSpec, bw: float, bd: float,
                      floor_rooms: dict, has_stair: bool) -> Rect:
    """Size the building from the PROGRAMME, not from the plot.

    The old engine tiled the whole envelope, so a bigger plot simply inflated
    every room. Here the footprint is the area the rooms actually want, capped
    by the envelope -- which is also what makes ground coverage a real number
    instead of a restatement of the setbacks."""
    need = 0.0
    for types in floor_rooms.values():
        t = list(types) + (["staircase"] if has_stair else [])
        need = max(need, programme_area(t))

    avail_d = bd
    if spec.parking:
        # keep a strip at the front of the envelope open for the car + approach
        avail_d = max(bd * 0.45, bd - PARKING_LEN)
    max_area = bw * avail_d
    target = min(need, max_area)

    ratio = bw / avail_d if avail_d > 0 else 1.0
    fd = math.sqrt(target / ratio) if ratio > 0 else avail_d
    fw = target / fd if fd > 0 else bw
    fw, fd = min(fw, bw), min(fd, avail_d)
    if fw * fd < target - 1e-6:                 # clamping lost area -- grow the other side
        if fw < bw - 1e-6:
            fw = min(bw, target / fd)
        elif fd < avail_d - 1e-6:
            fd = min(avail_d, target / fw)

    # Rule C: push the house to the REAR so the front stays open for the car,
    # the approach and the entrance.
    return Rect((bw - fw) / 2.0, bd - fd, fw, fd)


def make_core(fp: Rect, gx: list, gy: list):
    """Circulation core: one edge bay-column holding the stair at the rear and
    the entrance (ground) or landing (upper) at the front. Taking an EDGE
    column is what leaves the remainder a clean rectangle; taking an interior
    one would break the plate into two pieces.

    Returns (stair_rect, core_front_rect|None, main_rect)."""
    if len(gx) >= 3:
        core_x0, core_x1 = gx[-2], gx[-1]
        core = Rect(core_x0, fp.y, core_x1 - core_x0, fp.h)
        main = Rect(fp.x, fp.y, core_x0 - fp.x, fp.h)
    else:                                        # too narrow to spare a column
        core = Rect(fp.x, fp.y, fp.w, fp.h)
        main = None

    spec_row = ROOM_SPECS["staircase"]
    ideal, minimum, stair_min_w = spec_row["ideal"], spec_row["min"], spec_row["min_w"]
    rear = fp.y + fp.h
    cands = []
    for line in gy[:-1]:                         # preferred: whole grid rows
        depth = rear - line
        if core.w * depth >= minimum - 1e-6 and min(core.w, depth) >= stair_min_w - 1e-6:
            cands.append((abs(core.w * depth - ideal), line))
    depth = min(max(stair_min_w, ideal / core.w if core.w > 0 else stair_min_w), fp.h * 0.6)
    line = rear - depth
    if line > fp.y + 0.6 and core.w * depth >= minimum - 1e-6:
        # fallback: a partition across the core column. It sits inside a bay and
        # is carried by the slab, so it needs no column under it.
        cands.append((abs(core.w * depth - ideal), line))
    best = min(cands)[1] if cands else (gy[-2] if len(gy) >= 2 else fp.y)

    stair = Rect(core.x, best, core.w, rear - best)
    front_h = best - fp.y
    core_front = Rect(core.x, fp.y, core.w, front_h) if front_h > 0.6 else None
    return stair, core_front, main


def place_parking(fp: Rect, bw: float, bd: float, entrance_rect):
    """Parking is OUTSIDE the building, in the open strip between the house
    and the street, lined up with the front door. It is not a room: it takes
    no built-up area, no FAR and no structure cost."""
    depth = min(PARKING_LEN, fp.y)
    if depth < 1.0:
        return None
    width = PARKING_WID + 0.3
    cx = (entrance_rect.x + entrance_rect.w / 2.0) if entrance_rect else bw / 2.0
    x = min(max(0.0, cx - width / 2.0), max(0.0, bw - width))
    return Rect(x, max(0.0, fp.y - depth), min(width, bw), depth)


# ---------------------------------------------------------------------------
# Openings and columns
# ---------------------------------------------------------------------------

def _wall_segments(rect: Rect, fp: Rect):
    """The room's 4 walls, each tagged with whether it lies on the building
    footprint boundary. (orientation, fixed, a0, a1, exterior, side)"""
    return [
        ("v", rect.x, rect.y, rect.y + rect.h, abs(rect.x - fp.x) < EPS, "left"),
        ("v", rect.x + rect.w, rect.y, rect.y + rect.h,
         abs(rect.x + rect.w - (fp.x + fp.w)) < EPS, "right"),
        ("h", rect.y, rect.x, rect.x + rect.w, abs(rect.y - fp.y) < EPS, "front"),
        ("h", rect.y + rect.h, rect.x, rect.x + rect.w,
         abs(rect.y + rect.h - (fp.y + fp.h)) < EPS, "rear"),
    ]


def compute_openings(items: list, fp: Rect, root_types: tuple):
    """Doors follow the circulation graph -- one door per USED edge, so the
    plan is provably walkable and no opening is emitted twice. Windows go on
    exterior walls. Everything is computed here; the renderers only draw."""
    live = _indoor(items)
    by_id = {r["id"]: (r, rc) for r, rc in live}
    reachable, parent_of = build_access_graph(live, root_types)

    doors, windows, adjacency_lines = [], [], []

    # -- interior doors: exactly the edges the circulation graph actually uses
    for child_id, parent_id in parent_of.items():
        room_i, rect_i = by_id[child_id]
        _, rect_j = by_id[parent_id]
        wall = shared_wall(rect_i, rect_j)
        door_len = min(0.9, max(0.6, wall * 0.5))
        if abs(rect_i.x + rect_i.w - rect_j.x) < 0.01 or abs(rect_j.x + rect_j.w - rect_i.x) < 0.01:
            fixed_x = rect_i.x + rect_i.w if abs(rect_i.x + rect_i.w - rect_j.x) < 0.01 else rect_i.x
            lo, hi = max(rect_i.y, rect_j.y), min(rect_i.y + rect_i.h, rect_j.y + rect_j.h)
            swing = 1 if fixed_x <= rect_i.x + 0.01 else -1
            doors.append({"room_id": child_id, "orientation": "v", "x": fixed_x,
                          "y": (lo + hi) / 2 - door_len / 2, "length": door_len,
                          "swing": swing, "kind": "internal"})
        else:
            fixed_y = rect_i.y + rect_i.h if abs(rect_i.y + rect_i.h - rect_j.y) < 0.01 else rect_i.y
            lo, hi = max(rect_i.x, rect_j.x), min(rect_i.x + rect_i.w, rect_j.x + rect_j.w)
            swing = 1 if fixed_y <= rect_i.y + 0.01 else -1
            doors.append({"room_id": child_id, "orientation": "h",
                          "x": (lo + hi) / 2 - door_len / 2, "y": fixed_y,
                          "length": door_len, "swing": swing, "kind": "internal"})

    # -- the front door: on the street-facing wall of the entrance (Rule C)
    for room, rect in live:
        if room["type"] != "entrance":
            continue
        if abs(rect.y - fp.y) < EPS and rect.w >= DOOR_CLEAR:
            mid, dl = rect.x + rect.w / 2, min(1.1, rect.w)
            doors.append({"room_id": room["id"], "orientation": "h",
                          "x": mid - dl / 2, "y": rect.y, "length": dl,
                          "swing": 1, "kind": "front"})

    # -- windows on exterior walls
    for room, rect in live:
        if room["type"] in ("staircase", "corridor"):
            continue
        for orient, fixed, a0, a1, exterior, _side in _wall_segments(rect, fp):
            if not exterior or (a1 - a0) < 0.6:
                continue
            win_len = min(1.4, (a1 - a0) * 0.5)
            mid = (a0 + a1) / 2
            if orient == "v":
                windows.append({"room_id": room["id"], "orientation": "v", "x": fixed,
                                "y": mid - win_len / 2, "length": win_len})
            else:
                windows.append({"room_id": room["id"], "orientation": "h",
                                "x": mid - win_len / 2, "y": fixed, "length": win_len})

    # -- green links for pairs that WANT to touch and do
    for i in range(len(live)):
        room_i, rect_i = live[i]
        for j in range(i + 1, len(live)):
            room_j, rect_j = live[j]
            if shared_wall(rect_i, rect_j) > 0 and adjacency_weight(room_i["type"], room_j["type"]) > 0:
                adjacency_lines.append({
                    "a": room_i["id"], "b": room_j["id"],
                    "ax": rect_i.x + rect_i.w / 2, "ay": rect_i.y + rect_i.h / 2,
                    "bx": rect_j.x + rect_j.w / 2, "by": rect_j.y + rect_j.h / 2,
                    "weight": adjacency_weight(room_i["type"], room_j["type"]),
                })

    return doors, windows, adjacency_lines, reachable, parent_of


def compute_columns(gx: list, gy: list) -> list:
    """Rule D: one column at every grid intersection, identical on every floor,
    therefore continuous from foundation to roof."""
    return [{"x": round(x, 3), "y": round(y, 3)} for x in gx for y in gy]


# ---------------------------------------------------------------------------
# Top level
# ---------------------------------------------------------------------------

def generate_plan(spec: ProjectSpec) -> dict:
    plot_w, plot_d = ft_to_m(spec.plot_width_ft), ft_to_m(spec.plot_depth_ft)
    bw = plot_w - (spec.setback_left_m + spec.setback_right_m)
    bd = plot_d - (spec.setback_front_m + spec.setback_rear_m)

    result = {
        "ok": True, "reason": None, "notes": [],
        "plot_w_m": plot_w, "plot_d_m": plot_d,
        "envelope_w_m": bw, "envelope_d_m": bd,
        "setbacks": {"front": spec.setback_front_m, "rear": spec.setback_rear_m,
                     "left": spec.setback_left_m, "right": spec.setback_right_m},
        "footprint": None, "grid": None, "columns": [], "parking": None,
        "floors": [], "adjacency_score": 0.0,
    }

    if bw <= 0 or bd <= 0:
        result["ok"] = False
        result["reason"] = (f"Setbacks consume the entire plot: buildable envelope is "
                            f"{bw:.2f} x {bd:.2f} m. Increase plot size or reduce setbacks.")
        return result

    floor_rooms, has_stair, notes = build_room_requests(spec)
    has_stair = has_stair and len(floor_rooms) > 1   # floors may have been dropped
    result["notes"] = notes
    n_floors = len(floor_rooms)

    # -- feasibility, PER FLOOR: an aggregate check passes plans whose
    # -- individual floors are impossible, which is how undersized rooms used
    # -- to slip through and get drawn anyway.
    fp_probe = compute_footprint(spec, bw, bd, floor_rooms, has_stair)
    for idx in sorted(floor_rooms):
        types = list(floor_rooms[idx]) + (["staircase"] if has_stair else [])
        need_min = sum(ROOM_SPECS[t]["min"] for t in types)
        if need_min > fp_probe.area + 1e-6:
            result["ok"] = False
            result["reason"] = (
                f"Floor {idx} needs at least {need_min:.1f} m2 for its rooms but the "
                f"buildable footprint is only {fp_probe.area:.1f} m2 "
                f"({bw:.1f} x {bd:.1f} m envelope). Enlarge the plot, reduce the room "
                f"count on this floor, or add a floor.")
            return result

    fp = fp_probe
    gx, gy = make_grid(fp)

    stair_rect, core_front, main_rect = (None, None, fp)
    if has_stair:
        stair_rect, core_front, main_rect = make_core(fp, gx, gy)

    result["footprint"] = {"x": round(fp.x, 3), "y": round(fp.y, 3),
                           "w": round(fp.w, 3), "h": round(fp.h, 3),
                           "area": round(fp.area, 2)}
    result["grid"] = {"x": [round(v, 3) for v in gx], "y": [round(v, 3) for v in gy],
                      "bay_w": round(gx[1] - gx[0], 2) if len(gx) > 1 else round(fp.w, 2),
                      "bay_d": round(gy[1] - gy[0], 2) if len(gy) > 1 else round(fp.h, 2)}
    result["columns"] = compute_columns(gx, gy)

    rng = random.Random(RNG_SEED)
    total_score = 0.0
    entrance_rect = None

    for floor_idx in sorted(floor_rooms):
        types = floor_rooms[floor_idx]
        rooms = [{"id": f"F{floor_idx}_{t}_{k}", "type": t} for k, t in enumerate(types)]

        fixed = []
        if has_stair:
            fixed = [({"id": f"F{floor_idx}_staircase_0", "type": "staircase"}, stair_rect)]
        root_types = ("entrance",) if floor_idx == 0 else ("staircase",)

        if has_stair:
            regions = [r for r in (main_rect, core_front) if r is not None and r.area > 1e-6]
        else:
            regions = [fp]

        placed, score = best_split(regions, rooms, fixed, fp, gx, gy,
                                   root_types, rng, tag=f"F{floor_idx}")
        total_score += score
        items = fixed + placed
        doors, windows, adj_lines, reachable, parent_of = compute_openings(items, fp, root_types)

        floor_out = {"index": floor_idx, "rooms": [], "adjacency_score": round(score, 1),
                     "doors": doors, "windows": windows, "adjacency_lines": adj_lines}
        for room, rect in items:
            row = ROOM_SPECS[room["type"]]
            if room["type"] == "entrance":
                entrance_rect = rect
            floor_out["rooms"].append({
                "id": room["id"], "type": room["type"],
                "name": room["type"].replace("_", " ").title(),
                "zone": row["zone"], "floor": floor_idx,
                "x": round(rect.x, 3), "y": round(rect.y, 3),
                "w": round(rect.w, 3), "h": round(rect.h, 3),
                "area": round(rect.area, 3),
                "min_area": row["min"], "max_area": row["max"], "min_w": row["min_w"],
                "exterior": touches_boundary(rect, fp),
                "exterior_required": row["exterior"] in ("required", "cantilever", "front"),
                "reachable": room["id"] in reachable,
                "entered_from": parent_of.get(room["id"]),
                # an "open" room is built without a roof: no walls in 3D, a
                # parapet instead. A balcony with four walls is just a room.
                "open": row["exterior"] == "cantilever",
            })
        result["floors"].append(floor_out)

    # -- parking last: it needs to know where the front door ended up
    if spec.parking:
        park = place_parking(fp, bw, bd, entrance_rect)
        if park is None:
            result["notes"].append(
                "No open strip left in front of the house for parking -- reduce the "
                "room programme or enlarge the plot.")
        else:
            result["parking"] = {
                "x": round(park.x, 3), "y": round(park.y, 3),
                "w": round(park.w, 3), "h": round(park.h, 3),
                "area": round(park.area, 2), "zone": "service",
                "name": "Parking", "type": "parking", "open": True,
                "fits_car": (park.w >= PARKING_WID - 1e-6 and park.h >= PARKING_LEN - 1e-6),
            }

    result["floor_count"] = n_floors
    result["adjacency_score"] = round(total_score, 1)
    return result
