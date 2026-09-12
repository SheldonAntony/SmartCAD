"""Planning-compliance checks over an already-generated plan.

The engine cannot render invalid *geometry* -- exact tiling, no overlaps,
inside the envelope, one continuous column grid -- those hold by construction.
This module checks the things geometry alone does NOT guarantee: sizes, ratios,
ventilation, circulation, coverage and buildability. Every failure states
actual vs required and a concrete fix.

Passing per-room checks are collapsed into one summary line per category. The
old version emitted an "ok" row for every check on every room on every floor,
which buried the handful of rows that actually mattered.
"""
from .engine import (ASPECT_LIMIT, CORRIDOR_W, MAX_BAY, PARKING_LEN, PARKING_WID,
                     Rect, shared_wall)
from .models import ProjectSpec
from .rooms import ROOM_SPECS

GROUND_COVERAGE_MAX = 0.65
FAR_MAX = 2.0  # typical low-rise residential ceiling; state bye-laws vary
RECOMMENDED_SETBACKS = {"front": 3.0, "rear": 1.5, "left": 1.2, "right": 1.2}

# rooms whose shape is circulation, not habitation -- exempt from the
# habitable-room proportion rule
SHAPE_EXEMPT = {"staircase", "corridor", "open_terrace", "balcony"}


def issue(level, message, actual=None, required=None, fix=None):
    return {"level": level, "message": message, "actual": actual,
            "required": required, "fix": fix}


def _overlap(a: Rect, b: Rect) -> float:
    ox = max(0.0, min(a.x + a.w, b.x + b.w) - max(a.x, b.x))
    oy = max(0.0, min(a.y + a.h, b.y + b.h) - max(a.y, b.y))
    return ox * oy


def validate(spec: ProjectSpec, plan: dict) -> list:
    issues = []

    if not plan["ok"]:
        issues.append(issue("fail", plan["reason"],
                            fix="Reduce the room count, add a floor, or enlarge the plot."))
        return issues

    for note in plan.get("notes", []):
        issues.append(issue("warn", note, fix="Adjust the brief if this was not intended."))

    plot_area = plan["plot_w_m"] * plan["plot_d_m"]
    fp = plan["footprint"]
    fp_rect = Rect(fp["x"], fp["y"], fp["w"], fp["h"])
    n_floors = plan.get("floor_count", len(plan["floors"]))

    # ---------------------------------------------------------------- setbacks
    short = [(s, plan["setbacks"][s], r) for s, r in RECOMMENDED_SETBACKS.items()
             if plan["setbacks"][s] < r]
    for side, actual, required in short:
        issues.append(issue("fail", f"{side.title()} setback below recommended minimum",
                            actual=actual, required=required,
                            fix=f"Increase the {side} setback to at least {required} m."))
    if not short:
        issues.append(issue("ok", "All four setbacks compliant"))

    # -------------------------------------------------- coverage and FAR (real)
    # Coverage is now the BUILDING footprint over the plot. Previously it was
    # the envelope over the plot, which made it a restatement of the setbacks:
    # the room programme could not affect it at all.
    coverage = fp["area"] / plot_area if plot_area else 0
    lvl = "fail" if coverage > GROUND_COVERAGE_MAX else "ok"
    issues.append(issue(lvl, "Ground coverage " + ("exceeds limit" if lvl == "fail" else "within limit"),
                        actual=round(coverage * 100, 1), required=GROUND_COVERAGE_MAX * 100,
                        fix="Reduce the room programme or enlarge the plot." if lvl == "fail" else None))

    far = (fp["area"] * n_floors) / plot_area if plot_area else 0
    lvl = "warn" if far > FAR_MAX else "ok"
    issues.append(issue(lvl, "Floor Area Ratio " + ("exceeds typical ceiling" if lvl == "warn" else "within typical ceiling"),
                        actual=round(far, 2), required=FAR_MAX,
                        fix="Reduce the number of floors or the built-up area." if lvl == "warn" else None))

    # ------------------------------------------------------------- per-room
    counts = {"area": 0, "width": 0, "aspect": 0, "vent": 0, "reach": 0, "total": 0}
    bedroom_rects, bathroom_rects, stair_by_floor = [], [], {}

    for floor in plan["floors"]:
        for room in floor["rooms"]:
            counts["total"] += 1
            name = f"{room['name']} (floor {room['floor']})"
            side_short = min(room["w"], room["h"])
            side_long = max(room["w"], room["h"])

            if room["area"] < room["min_area"] - 0.05:
                issues.append(issue("fail", f"{name} area below minimum",
                                    actual=round(room["area"], 2), required=room["min_area"],
                                    fix="Enlarge the plot, drop a room, or move a room to another floor."))
            else:
                counts["area"] += 1

            # NBC sets minimums, not maximums. An oversized room is a comfort
            # and cost observation, not a code breach -- so this warns.
            if room["area"] > room.get("max_area", 1e9) + 0.05:
                issues.append(issue("warn", f"{name} is larger than a comfortable maximum",
                                    actual=round(room["area"], 2), required=room["max_area"],
                                    fix="Add a room to this floor, or accept the generous size."))

            if side_short < room["min_w"] - 0.02:
                issues.append(issue("fail", f"{name} below minimum usable width",
                                    actual=round(side_short, 2), required=room["min_w"],
                                    fix="Reduce the number of rooms sharing this floor."))
            else:
                counts["width"] += 1

            ratio = side_long / side_short if side_short > 0 else float("inf")
            if ratio > ASPECT_LIMIT and room["type"] not in SHAPE_EXEMPT:
                issues.append(issue("fail", f"{name} too elongated to use",
                                    actual=round(ratio, 2), required=ASPECT_LIMIT,
                                    fix="Rebalance this floor's room list."))
            else:
                counts["aspect"] += 1

            # -- ventilation / daylight / position. This is the check the old
            # -- validator had no equivalent of at all, which is why a sealed
            # -- interior bathroom passed every test.
            if room["exterior_required"] and not room["exterior"]:
                need = ROOM_SPECS[room["type"]]["exterior"]
                msg = (f"{name} has no street-facing wall" if need == "front"
                       else f"{name} has no external wall -- no ventilation or daylight")
                issues.append(issue("fail", msg, fix="Reduce this floor's room count so the "
                                                     "room can reach the building perimeter."))
            else:
                counts["vent"] += 1

            if not room["reachable"]:
                issues.append(issue("fail", f"{name} cannot be reached from the front door",
                                    fix="No permitted door route exists. Reduce the room count "
                                        "or add circulation on this floor."))
            else:
                counts["reach"] += 1

            rect = Rect(room["x"], room["y"], room["w"], room["h"])
            if room["type"] in ("bedroom", "master_bedroom"):
                bedroom_rects.append((room["floor"], name, rect))
            elif room["type"] == "bathroom":
                bathroom_rects.append((room["floor"], rect))
            elif room["type"] == "staircase":
                stair_by_floor[room["floor"]] = rect

    total = counts["total"]
    for key, label in (("area", "meet their minimum area"),
                       ("width", "meet their minimum usable width"),
                       ("aspect", "are usably proportioned"),
                       ("vent", "have the external wall they require"),
                       ("reach", "are reachable from the front door")):
        if counts[key] == total:
            issues.append(issue("ok", f"All {total} rooms {label}"))

    # ------------------------------------------------- structure (Rule D)
    grid = plan.get("grid") or {}
    bay_w, bay_d = grid.get("bay_w", 0), grid.get("bay_d", 0)
    if bay_w > MAX_BAY + 0.01 or bay_d > MAX_BAY + 0.01:
        issues.append(issue("warn", "Structural bay wider than an ordinary RCC beam span",
                            actual=f"{bay_w} x {bay_d}", required=MAX_BAY,
                            fix="Beams at this span need to deepen, or add a column line."))
    else:
        issues.append(issue("ok", "Column bays within ordinary RCC spans",
                            actual=f"{bay_w} x {bay_d} m", required=MAX_BAY))

    # Columns come from one grid computed once for the whole building, so
    # continuity is structural rather than hoped for. Assert it anyway -- this
    # is the check that would catch a regression reintroducing per-floor grids.
    n_cols = len(plan.get("columns", []))
    if n_cols:
        issues.append(issue("ok", f"{n_cols} columns continuous through all {n_floors} floor(s)",
                            actual=n_cols))

    if len(stair_by_floor) > 1:
        base = list(stair_by_floor.values())[0]
        aligned = all(abs(r.x - base.x) < 0.05 and abs(r.y - base.y) < 0.05
                      and abs(r.w - base.w) < 0.05 and abs(r.h - base.h) < 0.05
                      for r in stair_by_floor.values())
        issues.append(issue("ok", "Staircase aligned on every floor") if aligned else
                      issue("fail", "Staircase position differs between floors",
                            fix="The stair must occupy the same x/y/w/h on every floor."))

    # ------------------------------------------------------------ parking
    if spec.parking:
        park = plan.get("parking")
        if not park:
            issues.append(issue("fail", "Parking was requested but does not fit outside the building",
                                fix="Reduce the room programme or enlarge the plot."))
        else:
            prect = Rect(park["x"], park["y"], park["w"], park["h"])
            if _overlap(prect, fp_rect) > 0.01:
                issues.append(issue("fail", "Parking overlaps the building footprint",
                                    fix="Parking must sit in open ground, not inside the house."))
            else:
                issues.append(issue("ok", "Parking is outside the building, in open ground",
                                    actual=f"{park['w']} x {park['h']} m"))
            if not park["fits_car"]:
                issues.append(issue("fail", "Parking bay does not fit a standard car",
                                    actual=f"{park['w']:.2f} x {park['h']:.2f}",
                                    required=f"{PARKING_WID} x {PARKING_LEN}",
                                    fix="Widen the plot or shrink the footprint."))
            else:
                issues.append(issue("ok", "Parking bay fits a standard car",
                                    actual=f"{park['w']:.2f} x {park['h']:.2f}",
                                    required=f"{PARKING_WID} x {PARKING_LEN}"))

    # ------------------------------------------------- bedroom / bathroom
    missing = []
    for floor_idx, name, brect in bedroom_rects:
        same = [r for f, r in bathroom_rects if f == floor_idx]
        if not any(shared_wall(brect, bath) > 0 for bath in same):
            missing.append(name)
    if missing and bathroom_rects:
        for name in missing:
            issues.append(issue("warn", f"{name} has no directly adjacent bathroom",
                                fix="Reorder rooms so a bathroom shares a wall with this bedroom."))
    elif bedroom_rects:
        issues.append(issue("ok", f"All {len(bedroom_rects)} bedrooms adjoin a bathroom"))

    # --------------------------------------------------- floor distribution
    ground_types = {r["type"] for r in plan["floors"][0]["rooms"]}
    for required_type in ("entrance", "living"):
        if required_type not in ground_types:
            issues.append(issue("fail", f"Ground floor missing {required_type}",
                                fix=f"Add {required_type} to the ground floor."))
    if {"entrance", "living"} <= ground_types:
        issues.append(issue("ok", "Ground floor carries the entrance and living room"))

    if n_floors > 1:
        issues.append(issue("ok", f"Upper floors served by a {CORRIDOR_W} m landing off the stair"))

    # ------------------------------------------------------------ adjacency
    lvl = "warn" if plan["adjacency_score"] < 0 else "ok"
    issues.append(issue(lvl,
                        "Adjacency score is negative -- more conflicting than desirable adjacencies"
                        if lvl == "warn" else "Adjacency score is net positive",
                        actual=plan["adjacency_score"], required=0,
                        fix="Revisit the room list." if lvl == "warn" else None))
    return issues
