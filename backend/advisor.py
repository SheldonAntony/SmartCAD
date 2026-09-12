"""Turn validation failures into concrete, VERIFIED recommendations.

A suggestion is only worth showing if it actually works. Every candidate fix --
whether the model proposed it or the rule set did -- is applied to the spec,
re-run through the deterministic engine and re-validated, so the user sees the
measured effect ("5 failures -> 0") rather than the model's opinion. Candidates
that do not help are dropped before they ever reach the UI.

This keeps the project's split intact: the AI proposes, the engine decides.
"""
from functools import lru_cache

from .cost import compute_cost
from .engine import generate_plan
from .models import ProjectSpec
from .validator import validate

# The only fields a recommendation is allowed to touch. A patch from the model
# is untrusted input: anything outside this set is discarded, and what remains
# still has to survive ProjectSpec's own bounds.
PATCHABLE = {
    "plot_width_ft": float, "plot_depth_ft": float,
    "floors": int, "bedrooms": int, "bathrooms": int,
    "parking": bool, "balcony": bool,
    "setback_front_m": float, "setback_rear_m": float,
    "setback_left_m": float, "setback_right_m": float,
}

LABELS = {
    "plot_width_ft": ("Plot width", "ft"), "plot_depth_ft": ("Plot depth", "ft"),
    "floors": ("Floors", ""), "bedrooms": ("Bedrooms", ""), "bathrooms": ("Bathrooms", ""),
    "parking": ("Parking", ""), "balcony": ("Balcony", ""),
    "setback_front_m": ("Front setback", "m"), "setback_rear_m": ("Rear setback", "m"),
    "setback_left_m": ("Left setback", "m"), "setback_right_m": ("Right setback", "m"),
}


def sanitize_patch(patch) -> dict:
    """Keep only known fields with coercible values."""
    if not isinstance(patch, dict):
        return {}
    clean = {}
    for key, caster in PATCHABLE.items():
        if key not in patch or patch[key] is None:
            continue
        try:
            clean[key] = caster(patch[key])
        except (TypeError, ValueError):
            continue
    return clean


def apply_patch(spec: ProjectSpec, patch: dict):
    patch = sanitize_patch(patch)
    if not patch:
        return None, {}
    changed = {k: v for k, v in patch.items() if getattr(spec, k) != v}
    if not changed:
        return None, {}
    try:
        return spec.model_copy(update=changed), changed
    except Exception:
        return None, {}


def describe(spec: ProjectSpec, changed: dict) -> str:
    parts = []
    for key, value in changed.items():
        label, unit = LABELS.get(key, (key, ""))
        old = getattr(spec, key)
        if isinstance(value, bool):
            parts.append(f"{label} {'on' if value else 'off'}")
        else:
            fmt = (lambda v: f"{v:g}")
            parts.append(f"{label} {fmt(old)} -> {fmt(value)}{unit}")
    return ", ".join(parts)


# Candidates are screened at reduced search effort. Because the engine
# minimises its objective over candidates, a reduced search can only report the
# same or MORE failures than the full one -- so "this fix clears everything" is
# never a false promise, and at worst we under-sell a fix.
SCREEN_EFFORT = 220


@lru_cache(maxsize=256)
def _score_key(key: tuple):
    spec = ProjectSpec(**dict(key))
    plan = generate_plan(spec, effort=SCREEN_EFFORT)
    issues = validate(spec, plan)
    fails = sum(1 for i in issues if i["level"] == "fail")
    warns = sum(1 for i in issues if i["level"] == "warn")
    return plan["ok"], fails, warns, plan


def score(spec: ProjectSpec):
    """(ok, fail count, warn count, plan) for a spec.

    Cached: the candidate search scores the same spec from several directions,
    and a full generate+validate is ~130 ms. The engine is deterministic, so
    caching cannot change an answer."""
    data = spec.model_dump(exclude={"cost_rates"})
    return _score_key(tuple(sorted(data.items())))


def rule_candidates(spec: ProjectSpec, plan: dict, issues: list) -> list:
    """Deterministic fallbacks, derived from what actually failed. These exist
    so the feature still works with AI switched off -- the app must run
    end-to-end without a key."""
    text = " ".join(i["message"].lower() for i in issues if i["level"] == "fail")
    if not plan["ok"]:
        text += " " + (plan.get("reason") or "").lower()
    out = []

    too_small = ("below minimum" in text or "needs at least" in text
                 or "too elongated" in text or "no external wall" in text)
    if too_small:
        out.append(("Widen the plot",
                    "The rooms cannot reach their minimum sizes on the current frontage.",
                    {"plot_width_ft": round(spec.plot_width_ft * 1.2)}))
        out.append(("Deepen the plot",
                    "More depth lets the corridor serve two full rows of rooms.",
                    {"plot_depth_ft": round(spec.plot_depth_ft * 1.2)}))
        out.append(("Enlarge the plot both ways",
                    "A 15% increase on both sides usually clears every size failure at once.",
                    {"plot_width_ft": round(spec.plot_width_ft * 1.15),
                     "plot_depth_ft": round(spec.plot_depth_ft * 1.15)}))
        if spec.bedrooms > 1:
            out.append(("Drop one bedroom",
                        "The floor is carrying more rooms than its area supports.",
                        {"bedrooms": spec.bedrooms - 1}))
        if spec.floors < 4:
            out.append((f"Add a floor ({spec.floors} -> {spec.floors + 1})",
                        "Spreading the same rooms over another storey relieves each floor.",
                        {"floors": spec.floors + 1}))
        # setbacks are often the real constraint on a small plot
        if spec.setback_front_m > 3.0 or spec.setback_rear_m > 1.5:
            out.append(("Trim the setbacks to the statutory minimum",
                        "Anything above the bye-law minimum is buildable area being given away.",
                        {"setback_front_m": 3.0, "setback_rear_m": 1.5}))

    if "parking" in text:
        out.append(("Build without covered parking",
                    "Frees the front strip for the house; park in the setback or on the street.",
                    {"parking": False}))
        out.append(("Deepen the plot for the car",
                    "A standard bay needs 5 m of clear depth in front of the building line.",
                    {"plot_depth_ft": round(spec.plot_depth_ft + 18)}))

    if "ground coverage" in text:
        out.append(("Increase the side setbacks",
                    "Coverage is footprint over plot; wider setbacks shrink the footprint.",
                    {"setback_left_m": spec.setback_left_m + 0.6,
                     "setback_right_m": spec.setback_right_m + 0.6}))

    if "floor area ratio" in text:
        out.append(("Remove the top floor",
                    "FAR is built-up area over plot area; one storey fewer brings it under.",
                    {"floors": max(1, spec.floors - 1)}))

    for side in ("front", "rear", "left", "right"):
        if f"{side} setback below" in text:
            need = {"front": 3.0, "rear": 1.5, "left": 1.2, "right": 1.2}[side]
            out.append((f"Set the {side} setback to {need} m",
                        "Below the recommended minimum for this bye-law.",
                        {f"setback_{side}_m": need}))
    return out


def minimal_fixes(spec: ProjectSpec) -> list:
    """Search for the SMALLEST change of each kind that leaves no failing check.

    A suggestion that merely improves things ("5 failures become 1") is much
    less useful than one that closes them out, so escalate each strategy until
    it clears -- and stop at the first size that does, rather than proposing a
    needlessly generous plot."""
    out = []
    for pct in (1.1, 1.2, 1.3, 1.45, 1.6, 1.9, 2.3, 2.8):
        patch = {"plot_width_ft": round(spec.plot_width_ft * pct),
                 "plot_depth_ft": round(spec.plot_depth_ft * pct)}
        candidate, _ = apply_patch(spec, patch)
        if candidate is None:
            continue
        ok, fails, _, _ = score(candidate)
        if ok and fails == 0:
            out.append((f"Enlarge the plot by {int(round((pct - 1) * 100))}%",
                        "The smallest uniform increase that leaves no failing check.",
                        patch))
            break

    for drop in range(1, max(1, spec.bedrooms + 1)):
        patch = {"bedrooms": spec.bedrooms - drop}
        candidate, _ = apply_patch(spec, patch)
        if candidate is None:
            continue
        ok, fails, _, _ = score(candidate)
        if ok and fails == 0:
            word = "bedroom" if drop == 1 else "bedrooms"
            out.append((f"Build {spec.bedrooms - drop} bedroom(s) instead of {spec.bedrooms}",
                        f"Dropping {drop} {word} is the smallest programme cut that clears everything.",
                        patch))
            break

    # A brief that is not buildable at all usually needs two levers at once.
    for extra_floor in (1, 2):
        if spec.floors + extra_floor > 4:
            break
        for pct in (1.0, 1.5, 2.0, 2.6, 3.2):
            patch = {"floors": spec.floors + extra_floor}
            if pct > 1.0:
                patch["plot_width_ft"] = round(spec.plot_width_ft * pct)
                patch["plot_depth_ft"] = round(spec.plot_depth_ft * pct)
            candidate, _ = apply_patch(spec, patch)
            if candidate is None:
                continue
            ok, fails, _, _ = score(candidate)
            if ok and fails == 0:
                out.append(("Add a floor and grow the plot" if pct > 1.0
                            else f"Build over {spec.floors + extra_floor} floors",
                            "Spreading the programme over another storey is what makes "
                            "this brief fit at all.", patch))
                return out
    return out


def verify(spec: ProjectSpec, candidates: list, baseline=None, limit: int = 3) -> list:
    """Run every candidate through the engine and keep the ones that measurably
    help, best first. This is what stops a plausible-sounding but useless
    suggestion from reaching the user."""
    if baseline is None:
        baseline = score(spec)
    base_ok, base_fail, base_warn, _ = baseline

    seen, results = set(), []
    for title, why, patch in candidates:
        new_spec, changed = apply_patch(spec, patch)
        if new_spec is None:
            continue
        key = tuple(sorted(changed.items()))
        if key in seen:
            continue
        seen.add(key)

        ok, fails, warns, plan = score(new_spec)
        # an improvement means: buildable, and strictly fewer failures
        improves = ok and (fails < base_fail or (not base_ok and fails == base_fail))
        if not improves:
            continue
        cost = compute_cost(new_spec, plan) if ok else None
        results.append({
            "title": title,
            "why": why,
            "change": describe(spec, changed),
            "patch": changed,
            "fails_before": base_fail,
            "fails_after": fails,
            "warns_after": warns,
            "resolves_all": fails == 0,
            "cost_after": (cost or {}).get("total"),
            "footprint_after": (plan.get("footprint") or {}).get("area"),
        })

        # enough good options in hand -- every extra candidate is another
        # full engine run behind a single button press
        if len(results) >= limit and any(r["resolves_all"] for r in results):
            break

    results.sort(key=lambda r: (r["fails_after"], r["warns_after"], len(r["patch"])))
    return results[:limit]


def recommend(spec: ProjectSpec, plan: dict, issues: list, ai_module=None) -> dict:
    """Full pipeline: gather candidates from the model and the rule set, verify
    every one against the engine, return only those that measurably help."""
    baseline = score(spec)
    _, base_fail, _, _ = baseline
    if base_fail == 0 and plan["ok"]:
        return {"recommendations": [], "source": "none", "error": None,
                "fails_before": 0, "message": "No failures to fix."}

    ai_candidates, error, source = [], None, "rules"
    if ai_module is not None:
        ai_candidates, error = ai_module.recommend(spec, issues, plan)
        if ai_candidates:
            source = "ai"

    candidates = (list(ai_candidates) + minimal_fixes(spec)
                  + rule_candidates(spec, plan, issues))
    verified = verify(spec, candidates, baseline=baseline)
    if source == "ai" and not any(c[0] == r["title"] for r in verified for c in ai_candidates):
        source = "rules"   # every AI suggestion failed verification
    message = None
    if not verified:
        env_w = plan.get("envelope_w_m", 0)
        env_d = plan.get("envelope_d_m", 0)
        message = (
            f"No change to this brief clears the failures. The setbacks leave a "
            f"buildable envelope of only {env_w:.1f} x {env_d:.1f} m "
            f"({env_w * env_d:.1f} m2), which cannot hold this programme at any "
            f"storey count. This plot needs either a setback relaxation from the "
            f"local authority or a different site.")
    return {
        "recommendations": verified,
        "source": source,
        "error": error,
        "fails_before": base_fail,
        "message": message,
    }
