"""Two optional Gemini calls. Both degrade to None on ANY failure -- missing
key, network error, malformed response, SDK shape mismatch. The app must run
end-to-end with AI fully off (rule 4); nothing here may ever raise outward.
"""
import json
import os

from .models import ProjectSpec

MODEL_NAME = "gemini-3.8-flash"


def _get_client():
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return None
    try:
        from google import genai
        return genai.Client(api_key=api_key)
    except Exception:
        return None


def parse_brief(text: str):
    """Natural language -> ProjectSpec, or None if AI is unavailable/fails."""
    try:
        client = _get_client()
        if client is None:
            return None
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
                response_schema=ProjectSpec,
            ),
        )
        data = json.loads(response.text)
        return ProjectSpec(**data)
    except Exception:
        return None


def critique(plan: dict, issues: list):
    """Plan + validation report -> 3 short architect-style bullets, or None."""
    try:
        client = _get_client()
        if client is None:
            return None

        fails = [i for i in issues if i.get("level") == "fail"]
        warns = [i for i in issues if i.get("level") == "warn"]
        summary = {
            "adjacency_score": plan.get("adjacency_score"),
            "num_floors": len(plan.get("floors", [])),
            "fail_count": len(fails),
            "top_fails": [i["message"] for i in fails[:5]],
            "top_warnings": [i["message"] for i in warns[:3]],
        }
        response = client.models.generate_content(
            model=MODEL_NAME,
            contents=(
                "You are a senior architect reviewing an automatically generated "
                "floor plan and its validation report. Give exactly 3 short "
                "bullet observations, one line each, no preamble, no markdown "
                "headers:\n\n" + json.dumps(summary)
            ),
        )
        text = (response.text or "").strip()
        return text or None
    except Exception:
        return None
