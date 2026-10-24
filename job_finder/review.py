"""Judge adverts in a Claude Code chat instead of through the paid API.

`tools/review_adverts.py export` lists the adverts nobody has judged yet in
output/review/to_review.md, followed by the entry-level jobs judged earlier. Claude Code reads
it and writes its verdicts to verdicts.json in the same folder, with any picks, notes and
summary for the "Claude's review" box at the top of the report. `import` saves them alongside
the API's answers, so every later run of find_jobs.py shows them.
"""

from __future__ import annotations

import json
import textwrap
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from job_finder.entry_check import (
    KEEP_DAYS,
    SYSTEM_PROMPT,
    VERDICTS,
    EntryLevelChecker,
    advert_text,
)
from job_finder.filters import TAG_ENTRY, apply_claude_verdicts, job_key
from job_finder.models import Job, Review
from job_finder.storage import read_json, write_json
from job_finder.text import shorten

REVIEWER = "claude-code-review"  # stored as the "model", to tell these apart from API answers
REVIEW_FILE = "claude_review.json"  # the picks, notes and summary, in the cache folder
EARLIER_CHARS = 600  # how much of each earlier job's advert the list repeats
INSTRUCTIONS = """\
Write the verdicts to verdicts.json in this folder (several verdicts_*.json files are fine):

    {"R1": {"verdict": "needs_experience", "years": 2, "evidence": "2+ years of SQL"}}

verdict is entry_level, needs_experience or unclear; years is the fewest years of experience
the advert asks for (0 if none or not stated); evidence is the shortest quote that decided it.

For the "Claude's review" box at the top of the report, any R or C number can also have:
- "pick": a short reason, on the few jobs most worth applying for;
- "note": a short heads-up to show on the job, such as "fees apply: a paid course".
An empty "pick" or "note" removes one given before. C jobs keep their verdict unless you
give a new one. Write "summary" last: a list of short points about these results as a whole.
The report counts the verdicts itself, so leave numbers out.

    {"summary": ["..."], "C3": {"pick": "Graduate scheme, fully remote, uses Python"}}

Then run:  .venv\\Scripts\\python.exe tools\\review_adverts.py import"""


class ReviewError(Exception):
    """The review files can't be used as they are. The message says what to do."""


@dataclass
class ImportResult:
    """What one import saved, and anything it had to skip."""

    verdicts: int = 0
    picks: int = 0
    notes: int = 0
    summary: bool = False
    problems: list[str] = field(default_factory=list)


def write_review_list(
    jobs: list[Job], checker: EntryLevelChecker, folder: Path, review_path: Path
) -> tuple[int, int]:
    """List the adverts with no saved verdict, then the entry-level jobs judged earlier.

    Returns how many of each. `checker` should have no API client: nothing is sent to Claude.
    """
    folder.mkdir(parents=True, exist_ok=True)
    if list(folder.glob("verdicts*.json")):
        raise ReviewError("Import or delete the verdict files in output/review first: a new "
                          "list numbers the adverts afresh.")
    todo = checker.use_saved(jobs)
    apply_claude_verdicts(jobs, checker.today)  # so the list matches "Entry level only"
    review = apply_review(jobs, review_path)  # shows the current picks and notes in the list
    # Picked jobs are always listed, so a pick can be corrected or removed whatever its verdict.
    earlier = [job for job in jobs
               if job.claude_verdict and (TAG_ENTRY in job.tags or job.claude_pick)]
    numbered = [(f"R{n}", job) for n, job in enumerate(todo, start=1)]
    numbered += [(f"C{n}", job) for n, job in enumerate(earlier, start=1)]
    with (folder / "to_review.jsonl").open("w", encoding="utf-8") as handle:
        for review_id, job in numbered:
            entry = {"id": review_id, "key": job_key(job), "fingerprint": checker.fingerprint(job)}
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    lines = ["# Adverts to judge", "", SYSTEM_PROMPT, "", INSTRUCTIONS, ""]
    for review_id, job in numbered[:len(todo)]:
        lines += [f"## {review_id}", "", _wrap(advert_text(job)), ""]
    lines += ["# Entry-level jobs judged earlier", "",
              "These already have a verdict (picked jobs are listed whatever their verdict). "
              "Give any of them a pick or a note, or a new verdict."]
    if review.summary:
        written = review.written.isoformat() if review.written else "an earlier review"
        lines += ["", f"The summary from {written}, to keep or replace:"]
        lines += [f"- {point}" for point in review.summary]
    lines.append("")
    for review_id, job in numbered[len(todo):]:
        lines += [f"## {review_id}", "", _wrap(_earlier_text(job)), ""]
    (folder / "to_review.md").write_text("\n".join(lines), encoding="utf-8")
    return len(todo), len(earlier)


def import_verdicts(
    folder: Path, saved_path: Path, review_path: Path, today: date
) -> ImportResult:
    """Save what Claude Code wrote: verdicts, and the picks, notes and summary for the report."""
    files = sorted(folder.glob("verdicts*.json"))
    listing = folder / "to_review.jsonl"
    if not files or not listing.exists():
        raise ReviewError("There's nothing to import: export a list and write verdicts.json "
                          "first.")
    entries = {}
    for line in listing.read_text(encoding="utf-8").splitlines():
        if line.strip():
            entry = json.loads(line)
            entries[entry["id"]] = entry
    saved = read_json(saved_path)
    saved = saved if isinstance(saved, dict) else {}
    review = _read_review(review_path)
    result = ImportResult()
    imported = []
    for path in files:
        try:
            written = json.loads(path.read_text(encoding="utf-8"))
        except ValueError as exc:  # left in place, to be fixed and imported again
            result.problems.append(f"{path.name} isn't valid JSON ({exc})")
            continue
        if not isinstance(written, dict):
            result.problems.append(f"{path.name} should hold one {{...}} object")
            continue
        for review_id, value in written.items():
            _import_one(review_id, value, entries, saved, review, today, result)
        imported.append(path)
    write_json(saved_path, saved)
    cutoff = (today - timedelta(days=KEEP_DAYS)).isoformat()
    review["jobs"] = {key: notes for key, notes in review["jobs"].items()
                      if str(notes.get("date", "")) >= cutoff}
    write_json(review_path, review)
    for path in imported:  # set aside, so a later list can't be matched against old numbers
        path.replace(path.with_name(f"done_{path.name}"))
    return result


def apply_review(jobs: list[Job], review_path: Path) -> Review:
    """Put the last review's picks and notes on the jobs they're about. Returns its summary."""
    review = _read_review(review_path)
    for job in jobs:
        notes = review["jobs"].get(job_key(job), {})
        job.claude_pick = str(notes.get("pick", "")).strip()
        job.claude_note = str(notes.get("note", "")).strip()
    try:
        written = date.fromisoformat(review["date"])
    except ValueError:
        written = None
    return Review(written, review["summary"])


def _import_one(
    review_id: str,
    value: Any,
    entries: dict[str, dict[str, str]],
    saved: dict[str, Any],
    review: dict[str, Any],
    today: date,
    result: ImportResult,
) -> None:
    if review_id == "summary":
        points = [value] if isinstance(value, str) else value
        if not isinstance(points, list) or not all(isinstance(p, str) for p in points):
            result.problems.append("summary should be a list of short points")
            return
        review["summary"] = [point.strip() for point in points if point.strip()]
        review["date"] = today.isoformat()
        result.summary = True
        return
    entry = entries.get(review_id)
    if entry is None:
        result.problems.append(f"{review_id} isn't in the list")
        return
    if not isinstance(value, dict):
        result.problems.append(f"{review_id} should be a {{...}} object")
        return
    if "verdict" in value or review_id.startswith("R"):  # R adverts have no verdict yet
        if value.get("verdict") not in VERDICTS:
            result.problems.append(f"{review_id} has no verdict of {', '.join(VERDICTS)}")
            return
        try:
            years = max(0, int(value.get("years") or 0))
        except (TypeError, ValueError):
            result.problems.append(f"{review_id} has an unreadable number of years")
            return
        saved[entry["key"]] = {
            "fingerprint": entry["fingerprint"],
            "checked": today.isoformat(),
            "verdict": value["verdict"],
            "years": years,
            "evidence": str(value.get("evidence", "")).strip(),
            "model": REVIEWER,
            "input_tokens": 0,
            "output_tokens": 0,
        }
        result.verdicts += 1
    notes = dict(review["jobs"].get(entry["key"], {}))
    for name in ("pick", "note"):
        if name in value:
            text = str(value[name] or "").strip()
            if text:
                notes[name] = text
                if name == "pick":
                    result.picks += 1
                else:
                    result.notes += 1
            else:
                notes.pop(name, None)
    if notes.get("pick") or notes.get("note"):
        notes["date"] = today.isoformat()
        review["jobs"][entry["key"]] = notes
    else:
        review["jobs"].pop(entry["key"], None)


def _read_review(path: Path) -> dict[str, Any]:
    """The saved review, with anything malformed left out."""
    saved = read_json(path)
    saved = saved if isinstance(saved, dict) else {}
    summary = saved.get("summary")
    jobs = saved.get("jobs")
    return {
        "date": str(saved.get("date", "")),
        "summary": [str(point) for point in summary] if isinstance(summary, list) else [],
        "jobs": ({key: notes for key, notes in jobs.items() if isinstance(notes, dict)}
                 if isinstance(jobs, dict) else {}),
    }


def _earlier_text(job: Job) -> str:
    """A job judged earlier, briefly: enough to pick from, without the whole advert."""
    details = [job.company or "employer not given", job.location or "location not given"]
    if job.work_mode:
        details.append(job.work_mode)
    if job.distance_km is not None:
        details.append(f"~{job.distance_km:.0f} km from home")
    details += [text for text in (job.salary, job.contract) if text]
    if job.posted:
        details.append(f"posted {job.posted.isoformat()}")
    years = f", {job.claude_years} years" if job.claude_years else ""
    lines = [
        job.title,
        " · ".join(details),
        f"Report section: {job.section}",
        f"Verdict: {job.claude_verdict}{years}: {job.claude_evidence}",
    ]
    if job.matched_skills:
        lines.append(f"Matches the CV: {', '.join(job.matched_skills)}")
    if job.claude_pick:
        lines.append(f"Current pick: {job.claude_pick}")
    if job.claude_note:
        lines.append(f"Current note: {job.claude_note}")
    lines += ["", shorten(job.description, EARLIER_CHARS)]
    return "\n".join(lines)


def _wrap(text: str, width: int = 150) -> str:
    return "\n".join(textwrap.fill(line, width) if len(line) > width else line
                     for line in text.splitlines())
