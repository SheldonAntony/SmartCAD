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
    ("bathroom", "living"):     -3,
    ("bathroom", "kitchen"):     -10,
    ("parking", "living"):     -2,
    ("parking", "kitchen"):    -2,
}
