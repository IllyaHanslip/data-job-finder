"""The Job record that every part of the program passes around."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

# How a listing was found. This decides how its location is read later on.
SCOPE_LOCAL = "local"  # radius search around home (Adzuna, Reed)
SCOPE_UK_REMOTE = "uk-remote"  # UK-wide search with "remote" in the keywords (Adzuna, Reed)
SCOPE_REMOTE_BOARD = "remote-board"  # remote-only job board (Himalayas, Remotive, Jobicy)

# The three groups shown in the report, in display order.
SECTION_REMOTE = "remote"
SECTION_LOCAL = "local"
SECTION_CHECK = "check"

# Claude's answer to "is this advert really entry level?" (entry_check.py, optional).
VERDICT_ENTRY = "entry_level"
VERDICT_NEEDS_EXPERIENCE = "needs_experience"
VERDICT_UNCLEAR = "unclear"


@dataclass
class Job:
    source: str
    source_id: str
    title: str
    company: str
    location: str
    url: str
    description: str = ""
    posted: date | None = None
    salary: str = ""
    contract: str = ""
    latitude: float | None = None
    longitude: float | None = None
    scope: str = SCOPE_LOCAL
    remote_regions: str = ""  # who may apply to a remote-board job, e.g. "UK, Europe"
    level: str = ""  # seniority stated by the source, e.g. "Entry-level"
    excerpt_only: bool = False  # the source sent only the start of the advert

    # Worked out by filters.enrich()
    work_mode: str = ""
    distance_km: float | None = None
    drive_minutes: int | None = None
    section: str = ""
    tags: list[str] = field(default_factory=list)
    matched_skills: list[str] = field(default_factory=list)
    score: int = 0
    is_new: bool = False
    also_on: list[str] = field(default_factory=list)
    also_urls: dict[str, str] = field(default_factory=dict)  # other site's name -> its own link

    # Filled in by entry_check, when that optional step runs
    claude_verdict: str = ""
    claude_years: int | None = None
    claude_evidence: str = ""
