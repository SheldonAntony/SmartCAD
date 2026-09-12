"""Planning-rule tests.

The previous suite asserted four things: areas matched w*h, per-floor areas
summed to the plate, no rectangles overlapped, and everything sat inside the
envelope. Those are exactly the invariants the tiler guarantees BY
CONSTRUCTION -- they cannot fail, which is why every plan "passed" while
shipping sealed bathrooms, interior balconies, a front door in the middle of
the house and phantom storeys.

These tests check the properties that can actually break.

Run:  python tests.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from SmartCAD.backend import advisor                                # noqa: E402
from SmartCAD.backend.cost import compute_cost                      # noqa: E402
from SmartCAD.backend.engine import MAX_BAY, PARKING_LEN, PARKING_WID, Rect, generate_plan  # noqa: E402
from SmartCAD.backend.models import ProjectSpec                     # noqa: E402
from SmartCAD.backend.rooms import ROOM_SPECS                       # noqa: E402
from SmartCAD.backend.validator import validate                     # noqa: E402

CASES = [
    ("3BHK 2-floor", dict(plot_width_ft=40, plot_depth_ft=60, floors=2, bedrooms=3, bathrooms=2)),
    ("2BHK 1-floor", dict(plot_width_ft=30, plot_depth_ft=45, floors=1, bedrooms=2, bathrooms=1, parking=False)),
    ("large plot",   dict(plot_width_ft=100, plot_depth_ft=120, floors=1, bedrooms=2, bathrooms=1)),
    ("4BR 2-floor",  dict(plot_width_ft=45, plot_depth_ft=70, floors=2, bedrooms=4, bathrooms=3)),
    ("6BR 3-floor",  dict(plot_width_ft=50, plot_depth_ft=80, floors=3, bedrooms=6, bathrooms=4)),
    ("no parking",   dict(plot_width_ft=40, plot_depth_ft=60, floors=2, bedrooms=3, bathrooms=2, parking=False)),
    ("no balcony",   dict(plot_width_ft=40, plot_depth_ft=60, floors=2, bedrooms=3, bathrooms=2, balcony=False)),
]

# plots with enough room to satisfy the full programme; these must come out
# completely compliant
ROOMY = {"3BHK 2-floor", "large plot", "6BR 3-floor", "no parking", "no balcony"}

_failures = []


def check(cond, label, detail=""):
    if not cond:
        _failures.append(f"{label}: {detail}")
    return cond


def overlap(a, b):
    ox = max(0.0, min(a.x + a.w, b.x + b.w) - max(a.x, b.x))
    oy = max(0.0, min(a.y + a.h, b.y + b.h) - max(a.y, b.y))
    return ox * oy


def run_case(label, kw):
    spec = ProjectSpec(**kw)
    plan = generate_plan(spec)
    if not plan["ok"]:
        print(f"  {label}: reported infeasible -- {plan['reason'][:60]}")
        return
    shortfalls = []
    fp = plan["footprint"]
    fp_rect = Rect(fp["x"], fp["y"], fp["w"], fp["h"])
    env_w, env_d = plan["envelope_w_m"], plan["envelope_d_m"]

    # -- geometry invariants (cheap, still worth pinning) -------------------
    for floor in plan["floors"]:
        rects = [Rect(r["x"], r["y"], r["w"], r["h"]) for r in floor["rooms"]]
        total = sum(r.area for r in rects)
        check(abs(total - fp_rect.area) < 0.5, f"{label} F{floor['index']} tiling",
              f"rooms sum {total:.2f} != footprint {fp_rect.area:.2f}")
        for i in range(len(rects)):
            for j in range(i + 1, len(rects)):
                check(overlap(rects[i], rects[j]) < 0.01, f"{label} F{floor['index']} overlap",
                      f"{floor['rooms'][i]['name']} / {floor['rooms'][j]['name']}")
        for r, row in zip(rects, floor["rooms"]):
            check(r.x >= fp_rect.x - 0.05 and r.y >= fp_rect.y - 0.05
                  and r.x + r.w <= fp_rect.x + fp_rect.w + 0.05
                  and r.y + r.h <= fp_rect.y + fp_rect.h + 0.05,
                  f"{label} inside footprint", row["name"])

    # -- the building is SMALLER than the plot ------------------------------
    # regression: the old engine tiled the whole envelope, so a bigger plot
    # simply inflated every room and coverage was a restatement of setbacks
    check(fp_rect.area <= env_w * env_d + 0.5, f"{label} footprint <= envelope", "")
    coverage = fp_rect.area / (plan["plot_w_m"] * plan["plot_d_m"])
    check(coverage <= 0.65 + 1e-6, f"{label} ground coverage", f"{coverage*100:.1f}%")

    for floor in plan["floors"]:
        for r in floor["rooms"]:
            spec_row = ROOM_SPECS[r["type"]]
            # -- ventilation / daylight / street frontage -------------------
            check(not (r["exterior_required"] and not r["exterior"]),
                  f"{label} F{floor['index']} ventilation",
                  f"{r['name']} has no external wall")
            # -- walkable from the front door -------------------------------
            check(r["reachable"], f"{label} F{floor['index']} reachability",
                  f"{r['name']} unreachable")
            # -- sizing. On a plot that is genuinely too tight these CAN fail;
            # -- the contract is that the validator then reports them, which is
            # -- asserted below. On a comfortable plot they must hold outright.
            undersized = (r["area"] < spec_row["min"] - 0.05
                          or min(r["w"], r["h"]) < spec_row["min_w"] - 0.02)
            if undersized:
                shortfalls.append(f"{r['name']} (floor {r['floor']})")
            if label in ROOMY:
                check(not undersized, f"{label} F{floor['index']} sizing",
                      f"{r['name']} {r['area']:.2f} m2 / {min(r['w'], r['h']):.2f} m wide")

    # -- the front door is on the street -----------------------------------
    ground = plan["floors"][0]["rooms"]
    ent = [r for r in ground if r["type"] == "entrance"]
    check(len(ent) == 1, f"{label} one entrance", str(len(ent)))
    if ent:
        check(abs(ent[0]["y"] - fp["y"]) < 0.03, f"{label} entrance on street edge",
              f"y={ent[0]['y']} vs front {fp['y']}")
    front_doors = [d for d in plan["floors"][0]["doors"] if d.get("kind") == "front"]
    check(len(front_doors) >= 1, f"{label} has a front door", "none emitted")

    # -- parking is outside the building -----------------------------------
    if spec.parking:
        park = plan.get("parking")
        if check(park is not None, f"{label} parking placed", "missing"):
            prect = Rect(park["x"], park["y"], park["w"], park["h"])
            check(overlap(prect, fp_rect) < 0.01, f"{label} parking outside footprint",
                  f"{overlap(prect, fp_rect):.2f} m2 inside the house")
            check(park["w"] >= PARKING_WID - 1e-6 and park["h"] >= PARKING_LEN - 1e-6,
                  f"{label} parking fits a car", f"{park['w']}x{park['h']}")
            check(prect.y < fp_rect.y + 0.01, f"{label} parking on the street side", "")
    else:
        check(plan.get("parking") is None, f"{label} no parking when not asked", "")

    # -- balconies are on the outside and open -----------------------------
    for floor in plan["floors"]:
        for r in floor["rooms"]:
            if r["type"] in ("balcony", "open_terrace"):
                check(r["exterior"], f"{label} balcony external", r["name"])
                check(r["open"], f"{label} balcony is open", r["name"])
    if not spec.balcony:
        n_balc = sum(1 for f in plan["floors"] for r in f["rooms"] if r["type"] == "balcony")
        check(n_balc == 0, f"{label} no balcony when not asked", str(n_balc))

    # -- structure: one grid, columns continuous, bays buildable -----------
    grid = plan["grid"]
    check(grid["bay_w"] <= MAX_BAY + 0.01 and grid["bay_d"] <= MAX_BAY + 0.01,
          f"{label} bay span", f"{grid['bay_w']}x{grid['bay_d']} > {MAX_BAY}")
    cols = plan["columns"]
    check(len(cols) == len(grid["x"]) * len(grid["y"]), f"{label} column count", "")
    # every column must sit on the footprint, on every floor -- the grid is
    # computed once, so this is the test that would catch a regression that
    # reintroduced per-floor grids
    for c in cols:
        check(fp["x"] - 0.01 <= c["x"] <= fp["x"] + fp["w"] + 0.01
              and fp["y"] - 0.01 <= c["y"] <= fp["y"] + fp["h"] + 0.01,
              f"{label} column on footprint", str(c))

    stairs = {f["index"]: r for f in plan["floors"] for r in f["rooms"] if r["type"] == "staircase"}
    if len(stairs) > 1:
        base = list(stairs.values())[0]
        for r in stairs.values():
            check(abs(r["x"] - base["x"]) < 0.05 and abs(r["y"] - base["y"]) < 0.05
                  and abs(r["w"] - base["w"]) < 0.05 and abs(r["h"] - base["h"]) < 0.05,
                  f"{label} stair aligned across floors", r["id"])

    # -- no phantom storeys -------------------------------------------------
    for floor in plan["floors"][1:]:
        types = [r["type"] for r in floor["rooms"]]
        check(any(t in ("bedroom", "master_bedroom") for t in types),
              f"{label} F{floor['index']} is a real storey", str(types))

    # -- doors: one per used edge, none duplicated -------------------------
    for floor in plan["floors"]:
        keys = [(round(d["x"], 2), round(d["y"], 2), d["orientation"]) for d in floor["doors"]]
        check(len(keys) == len(set(keys)), f"{label} F{floor['index']} no duplicate doors",
              f"{len(keys)} doors, {len(set(keys))} unique")

    # -- the validator agrees ----------------------------------------------
    issues = validate(spec, plan)
    fails = [i for i in issues if i["level"] == "fail"]
    if label in ROOMY:
        check(not fails, f"{label} validator clean", "; ".join(i["message"] for i in fails[:3]))
    # Whatever the plot, an undersized room must be REPORTED, never drawn in
    # silence. This is the contract the old build broke.
    for name in shortfalls:
        check(any(name in i["message"] and i["level"] == "fail" for i in issues),
              f"{label} undersized room is reported", name)
    check(len(issues) < 40, f"{label} issue list readable", f"{len(issues)} rows")


def test_no_room_inflation():
    """Regression: rooms used to scale with the plot, giving a 36 m2 bathroom
    on a large site because only the MINIMUM area was clamped."""
    plan = generate_plan(ProjectSpec(plot_width_ft=100, plot_depth_ft=120, floors=1,
                                     bedrooms=2, bathrooms=1))
    for floor in plan["floors"]:
        for r in floor["rooms"]:
            cap = ROOM_SPECS[r["type"]]["max"]
            check(r["area"] <= cap * 1.35, "no inflation on a large plot",
                  f"{r['name']} {r['area']:.1f} vs max {cap}")


def test_cost_tracks_the_programme():
    """Regression: built-up area was envelope x floors, so the estimate ignored
    the room list entirely -- adding bedrooms did not change the price."""
    base = ProjectSpec(plot_width_ft=50, plot_depth_ft=80, floors=2, bedrooms=2, bathrooms=2)
    more = ProjectSpec(plot_width_ft=50, plot_depth_ft=80, floors=2, bedrooms=5, bathrooms=4)
    c1 = compute_cost(base, generate_plan(base))
    c2 = compute_cost(more, generate_plan(more))
    check(c2["total"] > c1["total"] * 1.05, "cost responds to the room programme",
          f"2BR Rs{c1['total']:,.0f} vs 5BR Rs{c2['total']:,.0f}")


def test_infeasible_is_reported_not_drawn():
    plan = generate_plan(ProjectSpec(plot_width_ft=15, plot_depth_ft=20, floors=1,
                                     bedrooms=4, bathrooms=2))
    check(not plan["ok"], "tiny plot rejected", "it returned a plan")
    check(plan["reason"] and "m2" in plan["reason"], "rejection states the numbers",
          str(plan["reason"]))


def test_determinism():
    kw = dict(plot_width_ft=40, plot_depth_ft=60, floors=2, bedrooms=3, bathrooms=2)
    a = generate_plan(ProjectSpec(**kw))
    b = generate_plan(ProjectSpec(**kw))
    check(a == b, "same spec gives the same plan", "output differed between runs")


def test_recommendations_are_verified():
    """A recommendation that claims to clear every failure must still do so at
    FULL engine effort, not just at the reduced effort used to screen it. This
    is the whole contract of the advisor: measured, not guessed."""
    spec = ProjectSpec(plot_width_ft=30, plot_depth_ft=40, floors=2, bedrooms=3, bathrooms=2)
    plan = generate_plan(spec)
    issues = validate(spec, plan)
    out = advisor.recommend(spec, plan, issues, ai_module=None)   # AI off on purpose
    check(out["recommendations"], "a failing brief produces suggestions", str(out["message"]))

    for rec in out["recommendations"]:
        patched, changed = advisor.apply_patch(spec, rec["patch"])
        check(patched is not None, "recommendation carries a usable patch", rec["title"])
        if patched is None:
            continue
        full = generate_plan(patched)                              # full effort
        fails = sum(1 for i in validate(patched, full) if i["level"] == "fail")
        if rec["resolves_all"]:
            check(full["ok"] and fails == 0, "'clears all' holds at full effort",
                  f"{rec['title']}: {fails} failures remain")
        check(fails <= rec["fails_before"], "recommendation does not make things worse",
              f"{rec['title']}: {rec['fails_before']} -> {fails}")


def test_patches_are_sanitised():
    """Model output is untrusted input: only known brief fields may survive."""
    spec = ProjectSpec(plot_width_ft=40, plot_depth_ft=60, floors=2, bedrooms=3, bathrooms=2)
    dirty = {"bedrooms": 2, "cost_rates": {"structure_per_sqft": 0},
             "__class__": "x", "plot_width_ft": "44", "nonsense": 9}
    clean = advisor.sanitize_patch(dirty)
    check(set(clean) == {"bedrooms", "plot_width_ft"}, "unknown fields dropped", str(clean))
    check(clean["plot_width_ft"] == 44.0, "values coerced to the declared type", str(clean))
    check(advisor.sanitize_patch("not a dict") == {}, "non-dict patch rejected", "")
    check(advisor.apply_patch(spec, {"bedrooms": 3})[0] is None, "no-op patch rejected", "")


def test_clean_brief_gets_no_recommendations():
    spec = ProjectSpec(plot_width_ft=40, plot_depth_ft=60, floors=2, bedrooms=3, bathrooms=2)
    plan = generate_plan(spec)
    issues = validate(spec, plan)
    out = advisor.recommend(spec, plan, issues, ai_module=None)
    check(not out["recommendations"], "nothing to fix on a compliant brief",
          str(out["recommendations"]))


def test_reduced_effort_never_flatters():
    """The advisor screens at reduced effort. That is only safe because the
    engine minimises over candidates, so fewer candidates can never report
    FEWER failures than the full search."""
    spec = ProjectSpec(plot_width_ft=30, plot_depth_ft=42, floors=2, bedrooms=3, bathrooms=2)
    low = generate_plan(spec, effort=advisor.SCREEN_EFFORT)
    full = generate_plan(spec)
    low_fails = sum(1 for i in validate(spec, low) if i["level"] == "fail")
    full_fails = sum(1 for i in validate(spec, full) if i["level"] == "fail")
    check(full_fails <= low_fails, "full search is at least as good as the screen",
          f"screen {low_fails} vs full {full_fails}")


if __name__ == "__main__":
    print("running planning-rule tests\n")
    for label, kw in CASES:
        run_case(label, kw)
    test_no_room_inflation()
    test_cost_tracks_the_programme()
    test_infeasible_is_reported_not_drawn()
    test_determinism()
    test_recommendations_are_verified()
    test_patches_are_sanitised()
    test_clean_brief_gets_no_recommendations()
    test_reduced_effort_never_flatters()

    if _failures:
        print(f"\n{len(_failures)} FAILURE(S):")
        for f in _failures:
            print("  x", f)
        sys.exit(1)
    print("\nall planning-rule tests passed")
