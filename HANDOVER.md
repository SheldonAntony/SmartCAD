# SmartCAD — Build Handover

**For:** the next agent/developer picking this up cold.
**Status:** Planning complete. Zero code written. Repo contains only `README.md` and this file.
**Date:** 2026-09-12
**Time budget:** 3 hours total (hackathon constraint).

Read this whole file before writing code. Everything you need is here — you should not need to re-read the problem statement PDF or redo the research.

---

## 1. WHAT WE ARE BUILDING

**Event:** Gradskey Technologies "HACKATHON 2026 — AI Application Hackathon", 3-hour challenge.
**Chosen problem:** Challenge 01 — *AI-Powered Architectural & Spatial Planning System*.
**PDF location:** `C:\Users\Sheldon Antony\Downloads\HACKATHON 2026 AI APPLICATION HACKATHON PROBLEM STATEMENT.pdf` (Challenge 01 is pages 1–11; Challenge 02 is unrelated, ignore it).

### The problem in one paragraph

A client gives requirements ("30×40 ft plot, 3 bedrooms, 2 floors, car parking"). Converting that into a workable floor plan normally takes an architect days of iteration. Build an app that accepts plot + requirements and produces a **validated 2D floor plan** plus a **consistent 3D representation**, with cost estimation.

### The single most important line in the PDF

> *"The system should not merely generate an attractive visual. It should demonstrate spatial logic, feasibility and consistency."*

Judges have seen pretty pictures. They want provably-correct rooms. Design every decision around that.

### Required functionality (from the PDF)

- **A. Parametric input** — UI to enter/modify planning parameters, responds dynamically to changes.
- **B. 2D floor plan** — room zoning, room relationships, approximate dimensions, floor-wise organisation, internal spaces, structural elements, furniture where applicable. SVG/canvas encouraged. *"Understandable to a user rather than merely a collection of boxes."*
- **C. Spatial validation** — plot area, built-up area, space utilisation, setback compliance, room-size feasibility, spatial relationships, parking feasibility, floor-wise distribution. *"Should clearly identify issues instead of silently generating an invalid plan."*
- **D. Topological relationships** — Kitchen↔Dining, Bedroom↔Bathroom, Living↔Entrance, Parking↔Entrance, Staircase↔Floor circulation.
- **E. Cost estimation** — based on built-up area, floors, construction assumptions. *"Transparent and explainable."*
- **F. 3D visualisation** — massing model, *"reasonably consistent with the 2D layout."*

### AI expectation (from the PDF)

AI may: interpret natural-language requirements, suggest room arrangements, generate spatial configurations, identify planning conflicts, provide design suggestions.
**Critical caveat in the PDF:** *"AI output should not be blindly trusted. The application should apply appropriate validation."*

### Expected user flow (from the PDF)

```
User Requirements → AI Interpretation → Spatial Planning → 2D Floor Plan
→ Validation → Corrections/Suggestions → 3D Visualization → Cost/Planning Summary
```

### EVALUATION RUBRIC — optimise against this

| Criterion | Weight | Sub-criteria |
|---|---|---|
| **Spatial Logic & Validation** | **30%** | Correct interpretation, logical room relationships, spatial feasibility, constraint checking, validation quality |
| **Rendering & Visualization** | **30%** | 2D quality, 3D/massing output, **consistency between 2D and 3D**, visual clarity |
| **Interactive UX & Customization** | **20%** | Ease of use, parameter modification, responsiveness |
| **Technical Integration** | **20%** | AI integration, backend/frontend architecture, data flow, code quality |

PDF's closing instruction: *"Do not attempt to build a production-scale platform. Build a working, demonstrable MVP. A simple application that works correctly is stronger than a sophisticated application that fails during demonstration."*

---

## 2. RESEARCH FINDINGS (already done — do not repeat)

GitHub and arXiv were searched. Summary of what exists and what it means for us.

### Academic landscape splits in two

**Camp A — Deep learning.** Graph2Plan (arXiv:2004.13204), HypergraphFormer (arXiv:2605.18932), DPLAN (arXiv:2606.21159), ChatHouseDiffusion (arXiv:2410.11908), GFLAN (arXiv:2512.16275), GAN repos like `SebGr/fml-wright`. **Unusable here** — needs datasets, GPUs, training time.

**Camp B — Procedural geometry.** This is our camp.

### The key paper

**Marson & Musse, "A Novel Algorithm for Real-time Procedural Generation of Building Floor Plans"** — arXiv:1211.5842, also published as *"Automatic Real-Time Generation of Floor Plans Based on Squarified Treemaps Algorithm."*

Its method (fetched and read):
1. Rectangular building outline
2. Rooms grouped hierarchically into **social / service / private** zones
3. Recursive **squarified treemap** slicing to exact target areas
4. **Connectivity graph** with rules (e.g. bedrooms shouldn't interconnect)
5. Corridor optimisation via shortest path
6. Doors placed on shared walls

That is ~80% of Challenge 01, peer-reviewed. **Its stated limitations are our differentiators:**

| Paper's limitation | What we add |
|---|---|
| Single floor only | Multi-floor with vertically aligned staircase |
| Doors placed **randomly** | Doors on best shared-wall segment |
| No natural-language input | Gemini parses the brief |
| No validation reporting | Full compliance panel |
| No 3D | Three.js extrusion |
| No cost | Transparent BOQ |

### The killer quote — use this with judges

From the DPLAN paper, describing the entire deep-learning field:

> *"Structural constraints such as adjacency are usually **encouraged through training objectives rather than guaranteed by construction**."*

State-of-the-art neural models *hope* the kitchen lands near the dining room. **Our approach guarantees it.** This is the strongest talking point available.

### GitHub survey

| Repo | What | Verdict |
|---|---|---|
| `cvdlab/react-planner` | 2D drawing tool + 3D view | ❌ Manual drawing — wrong problem, heavy dep |
| `furnishup/blueprint3d` | Interior design in 3D | ❌ Same |
| `z-aqib/Floor-Plan-Generator-Using-AI` | CSP-tree generator, Python+Java via Jython | ⚠️ Right idea, unusable stack |
| `vencecai/LLM-Floor-plan` | NL → plan via Rhino/Grasshopper | ⚠️ Needs Rhino licence; confirms JSON/DSL pattern |
| `peterdmv/treemap` | Squarified treemap impl | ✅ Reference only — but see Decision 1, we're not using it |

### Bottom line

**No open-source project does the complete pipeline: natural language → validated 2D → consistent 3D → cost.** The pieces exist separately; nobody has assembled them. We are assembling known parts, not inventing new ones. This is why 3 hours is realistic.

---

## 3. TWO ENGINEERING DECISIONS (already made — do not relitigate)

### Decision 1: Recursive split-on-longer-side, NOT squarified treemap

Real squarified treemap needs row accumulation, a `worst()` aspect-ratio function, and `layoutrow()`. ~80 fiddly lines and a prime source of off-by-one bugs.

**Recursive binary split gives identical guarantees in ~25 lines:** exact areas ✓, zero overlaps ✓, zero gaps ✓, good aspect ratios ✓.

The aspect-ratio trick: **always cut perpendicular to the longer side.** That self-squarifies. 95% of the benefit, 30% of the code, 10% of the bug risk.

### Decision 2: No React, no npm, no build step

Serve a static HTML/JS frontend **from FastAPI itself**. This deletes whole bug categories:

| Eliminated | Why it matters |
|---|---|
| CORS errors | Same origin — impossible by construction |
| `npm install` failures | No node_modules |
| Vite proxy misconfiguration | No proxy |
| Build breaking at minute 170 | No build |
| Version conflicts | Three.js from CDN, pinned |

The PDF explicitly permits *"React / HTML / JavaScript"* — zero marks lost. Clean architecture is preserved by splitting into separate JS modules. Refresh the browser and it's live.

**Stack:** Python 3 + FastAPI + Pydantic + `google-genai` · static HTML/JS · SVG for 2D · Three.js (CDN) for 3D · Gemini free tier.

---

## 4. THE SIX ANTI-BUG RULES

The user's explicit requirement was *"without any bugs or least bugs — design it in that way."* These rules are the mechanism.

1. **One source of truth.** Backend returns one `plan` JSON. 2D and 3D both render *that*. They cannot disagree — which also directly satisfies the rubric's "consistency between 2D and 3D".
2. **All geometry in Python.** Zero geometry math in JavaScript. JS only draws. One place for bugs, not two.
3. **Metres everywhere internally.** Convert feet→metres at input, metres→feet at display only. Unit mixing is the classic silent killer.
4. **AI is optional and isolated.** Every AI function wrapped in `try/except`, returns `None` on any failure. **The entire app must work with AI switched off.**
5. **Every phase ends in a demoable state.** Run out of time at any point, still have something to show.
6. **No feature without its verification test.** Tests are specified per phase below — run them.

---

## 5. FILE TREE

```
SmartCAD/
├── backend/
│   ├── main.py          # FastAPI app + static mount + /api/generate
│   ├── models.py        # Pydantic: ProjectSpec, Room, Plan, Issue
│   ├── rooms.py         # NBC room table + adjacency weights (data only)
│   ├── engine.py        # ★ envelope, budget, splitter, adjacency scoring
│   ├── validator.py     # compliance checks → list[Issue]
│   ├── cost.py          # BOQ line items
│   └── ai.py            # Gemini calls, fully optional
├── static/
│   ├── index.html
│   ├── app.js           # state, fetch, controls
│   ├── render2d.js      # SVG renderer
│   └── render3d.js      # Three.js renderer
├── requirements.txt     # fastapi uvicorn pydantic google-genai
├── HANDOVER.md          # this file
└── README.md            # write in Phase 8
```

---

## 6. REFERENCE DATA (NBC 2016 baseline)

Citing real National Building Code of India values instead of invented numbers is a cheap credibility win. NBC sets habitable rooms at roughly 9.5–12 m². **Keep the comment in the code** — it signals domain understanding.

```python
# backend/rooms.py
# NBC 2016 baseline; exact minimums vary by state bye-law
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

# Positive = should be adjacent. Negative = should NOT be adjacent.
ADJACENCY = {
  ("kitchen", "dining"):    10,
  ("dining", "living"):      8,
  ("bedroom", "bathroom"):   9,
  ("living", "entrance"):    8,
  ("parking", "entrance"):   7,
  ("living", "staircase"):   6,
  ("bedroom", "kitchen"):   -5,
  ("parking", "bedroom"):   -4,
}
```

**Defaults:** setbacks front 3.0 m, rear 1.5 m, sides 1.2 m · ground coverage ≤ 65% · floor height 3.0 m · aspect ratio limit 3:1 · parking min 2.5 × 5.0 m.

**Cost rates (editable in UI):** structure ₹1,600/sqft · finishes ₹450/sqft · electrical ₹180/sqft · plumbing ₹45,000/bathroom · parking ₹800/sqft.

---

## 7. BUILD PHASES

Build strictly in this order. The riskiest, highest-value component (the engine) comes first and is verified in a terminal before any UI exists.

### PHASE 0 — Skeleton (15 min)

Create the tree. `pip install fastapi uvicorn pydantic google-genai`.
`main.py`: one route `GET /api/health` → `{"ok": true}`, plus static file mounting so `/` serves `static/index.html`.

> **✅ VERIFY:** `uvicorn backend.main:app --reload` → `localhost:8000` shows a page; `/api/health` returns JSON.
> **Do not proceed until this works.** Everything hangs off it.

---

### PHASE 1 — Room data table (10 min)

Write `rooms.py` exactly as in section 6 above. Data only, no logic.

> **✅ VERIFY:** `python -c "from backend.rooms import ROOM_SPECS; print(len(ROOM_SPECS))"` → prints 11.

---

### PHASE 2 — ★ THE ENGINE (40 min) — most important phase

This is 30% of the marks. Build in `engine.py`, test in the terminal, **no UI yet**.

**2a. Envelope**
```python
bw = plot_w - (setback_left + setback_right)
bd = plot_d - (setback_front + setback_rear)
```
Every room must live inside this rectangle. Setback compliance becomes structural, not a bolted-on check.

**2b. Feasibility gate.** Sum minimum areas of all requested rooms vs `bw * bd * floors`. If it doesn't fit, **stop and return a clear explanation** with the numbers and a suggested fix. Failing loudly here scores better than silently drawing something broken.

**2c. Floor assignment.**
- Ground: entrance, living, dining, kitchen, parking, utility, staircase, 1 bathroom
- Upper: bedrooms, bathrooms, balcony, staircase
- **Staircase is inserted into every floor's room list.**

**2d. Area budget.** Take ideal areas for one floor, scale proportionally to fill that floor plate exactly, then clamp anything below its minimum and redistribute the deficit from the largest rooms. Result: exact per-room budget summing to the plate.

**2e. The splitter — the core, ~25 lines:**

```python
def split(rect, rooms):
    """rect = (x, y, w, h); rooms = [{"name":..., "area":...}, ...]
    Returns [(room, rect), ...]. Guarantees: exact areas, no overlap, no gaps."""
    if len(rooms) == 1:
        return [(rooms[0], rect)]
    total = sum(r["area"] for r in rooms)
    acc, i = 0, 0
    for i in range(len(rooms)):
        if acc + rooms[i]["area"] > total / 2:
            break
        acc += rooms[i]["area"]
    i = max(1, min(i, len(rooms) - 1))          # never an empty group
    A, B = rooms[:i], rooms[i:]
    frac = sum(r["area"] for r in A) / total
    x, y, w, h = rect
    if w >= h:                                   # cut ⟂ to the longer side
        return (split((x, y, w * frac, h), A)
              + split((x + w * frac, y, w * (1 - frac), h), B))
    else:
        return (split((x, y, w, h * frac), A)
              + split((x, y + h * frac, w, h * (1 - frac)), B))
```

Because the split preserves list order, rooms adjacent in the list tend to be adjacent in space. That's the hook for 2f.

**2f. Adjacency optimisation.** Generate 200 candidates by shuffling room order (**seed the RNG** so demo results are reproducible), score each, keep the best.

```python
def shared_wall(a, b):
    """Length of wall shared between two rects. 0 if not touching."""
    eps = 0.01
    if abs(a.x + a.w - b.x) < eps or abs(b.x + b.w - a.x) < eps:
        return max(0, min(a.y + a.h, b.y + b.h) - max(a.y, b.y))
    if abs(a.y + a.h - b.y) < eps or abs(b.y + b.h - a.y) < eps:
        return max(0, min(a.x + a.w, b.x + b.w) - max(a.x, b.x))
    return 0
```

`score = Σ(ADJACENCY[pair] × shared_wall(a,b)) − aspect_ratio_penalty − min_width_violations`

200 candidates is microseconds of work and turns a random partition into a layout an architect would recognise. **Expose the winning score in the API response** — the UI must display it.

**2g. Staircase alignment.** After picking the best ground floor, **hard-copy the staircase rectangle to every upper floor** and lay out remaining upper rooms around it. Do not try to solve this elegantly — copy the rect.

> **✅ VERIFY — write these as 4 asserts in `if __name__ == "__main__":` and run it:**
> 1. Every room's computed area vs target → within 0.1 m²
> 2. `sum(room areas) == buildable area` → within 0.1 m²
> 3. Loop all rectangle pairs → assert **zero overlap**
> 4. Every room lies inside the envelope
>
> **If all four pass, the engine is correct and the app cannot render an invalid plan.** Everything after this is presentation.

---

### PHASE 3 — Validator (20 min)

`validator.py` → `list[Issue]` where `Issue = {level, message, actual, required, fix}`, level ∈ {ok, warn, fail}.

Checks: setback compliance · ground coverage ≤65% · FAR · each room vs min area **and** min width · aspect ratio ≤3:1 · parking ≥2.5×5.0 m · staircase aligned across floors · adjacency score · floor-wise distribution.

Every failure must state the actual number, the required threshold, and a concrete fix.

> **✅ VERIFY:** 6×6 m plot with 4 bedrooms → **must** produce red failures with real numbers. 12×15 m with 2 bedrooms → all green. **If a tiny plot passes, the validator is fake.**

---

### PHASE 4 — SVG 2D renderer (35 min)

`render2d.js`. Renders from the JSON only, no math. Build in this order, stop when time runs out:

1. Plot boundary (dashed) + setback line (dotted) + envelope
2. Room rectangles, fill-coloured by zone (social / service / private / circulation)
3. **Room name + area + dimensions label inside each** ← this is what makes it "not just boxes"
4. Wall thickness — thick stroke, not a hairline
5. Doors — small arc on the best shared-wall segment
6. Windows — thin white gaps on exterior walls
7. Dimension lines along outer edges
8. Furniture — 5 glyphs only: bed, sofa, car, toilet, stove
9. **Green adjacency lines** between related rooms ← makes the logic visible

Plus: floor tabs at top, hover tooltip with exact dimensions.

> **✅ VERIFY:** Change plot size → plan redraws correctly. Switch floor tabs → **staircase stays in the same screen position** (visually proves alignment).

---

### PHASE 5 — Three.js 3D (25 min — HARD TIMEBOX)

`render3d.js`, Three.js from CDN, OrbitControls.

Per room: floor slab + 4 wall boxes at `y = floor_index * 3.0`. Colour walls by zone to match the 2D. Ground plane for the plot. One directional + one ambient light.

**Do not attempt:** textures, shadows, doors/windows in 3D, roof detail.

> **✅ VERIFY:** Rotate. Ground floor positions match 2D exactly. Staircase lines up vertically through all floors.
>
> **⏰ HARD RULE: if 3D isn't working at 25 minutes, delete it and move on.** 2D is worth more marks; a broken 3D canvas ruins the demo.

---

### PHASE 6 — AI layer (20 min) — build LAST, keep optional

`ai.py`. Two functions, both `try/except` → `None`.

```python
def parse_brief(text: str) -> ProjectSpec | None:
    try:
        ...
    except Exception:
        return None      # app continues on slider values
```

**Call 1 — parse.** Natural language → `ProjectSpec` via Pydantic + Gemini structured output.
**Call 2 — critique.** Send finished plan + validation report → 3 architect-style bullet observations as plain text.

Current API shape (verified 2026-09-12 against https://ai.google.dev/gemini-api/docs/structured-output): `google-genai` supports JSON Schema natively, so `YourModel.model_json_schema()` works out of the box. Recommended model `gemini-3.8-flash`. Docs show an `client.interactions.create(...)` form with `response_format={"type":"text","mime_type":"application/json","schema":...}`; a legacy `generate_content` form with `response_mime_type` / `response_schema` also exists. **Wrap the call so an SDK-shape mismatch degrades to `None` instead of crashing** — do not spend demo time debugging SDK versions.

Two AI calls total. Satisfies the PDF's efficiency and "don't blindly trust AI" principles.

> **✅ VERIFY — the single most important test in this document:**
> Set an invalid API key. **The whole app must still work end-to-end.** If it crashes, the fallback is wrong — fix it before anything else. This test protects the entire demo.

---

### PHASE 7 — Cost panel (10 min)

`cost.py` — transparent line items using the rates in section 6. Rates editable in the UI, recompute live. Show every row with its rate and multiplier — no black box.

> **✅ VERIFY:** Add a floor → cost roughly doubles.

---

### PHASE 8 — Polish + rehearse (15 min) — DO NOT SKIP

README with architecture diagram and an explicit statement of the AI/code split. Preset buttons: *"30×40 3BHK"*, *"20×30 2BHK"*, *"Tiny plot (fails)"*. Loading spinner. **Then rehearse the demo twice, out loud.**

---

## 8. DEMO SCRIPT (4 minutes)

| Time | Action | Point being made |
|---|---|---|
| 0:00 | Type *"30x40 plot, 3BHK, 2 floors, car parking, balcony"* → Generate | AI understands humans |
| 0:30 | Show the extracted JSON spec | AI's job is interpretation only |
| 0:50 | Plan appears — point at labels, dimensions, doors, furniture | Not just boxes |
| 1:20 | Point at green adjacency lines: *"kitchen touches dining, every bedroom touches a bathroom — score 87/100"* | **Spatial logic made visible** |
| 1:50 | **Drag plot slider down → red panel with exact numbers and a fix** | **THE MONEY SHOT** |
| 2:20 | Fix it → green | Validator is real, not decorative |
| 2:40 | Flip floor tabs → staircase stays put | Multi-floor consistency |
| 3:00 | Switch to 3D, rotate | *"Same data structure — 2D and 3D cannot disagree"* |
| 3:30 | Cost breakdown, edit a rate live | Transparent, explainable |
| 3:50 | AI critique bullets | AI advises, code decides |

**Closing line:**
> *"State-of-the-art neural floor plan models only encourage adjacency through training loss — they can't guarantee it. We guarantee it by construction. The AI interprets the brief; deterministic geometry builds the plan; the validator proves every room is legal."*

**Why the deliberate failure at 1:50 matters:** it takes 30 seconds and proves the validator works. Almost no other team will do this, because almost no other team's validator will actually work. It is the highest-value 30 seconds in the demo.

---

## 9. CONTINGENCY — cut in this exact order

Each cut still leaves a working demo:

1. Furniture glyphs
2. AI critique (Call 2)
3. **3D entirely** — say *"2D-first; 3D was scoped out"*
4. Windows and doors
5. Cost panel
6. AI parse (Call 1) — sliders alone still demo the full engine

**Never cut:** the engine, the validator, room labels. Those are ~50% of the marks.

**If only 90 minutes remain:** Phases 0, 1, 2, 3 + stripped Phase 4 (steps 1–3 only). Still a strong submission — the validator and adjacency scoring are what nobody else will have.

---

## 10. KNOWN RISKS

| Risk | Mitigation |
|---|---|
| Three.js eats 90 min | Hard 25-min timebox. Grey boxes + orbit controls is enough |
| Gemini rate limit mid-demo | Cache last good response; hardcoded fallback spec; never let a network call block the UI |
| Gemini returns malformed JSON / SDK shape mismatch | `try/except` → fall back to slider values. Pipeline must run with AI fully off |
| Scope creep into furniture detail | 5 glyphs, 20 minutes maximum |
| No time to rehearse | Reserve final 10 min. An unrehearsed demo of good software loses to a rehearsed demo of average software |
| Unit mixing (ft vs m) | Rule 3 — metres internally, convert only at the edges |

---

## 11. SOURCES

- Marson & Musse, *A Novel Algorithm for Real-time Procedural Generation of Building Floor Plans* — https://ar5iv.labs.arxiv.org/html/1211.5842
- Squarified Treemaps (van Wijk et al.) — https://www.win.tue.nl/~vanwijk/stm.pdf
- Graph2Plan — https://ar5iv.labs.arxiv.org/html/2004.13204
- DPLAN (source of the "guaranteed by construction" quote) — https://arxiv.org/html/2606.21159
- HypergraphFormer — https://arxiv.org/html/2605.18932v2
- ChatHouseDiffusion — https://arxiv.org/html/2410.11908v1
- GFLAN — https://arxiv.org/html/2512.16275
- Gemini structured output docs — https://ai.google.dev/gemini-api/docs/structured-output
- Gemini JSON Schema support announcement — https://blog.google/innovation-and-ai/technology/developers-tools/gemini-api-structured-outputs/
- NBC India residential standards — https://www.brigadegroup.com/blog/residential/national-building-code-for-residential-apartments-in-india
- Minimum room size standards India — https://www.houseyog.com/blog/minimum-room-size-standards-india/
- `cvdlab/react-planner` · `furnishup/blueprint3d` · `z-aqib/Floor-Plan-Generator-Using-AI` · `vencecai/LLM-Floor-plan` · `peterdmv/treemap`

---

## 12. START HERE

Begin with **Phase 0**, then go straight through **Phase 2** and get the four assertions passing before touching any UI. By roughly minute 65 you will know whether you have a winning submission, with two hours left to make it look good.

The four assertions in Phase 2 are the backbone of the no-bugs requirement: if rooms have correct areas, never overlap, and always sit inside the envelope, it is **mathematically impossible** for the app to render an invalid plan.
