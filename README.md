# SmartCAD

AI-assisted architectural & spatial planning system, built for Gradskey
Technologies' HACKATHON 2026 (Challenge 01). Turns a plot + requirements
(via sliders or a natural-language brief) into a validated 2D floor plan, a
consistent 3D massing model, and a transparent cost estimate.

## The core claim

> *State-of-the-art neural floor-plan models only **encourage** adjacency
> through a training loss. We **guarantee** it by construction.*

The layout engine is deterministic geometry, not machine learning. AI is used
only to interpret a natural-language brief and to offer a plain-English
critique — it never touches the geometry, and the app works completely with
AI switched off.

## Architecture

```
 Natural-language brief (optional)
        |
        v
 [ai.py: Gemini] -- parse_brief() --> ProjectSpec  (falls back to slider
        |                                            values on any failure)
        v
 [engine.py] deterministic geometry
   envelope -> feasibility gate -> per-floor room budget ->
   recursive rectangle split -> adjacency-optimised room ordering ->
   doors / windows / adjacency lines
        |
        v
      Plan JSON  <---------------------------+  <- single source of truth
        |                                     |
        v                                     |
 [validator.py] setback / coverage / FAR /    |
   room-size / aspect-ratio / parking /       |
   staircase-alignment / adjacency checks     |
        |                                     |
        v                                     |
 [cost.py] transparent BOQ line items         |
        |                                     |
        v                                     v
 render2d.js (SVG)                    render3d.js (Three.js)
   reads the SAME Plan JSON              reads the SAME Plan JSON
   2D and 3D cannot disagree -- there is only one plan.

 [ai.py] critique() -- plan + validation report --> 3 architect-style
   bullets (optional, falls back to nothing on any failure)
```

## What AI does vs. what code does

| | AI (Gemini) | Deterministic code |
|---|---|---|
| Reads a free-text brief and extracts plot size / room counts | Yes | -- |
| Decides where rooms go, their sizes, adjacency | -- | Yes (`engine.py`) |
| Checks setbacks, coverage, FAR, room minimums, parking fit | -- | Yes (`validator.py`) |
| Computes cost | -- | Yes (`cost.py`) |
| Offers a plain-English critique of the finished, validated plan | Yes | -- |

AI never decides whether a plan is valid, and never produces geometry. Every
AI call is wrapped in `try/except` and returns `None` on any failure
(missing/invalid API key, network error, malformed response) — the app is
fully usable end-to-end with AI completely off.

## Engine guarantees

Four properties are asserted in `backend/engine.py` (`python -m backend.engine`):

1. Every room's rendered area matches its computed target area.
2. Room areas on a floor sum exactly to that floor's buildable area.
3. No two rooms on the same floor overlap.
4. Every room lies inside the setback-derived building envelope.

If these hold, the app cannot render a geometrically invalid plan — only
*planning* violations (undersized rooms, poor coverage, etc.) are possible,
and those are exactly what `validator.py` is built to catch and explain with
real numbers and a suggested fix.

## Running it

```
pip install -r requirements.txt
uvicorn backend.main:app --reload
```

Open `http://localhost:8000`. Optionally set `GEMINI_API_KEY` in the
environment to enable the two AI calls (brief parsing, plan critique); the
app works fully without it.

## Stack

Python 3 + FastAPI + Pydantic + `google-genai`, static HTML/JS (no build
step), SVG for 2D, Three.js (CDN) for 3D massing.
