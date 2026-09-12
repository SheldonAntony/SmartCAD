# SmartCAD — Code Understanding

> ## ⚠ SUPERSEDED BELOW — read this section first
>
> Sections 3 onwards describe the **original** build. That engine was rewritten
> after a design review found that it satisfied its own tests while producing
> plans that could not be built. What follows is the current design; the older
> sections are kept because the contrast is the point, but where they disagree
> with this section, this section is correct.

## What the original engine got wrong

The old objective function contained **only room-to-room terms**
(`shared_wall × adjacency_weight`, plus aspect and width penalties). Every real
planning rule is either a room-to-*boundary* rule or a room-to-*graph* rule, and
it could express neither. Exterior walls were detected, but only in
`compute_openings()` — *after* placement was frozen — so they decided where
windows were drawn, never where rooms went. Concretely, the default 30×40 ft
2-storey plan shipped with:

| Symptom | Cause |
|---|---|
| Upper bathroom sealed inside the plan, 0 windows | no ventilation constraint anywhere, in engine or validator |
| Balcony landlocked in the middle of the house | balcony was tiled as an ordinary interior room |
| Front door in the centre of the house, **no exterior door at all** | no concept of which plot edge is the street |
| A car parked inside the living area | parking was a room in the tiled footprint |
| 36 m² bathroom on a large plot | only the *minimum* area was clamped, never the maximum |
| Whole empty storeys, fully costed | `ceil()` bedroom distribution front-loaded floor 1 |
| Cost unchanged by adding bedrooms | built-up area was envelope × floors, not the building |
| Columns that did not line up between floors | each floor was laid out independently |

The four `__main__` assertions passed throughout, because they only checked
tiling invariants — exactly the properties `split()` guarantees by
construction. They could not fail. See `tests.py` for the replacement.

## The current design

**Rule A** metres internally, feet only at the input boundary.
**Rule B** all geometry lives in `engine.py`; renderers only draw.
**Rule C** `y = 0` is the STREET side. "Front" always means smaller y. The
engine, validator and both renderers depend on this.
**Rule D** ONE structural grid for the whole building. Columns sit at grid
intersections and are therefore continuous foundation→roof *by construction*.
Upper floors may arrange rooms differently; they may not move the grid.

Pipeline:

```
envelope
  -> room programme          build_room_requests()   round-robin, drops phantom storeys
  -> footprint sizing        compute_footprint()     sized from the PROGRAMME, not the plot
  -> structural grid         make_grid()             3.0-4.5 m bays, computed once
  -> stair + corridor core   make_core()             rear-corner stair, double-loaded landing
  -> grid-snapped tiling     split()                 snaps to a beam line when close, else a partition
  -> hard-constraint search  best_split()            500-900 candidates
  -> circulation graph       build_access_graph()    reachability from the front door
  -> openings + columns      compute_openings()
```

The search ranks candidates **lexicographically**, lowest wins:

```
(unreachable rooms, ventilation/position failures, size failures,
 -adjacency score, penalty magnitude)
```

The order is the whole point. A plan nobody can walk through, or one with a
windowless bathroom, is worse than one that merely scores badly on adjacency —
so those terms outrank the adjacency score rather than being blended into it.

Three rules that are now enforced rather than hoped for:

* **Ventilation** — `exterior_violations()` is a hard term. A room whose spec
  says `exterior: required` must touch the footprint boundary; `front` must sit
  on the street edge. This is why a bathroom can no longer be sealed inside.
* **Circulation** — `build_access_graph()` walks a fixpoint from the front door
  (or the landing, upstairs), following only edges `ACCESS_PARENTS` permits.
  Terminal rooms are never routed through, so no path crosses a bedroom. Doors
  are then cut on exactly the edges the graph used — which is also why no door
  is emitted twice.
* **Structure** — the grid is built once from the footprint. Partition walls
  inside a bay are allowed and carried by the slab; only the columns are fixed,
  and they cannot drift because no floor ever recomputes them.

Wall-sharing and door-opening are now separate concerns: `ADJACENCY` scores
which rooms benefit from a shared wall (a bathroom next to a kitchen is *good* —
one plumbing stack), while `ACCESS_PARENTS` says what a room may open into (a
WC may not open into a kitchen). Conflating the two is what made the old table
push bathrooms away from kitchens.

Parking and balconies are no longer rooms: parking is open ground between the
house and the street, taking no built-up area, no FAR and no structure cost;
balconies and terraces are `open`, rendered with a parapet and no roof, and
billed at half rate.

---

This file documents everything that was coded in this project: what each
file does, how each function works, why it works that way, and how data
flows end to end. It reflects the code **as it currently stands on disk**
(including a manual tuning edit made directly to `backend/rooms.py` after
the initial build — noted in that section).

For the original build plan / research / phase checklist, see `HANDOVER.md`.
For a short project-level summary (architecture diagram, AI-vs-code split),
see `README.md`. This file goes one level deeper: function-by-function.

---

## 1. File tree

```
SmartCAD/
├── backend/
│   ├── __init__.py       # empty, makes backend a package
│   ├── main.py           # FastAPI app, routes, static mount
│   ├── models.py         # Pydantic request/response models
│   ├── rooms.py          # room size table + adjacency weight table (data only)
│   ├── engine.py         # ★ the geometry engine (envelope -> split -> optimise)
│   ├── validator.py      # planning-compliance checks -> list of issues
│   ├── cost.py           # transparent BOQ cost estimate
│   └── ai.py             # two optional Gemini calls (parse + critique)
├── static/
│   ├── index.html        # page shell, all controls, panel layout, CSS
│   ├── app.js            # state, fetch, event wiring, issues/cost rendering
│   ├── render2d.js       # SVG 2D floor plan renderer
│   └── render3d.js       # Three.js 3D massing renderer
├── requirements.txt      # fastapi, uvicorn, pydantic, google-genai
├── .gitignore            # __pycache__ / *.pyc
├── README.md             # architecture overview + AI/code split
├── HANDOVER.md           # original build plan (pre-existing, do not edit)
└── CODE_UNDERSTANDING.md # this file
```

Nothing in `auxillary/render_plan.json` is used by the running app — it's a
leftover schema sketch from before the build started and is not imported or
referenced anywhere.

---

## 2. Data flow (end to end)

```
Browser (index.html + app.js)
  |  reads form inputs (or AI-parsed spec) into a ProjectSpec-shaped JSON
  v
POST /api/generate  (backend/main.py)
  |
  |-- engine.generate_plan(spec)   -> Plan dict (rooms, doors, windows, adjacency lines)
  |-- validator.validate(spec,plan)-> list[Issue]   (attached as plan["issues"])
  |-- cost.compute_cost(spec,plan) -> BOQ dict       (attached as plan["cost"], or None if infeasible)
  v
Plan JSON returned to browser
  |
  |-- render2d.js draws the SVG from plan.floors[i]
  |-- render3d.js draws the Three.js massing from the SAME plan.floors
  |-- app.js renders the issues panel and cost table from plan.issues / plan.cost
```

**Everything downstream reads the exact same `plan` object.** There is no
separate 3D data path and no separate validation data path — this is what
guarantees 2D/3D consistency and stops the app from ever silently rendering
something the validator didn't also see.

Two more optional endpoints:

```
POST /api/parse      text -> ai.parse_brief(text) -> ProjectSpec | null
POST /api/critique    plan+issues -> ai.critique(...) -> 3-bullet string | null
```

Both are called from the browser only when the user clicks a button
("Parse with AI" / "Get AI Critique"). Neither is on the critical path of
`/api/generate` — the plan is always generated by deterministic code first.

---

## 3. `backend/rooms.py` — data tables (no logic)

```python
ROOM_SPECS = {
  "master_bedroom": {"min": 11.0, "ideal": 14.0, "min_w": 3.0, "zone": "private"},
  "bedroom":        {"min":  9.5, "ideal": 12.0, "min_w": 2.7, "zone": "private"},
  "living":         {"min": 12.0, "ideal": 20.0, "min_w": 3.0, "zone": "social"},
  "dining":         {"min":  8.0, "ideal": 12.0, "min_w": 2.5, "zone": "social"},
  "kitchen":        {"min":  5.5, "ideal":  9.0, "min_w": 1.8, "zone": "service"},
  "bathroom":       {"min":  2.8, "ideal":  4.0, "min_w": 1.2, "zone": "private"},
  "utility":        {"min":  2.0, "ideal":  3.5, "min_w": 1.2, "zone": "service"},
  "staircase":      {"min":  8.0, "ideal": 10.0, "min_w": 2.4, "zone": "circ"},
  "parking":        {"min": 12.5, "ideal": 15.0, "min_w": 2.5, "zone": "service"},
  "balcony":        {"min":  3.0, "ideal":  5.0, "min_w": 1.2, "zone": "social"},
  "entrance":       {"min":  2.0, "ideal":  4.0, "min_w": 1.2, "zone": "circ"},
}
```

11 room types, each with an NBC-2016-inspired minimum area, an "ideal"
(comfortable) area used as the target the optimizer scales toward, a
minimum usable width, and a zone (`social` / `service` / `private` /
`circ`) used both for 2D/3D coloring and for some validator rules.

```python
ADJACENCY = {
  ("kitchen", "dining"):    10,
  ("dining", "living"):      8,
  ("bedroom", "bathroom"):   9,
  ("living", "entrance"):    8,
  ("parking", "entrance"):   7,
  ("living", "staircase"):   6,
  ("bedroom", "kitchen"):   -5,
  ("parking", "bedroom"):   -4,
  ("bathroom", "living"):   -3,
  ("bathroom", "kitchen"):  -10,
  ("parking", "living"):    -2,
  ("parking", "kitchen"):   -2,
}
```

Positive = these two room types should share a wall; negative = they
shouldn't. **The last four entries (`bathroom`↔`living`, `bathroom`↔`kitchen`,
`parking`↔`living`, `parking`↔`kitchen`) were added manually after the
initial build** to push bathrooms and parking away from the social/service
rooms they'd otherwise land next to — this directly shapes the optimizer's
scoring in `engine.py` (see §4.5) and needs no other code change to take
effect, since `adjacency_weight()` just does a dictionary lookup.

Lookup is order-independent: `engine.adjacency_weight(a, b)` checks both
`(a, b)` and `(b, a)`, and `master_bedroom` is normalized to `bedroom` before
the lookup (so it shares `bedroom`'s adjacency rules).

---

## 4. `backend/engine.py` — the geometry engine (the core of the app)

This is the only file that does geometry math. Everything else (validator,
cost, both renderers) reads its output; nothing recomputes positions.

### 4.1 `Rect` — the one geometry primitive

```python
class Rect:
    __slots__ = ("x", "y", "w", "h")
    def __init__(self, x, y, w, h): ...
    @property
    def area(self): return self.w * self.h
```

Plain axis-aligned rectangle in metres, `(x, y)` = top-left corner. Used
everywhere internally; never serialized directly (rooms are serialized as
plain dicts with `x/y/w/h/area` fields).

### 4.2 Unit conversion — `ft_to_m`

```python
FT_TO_M = 0.3048
def ft_to_m(x): return x * FT_TO_M
```

The **only** place feet enter the system. `ProjectSpec.plot_width_ft` /
`plot_depth_ft` are converted to metres at the very top of `generate_plan`;
every other number in the engine, validator, and both renderers is in
metres. This was a deliberate anti-bug rule (mixing ft/m silently is a
classic source of wrong-looking output).

### 4.3 The envelope and the feasibility gate

```python
bw = plot_w - (spec.setback_left_m + spec.setback_right_m)
bd = plot_d - (spec.setback_front_m + spec.setback_rear_m)
```

`bw × bd` is the **buildable envelope** — every room, on every floor, must
fit inside this rectangle. Setback compliance is therefore structural: it's
not possible for a room to render outside the setback line, because the
envelope rectangle passed to the splitter is already inset by the setbacks.

Two early-exit checks, both returning `{"ok": False, "reason": "..."}`
instead of drawing anything:

1. **Degenerate envelope** — if setbacks consume the whole plot (`bw <= 0`
   or `bd <= 0`), stop immediately with the actual negative dimensions.
2. **Aggregate feasibility gate** — sum every requested room's *minimum*
   area (across all floors, plus one staircase per floor if the building is
   multi-storey) and compare it to `bw * bd * spec.floors` (total buildable
   area across all floors). If the requested rooms can't possibly fit even
   at minimum size, stop with a message stating the exact numbers needed
   vs. available and a concrete suggestion (bigger plot / fewer rooms / add
   a floor).

This gate is intentionally **aggregate**, not per-floor — matching the
original plan. A plan can still pass this gate and yet have individual
rooms come out undersized on a specific floor (e.g. if one floor is asked to
carry disproportionately many rooms); that's not a geometry bug, it's a
planning problem, and it's exactly what `validator.py`'s per-room min-area
check is for. See §7 for a worked example of this distinction.

### 4.4 The splitter — `split()`

```python
def split(rect, rooms):
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
    if w >= h:
        return split(Rect(x, y, w*frac, h), A) + split(Rect(x+w*frac, y, w*(1-frac), h), B)
    else:
        return split(Rect(x, y, w, h*frac), A) + split(Rect(x, y+h*frac, w, h*(1-frac)), B)
```

Recursive binary rectangle partition — this replaces a full squarified
treemap (see `HANDOVER.md` Decision 1) at roughly a third of the code and a
tenth of the bug surface, at the cost of slightly less-square aspect ratios.

- Splits the room list into two contiguous groups `A`/`B` at whatever index
  makes their area sums as close to 50/50 as possible.
- Cuts the current rectangle **perpendicular to its longer side** — this is
  the "self-squarifying" trick: it keeps recursively-produced rectangles
  from becoming needle-thin as often as a naive always-cut-vertically
  approach would.
- Recurses on each half with its own sub-list and sub-rectangle.
- **Guarantees by construction**: every room's returned area is an exact
  proportional slice of `rect.area` (no rounding drift beyond float
  precision), the two children never overlap (they partition the parent
  exactly), and nothing falls outside the parent rectangle. These are
  exactly the four properties asserted at the bottom of this file (§4.9).

Because list order is preserved through the recursion, rooms that are
adjacent in the input list tend to end up spatially adjacent — which is the
hook the optimizer (§4.5) exploits by trying many different orderings.

### 4.5 Per-floor room budget — `compute_budget()`

```python
def compute_budget(room_types, plate_area):
    ideal = [ROOM_SPECS[t]["ideal"] for t in room_types]
    scale = plate_area / sum(ideal)
    areas = [a * scale for a in ideal]
    mins = [ROOM_SPECS[t]["min"] for t in room_types]
    # clamp anything below its minimum, track the shortfall as `deficit`
    # redistribute deficit proportionally from the rooms that weren't clamped
```

Takes a list of room types for one floor and a target plate area (the
envelope, or the envelope minus the staircase strip), and returns one area
per room such that:

1. Areas start proportional to each room's "ideal" size.
2. Any room that would land below its `min` is clamped up to `min`.
3. The resulting shortfall (`deficit`) is taken back out of the rooms that
   *weren't* clamped, proportional to their own size, so the areas still
   sum to `plate_area` overall.
4. If the deficit is bigger than what the non-clamped pool can give up (an
   extremely tight floor), the areas are left as-is — the note in the code
   says the aggregate feasibility gate should have already caught the truly
   impossible cases, and downstream `split()` will still produce a
   geometrically valid (just visually tight) layout, which the validator
   will flag per-room.

### 4.6 Scoring and candidate selection — `evaluate_layout()` / `best_split()`

```python
def evaluate_layout(placed, fixed):
    # adjacency_score = sum over every pair of rooms that share a wall of
    #                    ADJACENCY[(type_a, type_b)] * shared_wall_length
    # violations = count of `placed` rooms whose aspect ratio > 3:1
    #              or whose short side < that room's min_w
    return violations, adjacency_score, penalty_total
```

`shared_wall(a, b)` (a small standalone function) returns the length of
wall two rectangles have in common, or 0 if they don't touch — it checks
whether one rectangle's right edge coincides with the other's left edge (or
top/bottom) within a 1cm epsilon, then returns the overlapping span.

`best_split()` then does the actual search:

```python
for _ in range(N_CANDIDATES):          # N_CANDIDATES = 400
    order = shuffle(rooms)
    areas = compute_budget(order, rect.area)
    placed = split(rect, order_with_areas)
    violations, adj, penalty = evaluate_layout(placed, fixed)
    key = (violations, -adj, penalty)
    keep the candidate with the smallest key
```

This is the one place where a deliberate design change was made mid-build
(documented in the session, not just in this file): the very first version
scored candidates with a single blended number
(`adjacency_score - penalties`), which let the search accept a
higher-adjacency layout that left a service room (like a bathroom or
utility) too thin to be usable. The fix was to rank candidates
**lexicographically**: first by fewest rooms violating min-width/aspect-
ratio at all, only using the adjacency score to break ties among
equally-compliant layouts. In practice this reliably finds a
zero-violation layout whenever one exists among the 400 shuffles, at zero
extra cost (still one pass over the same candidates).

`rng` is a single `random.Random(RNG_SEED)` (`RNG_SEED = 42`) created once
per `generate_plan()` call and threaded through every floor, so **the same
input spec always produces the exact same plan** — required for a
reproducible demo.

### 4.7 Multi-floor staircase alignment — `compute_staircase_strip()`

```python
def compute_staircase_strip(bw, bd, rng):
    if bw >= bd:
        w = max(min_w, ideal_area / bd); w = min(w, bw * 0.4)
        stair = Rect(bw - w, 0, w, bd)          # full-height strip, right edge
        remaining = Rect(0, 0, bw - w, bd)
    else:
        h = max(min_w, ideal_area / bw); h = min(h, bd * 0.4)
        stair = Rect(0, bd - h, bw, h)           # full-width strip, bottom edge
        remaining = Rect(0, 0, bw, bd - h)
    return stair, remaining
```

Rather than letting the optimizer place the staircase independently on
every floor (which could put it in a different spot each time — visually
proving *misalignment*, the opposite of what a real staircase needs), the
staircase's rectangle is computed **once**, from the building envelope
alone (which is the same on every floor), and is then reused as a literal
`fixed` rect on every floor. `generate_plan()`'s per-floor loop does:

```python
fixed = [(stair_room, stair_rect)]           # same stair_rect object every time
placed, score = best_split(remaining_rect, other_rooms, fixed, rng)
```

The staircase spans the full width or full height of the *shorter* side of
the envelope (whichever keeps its area smaller), so the "remaining"
rectangle for everything else is always a clean rectangle too — no
L-shaped regions, so the same `split()` function keeps working unmodified.
The staircase is still included in `evaluate_layout`'s adjacency scoring
(it's in `fixed`, contributing to `adjacency_score` via shared walls with
whatever ends up next to it) but is **not** scored for width/aspect
violations, since its shape is fixed by construction, not by the optimizer
— `validator.py` mirrors this exemption (§5).

### 4.8 Doors, windows, adjacency lines — `compute_openings()`

Everything the frontend draws beyond plain room rectangles is computed
here, in Python, so `render2d.js` never has to do geometry — it only reads
`floor.doors`, `floor.windows`, `floor.adjacency_lines` and draws them.

For every room:

- **Windows**: for each of the room's 4 wall segments that lies on the
  building's exterior envelope (checked via `_wall_segments()`, which tags
  each side with whether it touches `x=0`, `x=bw`, `y=0`, or `y=bd`), place
  a window centered on that segment (skipping segments under 0.6 m, and
  skipping staircases entirely — they don't get windows).
- **Doors**: for each room, find its best neighbor — the one it shares a
  wall with, preferring the neighbor with the highest positive
  `ADJACENCY` weight, tie-broken by longest shared wall. `entrance` and
  `parking` prefer an *exterior* door instead (a front door / car entry
  should face the street, not the interior), falling back to the
  interior-neighbor door if no suitable exterior wall exists, and falling
  back again to any exterior wall if the room is otherwise fully enclosed.
- **Adjacency lines**: for every pair of rooms that (a) share a wall and
  (b) have a *positive* `ADJACENCY` weight, record a line from room-center
  to room-center. This is exactly what `render2d.js` draws as the green
  dashed lines — "the spatial logic made visible."

### 4.9 `generate_plan()` — orchestration

Ties §4.3–4.8 together per floor and assembles the final `Plan` dict:
`{ok, reason, plot_w_m, plot_d_m, envelope_w_m, envelope_d_m, setbacks,
floors: [{index, rooms, adjacency_score, doors, windows, adjacency_lines}],
adjacency_score}`. `floor.rooms` includes the staircase (with `floor` set
per floor) alongside the optimizer's placed rooms.

### 4.10 Self-test — `if __name__ == "__main__":`

Running `python -m backend.engine` generates one sample plan (30×40 ft,
2 floors, 3 bedrooms, 2 bathrooms, parking, balcony) and asserts:

1. Every room's reported `area` matches its rectangle's actual `w * h`.
2. Every floor's room areas sum to that floor's buildable envelope area
   (within 0.1 m²).
3. No two rooms on the same floor overlap (checked pairwise, over every
   combination).
4. Every room's rectangle lies fully inside the envelope (within a 0.05 m
   tolerance for floating-point).

**If these four hold, it is mathematically impossible for the app to
render a geometrically invalid plan** — the only failures that remain
possible are *planning* failures (undersized rooms, poor adjacency, bad
coverage), which is exactly what the validator exists to catch and explain.

---

## 5. `backend/validator.py` — planning compliance

Pure function `validate(spec, plan) -> list[Issue-dict]`, where each issue
is `{level: "ok"|"warn"|"fail", message, actual, required, fix}`.

If `plan["ok"]` is already `False` (the engine's feasibility gate tripped),
validation short-circuits to a single `fail` issue carrying the engine's
own reason, plus a generic fix suggestion.

Otherwise it checks, in order:

| Check | Level on failure | Notes |
|---|---|---|
| Each of front/rear/left/right setback vs. `RECOMMENDED_SETBACKS` | fail | Compares the *values the user chose* against NBC-style recommended minimums — the engine will happily use a smaller setback if asked, this is what flags it. |
| Ground coverage (`envelope_area / plot_area`) ≤ 65% | fail | `GROUND_COVERAGE_MAX = 0.65` |
| FAR (`envelope_area * floors / plot_area`) ≤ 2.0 | warn | `FAR_MAX = 2.0`, documented as a typical low-rise residential ceiling (no single number is codified in the PDF; this is a reasonable assumption, called out in the code comment) |
| Each room's `area` ≥ its `min_area` | fail | Real numbers on both sides |
| Each room's shorter side ≥ its `min_w` | fail | |
| Each room's aspect ratio ≤ 3:1 | fail | **Except `staircase`** — exempted because it's a fixed full-span circulation strip by construction (§4.7), not something the optimizer ever tries to make square; validator and engine now agree on this. |
| Parking rectangle fits a 2.5×5.0 m car (either orientation) | fail | |
| Staircase rectangle identical (within 5cm) across every floor | fail | Directly tests the guarantee from §4.7 |
| Every bedroom shares a wall with a same-floor bathroom | warn | Uses `shared_wall()` imported from `engine.py` — no geometry re-derived here |
| Ground floor contains both `entrance` and `living` | fail | |
| Overall `adjacency_score` ≥ 0 | warn | |

Every `fail`/`warn` issue includes a `fix` string with a concrete next
step, per the PDF's requirement that the system "clearly identify issues
instead of silently generating an invalid plan."

---

## 6. `backend/cost.py` — transparent cost estimate

```python
def compute_cost(spec, plan):
    built_up_sqft = envelope_w_m * envelope_d_m * spec.floors * 10.7639
    bathroom_count = count of all "bathroom" rooms across all floors
    parking_sqft   = sum of all "parking" room areas, in sqft
    items = [
      Structure:  rate=spec.cost_rates.structure_per_sqft,      qty=built_up_sqft
      Finishes:   rate=spec.cost_rates.finishes_per_sqft,       qty=built_up_sqft
      Electrical: rate=spec.cost_rates.electrical_per_sqft,     qty=built_up_sqft
      Plumbing:   rate=spec.cost_rates.plumbing_per_bathroom,   qty=bathroom_count
      Parking:    rate=spec.cost_rates.parking_per_sqft,        qty=parking_sqft
    ]
    total = sum(rate * qty for each item)
```

Every line shows its own rate and quantity — no black-box total. Rates come
straight from `spec.cost_rates` (a `CostRates` pydantic model with sane
defaults), which the UI lets the user edit and resubmits with every
`/api/generate` call, so editing a rate and clicking through recomputes the
whole BOQ server-side (rule: no math duplicated in JS). Verified: adding a
floor takes structure/finishes/electrical to ~2×, while plumbing (per
bathroom) and parking (ground-floor only) don't necessarily double, so the
total lands at "roughly doubles," not exactly 2× — matches the plan's own
acceptance criterion.

---

## 7. `backend/ai.py` — the two optional Gemini calls

Both functions are wrapped end-to-end in `try/except Exception: return
None` — there is no code path in this file that can raise outward. This
was the single most safety-critical file in the project because the whole
app has to work with AI completely off.

```python
def _get_client():
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return None                      # AI off -- normal, not an error
    try:
        from google import genai
        return genai.Client(api_key=api_key)
    except Exception:
        return None
```

- **`parse_brief(text) -> ProjectSpec | None`** — sends the free-text brief
  to `gemini-3.8-flash` with `response_mime_type="application/json"` and
  `response_schema=ProjectSpec` (the SDK converts the Pydantic model to a
  JSON Schema itself), then re-validates the returned JSON through
  `ProjectSpec(**data)` before handing it back — so even a structurally
  weird-but-valid-JSON response still gets Pydantic's own validation before
  it can reach the engine.
- **`critique(plan, issues) -> str | None`** — builds a small summary dict
  (adjacency score, floor count, up to 5 fail messages, up to 3 warnings)
  and asks the model for exactly 3 short bullet observations as plain
  text. No structured output needed here since it's just prose.

Verified behavior (both in isolated Python calls and through the live
FastAPI endpoints, with `GEMINI_API_KEY` unset and separately set to an
obviously invalid string): `parse_brief` and `critique` both return `None`
in every failure mode, `/api/generate` is completely unaffected (AI never
touches it), and `/api/parse` / `/api/critique` return `{"spec": null}` /
`{"critique": null}` cleanly rather than a 500.

---

## 8. `backend/models.py` — the shared schema

- `CostRates` — the 5 editable cost rates, with the defaults from the plan.
- `ProjectSpec` — the one input shape used by `/api/generate`, AI parsing,
  and the frontend form: plot dimensions (feet), floors/bedrooms/bathrooms
  counts, parking/balcony flags, 4 setbacks (metres), and `cost_rates`.
  Bounds are enforced by Pydantic (`floors` 1–4, `bedrooms`/`bathrooms`
  0–10, dimensions `> 0`) — an invalid spec never reaches the engine at
  all; FastAPI rejects it with a 422 before `generate_plan()` is called.
- `RoomOut`, `Issue`, `FloorOut`, `Plan` — typed mirrors of what the engine
  and validator actually return. In practice `main.py` builds and returns
  plain dicts (not `Plan(...)` instances) for flexibility, but these
  models document the exact contract and would be the natural next step if
  response validation were added later.

---

## 9. `backend/main.py` — FastAPI wiring

```python
@app.post("/api/generate")
def generate(spec: ProjectSpec):
    plan = generate_plan(spec)
    issues = validate(spec, plan)
    plan["issues"] = issues
    plan["cost"] = compute_cost(spec, plan) if plan["ok"] else None
    return plan
```

This is the entire orchestration: engine → validator → cost, in that
order, all synchronous, all on one request. `/api/parse` and `/api/critique`
are thin wrappers around `ai.py`'s two functions. `/api/health` is a plain
liveness check. The static file mount (`StaticFiles(..., html=True)` at
`"/"`) is registered **last**, after every `/api/...` route — Starlette
matches routes in registration order, so the API routes are always tried
first and the mount only catches everything else (serving `index.html` at
`/` and the other static files by path).

---

## 10. Frontend

### 10.1 `static/index.html`

Single-page shell: a 3-column CSS grid (parameters / canvas+critique /
validation+cost, collapsing to one column under 1000px). All styling is
inline `<style>` — no build step, no CSS framework, per the plan's
"no React, no npm" decision. Loads Three.js and OrbitControls from
jsdelivr (r128, pinned versions — the originally-planned cdnjs URL for
OrbitControls 404s, jsdelivr was substituted after verifying it resolves),
then `render2d.js`, `render3d.js`, `app.js` in that order (both renderers
must be defined before `app.js` runs, since it calls into them
immediately on load).

Controls include: an AI-brief textarea + "Parse with AI" button; plot
width/depth (ft); floors/bedrooms/bathrooms; parking/balcony checkboxes;
4 setback fields (m); 5 cost-rate fields (₹); a manual "Generate Plan"
button; and the three preset buttons.

### 10.2 `static/app.js` — state and wiring

- `state = { plan, currentFloor, currentView }` — the only client-side
  state; everything else is derived from the last `plan` returned by the
  server.
- `readSpecFromForm()` / `writeSpecToForm()` — the two-way mapping between
  the DOM form and a `ProjectSpec`-shaped object (including the nested
  `cost_rates`). `writeSpecToForm` is used both by presets and by the
  AI-parse response.
- `generate()` — `fetch("/api/generate", ...)`, shows/hides the loading
  spinner, shows a red error banner on any thrown error or non-2xx
  response, and on success updates `state.plan` and calls
  `renderFloorTabs()`, `renderAll()`, `renderIssues()`,
  `window.renderCost()`.
- `renderFloorTabs()` — rebuilds the floor-tab buttons from
  `plan.floors`, labeling index 0 "Ground Floor" and others "Floor N".
- `renderAll()` — picks the current floor's data and calls either
  `renderPlan2D()` or `renderPlan3D()` depending on `state.currentView`;
  also updates the adjacency-score badge text. If the plan is infeasible,
  clears the SVG and shows "plan infeasible" instead of trying to draw
  nothing.
- `renderIssues()` — counts issues by level, colors the summary line
  red/amber/green accordingly, and renders every issue (fails first, then
  warnings, then oks) with its actual/required numbers and fix text.
- `renderCost()` (exposed as `window.renderCost` so `generate()` can call
  it without a circular import concern) — renders the BOQ table and total,
  or an explanatory message if the plan is infeasible.
- `applyPreset(name)` — looks up `PRESETS[name]`, writes it into the form,
  and calls `generate()`.
- `wireEvents()` — attaches all the above to their DOM elements, including
  a `change` listener on every numeric input/rate field and every
  checkbox, so **any parameter change regenerates the plan automatically**
  (the "responds dynamically to changes" rubric item), plus the
  `critiqueBtn` and `parseBtn` click handlers which call `/api/critique`
  and `/api/parse` respectively and always render a graceful fallback
  message on failure rather than leaving the button looking broken.

### 10.3 `static/render2d.js` — SVG renderer

Pure function `renderPlan2D(svg, plan, floorIndex, tooltip)` — clears the
SVG and redraws everything from scratch every call (simplest possible
correct approach; plans are small enough that this is instant). No
geometry is computed here — every coordinate comes straight from the plan
JSON, offset only by a fixed drawing origin/padding.

Draw order (matches the plan's priority list):

1. Plot boundary (dashed rect) and the building envelope (solid rect,
   inset by the setbacks — the gap between the two rects *is* the visual
   setback indicator, so no separate dotted setback line is drawn, that
   would just sit exactly on top of the envelope's own border).
2. Room rectangles, filled by `ZONE_COLORS[room.zone]`.
3. Furniture glyphs (`drawFurniture()` — 5 hand-drawn glyphs: bed, sofa,
   car, toilet, stove, chosen per room `type` via the `FURNITURE` map, at
   55% opacity so they don't fight the room label).
4. Room name + area + dimensions, as two centered `<text>` elements.
5. Windows — thin cyan-on-white double lines centered on each exterior
   wall segment from `floor.windows`.
6. Doors — a white gap plus a small quarter-circle swing arc, from
   `floor.doors`.
7. Green dashed adjacency lines, from `floor.adjacency_lines`.
8. Dimension lines (plot width along the top, plot depth along the left,
   with tick marks and a rotated label for the vertical one).
9. Per-room invisible hit-rectangles wired to `mousemove`/`mouseleave` for
   the hover tooltip (exact w×h×area vs. that room's minimum).

Room rectangle stroke width (`0.09`) is deliberately thick, not a hairline,
so walls read as walls at any zoom level.

### 10.4 `static/render3d.js` — Three.js massing renderer

Pure function `renderPlan3D(container, plan)`, with a module-level
`ensureScene()` that lazily creates the renderer/scene/camera/controls
exactly once per container and reuses them on every subsequent call (so
switching floor tabs or regenerating doesn't leak WebGL contexts).

Per room, 5 boxes: one thin dark floor slab (`0x555b66`, `SLAB_THICKNESS =
0.3` — made deliberately thick and dark after visual testing showed a
thin/light slab let same-colored zones on adjacent floors visually blend
into what looked like a single storey) plus 4 perimeter wall boxes
(`WALL_HEIGHT = 2.7`, `WALL_THICKNESS = 0.12`), colored by
`ZONE_COLOR_3D[room.zone]`. A single large ground plane represents the
plot. One ambient + one directional light. `OrbitControls` with damping
enabled for smooth rotation.

Camera framing (`renderPlan3D`'s tail): computed from spherical
coordinates — a fixed elevation angle (32°) and azimuth (45°) at a
distance scaled to the building's footprint and height, rather than a
fixed offset — an earlier version used a fixed offset formula that put the
camera almost directly overhead, which foreshortened the building's height
so badly that a 2-floor building looked like one storey. The current
formula was verified visually (headless-Chromium screenshots) to show
both floors clearly, with the floor-slab bands visible, at the default
camera position and after a rotate-drag.

Explicitly out of scope, per the original plan (§10.4 in HANDOVER.md):
textures, shadows, doors/windows in 3D, roof detail.

---

## 11. Design rules that shaped every file above

These were set before any code was written and are why the code looks the
way it does:

1. **One source of truth.** The backend returns one `plan` JSON; 2D and 3D
   both render *that same object*, so they cannot disagree.
2. **All geometry in Python.** `render2d.js` / `render3d.js` never compute
   a position — they only translate `plan` fields into SVG/Three.js draw
   calls. Even door/window/adjacency-line placement is computed in
   `engine.py`, not in JS.
3. **Metres everywhere internally.** Feet exist only in
   `ProjectSpec.plot_width_ft` / `plot_depth_ft` and are converted once, at
   the top of `generate_plan()`.
4. **AI is optional and isolated.** Every function in `ai.py` returns
   `None` on any failure; nothing else in the app depends on AI succeeding.
5. **Every phase ends in a demoable state.** (Process rule, not code, but
   it's why the engine was built and self-tested before any UI existed.)
6. **No feature without its verification test.** Section 12 lists what was
   actually run and what it proved.

---

## 12. What was actually verified (and how)

| Area | How it was verified | Result |
|---|---|---|
| Engine geometry (4 guarantees) | `python -m backend.engine` self-test, plus a separate edge-case script covering floors=1, bedrooms=0, bathrooms=0, floors=0 (Pydantic rejection), zero-buildable-envelope, and a tiny infeasible plot | All pass |
| Validator | Script asserting a 6×6 m / 4-bedroom plot produces `fail`-level issues with real numbers, and a 12×15 m / 2-bedroom plot produces zero `fail`s | Both pass |
| Full API | `curl` against a running `uvicorn` server: `/api/health`, `/api/generate` (both a tight and a comfortable spec), `/api/parse`, `/api/critique` | All respond correctly; tight-plot response correctly carries multiple `fail` issues with numbers |
| AI failure isolation | Called `ai.parse_brief` / `ai.critique` directly with no `GEMINI_API_KEY` set, and again with `GEMINI_API_KEY=invalid-fake-key-12345` set; then repeated through the live HTTP endpoints and through the browser UI buttons | Every path returns `None` / a graceful UI message; nothing crashes; `/api/generate` is unaffected either way |
| 2D rendering | Headless-Chromium (Playwright) screenshots: initial load, floor-tab switch (staircase pixel-identical position on both floors), room hover tooltip | Zero console errors; staircase alignment visually confirmed |
| 3D rendering | Headless-Chromium screenshots before and after a drag-rotate; a scene-graph inspection (`_buildingGroup.children`) confirming both floors' boxes exist at the correct Y ranges | Zero console errors; camera framing iterated twice until both floors were clearly visible |
| Cost panel | Direct Python call comparing 1-floor vs 2-floor cost (ratio ≈1.95×, "roughly doubles" as required); browser test editing the structure rate live and confirming the total recomputes | Both pass |
| Presets | Ran the literal `30×40 ft` "3BHK" configuration from the original brief and found it **genuinely infeasible** under NBC-style default setbacks (needs ~68 m², provides ~52 m² per floor) — the validator correctly flagged this rather than silently drawing broken rooms. Retuned the presets to `40×60 3BHK` and `30×45 2BHK` (both verified 0 `fail`s) and kept a `15×20` plot as the deliberate-failure preset. | Documented in README.md and reflected in `PRESETS` in `app.js` |
| Full demo script | One scripted Playwright run through the entire demo flow: preset load → shrink plot (red "money shot") → restore (green) → flip floor tabs → 3D rotate → edit cost rate → request AI critique | Zero console errors across the whole flow |

---

## 13. Known limitations (intentional, not oversights)

- The feasibility gate in `engine.py` is aggregate (whole-building), not
  per-floor — a plan can pass it and still have an individual floor come
  out with undersized rooms if that floor is asked to carry a
  disproportionate share of the room list. This is caught by
  `validator.py`'s per-room checks, by design (see §4.3, §7).
- `FAR_MAX = 2.0` in `validator.py` is a reasonable typical assumption, not
  a value taken from the problem statement (which didn't specify one) —
  called out in a code comment.
- 3D view has no textures, shadows, doors/windows, or roof — explicitly
  out of scope per the original plan, to protect the 2D view (worth more
  rubric marks) from a 3D rabbit hole.
- The staircase's rectangle is a fixed full-span strip on the shorter
  envelope dimension; this is what makes cross-floor alignment trivial and
  guaranteed, but it does mean the staircase is not adjacency-optimized
  the way other rooms are (only its neighbors are).
