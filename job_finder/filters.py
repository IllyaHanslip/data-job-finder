"""Decide which jobs to keep, label them, and put them in a useful order."""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, timedelta

from job_finder.config import Settings
from job_finder.geo import distance_km, estimate_drive_minutes, find_place, names_place
from job_finder.models import (
    SCOPE_LOCAL,
    SCOPE_REMOTE_BOARD,
    SCOPE_UK_REMOTE,
    SECTION_CHECK,
    SECTION_LOCAL,
    SECTION_REMOTE,
    VERDICT_ENTRY,
    VERDICT_NEEDS_EXPERIENCE,
    Job,
)

WORK_REMOTE = "Remote"
WORK_HYBRID = "Hybrid"
WORK_ON_SITE = "On-site"
WORK_NOT_STATED = "Not stated"

TAG_ENTRY = "Entry level"
TAG_SENIOR = "Senior"
TAG_TEACHING = "Teaching"
TAG_NEEDS_EXPERIENCE = "Needs experience"  # from the optional Claude check

REASON_TOO_OLD = "older than the age limit"
REASON_NOT_UK = "remote jobs not open to UK applicants"
REASON_OFF_TOPIC = "titles outside data/maths"
REASON_EXCLUDED = "excluded job types (IT support, security, recruiters...)"
REASON_SENIOR = "senior roles"

_SEPARATOR = r"[\s\-‐-―]+"  # spaces, hyphens and dashes all count as a gap

_NOT_ABOUT_WORKING = re.compile(
    r"\bremote(?:ly)?[\s\-]+(?:sensing|monitoring|patients?|access|control|desktop|support|"
    r"sites?|locations?|areas?|communities)\b",
    re.IGNORECASE,
)
_HYBRID = re.compile(r"\bhybrid\b", re.IGNORECASE)
_ON_SITE = re.compile(
    r"\b(?:on[\s\-]?site only|fully on[\s\-]?site|office[\s\-]based|site[\s\-]based|"
    r"not (?:a )?remote|no remote)\b",
    re.IGNORECASE,
)
_REMOTE = re.compile(
    r"\b(?:remote|remotely|work(?:ing)? from home|wfh|home[\s\-]?based|home[\s\-]?working|"
    r"work from anywhere)\b",
    re.IGNORECASE,
)
_UK_FRIENDLY = re.compile(
    r"\b(?:uk|u\.k|united kingdom|great britain|britain|england|scotland|wales|"
    r"europe|european|eu|emea|worldwide|world wide|anywhere|global|globally|"
    r"international|gmt|bst|wet|cet|cest)\b",
    re.IGNORECASE,
)
_SENIOR_LEVEL = re.compile(
    r"\b(?:senior|lead|manager|director|executive|principal|head)\b", re.IGNORECASE
)
_NOT_SENIOR_LEVEL = re.compile(
    r"\b(?:entry|junior|graduate|intern|internship|trainee|mid|midweight|any)\b", re.IGNORECASE
)
_ENTRY_LEVEL = re.compile(r"\b(?:entry|junior|graduate|intern|internship|trainee)\b", re.IGNORECASE)
_TEACHING = re.compile(
    r"\b(?:teacher|teaching|tutor|tutoring|lecturer|instructor)\b", re.IGNORECASE
)
_COMPANY_FILLER = frozenset({"ltd", "limited", "plc", "llp", "inc", "uk", "the", "group", "co"})
_SECTION_ORDER = {SECTION_REMOTE: 0, SECTION_LOCAL: 1, SECTION_CHECK: 2}


class KeywordSet:
    """Whole-word, case-insensitive matching for a list of phrases.

    A trailing * matches any ending ("statistic*" matches "statistician"), and a space
    also matches a hyphen ("entry level" matches "entry-level").
    """

    def __init__(self, phrases: Iterable[str]) -> None:
        self._patterns: list[re.Pattern[str]] = []
        for phrase in phrases:
            body = _phrase_regex(phrase)
            if body:
                self._patterns.append(re.compile(rf"(?<!\w)(?:{body})(?!\w)", re.IGNORECASE))

    def search(self, text: str) -> bool:
        return any(pattern.search(text) for pattern in self._patterns)

    def matches(self, text: str) -> list[str]:
        """The words found in `text`, lower-cased, at most one per phrase."""
        found: list[str] = []
        for pattern in self._patterns:
            match = pattern.search(text)
            if match:
                word = " ".join(match.group(0).lower().split())
                if word not in found:
                    found.append(word)
        return found


def _phrase_regex(phrase: str) -> str:
    pieces = []
    for word in re.split(_SEPARATOR, phrase.strip().lower()):
        stem = word.rstrip("*")
        if stem:
            pieces.append(re.escape(stem) + (r"\w*" if word.endswith("*") else ""))
    return _SEPARATOR.join(pieces)


@dataclass
class Rules:
    titles: KeywordSet
    excluded: KeywordSet
    senior: KeywordSet
    entry: KeywordSet
    skills: KeywordSet

    @classmethod
    def from_settings(cls, settings: Settings) -> Rules:
        return cls(
            titles=KeywordSet(settings.title_keywords),
            excluded=KeywordSet(settings.exclude_title_keywords),
            senior=KeywordSet(settings.senior_keywords),
            entry=KeywordSet(settings.entry_level_keywords),
            skills=KeywordSet(settings.cv_skills),
        )


@dataclass
class FilterSummary:
    received: int = 0
    kept: int = 0
    duplicates: int = 0
    removed: Counter[str] = field(default_factory=Counter)


def process_jobs(
    jobs: list[Job], settings: Settings, today: date
) -> tuple[list[Job], FilterSummary]:
    """Filter, de-duplicate, label and sort the raw listings from every source."""
    rules = Rules.from_settings(settings)
    summary = FilterSummary(received=len(jobs))
    oldest_allowed = today - timedelta(days=settings.max_days_old)
    candidates = []
    for job in jobs:
        reason = _rejection_reason(job, rules, settings, oldest_allowed)
        if reason:
            summary.removed[reason] += 1
        else:
            candidates.append(job)
    unique = dedupe(candidates)
    summary.duplicates = len(candidates) - len(unique)
    for job in unique:
        enrich(job, rules, settings, today)
    unique.sort(key=_sort_key)
    summary.kept = len(unique)
    return unique, summary


def _rejection_reason(
    job: Job, rules: Rules, settings: Settings, oldest_allowed: date
) -> str | None:
    if job.posted is not None and job.posted < oldest_allowed:
        return REASON_TOO_OLD
    if job.scope == SCOPE_REMOTE_BOARD and not open_to_uk(job.remote_regions):
        return REASON_NOT_UK
    if not rules.titles.search(job.title):
        return REASON_OFF_TOPIC
    if rules.excluded.search(job.title):
        return REASON_EXCLUDED
    if not settings.include_senior and is_senior(job, rules):
        return REASON_SENIOR
    return None


def enrich(job: Job, rules: Rules, settings: Settings, today: date) -> None:
    """Fill in work pattern, distance, report section, tags, CV matches and score."""
    home = settings.home
    if job.scope == SCOPE_REMOTE_BOARD:
        job.work_mode = WORK_REMOTE
    else:
        job.work_mode = detect_work_mode(f"{job.title}\n{job.location}\n{job.description}")
        km: float | None = None
        if job.latitude is not None and job.longitude is not None:
            km = distance_km(home.latitude, home.longitude, job.latitude, job.longitude)
        else:
            coords = find_place(job.location)
            if coords is None and job.scope == SCOPE_LOCAL and names_place(job.location, home.name):
                coords = (home.latitude, home.longitude)  # home can be missing from the town list
            if coords is not None:
                km = distance_km(home.latitude, home.longitude, coords[0], coords[1])
                # A radius search only returns jobs near home, so a town name far outside the
                # radius must belong to a namesake elsewhere (there are several Long Suttons).
                if job.scope == SCOPE_LOCAL and km > 2 * home.radius_km:
                    km = None
        if km is not None:
            job.distance_km = round(km, 1)
            job.drive_minutes = estimate_drive_minutes(km)
    job.section = choose_section(job, home.radius_km)

    senior = is_senior(job, rules)
    entry = not senior and (rules.entry.search(job.title) or level_is_entry(job.level))
    job.tags = [
        tag
        for tag, present in (
            (TAG_ENTRY, entry),
            (TAG_SENIOR, senior),
            (TAG_TEACHING, bool(_TEACHING.search(job.title))),
        )
        if present
    ]
    job.matched_skills = rules.skills.matches(f"{job.title}\n{job.description}")
    job.score = _score(job, today)


def apply_claude_verdicts(jobs: list[Job], today: date) -> None:
    """Let Claude's reading of the whole advert overrule the title's entry-level badge."""
    for job in jobs:
        if job.claude_verdict == VERDICT_NEEDS_EXPERIENCE:
            job.tags = [tag for tag in job.tags if tag != TAG_ENTRY]
            if TAG_NEEDS_EXPERIENCE not in job.tags:
                job.tags.append(TAG_NEEDS_EXPERIENCE)
        elif (job.claude_verdict == VERDICT_ENTRY and TAG_ENTRY not in job.tags
              and TAG_SENIOR not in job.tags):
            job.tags.insert(0, TAG_ENTRY)
        job.score = _score(job, today)
    jobs.sort(key=_sort_key)


def choose_section(job: Job, radius_km: float) -> str:
    if job.work_mode == WORK_REMOTE:
        return SECTION_REMOTE
    if job.distance_km is not None:
        return SECTION_LOCAL if job.distance_km <= radius_km else SECTION_CHECK
    return SECTION_LOCAL if job.scope == SCOPE_LOCAL else SECTION_CHECK


def detect_work_mode(text: str) -> str:
    """Read 'remote', 'hybrid' or 'office-based' wording in an advert."""
    text = _NOT_ABOUT_WORKING.sub(" ", text)
    if _HYBRID.search(text):
        return WORK_HYBRID
    if _ON_SITE.search(text):
        return WORK_ON_SITE
    if _REMOTE.search(text):
        return WORK_REMOTE
    return WORK_NOT_STATED


def open_to_uk(regions: str) -> bool:
    """True if a remote job's allowed locations include the UK, or are not restricted."""
    return not regions.strip() or bool(_UK_FRIENDLY.search(regions))


def is_senior(job: Job, rules: Rules) -> bool:
    return rules.senior.search(job.title) or level_is_senior(job.level)


def level_is_senior(level: str) -> bool:
    return bool(_SENIOR_LEVEL.search(level)) and not _NOT_SENIOR_LEVEL.search(level)


def level_is_entry(level: str) -> bool:
    return bool(_ENTRY_LEVEL.search(level))


def job_key(job: Job) -> str:
    """Identity used to spot the same advert twice, and to remember jobs already seen."""
    company = " ".join(w for w in _normalise(job.company).split() if w not in _COMPANY_FILLER)
    if not company:
        return f"{job.source}:{job.source_id}"
    return f"{_normalise(job.title)}|{company}"


def dedupe(jobs: list[Job]) -> list[Job]:
    """Merge listings of the same job (same title and employer), keeping the first one."""
    kept: dict[str, Job] = {}
    for job in jobs:
        key = job_key(job)
        if key in kept:
            _merge(kept[key], job)
        else:
            kept[key] = job
    return list(kept.values())


def _merge(kept: Job, other: Job) -> None:
    if other.source != kept.source and other.source not in kept.also_on:
        kept.also_on.append(other.source)
        kept.also_urls[other.source] = other.url  # so the report can credit and link it
    if kept.scope == SCOPE_UK_REMOTE and other.scope == SCOPE_LOCAL:
        kept.scope = SCOPE_LOCAL
    if kept.latitude is None and other.latitude is not None and other.longitude is not None:
        kept.latitude, kept.longitude = other.latitude, other.longitude
    if len(other.description) > len(kept.description):
        kept.description = other.description
        kept.excerpt_only = other.excerpt_only
    if not kept.salary and other.salary:
        kept.salary = other.salary
    if kept.posted is None:
        kept.posted = other.posted


def _normalise(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text.lower()).split())


def _score(job: Job, today: date) -> int:
    score = min(len(job.matched_skills), 6)
    if TAG_ENTRY in job.tags:
        score += 4
    if job.section == SECTION_REMOTE:
        score += 2
    if job.posted is not None and (today - job.posted).days <= 7:
        score += 1
    if TAG_TEACHING in job.tags:
        score -= 3
    if TAG_SENIOR in job.tags:
        score -= 6
    if TAG_NEEDS_EXPERIENCE in job.tags:
        score -= 4
    return score


def _sort_key(job: Job) -> tuple[int, int, int, str]:
    posted = job.posted.toordinal() if job.posted else 0
    section = _SECTION_ORDER.get(job.section, len(_SECTION_ORDER))
    return (section, -job.score, -posted, job.title.lower())
