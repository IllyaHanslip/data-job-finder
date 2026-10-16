"""Distances from home and rough drive times, worked out locally (no extra web requests)."""

from __future__ import annotations

import csv
import math
import re
from pathlib import Path

EARTH_RADIUS_KM = 6371.0

# Real routes are roughly 30% longer than a straight line, and a mix of A-roads and
# towns averages about 60 km/h. Good enough for sorting jobs, not for planning a journey.
ROAD_FACTOR = 1.3
AVERAGE_SPEED_KMH = 60.0

# Town and city centres across the UK, used when a listing names a place but gives no
# coordinates (Reed never does). Built from GeoNames by tools/build_uk_places.py, which
# lists names shared by different places (Newport, St Ives...) without coordinates so
# that they are never guessed.
PLACES_FILE = Path(__file__).with_name("uk_places.tsv")


def normalise_place(text: str) -> str:
    """Lower-case, drop apostrophes and punctuation, and write Saint as St.

    "Bishop's Stortford" -> "bishops stortford", "Saint Albans" -> "st albans".
    """
    text = text.lower().replace("'", "").replace("’", "")
    words = re.sub(r"[^a-z0-9]+", " ", text).split()
    return " ".join("st" if word == "saint" else word for word in words)


def _load_places(path: Path) -> tuple[dict[str, tuple[float, float]], set[str]]:
    places: dict[str, tuple[float, float]] = {}
    shared: set[str] = set()
    with path.open(encoding="utf-8", newline="") as handle:
        rows = csv.reader((line for line in handle if not line.startswith("#")), delimiter="\t")
        next(rows)  # column names
        for name, latitude, longitude in rows:
            if latitude:
                places[normalise_place(name)] = (float(latitude), float(longitude))
            else:
                shared.add(normalise_place(name))
    return places, shared


PLACES, SHARED_NAMES = _load_places(PLACES_FILE)
_MOST_WORDS = max(len(name.split()) for name in (*PLACES, *SHARED_NAMES))


def find_place(location: str) -> tuple[float, float] | None:
    """Coordinates of a known place named in `location`, or None.

    Longer names are tried first, so "Bourne End" is not read as Bourne. A name shared by
    several places is never guessed, and its words can't match on their own either, so
    "Long Sutton" doesn't become Sutton in London.
    """
    words = normalise_place(location).split()
    used = [False] * len(words)
    for size in range(min(len(words), _MOST_WORDS), 0, -1):
        for start in range(len(words) - size + 1):
            if any(used[start:start + size]):
                continue
            name = " ".join(words[start:start + size])
            if name in PLACES:
                return PLACES[name]
            if name in SHARED_NAMES:
                used[start:start + size] = [True] * size
    return None


def names_place(location: str, name: str) -> bool:
    """True if `location` names the place `name`, as whole words in any surrounding text."""
    wanted = normalise_place(name).split()
    words = normalise_place(location).split()
    size = len(wanted)
    return bool(wanted) and any(words[i:i + size] == wanted for i in range(len(words) - size + 1))


def distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Straight-line distance between two points on the Earth (haversine formula)."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(min(1.0, a)))


def estimate_drive_minutes(straight_line_km: float) -> int:
    """Very rough driving time for a straight-line distance."""
    return round(straight_line_km * ROAD_FACTOR / AVERAGE_SPEED_KMH * 60)
