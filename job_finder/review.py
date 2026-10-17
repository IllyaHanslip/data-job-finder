"""Judge adverts in a Claude Code chat instead of through the paid API.

`tools/review_adverts.py export` lists the adverts nobody has judged yet in
output/review/to_review.md. Claude Code reads it and writes its verdicts to verdicts.json in
the same folder, and `import` saves them alongside the API's answers, so every later run of
find_jobs.py shows them.
"""

from __future__ import annotations

import json
import textwrap
from datetime import date
from pathlib import Path

from job_finder.entry_check import SYSTEM_PROMPT, VERDICTS, EntryLevelChecker, advert_text
from job_finder.filters import job_key
from job_finder.models import Job
from job_finder.storage import read_json, write_json

REVIEWER = "claude-code-review"  # stored as the "model", to tell these apart from API answers
INSTRUCTIONS = """\
Write the verdicts to verdicts.json in this folder (several verdicts_*.json files are fine):

    {"R1": {"verdict": "needs_experience", "years": 2, "evidence": "2+ years of SQL"}}

verdict is entry_level, needs_experience or unclear; years is the fewest years of experience
the advert asks for (0 if none or not stated); evidence is the shortest quote that decided it.
Then run:  .venv\\Scripts\\python.exe tools\\review_adverts.py import"""


class ReviewError(Exception):
    """The review files can't be used as they are. The message says what to do."""


def write_review_list(jobs: list[Job], checker: EntryLevelChecker, folder: Path) -> int:
    """List the adverts that have no saved verdict, for Claude Code. Returns how many."""
    folder.mkdir(parents=True, exist_ok=True)
    if list(folder.glob("verdicts*.json")):
        raise ReviewError("Import or delete the verdict files in output/review first: a new "
                          "list numbers the adverts afresh.")
    todo = [job for job in jobs if checker.saved_answer(job) is None]
    with (folder / "to_review.jsonl").open("w", encoding="utf-8") as handle:
        for number, job in enumerate(todo, start=1):
            entry = {"id": f"R{number}", "key": job_key(job),
                     "fingerprint": checker.fingerprint(job)}
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    lines = ["# Adverts to judge", "", SYSTEM_PROMPT, "", INSTRUCTIONS, ""]
    for number, job in enumerate(todo, start=1):
        lines += [f"## R{number}", "", _wrap(advert_text(job)), ""]
    (folder / "to_review.md").write_text("\n".join(lines), encoding="utf-8")
    return len(todo)


def import_verdicts(folder: Path, saved_path: Path, today: date) -> tuple[int, list[str]]:
    """Save the verdicts Claude Code wrote. Returns how many were saved, and any problems."""
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
    added, problems = 0, []
    for path in files:
        for review_id, verdict in json.loads(path.read_text(encoding="utf-8")).items():
            entry = entries.get(review_id)
            if entry is None:
                problems.append(f"{review_id} isn't in the list")
                continue
            if not isinstance(verdict, dict) or verdict.get("verdict") not in VERDICTS:
                problems.append(f"{review_id} has no verdict of {', '.join(VERDICTS)}")
                continue
            try:
                years = max(0, int(verdict.get("years") or 0))
            except (TypeError, ValueError):
                problems.append(f"{review_id} has an unreadable number of years")
                continue
            saved[entry["key"]] = {
                "fingerprint": entry["fingerprint"],
                "checked": today.isoformat(),
                "verdict": verdict["verdict"],
                "years": years,
                "evidence": str(verdict.get("evidence", "")).strip(),
                "model": REVIEWER,
                "input_tokens": 0,
                "output_tokens": 0,
            }
            added += 1
    write_json(saved_path, saved)
    for path in files:  # set aside, so a later list can't be matched against old numbers
        path.replace(path.with_name(f"done_{path.name}"))
    return added, problems


def _wrap(text: str, width: int = 150) -> str:
    return "\n".join(textwrap.fill(line, width) if len(line) > width else line
                     for line in text.splitlines())
