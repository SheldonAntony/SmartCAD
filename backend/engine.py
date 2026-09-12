"""Deterministic geometry engine: envelope -> feasibility -> room budget ->
recursive rectangle split -> adjacency-optimised room ordering.

Rule 3: metres everywhere internally. Feet only appear at the input boundary.
Rule 2: all geometry math lives here. render2d.js / render3d.js only draw.
"""
import random

from .models import ProjectSpec
from .rooms import ADJACENCY, ROOM_SPECS

FT_TO_M = 0.3048
RNG_SEED = 42
N_CANDIDATES = 400
ASPECT_LIMIT = 3.0


def ft_to_m(x: float) -> float:
    return x * FT_TO_M


class Rect:
    __slots__ = ("x", "y", "w", "h")

    def __init__(self, x, y, w, h):
        self.x, self.y, self.w, self.h = x, y, w, h

    @property
    def area(self):
        return self.w * self.h

    def __repr__(self):
        return f"Rect({self.x:.2f},{self.y:.2f},{self.w:.2f},{self.h:.2f})"


def normalize_type(t: str) -> str:
    """master_bedroom scores adjacency the same as bedroom."""
    return "bedroom" if t == "master_bedroom" else t


def adjacency_weight(t1: str, t2: str) -> float:
    a, b = normalize_type(t1), normalize_type(t2)
    return ADJACENCY.get((a, b), ADJACENCY.get((b, a), 0))


def shared_wall(a: Rect, b: Rect) -> float:
    """Length of wall shared between two rects. 0 if not touching."""
    eps = 0.01
    if abs(a.x + a.w - b.x) < eps or abs(b.x + b.w - a.x) < eps:
        overlap = min(a.y + a.h, b.y + b.h) - max(a.y, b.y)
        return max(0.0, overlap)
    if abs(a.y + a.h - b.y) < eps or abs(b.y + b.h - a.y) < eps:
        overlap = min(a.x + a.w, b.x + b.w) - max(a.x, b.x)
        return max(0.0, overlap)
    return 0.0


def split(rect: Rect, rooms: list) -> list:
    """rooms = [{"id":..., "type":..., "area":...}, ...]
    Returns [(room, Rect), ...]. Guarantees: exact areas, no overlap, no gaps.
    """
    if len(rooms) == 1:
        return [(rooms[0], Rect(rect.x, rect.y, rect.w, rect.h))]
    total = sum(r["area"] for r in rooms)
    acc, i = 0.0, 0
    for i in range(len(rooms)):
        if acc + rooms[i]["area"] > total / 2:
            break
        acc += rooms[i]["area"]
    i = max(1, min(i, len(rooms) - 1))  # never an empty group
    A, B = rooms[:i], rooms[i:]
    frac = sum(r["area"] for r in A) / total
    x, y, w, h = rect.x, rect.y, rect.w, rect.h
    if w >= h:  # cut perpendicular to the longer side
        return (
            split(Rect(x, y, w * frac, h), A)
            + split(Rect(x + w * frac, y, w * (1 - frac), h), B)
        )
    else:
        return (
            split(Rect(x, y, w, h * frac), A)
            + split(Rect(x, y + h * frac, w, h * (1 - frac)), B)
        )


def compute_budget(room_types: list, plate_area: float) -> list:
    """Scale ideal areas to exactly fill plate_area, clamp below-min rooms,
    redistribute the deficit from the largest (non-clamped) rooms."""
    ideal = [ROOM_SPECS[t]["ideal"] for t in room_types]
    total_ideal = sum(ideal)
    scale = plate_area / total_ideal if total_ideal > 0 else 1.0
    areas = [a * scale for a in ideal]
    mins = [ROOM_SPECS[t]["min"] for t in room_types]

    clamped = [False] * len(areas)
    deficit = 0.0
    for i in range(len(areas)):
        if areas[i] < mins[i]:
            deficit += mins[i] - areas[i]
            areas[i] = mins[i]
            clamped[i] = True

    if deficit > 0:
        pool = [i for i in range(len(areas)) if not clamped[i]]
        pool_total = sum(areas[i] for i in pool)
        if pool_total > deficit:
            for i in pool:
                areas[i] -= deficit * (areas[i] / pool_total)
        # else: floor plate is too tight even at minimums; feasibility gate
        # upstream should have already caught the overall infeasible case.
    return areas


def aspect_penalty(rect: Rect) -> float:
    long_side, short_side = max(rect.w, rect.h), min(rect.w, rect.h)
    if short_side <= 0:
        return 1000.0
    ratio = long_side / short_side
    return max(0.0, ratio - ASPECT_LIMIT) * 10.0


def min_width_penalty(room: dict, rect: Rect) -> float:
    min_w = ROOM_SPECS[room["type"]]["min_w"]
    actual = min(rect.w, rect.h)
    return max(0.0, min_w - actual) * 20.0


def evaluate_layout(placed: list, fixed: list) -> tuple:
    """placed = [(room, Rect), ...] being optimised.
    fixed = [(room, Rect), ...] already placed (e.g. staircase) that still
    contribute to adjacency scoring but are not shuffled.

    Returns (violation_count, adjacency_score, penalty_total). Selection in
    best_split() ranks by violation_count first: a layout where every room
    meets its minimum width and aspect ratio always beats one that merely
    scores higher on adjacency but leaves a room unusably thin."""
    all_items = placed + fixed
    adjacency_score = 0.0
    for i in range(len(all_items)):
        room_a, rect_a = all_items[i]
        for j in range(i + 1, len(all_items)):
            room_b, rect_b = all_items[j]
            wall = shared_wall(rect_a, rect_b)
            if wall > 0:
                adjacency_score += adjacency_weight(room_a["type"], room_b["type"]) * wall

    violations, penalty_total = 0, 0.0
    for room, rect in placed:
        ap = aspect_penalty(rect)
        wp = min_width_penalty(room, rect)
        penalty_total += ap + wp
        if ap > 0 or wp > 0:
            violations += 1
    return violations, adjacency_score, penalty_total


def best_split(rect: Rect, rooms: list, fixed: list, rng: random.Random) -> tuple:
    """Try N_CANDIDATES shuffles of room order, keep the layout with fewest
    min-width/aspect-ratio violations, tie-broken by adjacency score.
    Returns (placed_list, adjacency_score)."""
    if not rooms:
        return [], 0.0
    best_placed, best_adj = None, 0.0
    best_key = None
    for _ in range(N_CANDIDATES):
        order = rooms[:]
        rng.shuffle(order)
        areas = compute_budget([r["type"] for r in order], rect.area)
        candidate = [{**order[k], "area": areas[k]} for k in range(len(order))]
        placed = split(rect, candidate)
        violations, adj, penalty = evaluate_layout(placed, fixed)
        key = (violations, -adj, penalty)
        if best_key is None or key < best_key:
            best_key, best_placed, best_adj = key, placed, adj
    return best_placed, best_adj


def compute_staircase_strip(bw: float, bd: float, rng: random.Random):
    """Fixed staircase rect + the remaining rectangle for other rooms.
    Same physical rect on every floor (rule: hard-copy, don't re-derive)."""
    ideal = ROOM_SPECS["staircase"]["ideal"]
    min_w = ROOM_SPECS["staircase"]["min_w"]
    if bw >= bd:
        w = max(min_w, ideal / bd)
        w = min(w, bw * 0.4)
        stair = Rect(bw - w, 0, w, bd)
        remaining = Rect(0, 0, bw - w, bd)
    else:
        h = max(min_w, ideal / bw)
        h = min(h, bd * 0.4)
        stair = Rect(0, bd - h, bw, h)
        remaining = Rect(0, 0, bw, bd - h)
    return stair, remaining


def build_room_requests(spec: ProjectSpec):
    """Returns dict floor_index -> list of room-type keys (with repeats)."""
    floors = spec.floors
    floor_rooms = {i: [] for i in range(floors)}

    if floors == 1:
        floor_rooms[0] += ["entrance", "living", "dining", "kitchen", "utility"]
        if spec.parking:
            floor_rooms[0].append("parking")
        if spec.bedrooms > 0:
            floor_rooms[0].append("master_bedroom")
        floor_rooms[0] += ["bedroom"] * max(0, spec.bedrooms - 1)
        floor_rooms[0] += ["bathroom"] * spec.bathrooms
        if spec.balcony:
            floor_rooms[0].append("balcony")
        return floor_rooms, False

    floor_rooms[0] += ["entrance", "living", "dining", "kitchen", "utility"]
    if spec.parking:
        floor_rooms[0].append("parking")
    if spec.bathrooms > 0:
        floor_rooms[0].append("bathroom")

    upper_floors = list(range(1, floors))
    n_upper = len(upper_floors)
    remaining_bedrooms = spec.bedrooms
    remaining_bathrooms = max(0, spec.bathrooms - 1)
    first_master_placed = False
    for idx, f in enumerate(upper_floors):
        slots_left = n_upper - idx
        take_bed = -(-remaining_bedrooms // slots_left)  # ceil
        take_bed = min(take_bed, remaining_bedrooms)
        remaining_bedrooms -= take_bed
        take_bath = -(-remaining_bathrooms // slots_left)  # ceil
        take_bath = min(take_bath, remaining_bathrooms)
        remaining_bathrooms -= take_bath

        if not first_master_placed and take_bed > 0:
            floor_rooms[f].append("master_bedroom")
            take_bed -= 1
            first_master_placed = True
        floor_rooms[f] += ["bedroom"] * take_bed
        floor_rooms[f] += ["bathroom"] * take_bath
        if spec.balcony:
            floor_rooms[f].append("balcony")

    return floor_rooms, True


def _wall_segments(rect: Rect, bw: float, bd: float, eps: float = 0.02):
    """Return this room's 4 wall segments tagged with whether each lies on
    the building's exterior envelope. Each segment: (orientation, fixed, a0, a1, exterior)
    orientation 'v' -> vertical wall at x=fixed spanning y in [a0,a1]
    orientation 'h' -> horizontal wall at y=fixed spanning x in [a0,a1]"""
    return [
        ("v", rect.x, rect.y, rect.y + rect.h, abs(rect.x - 0) < eps),
        ("v", rect.x + rect.w, rect.y, rect.y + rect.h, abs(rect.x + rect.w - bw) < eps),
        ("h", rect.y, rect.x, rect.x + rect.w, abs(rect.y - 0) < eps),
        ("h", rect.y + rect.h, rect.x, rect.x + rect.w, abs(rect.y + rect.h - bd) < eps),
    ]


def compute_openings(rooms_with_rects: list, bw: float, bd: float) -> tuple:
    """Doors on the best shared-wall segment, windows on exterior walls,
    adjacency connector lines between rooms that share a wall and are meant
    to be adjacent. All computed here so the frontend only draws (rule 2)."""
    n = len(rooms_with_rects)
    doors, windows, adjacency_lines = [], [], []

    for i in range(n):
        room_i, rect_i = rooms_with_rects[i]

        # -- windows: centered on each exterior wall segment, skip tiny walls --
        for orient, fixed, a0, a1, exterior in _wall_segments(rect_i, bw, bd):
            if not exterior or room_i["type"] == "staircase":
                continue
            length = a1 - a0
            if length < 0.6:
                continue
            win_len = min(1.2, length * 0.5)
            mid = (a0 + a1) / 2
            if orient == "v":
                windows.append({"room_id": room_i["id"], "orientation": "v", "x": fixed, "y": mid - win_len / 2, "length": win_len})
            else:
                windows.append({"room_id": room_i["id"], "orientation": "h", "x": mid - win_len / 2, "y": fixed, "length": win_len})

        # -- best neighbor for the door: longest shared wall, preferring positive adjacency --
        best_j, best_wall, best_weight = None, 0.0, None
        for j in range(n):
            if j == i:
                continue
            room_j, rect_j = rooms_with_rects[j]
            wall = shared_wall(rect_i, rect_j)
            if wall <= 0:
                continue
            weight = adjacency_weight(room_i["type"], room_j["type"])
            if best_j is None or (weight, wall) > (best_weight, best_wall):
                best_j, best_wall, best_weight = j, wall, weight
            if weight > 0 and wall > 0:
                adjacency_lines.append({
                    "a": room_i["id"], "b": room_j["id"],
                    "ax": rect_i.x + rect_i.w / 2, "ay": rect_i.y + rect_i.h / 2,
                    "bx": rect_j.x + rect_j.w / 2, "by": rect_j.y + rect_j.h / 2,
                    "weight": weight,
                })

        door_placed = False
        prefer_exterior = room_i["type"] in ("entrance", "parking")
        if prefer_exterior:
            for orient, fixed, a0, a1, exterior in _wall_segments(rect_i, bw, bd):
                if exterior and (a1 - a0) >= 0.9:
                    mid = (a0 + a1) / 2
                    door_len = min(1.0, a1 - a0)
                    if orient == "v":
                        doors.append({"room_id": room_i["id"], "orientation": "v", "x": fixed, "y": mid - door_len / 2, "length": door_len})
                    else:
                        doors.append({"room_id": room_i["id"], "orientation": "h", "x": mid - door_len / 2, "y": fixed, "length": door_len})
                    door_placed = True
                    break

        if not door_placed and best_j is not None:
            room_j, rect_j = rooms_with_rects[best_j]
            eps = 0.01
            door_len = min(0.9, best_wall)
            if abs(rect_i.x + rect_i.w - rect_j.x) < eps or abs(rect_j.x + rect_j.w - rect_i.x) < eps:
                fixed_x = rect_i.x + rect_i.w if abs(rect_i.x + rect_i.w - rect_j.x) < eps else rect_i.x
                lo = max(rect_i.y, rect_j.y)
                hi = min(rect_i.y + rect_i.h, rect_j.y + rect_j.h)
                mid = (lo + hi) / 2
                doors.append({"room_id": room_i["id"], "orientation": "v", "x": fixed_x, "y": mid - door_len / 2, "length": door_len})
            else:
                fixed_y = rect_i.y + rect_i.h if abs(rect_i.y + rect_i.h - rect_j.y) < eps else rect_i.y
                lo = max(rect_i.x, rect_j.x)
                hi = min(rect_i.x + rect_i.w, rect_j.x + rect_j.w)
                mid = (lo + hi) / 2
                doors.append({"room_id": room_i["id"], "orientation": "h", "x": mid - door_len / 2, "y": fixed_y, "length": door_len})
            door_placed = True

        if not door_placed:
            for orient, fixed, a0, a1, exterior in _wall_segments(rect_i, bw, bd):
                if exterior and (a1 - a0) >= 0.6:
                    mid = (a0 + a1) / 2
                    door_len = min(0.9, a1 - a0)
                    if orient == "v":
                        doors.append({"room_id": room_i["id"], "orientation": "v", "x": fixed, "y": mid - door_len / 2, "length": door_len})
                    else:
                        doors.append({"room_id": room_i["id"], "orientation": "h", "x": mid - door_len / 2, "y": fixed, "length": door_len})
                    break

    return doors, windows, adjacency_lines


def generate_plan(spec: ProjectSpec) -> dict:
    plot_w = ft_to_m(spec.plot_width_ft)
    plot_d = ft_to_m(spec.plot_depth_ft)
    bw = plot_w - (spec.setback_left_m + spec.setback_right_m)
    bd = plot_d - (spec.setback_front_m + spec.setback_rear_m)

    result = {
        "ok": True,
        "reason": None,
        "plot_w_m": plot_w,
        "plot_d_m": plot_d,
        "envelope_w_m": bw,
        "envelope_d_m": bd,
        "setbacks": {
            "front": spec.setback_front_m,
            "rear": spec.setback_rear_m,
            "left": spec.setback_left_m,
            "right": spec.setback_right_m,
        },
        "floors": [],
        "adjacency_score": 0.0,
    }

    if bw <= 0 or bd <= 0:
        result["ok"] = False
        result["reason"] = (
            f"Setbacks consume the entire plot: buildable envelope is "
            f"{bw:.2f} x {bd:.2f} m. Increase plot size or reduce setbacks."
        )
        return result

    floor_rooms, has_stair = build_room_requests(spec)
    all_types = [t for rooms in floor_rooms.values() for t in rooms]
    if has_stair:
        all_types += ["staircase"] * spec.floors
    total_min = sum(ROOM_SPECS[t]["min"] for t in all_types)
    total_buildable = bw * bd * spec.floors

    if total_min > total_buildable:
        result["ok"] = False
        result["reason"] = (
            f"Requested rooms need at least {total_min:.1f} m² but the plot "
            f"only provides {total_buildable:.1f} m² of buildable area across "
            f"{spec.floors} floor(s). Increase the plot size, remove rooms, "
            f"or add a floor."
        )
        return result

    rng = random.Random(RNG_SEED)

    stair_rect = None
    remaining_rect = Rect(0, 0, bw, bd)
    if has_stair:
        stair_rect, remaining_rect = compute_staircase_strip(bw, bd, rng)

    total_score = 0.0
    for floor_idx in sorted(floor_rooms.keys()):
        types = floor_rooms[floor_idx]
        rooms = [
            {"id": f"F{floor_idx}_{t}_{k}", "type": t}
            for k, t in enumerate(types)
        ]
        fixed = []
        if has_stair:
            stair_room = {"id": f"F{floor_idx}_staircase_0", "type": "staircase"}
            fixed = [(stair_room, stair_rect)]

        placed, score = best_split(remaining_rect, rooms, fixed, rng)
        total_score += score

        floor_out = {"index": floor_idx, "rooms": [], "adjacency_score": round(score, 1)}
        for room, rect in fixed + placed:
            spec_row = ROOM_SPECS[room["type"]]
            floor_out["rooms"].append({
                "id": room["id"],
                "type": room["type"],
                "name": room["type"].replace("_", " ").title(),
                "zone": spec_row["zone"],
                "floor": floor_idx,
                "x": round(rect.x, 3),
                "y": round(rect.y, 3),
                "w": round(rect.w, 3),
                "h": round(rect.h, 3),
                "area": round(rect.area, 3),
                "min_area": spec_row["min"],
                "min_w": spec_row["min_w"],
            })

        doors, windows, adjacency_lines = compute_openings(fixed + placed, bw, bd)
        floor_out["doors"] = doors
        floor_out["windows"] = windows
        floor_out["adjacency_lines"] = adjacency_lines
        result["floors"].append(floor_out)

    result["adjacency_score"] = round(total_score, 1)
    return result


if __name__ == "__main__":
    spec = ProjectSpec(
        plot_width_ft=30,
        plot_depth_ft=40,
        floors=2,
        bedrooms=3,
        bathrooms=2,
        parking=True,
        balcony=True,
    )
    plan = generate_plan(spec)
    assert plan["ok"], plan.get("reason")

    all_rects = []
    for floor in plan["floors"]:
        for room in floor["rooms"]:
            r = Rect(room["x"], room["y"], room["w"], room["h"])
            all_rects.append((floor["index"], room, r))

    # 1. computed area vs target within 0.1 m^2 (area matches w*h exactly by construction)
    for _, room, r in all_rects:
        assert abs(room["area"] - r.area) < 0.1, (room, r.area)

    # 2. sum(room areas) per floor == buildable area within 0.1 m^2
    bw, bd = plan["envelope_w_m"], plan["envelope_d_m"]
    for floor in plan["floors"]:
        s = sum(room["area"] for room in floor["rooms"])
        assert abs(s - bw * bd) < 0.1, (floor["index"], s, bw * bd)

    # 3. zero overlap between any two rectangle pairs on the same floor
    def overlap(a, b):
        ox = max(0.0, min(a.x + a.w, b.x + b.w) - max(a.x, b.x))
        oy = max(0.0, min(a.y + a.h, b.y + b.h) - max(a.y, b.y))
        return ox * oy

    for floor in plan["floors"]:
        rects = [Rect(r["x"], r["y"], r["w"], r["h"]) for r in floor["rooms"]]
        for i in range(len(rects)):
            for j in range(i + 1, len(rects)):
                ov = overlap(rects[i], rects[j])
                assert ov < 0.01, (floor["index"], i, j, ov)

    # 4. every room lies inside the envelope
    eps = 0.05
    for _, room, r in all_rects:
        assert -eps <= r.x and -eps <= r.y, room
        assert r.x + r.w <= bw + eps, room
        assert r.y + r.h <= bd + eps, room

    print("ALL 4 ASSERTIONS PASSED")
    print(f"envelope: {bw:.2f} x {bd:.2f} m  |  adjacency score: {plan['adjacency_score']}")
    for floor in plan["floors"]:
        print(f"-- floor {floor['index']} (score {floor['adjacency_score']}) --")
        for room in floor["rooms"]:
            print(f"  {room['name']:16s} {room['area']:6.2f} m^2  "
                  f"({room['w']:.2f} x {room['h']:.2f}) at ({room['x']:.2f},{room['y']:.2f})")
