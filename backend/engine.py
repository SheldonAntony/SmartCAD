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
from .rooms import ACCESS_PARENTS, ADJACENCY, ROOM_SPECS, normalize_type

FT_TO_M = 0.3048
RNG_SEED = 42
N_CANDIDATES = 900
ASPECT_LIMIT = 3.0            # single source of truth; validator imports this

MIN_BAY = 2.4                 # m -- below this a bay is not a usable room width
MAX_BAY = 4.5                 # m -- past this an ordinary RCC beam has to deepen
CIRCULATION_ALLOWANCE = 0.12  # fraction of programme area lost to walls/passage
DOOR_CLEAR = 0.9              # m -- a shorter shared wall cannot hold a door
PARKING_LEN = 5.0             # m -- standard car
PARKING_WID = 2.5
CORRIDOR_W = 1.2             # m -- clear width of an upper-floor landing
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
    """Nearest grid line strictly inside (lo, hi), but ONLY if it is close
    enough to the area the budget actually asked for.

    Snapping unconditionally is worse than not snapping at all: it quantises
    every room to a whole bay, which is how a kitchen ends up at 17.5 m^2
    against a 14 m^2 maximum. Beyond the tolerance the cut is a light partition
    inside the bay instead -- the columns are unaffected either way."""
    inner = [v for v in lines if lo + margin < v < hi - margin]
    if not inner:
        return None
    best = min(inner, key=lambda v: abs(v - target))
    tol = max(0.30, 0.08 * (hi - lo))
    return best if abs(best - target) <= tol else None


def split(rect: Rect, rooms: list, gx: list, gy: list, axis_lock=None) -> list:
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

    order = [axis_lock] if axis_lock else (["x", "y"] if w >= h else ["y", "x"])
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
        return (split(Rect(x, y, cut - x, h), A, gx, gy, axis_lock)
                + split(Rect(cut, y, x + w - cut, h), B, gx, gy, axis_lock))
    return (split(Rect(x, y, w, cut - y), A, gx, gy, axis_lock)
            + split(Rect(x, cut, w, y + h - cut), B, gx, gy, axis_lock))


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
                # ACCESS_PARENTS already encodes "never route onward through a
                # terminal room": no terminal type appears as anyone's parent
                # except the attached bath/balcony-off-a-bedroom case.
                if cand["id"] not in reachable or cand["type"] not in allowed:
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


def best_split(regions, rooms, fixed, fp, gx, gy, root_types, rng, tag="F",
               axis_lock=None, effort=N_CANDIDATES):
    axis_options = [axis_lock, None] if axis_lock else [None]
    """Search shuffles of the room order. The order drives BOTH which region a
    room lands in and how the region is subdivided, so one shuffle explores
    both decisions at once. Regions are fixed rectangles shared by every floor,
    so cross-floor alignment survives whatever the search picks."""
    if not rooms:
        return [], 0.0
    best_key, best_placed, best_adj = None, None, 0.0
    for _ in range(effort):
        order = rooms[:]
        rng.shuffle(order)
        lock = rng.choice(axis_options)
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
            if slack >= ROOM_SPECS["corridor"]["min"]:
                filler = "corridor" if slack <= ROOM_SPECS["corridor"]["max"] else "open_terrace"
                candidate.append({"id": f"{tag}_{filler}_r{ri}", "type": filler, "area": slack})
            placed += split(region, candidate, gx, gy, lock)
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
        if spec.balcony:
            floor_rooms[f].append("balcony")

    if spec.balcony and len(floor_rooms) == 1:
        floor_rooms[0].append("balcony")
    return floor_rooms, True, notes


# ---------------------------------------------------------------------------
# Footprint, core, parking
# ---------------------------------------------------------------------------

def compute_footprint(spec: ProjectSpec, bw: float, bd: float, floor_rooms: dict,
                      has_stair: bool, corridor_extra: float = 0.0,
                      min_width: float = 0.0, reserve_parking: bool = True) -> Rect:
    """Size the building from the PROGRAMME, not from the plot.

    The old engine tiled the whole envelope, so a bigger plot simply inflated
    every room. Here the footprint is the area the rooms actually want, capped
    by the envelope -- which is also what makes ground coverage a real number
    instead of a restatement of the setbacks."""
    need = 0.0
    for idx, types in floor_rooms.items():
        t = list(types) + (["staircase"] if has_stair else [])
        extra = corridor_extra if idx > 0 else 0.0
        need = max(need, programme_area(t) + extra)

    avail_d = bd
    if spec.parking and reserve_parking:
        # keep a strip at the front of the envelope open for the car + approach
        avail_d = max(bd * 0.45, bd - PARKING_LEN)
    max_area = bw * avail_d
    target = min(need, max_area)

    ratio = bw / avail_d if avail_d > 0 else 1.0
    fd = math.sqrt(target / ratio) if ratio > 0 else avail_d
    fw = target / fd if fd > 0 else bw
    fw, fd = min(fw, bw), min(fd, avail_d)

    # Rooms are dealt along the corridor, which runs across the WIDTH. A house
    # proportioned narrow-and-deep cannot fit a row of bedrooms at their
    # minimum width no matter how much total area it has, so widen it first and
    # take the depth back.
    if min_width > 0 and fw < min_width:
        fw = min(bw, min_width)
        fd = min(avail_d, target / fw) if fw > 0 else fd

    if fw * fd < target - 1e-6:                 # clamping lost area -- grow the other side
        if fw < bw - 1e-6:
            fw = min(bw, target / fd)
        elif fd < avail_d - 1e-6:
            fd = min(avail_d, target / fw)

    # Rule C: push the house to the REAR so the front stays open for the car,
    # the approach and the entrance.
    return Rect((bw - fw) / 2.0, bd - fd, fw, fd)


def make_core(fp: Rect, gx: list, gy: list):
    """The stair is a block in the REAR-RIGHT corner, sized close to its ideal
    area. Putting it there (rather than in a full-depth column) leaves a rear
    BAND beside it, which becomes the second row of a double-loaded corridor --
    without that band an upper floor collapses into a single row of rooms and
    everything comes out too narrow to use.

    Returns (stair_rect, rear_band_rect, front_rect). All three are rectangles,
    and all three are identical on every floor, so alignment is structural."""
    row = ROOM_SPECS["staircase"]
    ideal, stair_min_w = row["ideal"], row["min_w"]
    rear = fp.y + fp.h

    # rear band deep enough to hold a usable row of rooms
    band_d = min(fp.h * 0.45, max(3.1, stair_min_w + 0.7))
    band_d = max(band_d, stair_min_w)
    stair_w = min(max(stair_min_w, ideal / band_d), fp.w * 0.45)

    # prefer a grid line for the band edge when one is close by -- the band edge
    # is a full-width wall, so it is worth landing on a beam line
    near = [v for v in gy if abs(v - (rear - band_d)) < 0.6 and fp.y + 1.0 < v < rear - 1.0]
    if near:
        band_d = rear - min(near, key=lambda v: abs(v - (rear - band_d)))
        stair_w = min(max(stair_min_w, ideal / band_d), fp.w * 0.45)

    stair = Rect(fp.x + fp.w - stair_w, rear - band_d, stair_w, band_d)
    rear_band = Rect(fp.x, rear - band_d, fp.w - stair_w, band_d)
    front = Rect(fp.x, fp.y, fp.w, fp.h - band_d)
    return stair, rear_band, front


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

def generate_plan(spec: ProjectSpec, effort: int = N_CANDIDATES) -> dict:
    """`effort` is the number of layout candidates searched. Lowering it trades
    layout quality for speed and never makes a plan look BETTER than the full
    search would -- the objective is minimised over candidates, so more
    candidates can only match or beat fewer. That one-sidedness is what lets
    the advisor screen fixes at reduced effort and still trust a clean verdict."""
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

    def plan_for(active: ProjectSpec):
        """Programme + footprint for a given brief, with the shortfall (if any).
        Returns (floor_rooms, has_stair, notes, footprint, shortfall)."""
        fr, hs, ns = build_room_requests(active)
        hs = hs and len(fr) > 1              # floors may have been dropped

        def shortfall_of(candidate: Rect):
            for idx in sorted(fr):
                types = list(fr[idx]) + (["staircase"] if hs else [])
                need = sum(ROOM_SPECS[t]["min"] for t in types)
                if hs and idx > 0:
                    need += candidate.w * CORRIDOR_W   # the landing is floor area too
                if need > candidate.area + 1e-6:
                    return idx, need
            return None

        upper = [len(v) for k, v in fr.items() if k > 0]
        per_row = max(1, math.ceil(max(upper) / 2)) if upper else 1
        row_w = per_row * ROOM_SPECS["bedroom"]["min_w"] if hs else 0.0

        cand = compute_footprint(active, bw, bd, fr, hs, min_width=row_w)
        # On a shallow plot, holding a full car length clear in front of the
        # house can leave less ground than the house itself needs. The building
        # wins; the parking check then reports that the car no longer fits.
        if active.parking and shortfall_of(cand) is not None:
            relaxed = compute_footprint(active, bw, bd, fr, hs, min_width=row_w,
                                        reserve_parking=False)
            if shortfall_of(relaxed) is None:
                cand = relaxed
                ns = ns + ["The plot is too shallow to keep a full car length clear in "
                           "front of the house, so the building was given that ground "
                           "instead. Parking will need the front setback or the street."]
        short = shortfall_of(cand)
        if short is None:                     # second pass, corridor width now known
            grown = compute_footprint(active, bw, bd, fr, hs,
                                      corridor_extra=cand.w * CORRIDOR_W,
                                      min_width=row_w, reserve_parking=cand.y > 0.01)
            if shortfall_of(grown) is None:
                cand = grown
        return fr, hs, ns, cand, short

    floor_rooms, has_stair, notes, fp_probe, shortfall = plan_for(spec)

    # A designer whose floor will not close drops the optional things first.
    # Balconies are the cheapest thing to give up, so try that before declaring
    # the brief impossible.
    if shortfall is not None and spec.balcony:
        trimmed = spec.model_copy(update={"balcony": False})
        alt = plan_for(trimmed)
        if alt[4] is None:
            floor_rooms, has_stair, notes, fp_probe, shortfall = alt
            notes = notes + ["Balconies were dropped: the floors could not close with "
                             "them at minimum room sizes. Enlarge the plot or reduce the "
                             "bedroom count to get them back."]
            spec = trimmed

    result["notes"] = notes
    n_floors = len(floor_rooms)

    if shortfall is not None:
        idx, need_min = shortfall
        result["ok"] = False
        result["reason"] = (
            f"Floor {idx} needs at least {need_min:.1f} m2 for its rooms but the "
            f"buildable footprint is only {fp_probe.area:.1f} m2 "
            f"({bw:.1f} x {bd:.1f} m envelope). Enlarge the plot, reduce the room "
            f"count on this floor, or add a floor.")
        return result

    fp = fp_probe
    gx, gy = make_grid(fp)

    stair_rect = rear_band = front_rect = None
    corridor_rect = up_front = None
    if has_stair:
        stair_rect, rear_band, front_rect = make_core(fp, gx, gy)
        # A landing the full width of the floor, immediately in front of the
        # rear band. Rooms sit on BOTH sides of it, so every one opens onto the
        # corridor -- which is what "reachable from the front door" needs, and
        # two rows is what keeps them wide enough to use.
        cw = min(CORRIDOR_W, max(0.9, front_rect.h * 0.2))
        corridor_rect = Rect(fp.x, stair_rect.y - cw, fp.w, cw)
        up_front = Rect(fp.x, fp.y, fp.w, front_rect.h - cw)

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

        fixed, axis_lock = [], None
        root_types = ("entrance",) if floor_idx == 0 else ("staircase",)
        if has_stair:
            fixed = [({"id": f"F{floor_idx}_staircase_0", "type": "staircase"}, stair_rect)]

        if has_stair and floor_idx > 0:
            fixed.append(({"id": f"F{floor_idx}_corridor_0", "type": "corridor"}, corridor_rect))
            regions = [r for r in (up_front, rear_band) if r is not None and r.area > 0.5]
            axis_lock = "x"
        elif has_stair:
            regions = [r for r in (front_rect, rear_band) if r is not None and r.area > 0.5]
        else:
            regions = [fp]

        placed, score = best_split(regions, rooms, fixed, fp, gx, gy, root_types,
                                   rng, tag=f"F{floor_idx}", axis_lock=axis_lock,
                                   effort=effort)
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
