from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import advisor, ai
from .cost import compute_cost
from .engine import generate_plan
from .models import Plan, ProjectSpec
from .validator import validate

app = FastAPI(title="SmartCAD")

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


class BriefRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=4000)


class CritiqueRequest(BaseModel):
    """The client sends only the SPEC, never a plan.

    The old endpoint accepted a client-supplied `plan` dict and forwarded it
    straight to the model, so the critique could describe a plan this engine
    never produced. The plan is regenerated here instead."""
    spec: ProjectSpec
    # The model call costs ~7 s. The UI asks for rule-based fixes first so
    # something useful appears immediately, then asks again with AI enabled.
    use_ai: bool = True


@app.get("/api/health")
def health():
    return {"ok": True, "ai": ai.status()}


@app.post("/api/generate", response_model=Plan)
def generate(spec: ProjectSpec):
    plan = generate_plan(spec)
    plan["issues"] = validate(spec, plan)
    plan["cost"] = compute_cost(spec, plan) if plan["ok"] else None
    return plan


@app.post("/api/parse")
def parse(req: BriefRequest):
    spec, error = ai.parse_brief(req.text)
    return {"spec": spec.model_dump() if spec else None, "error": error}


@app.post("/api/recommend")
def recommend(req: CritiqueRequest):
    """AI-proposed fixes for the failing checks, each one verified by re-running
    the engine. Falls back to deterministic rules when AI is unavailable, so the
    feature works with no API key."""
    plan = generate_plan(req.spec)
    issues = validate(req.spec, plan)
    return advisor.recommend(req.spec, plan, issues,
                             ai_module=ai if req.use_ai else None)


@app.post("/api/critique")
def critique(req: CritiqueRequest):
    plan = generate_plan(req.spec)
    if not plan["ok"]:
        raise HTTPException(status_code=422, detail=plan["reason"])
    issues = validate(req.spec, plan)
    text, error = ai.critique(plan, issues)
    return {"critique": text, "error": error}


app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
