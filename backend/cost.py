"""Transparent BOQ-style cost estimate: every line shows its rate and quantity.

Two things the old version got wrong, both of which made the number meaningless:

  * built-up area was the ENVELOPE x floors, so the estimate depended only on
    the plot and the setbacks. Adding three bedrooms changed nothing. It is now
    the actual building footprint x floors.
  * parking sat inside the footprint, so it was billed twice -- once as RCC
    structure inside built-up area, once again as a parking line. Parking is
    now open ground outside the building and is billed once, at the open-area
    rate, with no structure above it.

Balconies and terraces are open: they carry a slab and a parapet but no roof,
walls or finishes, so they are billed at a reduced factor and excluded from
built-up area, as they are from FAR.
"""
SQM_TO_SQFT = 10.7639
OPEN_AREA_FACTOR = 0.5     # balconies/terraces: slab + parapet, no roof or walls

from .models import ProjectSpec


def compute_cost(spec: ProjectSpec, plan: dict) -> dict:
    rates = spec.cost_rates
    n_floors = plan.get("floor_count", len(plan["floors"]))

    open_sqm = 0.0
    enclosed_sqm = 0.0
    bathroom_count = 0
    for floor in plan["floors"]:
        for room in floor["rooms"]:
            if room.get("open"):
                open_sqm += room["area"]
            else:
                enclosed_sqm += room["area"]
            if room["type"] == "bathroom":
                bathroom_count += 1

    built_up_sqft = enclosed_sqm * SQM_TO_SQFT
    open_sqft = open_sqm * SQM_TO_SQFT
    park = plan.get("parking")
    parking_sqft = (park["area"] * SQM_TO_SQFT) if park else 0.0
    n_columns = len(plan.get("columns", []))

    items = [
        {"label": "Structure (RCC frame, brickwork)", "rate": rates.structure_per_sqft,
         "unit": "sqft", "qty": round(built_up_sqft, 1)},
        {"label": "Finishes (flooring, painting, doors/windows)", "rate": rates.finishes_per_sqft,
         "unit": "sqft", "qty": round(built_up_sqft, 1)},
        {"label": "Electrical", "rate": rates.electrical_per_sqft,
         "unit": "sqft", "qty": round(built_up_sqft, 1)},
        {"label": "Plumbing", "rate": rates.plumbing_per_bathroom,
         "unit": "bathroom", "qty": bathroom_count},
        {"label": "Balconies / open terraces (slab + parapet only)",
         "rate": round(rates.structure_per_sqft * OPEN_AREA_FACTOR, 0),
         "unit": "sqft", "qty": round(open_sqft, 1)},
        {"label": "Parking / car porch (open ground)", "rate": rates.parking_per_sqft,
         "unit": "sqft", "qty": round(parking_sqft, 1)},
        {"label": "Foundation / column footings", "rate": rates.footing_per_column,
         "unit": "column", "qty": n_columns},
    ]
    for item in items:
        item["amount"] = round(item["rate"] * item["qty"], 0)

    subtotal = sum(item["amount"] for item in items)
    contingency = round(subtotal * rates.contingency_pct / 100.0, 0)
    items.append({"label": f"Contingency @ {rates.contingency_pct}%", "rate": rates.contingency_pct,
                  "unit": "%", "qty": 1, "amount": contingency})

    return {
        "built_up_sqft": round(built_up_sqft, 1),
        "open_sqft": round(open_sqft, 1),
        "floors": n_floors,
        "items": items,
        "total": round(subtotal + contingency, 0),
    }
