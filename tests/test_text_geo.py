from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from job_finder.geo import (
    PLACES,
    PLACES_FILE,
    distance_km,
    estimate_drive_minutes,
    find_place,
    normalise_place,
)
from job_finder.text import format_salary, html_to_text, parse_date, plain, safe_url, shorten


def test_html_to_text_strips_tags_scripts_and_entities():
    raw = (
        "<p>Hello &amp; welcome</p><ul><li>Python</li><li><p>SQL</p></li></ul>"
        "<script>alert(1)</script>"
    )
    assert html_to_text(raw) == "Hello & welcome\n• Python\n• SQL"


def test_html_to_text_handles_empty_and_plain_values():
    assert html_to_text(None) == ""
    assert html_to_text("   ") == ""
    assert html_to_text("Salary < £30k") == "Salary < £30k"
    assert plain("<strong>Data</strong>  Analyst\n") == "Data Analyst"


def test_shorten_cuts_on_a_word_boundary():
    text = "one two three four five"
    assert shorten(text, 100) == text
    assert shorten(text, 12) == "one two…"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("14/10/2026", date(2026, 10, 14)),  # Reed
        ("2026-10-14T09:15:00Z", date(2026, 10, 14)),  # Adzuna
        ("2026-10-14T09:15:00", date(2026, 10, 14)),  # Remotive
        ("2026-10-14 09:15:00", date(2026, 10, 14)),  # Jobicy
        (int(datetime(2026, 10, 14, 12, tzinfo=UTC).timestamp() * 1000), date(2026, 10, 14)),
        ("not a date", None),
        (None, None),
        ("", None),
    ],
)
def test_parse_date(value, expected):
    assert parse_date(value) == expected


def test_format_salary():
    assert format_salary(28000, 32000) == "£28,000 – £32,000"
    assert format_salary("35,000", None) == "£35,000"
    assert format_salary(None, None) == ""
    assert format_salary(0, 0) == ""
    assert format_salary(50000, 50000, "USD", "yearly") == "$50,000 per year"
    assert format_salary(12.5, 14, "GBP") == "£12.50 – £14"
    estimated = format_salary(30000, 40000, note="Adzuna estimate")
    assert estimated == "£30,000 – £40,000 (Adzuna estimate)"
    assert format_salary(60000, None, "CAD") == "CAD 60,000"


def test_safe_url_only_allows_web_links():
    assert safe_url("https://example.com/job") == "https://example.com/job"
    assert safe_url("javascript:alert(1)") == ""
    assert safe_url(None) == ""


def test_distances_are_sensible():
    coventry = distance_km(*PLACES["birmingham"], *PLACES["coventry"])
    london = distance_km(*PLACES["birmingham"], *PLACES["london"])
    assert 25 < coventry < 30
    assert 155 < london < 170
    assert 33 <= estimate_drive_minutes(coventry) <= 39


def test_find_place_handles_punctuation_and_longer_names():
    assert find_place("Bishop's Stortford, Hertfordshire") == PLACES["bishops stortford"]
    assert find_place("Bury St. Edmunds") == PLACES["bury st edmunds"]
    assert find_place("Bourne End, Buckinghamshire") == PLACES["bourne end"]
    assert find_place("Warwickshire") is None
    assert find_place("") is None


def test_places_across_the_uk_are_found():
    for location in ("Truro, Cornwall", "Inverness", "Belfast", "Swansea", "Aberdeen"):
        assert find_place(location) is not None, location
    assert find_place("Newcastle") == PLACES["newcastle upon tyne"]
    assert find_place("Hull") == PLACES["kingston upon hull"]
    assert find_place("Leamington Spa") == PLACES["royal leamington spa"]
    assert find_place("Saint Albans") is not None
    assert find_place("Saint Albans") == find_place("St. Albans")


def test_names_shared_by_different_places_are_left_out():
    assert find_place("St Ives") is None  # there are two
    assert find_place("Newport") is None
    assert find_place("Long Sutton") is None  # and not Sutton in London
    assert find_place("Caerleon, Newport") == PLACES["caerleon"]


def test_words_that_are_not_places_are_ignored():
    assert find_place("High Street, Leeds") == PLACES["leeds"]
    assert find_place("Remote (UK)") is None


def test_place_list_has_one_entry_per_name():
    with PLACES_FILE.open(encoding="utf-8") as handle:
        names = [line.split("\t")[0] for line in handle if not line.startswith("#")][1:]
    keys = [normalise_place(name) for name in names]
    assert len(keys) == len(set(keys)) > 4000
