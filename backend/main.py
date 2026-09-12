from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from pathlib import Path
from pydantic import BaseModel

from . import ai
from .cost import compute_cost
from .engine import generate_plan
from .models import ProjectSpec
from .validator import validate

app = FastAPI(title="SmartCAD")

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


class BriefRequest(BaseModel):
    text: str


class CritiqueRequest(BaseModel):
    plan: dict
    issues: list = []


@app.get("/api/health")
def health():
    return {"ok": True}


@app.post("/api/generate")
def generate(spec: ProjectSpec):
    plan = generate_plan(spec)
    issues = validate(spec, plan)
    plan["issues"] = issues
    plan["cost"] = compute_cost(spec, plan) if plan["ok"] else None
    return plan


@app.post("/api/parse")
def parse(req: BriefRequest):
    spec = ai.parse_brief(req.text)
    return {"spec": spec.model_dump() if spec else None}


@app.post("/api/critique")
def critique(req: CritiqueRequest):
    text = ai.critique(req.plan, req.issues)
    return {"critique": text}


app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
