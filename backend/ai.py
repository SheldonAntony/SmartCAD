"""Two optional Gemini calls. Both degrade gracefully -- the app must run
end-to-end with AI fully off, and nothing here may raise outward.

What changed: every failure used to be swallowed by a bare `except` that
returned None with no logging, so a missing API key, a wrong model id, a
network error and an SDK change were all indistinguishable -- the UI just said
"unavailable" forever. Each call now returns (value, error) and logs the cause,
so a misconfiguration is visible instead of silent.
"""
import json
import logging
import os

from .models import ProjectSpec

log = logging.getLogger("smartcad.ai")

# Override with SMARTCAD_MODEL if this id is retired; a wrong id is exactly the
# failure the old silent handler hid.
MODEL_NAME = os.environ.get("SMARTCAD_MODEL", "gemini-3.6-flash")
# Calls normally land in 5-8 s; the ceiling is for spikes, not the common case.
TIMEOUT_MS = int(os.environ.get("SMARTCAD_AI_TIMEOUT_MS", "45000"))


def _get_client():
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return None, "GEMINI_API_KEY is not set -- AI features are off."
    try:
        from google import genai
        from google.genai import types
        return genai.Client(api_key=api_key,
                            http_options=types.HttpOptions(timeout=TIMEOUT_MS)), None
    except ImportError:
        return None, "google-genai is not installed (pip install google-genai)."
    except Exception as exc:                       # pragma: no cover
        log.warning("Gemini client init failed: %s", exc)
        return None, f"Could not create the Gemini client: {exc}"


def status() -> dict:
    client, error = _get_client()
    return {"available": client is not None, "model": MODEL_NAME, "error": error}


# Passing ProjectSpec straight through as a response_schema fails: the API
# rejects schemas carrying default values. Declare the shape explicitly and let
# ProjectSpec apply the defaults on the way back in.
_BRIEF_SCHEMA = {
    "type": "object",
    "properties": {
        "plot_width_ft": {"type": "number"},
        "plot_depth_ft": {"type": "number"},
        "floors": {"type": "integer"},
        "bedrooms": {"type": "integer"},
        "bathrooms": {"type": "integer"},
        "parking": {"type": "boolean"},
        "balcony": {"type": "boolean"},
        "setback_front_m": {"type": "number"},
        "setback_rear_m": {"type": "number"},
        "setback_left_m": {"type": "number"},
        "setback_right_m": {"type": "number"},
    },
    "required": ["plot_width_ft", "plot_depth_ft", "floors", "bedrooms", "bathrooms"],
}


def parse_brief(text: str):
    """Natural language -> ProjectSpec. Returns (spec|None, error|None)."""
    client, error = _get_client()
    if client is None:
        return None, error
    try:
        from google.genai import types
        response = client.models.generate_content(
            model=MODEL_NAME,
            contents=(
                "Extract structured residential architectural planning parameters "
                "from this client brief. Use sensible defaults for anything not "
                "mentioned (2 bedrooms, 2 bathrooms, 1 floor, parking off, balcony "
                "on, standard NBC setbacks).\n\nBrief: " + text
            ),
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=_BRIEF_SCHEMA,
            ),
        )
        data = json.loads(response.text or "{}")
        known = set(_BRIEF_SCHEMA["properties"])
        return ProjectSpec(**{k: v for k, v in data.items()
                              if k in known and v is not None}), None
    except Exception as exc:
        log.warning("parse_brief failed (model=%s): %s", MODEL_NAME, exc)
        return None, f"{type(exc).__name__}: {exc}"


def critique(plan: dict, issues: list):
    """Plan + validation report -> 3 short bullets. Returns (text|None, error|None)."""
    client, error = _get_client()
    if client is None:
        return None, error
    try:
        fails = [i for i in issues if i.get("level") == "fail"]
        warns = [i for i in issues if i.get("level") == "warn"]
        summary = {
            "adjacency_score": plan.get("adjacency_score"),
            "num_floors": plan.get("floor_count", len(plan.get("floors", []))),
            "footprint_m2": (plan.get("footprint") or {}).get("area"),
            "fail_count": len(fails),
            "top_fails": [i["message"] for i in fails[:5]],
            "top_warnings": [i["message"] for i in warns[:3]],
        }
        response = client.models.generate_content(
            model=MODEL_NAME,
            contents=(
                "You are a senior architect reviewing an automatically generated "
                "floor plan and its validation report. Give exactly 3 short bullet "
                "observations, one line each, no preamble, no markdown headers:\n\n"
                + json.dumps(summary)
            ),
        )
        text = (response.text or "").strip()
        return (text or None), (None if text else "Model returned an empty response.")
    except Exception as exc:
        log.warning("critique failed (model=%s): %s", MODEL_NAME, exc)
        return None, f"{type(exc).__name__}: {exc}"


# Only these fields may appear in a suggested patch; advisor.sanitize_patch
# enforces it again on the way back, since model output is untrusted input.
_PATCH_SCHEMA = {
    "type": "object",
    "properties": {
        "suggestions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "why": {"type": "string"},
                    "patch": {
                        "type": "object",
                        "properties": {
                            "plot_width_ft": {"type": "number"},
                            "plot_depth_ft": {"type": "number"},
                            "floors": {"type": "integer"},
                            "bedrooms": {"type": "integer"},
                            "bathrooms": {"type": "integer"},
                            "parking": {"type": "boolean"},
                            "balcony": {"type": "boolean"},
                            "setback_front_m": {"type": "number"},
                            "setback_rear_m": {"type": "number"},
                            "setback_left_m": {"type": "number"},
                            "setback_right_m": {"type": "number"},
                        },
                    },
                },
                "required": ["title", "why", "patch"],
            },
        }
    },
    "required": ["suggestions"],
}


def recommend(spec, issues: list, plan: dict):
    """Propose fixes for the failing checks, as (title, why, patch) tuples.

    Nothing here is trusted: the caller applies each patch, re-runs the
    deterministic engine and keeps only the ones that measurably reduce the
    failure count. Returns ([], error) when AI is unavailable, and the rule-based
    fallback in advisor.py takes over.
    """
    client, error = _get_client()
    if client is None:
        return [], error
    try:
        from google.genai import types
        fails = [i for i in issues if i.get("level") == "fail"]
        warns = [i for i in issues if i.get("level") == "warn"]
        context = {
            "current_brief": spec.model_dump(exclude={"cost_rates"}),
            "buildable": plan.get("ok"),
            "why_not_buildable": plan.get("reason"),
            "footprint_m2": (plan.get("footprint") or {}).get("area"),
            "failures": [
                {"problem": i["message"], "actual": i.get("actual"),
                 "required": i.get("required")} for i in fails[:12]
            ],
            "warnings": [i["message"] for i in warns[:5]],
        }
        response = client.models.generate_content(
            model=MODEL_NAME,
            contents=(
                "You are a senior residential architect fixing a failing floor plan. "
                "Propose 3 DIFFERENT changes to the client brief that would resolve the "
                "failures below. Each suggestion must be a minimal patch to the brief -- "
                "change as few fields as possible, and only these fields: plot_width_ft, "
                "plot_depth_ft, floors, bedrooms, bathrooms, parking, balcony, "
                "setback_front_m, setback_rear_m, setback_left_m, setback_right_m. "
                "Give the smallest change that plausibly works, not a generous one. "
                "Vary the strategy across the three: one that grows the plot, one that "
                "reduces the programme, one that changes the storey count or setbacks. "
                "'why' must be one short sentence naming the specific failure it targets.\n\n"
                + json.dumps(context)
            ),
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=_PATCH_SCHEMA,
            ),
        )
        data = json.loads(response.text or "{}")
        out = []
        for item in data.get("suggestions", [])[:5]:
            title, why, patch = item.get("title"), item.get("why"), item.get("patch")
            if title and isinstance(patch, dict):
                out.append((str(title)[:90], str(why or "")[:200], patch))
        return out, None
    except Exception as exc:
        log.warning("recommend failed (model=%s): %s", MODEL_NAME, exc)
        return [], f"{type(exc).__name__}: {exc}"
