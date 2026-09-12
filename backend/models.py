from typing import Optional
from pydantic import BaseModel, Field


class CostRates(BaseModel):
    structure_per_sqft: float = 1600.0
    finishes_per_sqft: float = 450.0
    electrical_per_sqft: float = 180.0
    plumbing_per_bathroom: float = 45000.0
    parking_per_sqft: float = 800.0


class ProjectSpec(BaseModel):
    plot_width_ft: float = Field(..., gt=0)
    plot_depth_ft: float = Field(..., gt=0)
    floors: int = Field(1, ge=1, le=4)
    bedrooms: int = Field(2, ge=0, le=10)
    bathrooms: int = Field(2, ge=0, le=10)
    parking: bool = True
    balcony: bool = True
    setback_front_m: float = 3.0
    setback_rear_m: float = 1.5
    setback_left_m: float = 1.2
    setback_right_m: float = 1.2
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
    min_w: float


class Issue(BaseModel):
    level: str  # "ok" | "warn" | "fail"
    message: str
    actual: Optional[float] = None
    required: Optional[float] = None
    fix: Optional[str] = None


class FloorOut(BaseModel):
    index: int
    rooms: list[RoomOut]
    adjacency_score: float


class Plan(BaseModel):
    ok: bool
    reason: Optional[str] = None
    plot_w_m: float = 0.0
    plot_d_m: float = 0.0
    envelope_w_m: float = 0.0
    envelope_d_m: float = 0.0
    setbacks: dict = {}
    floors: list[FloorOut] = []
    adjacency_score: float = 0.0
    issues: list[Issue] = []
    cost: Optional[dict] = None
