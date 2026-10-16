"""Build job_finder/uk_places.tsv, the list of UK places used to estimate distances.

Only needed to refresh the list:

1. Download https://download.geonames.org/export/dump/cities1000.zip and unzip it.
2. Run: python tools/build_uk_places.py path/to/cities1000.txt

It keeps every UK town, city and district with at least 1,000 residents. When places more
than 15 km apart share a name, the name is kept for the biggest only if it is at least ten
times the size of the next: "Bradford" means the city, but "Newport" and "St Ives" are left
out rather than risk putting a job in the wrong place. Places are also listed under their
everyday names: "Southend-on-Sea" as "Southend", "Royal Leamington Spa" as "Leamington Spa"
and "Kingston upon Hull" as "Hull". If config.local.toml names your own town, that town is
left out, so it never appears in files you share; the job finder still knows it as home.

The place data comes from GeoNames (https://www.geonames.org), licensed under CC BY 4.0.
"""

from __future__ import annotations

import csv
import math
import re
import sys
import tomllib
from collections import defaultdict
from pathlib import Path

OUTPUT = Path(__file__).resolve().parent.parent / "job_finder" / "uk_places.tsv"
LOCAL_SETTINGS = Path(__file__).resolve().parent.parent / "config.local.toml"
SAME_PLACE_KM = 15
DOMINANCE = 10
HISTORICAL = {"PPLQ", "PPLW", "PPLH", "PPLCH"}  # abandoned, destroyed or historical places
SHORT_FORM = re.compile(r"^(.+?)[\s-]+(?:upon|on|super)[\s-]+", re.IGNORECASE)
ROYAL = re.compile(r"^Royal\s+(.+)$", re.IGNORECASE)
# Places whose short form people don't use: "Kingston" means Kingston upon Thames.
NO_SHORT_FORM = {"kingston upon hull"}
# Everyday names that the data doesn't list: everyday name -> name in the data.
ALIASES = {"hull": "kingston upon hull", "leamington": "royal leamington spa",
           "londonderry": "derry", "dereham": "east dereham"}
# Names the data lists once, although they belong to several places of similar size.
SHARED_ANYWAY = {"long sutton"}  # Lincolnshire, Hampshire and Somerset
# Words that appear in job locations without naming a place, so they must not match one.
NOT_PLACES = {
    "airport", "anywhere", "area", "based", "britain", "central", "centre", "city", "county",
    "east", "england", "estate", "field", "flexible", "greater", "home", "hospital", "hybrid",
    "industrial", "ireland", "lane", "multiple", "nationwide", "north", "office", "park",
    "region", "remote", "road", "scotland", "south", "station", "street", "the", "town", "uk",
    "university", "various", "wales", "west", "worldwide",
}


def normalise(text: str) -> str:
    """Same rules as job_finder.geo.normalise_place."""
    text = text.lower().replace("'", "").replace("’", "")
    words = re.sub(r"[^a-z0-9]+", " ", text).split()
    return " ".join("st" if word == "saint" else word for word in words)


def private_home() -> str:
    """The home town named in config.local.toml, normalised, or "" if there isn't one."""
    try:
        with LOCAL_SETTINGS.open("rb") as handle:
            home = tomllib.load(handle).get("home", {})
    except (OSError, tomllib.TOMLDecodeError):
        return ""
    return normalise(str(home.get("name", ""))) if isinstance(home, dict) else ""


def everyday_names(name: str) -> list[str]:
    names = []
    short = SHORT_FORM.match(name)
    if short and normalise(name) not in NO_SHORT_FORM:
        names.append(short.group(1))
    royal = ROYAL.match(name)
    if royal:
        names.append(royal.group(1))
    return names


def km_apart(a: tuple, b: tuple) -> float:
    phi1, phi2 = math.radians(a[1]), math.radians(b[1])
    d_phi, d_lambda = phi2 - phi1, math.radians(b[2] - a[2])
    h = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(min(1.0, h)))


def main(source: Path) -> None:
    # normalised name -> {GeoNames id: (population, latitude, longitude, name as written)}
    candidates: dict[str, dict[str, tuple[int, float, float, str]]] = defaultdict(dict)
    with source.open(encoding="utf-8", newline="") as handle:
        for row in csv.reader(handle, delimiter="\t", quoting=csv.QUOTE_NONE):
            if row[8] != "GB" or row[6] != "P" or row[7] in HISTORICAL:
                continue
            place = (int(row[14] or 0), float(row[4]), float(row[5]))
            for name in dict.fromkeys((row[1], row[2])):  # official name, then plain spelling
                for written in (name, *everyday_names(name)):
                    key = normalise(written)
                    if key:
                        candidates[key].setdefault(row[0], (*place, written))

    home = private_home()
    places, shared, not_places = {}, {}, []
    for key, entries in candidates.items():
        if key == home:
            continue
        if key in NOT_PLACES:
            not_places.append(key)
            continue
        ranked = sorted(entries.values(), key=lambda entry: entry[0], reverse=True)
        biggest = ranked[0]
        rivals = [entry for entry in ranked[1:] if km_apart(biggest, entry) > SAME_PLACE_KM]
        if key in SHARED_ANYWAY or (rivals and rivals[0][0] * DOMINANCE >= biggest[0]):
            shared[key] = (biggest[3], "", "")  # listed without coordinates: never guessed
        else:
            places[key] = (biggest[3], f"{biggest[1]:.4f}", f"{biggest[2]:.4f}")
    for alias, target in ALIASES.items():
        if target in places and alias not in places and alias != home:
            places[alias] = (alias.title(), *places[target][1:])
    rows = {**shared, **places}

    with OUTPUT.open("w", encoding="utf-8", newline="") as handle:
        handle.write("# UK places from GeoNames (https://www.geonames.org), CC BY 4.0.\n")
        handle.write("# Made by tools/build_uk_places.py: run it again to refresh this list.\n")
        handle.write("# Names with no coordinates are shared by several places: never guessed.\n")
        writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
        writer.writerow(["name", "latitude", "longitude"])
        for key in sorted(rows):
            writer.writerow(rows[key])
    print(f"Wrote {len(places)} places to {OUTPUT}")
    print(f"Marked {len(shared)} names shared by different places, such as",
          ", ".join(sorted(shared)[:15]))
    print("Left out place names that are also common words:", ", ".join(sorted(not_places)))
    if home:
        print("Left out the home town named in config.local.toml.")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python tools/build_uk_places.py path/to/cities1000.txt")
    main(Path(sys.argv[1]))
