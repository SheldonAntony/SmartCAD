"""Request and response schemas.

These are now WIRED UP. Previously Plan / FloorOut / RoomOut / Issue were
declared but never instantiated and no route declared a response_model, so the
API returned unvalidated raw dicts and the schemas silently drifted out of date
(Issue.actual was typed float while the parking check fed it a string).
"""
from typing import Optional, Union

from pydantic import BaseModel, Field


class CostRates(BaseModel):
    structure_per_sqft: float = Field(1600.0, ge=0)
    finishes_per_sqft: float = Field(450.0, ge=0)
    electrical_per_sqft: float = Field(180.0, ge=0)
    plumbing_per_bathroom: float = Field(45000.0, ge=0)
    parking_per_sqft: float = Field(800.0, ge=0)
    footing_per_column: float = Field(18000.0, ge=0)
    contingency_pct: float = Field(7.5, ge=0, le=50)


class ProjectSpec(BaseModel):
    # upper bounds matter: parse_brief() builds one of these from free text, and
    # "a ten acre plot" used to sail straight through into the engine.
    plot_width_ft: float = Field(..., gt=0, le=1000)
    plot_depth_ft: float = Field(..., gt=0, le=1000)
    floors: int = Field(1, ge=1, le=4)
    bedrooms: int = Field(2, ge=0, le=10)
    bathrooms: int = Field(2, ge=0, le=10)
    parking: bool = True
    balcony: bool = True
    setback_front_m: float = Field(3.0, ge=0, le=30)
    setback_rear_m: float = Field(1.5, ge=0, le=30)
    setback_left_m: float = Field(1.2, ge=0, le=30)
    setback_right_m: float = Field(1.2, ge=0, le=30)
    cost_rates: CostRates = Field(default_factory=CostRates)


class RoomOut(BaseModel):
    id: str
    type: str
    name: str
    zone: str
    floor: int
    x: float
    y: float
    w: float
    h: float
    area: float
    min_area: float
    max_area: float
    min_w: float
    exterior: bool
    exterior_required: bool
    reachable: bool
    entered_from: Optional[str] = None
    open: bool = False


class Opening(BaseModel):
    room_id: str
    orientation: str
    x: float
    y: float
    length: float
    swing: int = 1
    kind: str = "internal"


class AdjacencyLine(BaseModel):
    a: str
    b: str
    ax: float
    ay: float
    bx: float
    by: float
    weight: float


class Issue(BaseModel):
    level: str                                  # "ok" | "warn" | "fail"
    message: str
    # the parking check reports "2.80 x 5.00", so this is genuinely a union
    actual: Optional[Union[float, str]] = None
    required: Optional[Union[float, str]] = None
    fix: Optional[str] = None


class FloorOut(BaseModel):
    index: int
    rooms: list[RoomOut]
    adjacency_score: float
    doors: list[Opening] = []
    windows: list[Opening] = []
    adjacency_lines: list[AdjacencyLine] = []


class Plan(BaseModel):
    ok: bool
    reason: Optional[str] = None
    notes: list[str] = []
    plot_w_m: float = 0.0
    plot_d_m: float = 0.0
    envelope_w_m: float = 0.0
    envelope_d_m: float = 0.0
    setbacks: dict = {}
    footprint: Optional[dict] = None
    grid: Optional[dict] = None
    columns: list[dict] = []
    parking: Optional[dict] = None
    floors: list[FloorOut] = []
    floor_count: int = 0
    adjacency_score: float = 0.0
    issues: list[Issue] = []
    cost: Optional[dict] = None
