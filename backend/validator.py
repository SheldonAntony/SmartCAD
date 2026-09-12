"""Spatial compliance checks over an already-generated plan.

The engine cannot render an invalid *geometry* (exact areas, no overlaps,
inside envelope -- guaranteed in engine.py). This module checks *planning*
compliance instead: sizes, ratios, coverage and relationships that geometry
alone does not guarantee. Every failure states actual vs required and a fix.
"""
from .engine import Rect, shared_wall
from .models import ProjectSpec

GROUND_COVERAGE_MAX = 0.65
FAR_MAX = 2.0  # typical low-rise residential ceiling; state bye-laws vary
ASPECT_LIMIT = 3.0
PARKING_MIN_W, PARKING_MIN_L = 2.5, 5.0
RECOMMENDED_SETBACKS = {"front": 3.0, "rear": 1.5, "left": 1.2, "right": 1.2}


def issue(level, message, actual=None, required=None, fix=None):
    return {"level": level, "message": message, "actual": actual, "required": required, "fix": fix}


def validate(spec: ProjectSpec, plan: dict) -> list:
    issues = []

    if not plan["ok"]:
        issues.append(issue("fail", plan["reason"], fix="Reduce room count, add a floor, or enlarge the plot."))
        return issues

    plot_area = plan["plot_w_m"] * plan["plot_d_m"]
    envelope_area = plan["envelope_w_m"] * plan["envelope_d_m"]

    # -- setback compliance --
    for side, recommended in RECOMMENDED_SETBACKS.items():
        actual = plan["setbacks"][side]
        if actual < recommended:
            issues.append(issue(
                "fail", f"{side.title()} setback below recommended minimum",
                actual=actual, required=recommended,
                fix=f"Increase {side} setback to at least {recommended} m.",
            ))
        else:
            issues.append(issue("ok", f"{side.title()} setback compliant", actual=actual, required=recommended))

    # -- ground coverage --
    coverage = envelope_area / plot_area if plot_area else 0
    if coverage > GROUND_COVERAGE_MAX:
        issues.append(issue(
            "fail", "Ground coverage exceeds limit",
            actual=round(coverage * 100, 1), required=GROUND_COVERAGE_MAX * 100,
            fix="Increase setbacks or reduce building footprint.",
        ))
    else:
        issues.append(issue("ok", "Ground coverage within limit", actual=round(coverage * 100, 1), required=GROUND_COVERAGE_MAX * 100))

    # -- FAR --
    built_up = envelope_area * spec.floors
    far = built_up / plot_area if plot_area else 0
    if far > FAR_MAX:
        issues.append(issue(
            "warn", "Floor Area Ratio exceeds typical residential ceiling",
            actual=round(far, 2), required=FAR_MAX,
            fix="Reduce number of floors or built-up area.",
        ))
    else:
        issues.append(issue("ok", "FAR within typical ceiling", actual=round(far, 2), required=FAR_MAX))

    # -- per-room checks: min area, min width, aspect ratio --
    bedroom_rects, bathroom_rects = [], []
    stair_rects_by_floor = {}
    for floor in plan["floors"]:
        for room in floor["rooms"]:
            name = f"{room['name']} (floor {room['floor']})"
            short = min(room["w"], room["h"])
            long = max(room["w"], room["h"])

            if room["area"] < room["min_area"]:
                issues.append(issue(
                    "fail", f"{name} area below minimum",
                    actual=round(room["area"], 2), required=room["min_area"],
                    fix="Enlarge the plot, remove a room, or reduce room count so this room can reach minimum size.",
                ))
            else:
                issues.append(issue("ok", f"{name} area meets minimum", actual=round(room["area"], 2), required=room["min_area"]))

            if short < room["min_w"]:
                issues.append(issue(
                    "fail", f"{name} width below minimum usable width",
                    actual=round(short, 2), required=room["min_w"],
                    fix="Reduce adjoining room count on this floor to free up width.",
                ))

            ratio = long / short if short > 0 else float("inf")
            # Staircase is a fixed full-span circulation strip by construction
            # (needed for cross-floor alignment) -- exempt from the habitable
            # room proportion rule, same as the optimizer treats it as fixed.
            if ratio > ASPECT_LIMIT and room["type"] != "staircase":
                issues.append(issue(
                    "fail", f"{name} aspect ratio too elongated",
                    actual=round(ratio, 2), required=ASPECT_LIMIT,
                    fix="Rebalance floor room list; this shape is unusable as built.",
                ))

            if room["type"] == "parking":
                r_w, r_h = room["w"], room["h"]
                fits = (r_w >= PARKING_MIN_W and r_h >= PARKING_MIN_L) or (r_h >= PARKING_MIN_W and r_w >= PARKING_MIN_L)
                if not fits:
                    issues.append(issue(
                        "fail", "Parking does not fit a standard car (2.5 x 5.0 m)",
                        actual=f"{r_w:.2f} x {r_h:.2f}", required=f"{PARKING_MIN_W} x {PARKING_MIN_L}",
                        fix="Increase plot width/depth or remove another ground-floor room.",
                    ))
                else:
                    issues.append(issue("ok", "Parking fits a standard car", actual=f"{r_w:.2f} x {r_h:.2f}", required=f"{PARKING_MIN_W} x {PARKING_MIN_L}"))

            rect = Rect(room["x"], room["y"], room["w"], room["h"])
            if room["type"] in ("bedroom", "master_bedroom"):
                bedroom_rects.append((room["floor"], name, rect))
            if room["type"] == "bathroom":
                bathroom_rects.append((room["floor"], rect))
            if room["type"] == "staircase":
                stair_rects_by_floor[room["floor"]] = rect

    # -- staircase alignment across floors --
    if len(stair_rects_by_floor) > 1:
        rects = list(stair_rects_by_floor.values())
        base = rects[0]
        aligned = all(
            abs(r.x - base.x) < 0.05 and abs(r.y - base.y) < 0.05
            and abs(r.w - base.w) < 0.05 and abs(r.h - base.h) < 0.05
            for r in rects
        )
        if aligned:
            issues.append(issue("ok", "Staircase aligned across all floors"))
        else:
            issues.append(issue(
                "fail", "Staircase position differs between floors",
                fix="Staircase must occupy the same x/y/w/h on every floor for structural continuity.",
            ))

    # -- bedroom/bathroom adjacency --
    for floor_idx, name, brect in bedroom_rects:
        same_floor_baths = [r for f, r in bathroom_rects if f == floor_idx]
        touches = any(shared_wall(brect, bath) > 0 for bath in same_floor_baths)
        if touches:
            issues.append(issue("ok", f"{name} adjacent to a bathroom"))
        else:
            issues.append(issue(
                "warn", f"{name} has no directly adjacent bathroom",
                fix="Reorder rooms so a bathroom shares a wall with this bedroom.",
            ))

    # -- floor-wise distribution: ground floor must carry entrance + living --
    ground = plan["floors"][0]["rooms"]
    ground_types = {r["type"] for r in ground}
    for required_type in ("entrance", "living"):
        if required_type in ground_types:
            issues.append(issue("ok", f"Ground floor contains {required_type}"))
        else:
            issues.append(issue("fail", f"Ground floor missing {required_type}", fix=f"Add {required_type} to the ground floor room list."))

    # -- adjacency score --
    if plan["adjacency_score"] < 0:
        issues.append(issue(
            "warn", "Overall adjacency score is negative -- more conflicting than desirable adjacencies",
            actual=plan["adjacency_score"], required=0,
            fix="Increase candidate count or revisit room list.",
        ))
    else:
        issues.append(issue("ok", "Adjacency score is net positive", actual=plan["adjacency_score"], required=0))

    return issues
