"""Transparent BOQ-style cost estimate: every line shows its rate and
quantity, no black box. Rates are editable in the UI (ProjectSpec.cost_rates).
"""
SQM_TO_SQFT = 10.7639

from .models import ProjectSpec


def compute_cost(spec: ProjectSpec, plan: dict) -> dict:
    rates = spec.cost_rates
    built_up_sqm = plan["envelope_w_m"] * plan["envelope_d_m"] * spec.floors
    built_up_sqft = built_up_sqm * SQM_TO_SQFT

    bathroom_count = sum(
        1 for floor in plan["floors"] for room in floor["rooms"] if room["type"] == "bathroom"
    )
    parking_sqm = sum(
        room["area"] for floor in plan["floors"] for room in floor["rooms"] if room["type"] == "parking"
    )
    parking_sqft = parking_sqm * SQM_TO_SQFT

    items = [
        {"label": "Structure (RCC frame, brickwork)", "rate": rates.structure_per_sqft, "unit": "sqft", "qty": round(built_up_sqft, 1)},
        {"label": "Finishes (flooring, painting, doors/windows)", "rate": rates.finishes_per_sqft, "unit": "sqft", "qty": round(built_up_sqft, 1)},
        {"label": "Electrical", "rate": rates.electrical_per_sqft, "unit": "sqft", "qty": round(built_up_sqft, 1)},
        {"label": "Plumbing", "rate": rates.plumbing_per_bathroom, "unit": "bathroom", "qty": bathroom_count},
        {"label": "Parking / car porch", "rate": rates.parking_per_sqft, "unit": "sqft", "qty": round(parking_sqft, 1)},
    ]
    for item in items:
        item["amount"] = round(item["rate"] * item["qty"], 0)

    total = round(sum(item["amount"] for item in items), 0)
    return {
        "built_up_sqft": round(built_up_sqft, 1),
        "items": items,
        "total": total,
    }
