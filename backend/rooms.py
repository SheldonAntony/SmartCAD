"""Room programme tables. Data only -- no logic lives here.

Fields per room type:
  min/ideal/max  : area bounds in m^2. `max` exists so rooms cannot inflate to
                   fill an arbitrarily large plot (a 36 m^2 bathroom is not a
                   bathroom). `ideal` is the target the budget scales toward.
  min_w          : minimum usable clear width in m.
  zone           : social / service / private / circ -- drives colour + rules.
  exterior       : ventilation & daylight requirement, checked as a HARD
                   constraint by the optimiser, not as a scoring preference.
                     "required"   -> must touch the building footprint boundary
                     "front"      -> must sit on the street-facing edge
                     "cantilever" -> must touch the boundary and is built open
                     "outside"    -> not part of the tiled footprint at all
                     "preferred"  -> scored, not enforced
                     "none"       -> no requirement
  access         : "hub"  -> circulation; other rooms may be reached THROUGH it
                   "leaf" -> terminal; may be entered but never passed through
  wet            : shares a plumbing stack; wet rooms want to cluster.
"""

ROOM_SPECS = {
    "master_bedroom": {"min": 11.0, "ideal": 14.0, "max": 22.0, "min_w": 3.0,
                       "zone": "private", "exterior": "required", "access": "leaf", "wet": False},
    "bedroom":        {"min":  9.5, "ideal": 12.0, "max": 18.0, "min_w": 2.7,
                       "zone": "private", "exterior": "required", "access": "leaf", "wet": False},
    "living":         {"min": 12.0, "ideal": 20.0, "max": 34.0, "min_w": 3.0,
                       "zone": "social",  "exterior": "required", "access": "hub",  "wet": False},
    "dining":         {"min":  8.0, "ideal": 12.0, "max": 20.0, "min_w": 2.5,
                       "zone": "social",  "exterior": "preferred", "access": "hub",  "wet": False},
    "kitchen":        {"min":  5.5, "ideal":  9.0, "max": 14.0, "min_w": 1.8,
                       "zone": "service", "exterior": "required", "access": "leaf", "wet": True},
    "bathroom":       {"min":  2.8, "ideal":  4.0, "max":  6.5, "min_w": 1.2,
                       "zone": "private", "exterior": "required", "access": "leaf", "wet": True},
    "utility":        {"min":  2.0, "ideal":  3.5, "max":  6.0, "min_w": 1.2,
                       "zone": "service", "exterior": "preferred", "access": "leaf", "wet": True},
    "staircase":      {"min":  8.0, "ideal": 10.0, "max": 14.0, "min_w": 2.4,
                       "zone": "circ",    "exterior": "none",     "access": "hub",  "wet": False},
    "parking":        {"min": 12.5, "ideal": 15.0, "max": 20.0, "min_w": 2.5,
                       "zone": "service", "exterior": "outside",  "access": "outside", "wet": False},
    "balcony":        {"min":  3.0, "ideal":  5.0, "max":  9.0, "min_w": 1.2,
                       "zone": "social",  "exterior": "cantilever", "access": "leaf", "wet": False},
    "entrance":       {"min":  2.0, "ideal":  4.0, "max":  7.0, "min_w": 1.2,
                       "zone": "circ",    "exterior": "front",    "access": "hub",  "wet": False},
    # upper-floor landing / passage. A hub, so rooms at the front of an upper
    # floor stay reachable from the stair instead of being walled off behind
    # a terminal room.
    "corridor":       {"min":  1.5, "ideal":  3.5, "max": 12.0, "min_w": 0.9,
                       "zone": "circ",    "exterior": "none",     "access": "hub",  "wet": False},
    # Absorbs plate area left over once every room has hit its MAXIMUM. Without
    # it the surplus has nowhere to go and silently re-inflates the rooms,
    # because the tiler only ever honours ratios. Built open, like a balcony.
    "open_terrace":   {"min":  2.0, "ideal":  6.0, "max": 80.0, "min_w": 1.2,
                       "zone": "social",  "exterior": "cantilever", "access": "leaf", "wet": False},
}

# Positive = these two types benefit from sharing a wall. Negative = they don't.
# NOTE: this table scores WALL SHARING only. What a room may open INTO is a
# different rule entirely and lives in ACCESS_PARENTS below -- conflating the
# two is why the old table pushed bathrooms away from kitchens (bad for
# plumbing) when the real rule is only that a WC must not open into a kitchen.
ADJACENCY = {
    ("kitchen", "dining"):      10,
    ("kitchen", "utility"):      8,   # washing / back-of-house flow
    ("dining", "living"):        8,
    ("bedroom", "bathroom"):     9,
    ("living", "entrance"):      8,
    ("living", "staircase"):     6,
    ("entrance", "staircase"):   6,
    ("bathroom", "utility"):     5,   # wet cluster -- one plumbing stack
    ("kitchen", "bathroom"):     3,   # shared plumbing wall is GOOD
    ("balcony", "living"):       6,
    ("balcony", "bedroom"):      6,
    ("parking", "entrance"):     7,
    ("bedroom", "kitchen"):     -5,   # noise / smell
    ("bedroom", "living"):      -2,   # noise
    ("parking", "bedroom"):     -4,
}

# Which room types a given room is allowed to be ENTERED FROM. This is the
# circulation rule, enforced by reachability from the front door -- not by
# wall-sharing scores. A room with no reachable allowed parent is unreachable
# and is a hard failure.
ACCESS_PARENTS = {
    "entrance":       set(),          # root on the ground floor
    "staircase":      {"living", "entrance", "dining"},
    "living":         {"entrance", "staircase", "dining"},
    "dining":         {"living", "entrance", "staircase", "kitchen"},
    "kitchen":        {"dining", "living", "utility", "staircase", "entrance"},
    "utility":        {"kitchen", "staircase", "living"},
    # a WC may open off a bedroom (attached) or off circulation -- never off
    # a kitchen or dining room.
    "bathroom":       {"bedroom", "master_bedroom", "entrance", "living",
                       "staircase", "utility"},
    "bedroom":        {"entrance", "living", "dining", "staircase"},
    "master_bedroom": {"entrance", "living", "dining", "staircase"},
    "balcony":        {"living", "bedroom", "master_bedroom", "dining"},
    "parking":        set(),          # open to the street, not to the house
    "corridor":       {"staircase", "living", "entrance", "dining", "corridor"},
    "open_terrace":   {"living", "dining", "corridor", "staircase", "entrance",
                       "bedroom", "master_bedroom"},
}

# circulation may also deliver you to any of these from a landing
for _t in ("bedroom", "master_bedroom", "bathroom", "kitchen", "utility", "balcony"):
    ACCESS_PARENTS[_t] = ACCESS_PARENTS[_t] | {"corridor"}
del _t

# Rooms that circulation may pass THROUGH. Everything else is terminal: you may
# enter it, but no route to another room is allowed to cross it.
HUB_TYPES = {t for t, s in ROOM_SPECS.items() if s["access"] == "hub"}


def normalize_type(t: str) -> str:
    """master_bedroom scores adjacency the same as bedroom."""
    return "bedroom" if t == "master_bedroom" else t
