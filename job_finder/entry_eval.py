"""Measure how often the Claude entry-level check agrees with you.

tools/make_label_sheet.py picks adverts for you to label, and tools/evaluate_entry_check.py
runs the check on them and compares. The files live in output/eval/, which is never
uploaded, because the adverts belong to the job sites.
"""

from __future__ import annotations

import csv
import json
import math
import random
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from job_finder.filters import TAG_ENTRY, job_key
from job_finder.models import VERDICT_ENTRY, VERDICT_NEEDS_EXPERIENCE, VERDICT_UNCLEAR, Job
from job_finder.text import spreadsheet_safe

LABEL_COLUMNS = ["id", "title", "company", "source", "claude_sees", "link", "your_label", "notes"]
# How the report names where verdicts came from ("claude-code-review" is review.REVIEWER).
SOURCE_NAMES = {"claude-code-review": "a review in a Claude Code chat (not the API check)"}
LABEL_NAMES = {VERDICT_ENTRY: "entry", VERDICT_NEEDS_EXPERIENCE: "experienced",
               VERDICT_UNCLEAR: "unclear"}
_LABEL_WORDS = {
    "entry": VERDICT_ENTRY, "entry level": VERDICT_ENTRY, "e": VERDICT_ENTRY,
    "experienced": VERDICT_NEEDS_EXPERIENCE, "needs experience": VERDICT_NEEDS_EXPERIENCE,
    "x": VERDICT_NEEDS_EXPERIENCE,
    "unclear": VERDICT_UNCLEAR, "u": VERDICT_UNCLEAR, "?": VERDICT_UNCLEAR,
}


def normalise_label(text: str) -> str | None:
    """Read a label typed into the sheet: entry, experienced or unclear (or e, x, u, ?)."""
    return _LABEL_WORDS.get(" ".join(text.strip().lower().replace("-", " ").split()))


def pick_adverts(jobs: list[Job], count: int, seed: int = 2026) -> list[Job]:
    """A spread across job sites, and across adverts with and without an Entry level badge.

    The same jobs and seed always give the same pick.
    """
    groups: dict[tuple[str, bool], list[Job]] = defaultdict(list)
    for job in sorted(jobs, key=job_key):
        groups[(job.source, TAG_ENTRY in job.tags)].append(job)
    rng = random.Random(seed)
    queues = [groups[name] for name in sorted(groups)]
    for queue in queues:
        rng.shuffle(queue)
    picked: list[Job] = []
    while len(picked) < count and any(queues):
        for queue in queues:
            if queue and len(picked) < count:
                picked.append(queue.pop())
    return picked


def write_label_sheet(jobs: list[Job], folder: Path) -> list[Path]:
    """Write labels.csv to fill in, adverts.md to read, and adverts.jsonl for the check."""
    folder.mkdir(parents=True, exist_ok=True)
    labels, readable, adverts = (folder / "labels.csv", folder / "adverts.md",
                                 folder / "adverts.jsonl")
    with adverts.open("w", encoding="utf-8") as handle:
        for job in jobs:
            handle.write(json.dumps(_case(job), ensure_ascii=False) + "\n")
    with labels.open("w", encoding="utf-8-sig", newline="") as handle:  # BOM: Excel reads £
        writer = csv.writer(handle)
        writer.writerow(LABEL_COLUMNS)
        for job in jobs:
            writer.writerow([job_key(job), spreadsheet_safe(job.title),
                             spreadsheet_safe(job.company), job.source, _sees(job), job.url, "",
                             ""])
    readable.write_text(_markdown(jobs), encoding="utf-8")
    return [labels, readable, adverts]


def load_cases(folder: Path) -> tuple[list[dict[str, Any]], list[str]]:
    """The labelled adverts (each with its label), and any rows that couldn't be used."""
    adverts = {}
    for line in (folder / "adverts.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            case = json.loads(line)
            adverts[case["id"]] = case
    cases, problems = [], []
    for row in _read_label_rows(folder / "labels.csv"):
        typed = (row.get("your_label") or "").strip()
        if not typed:
            continue
        label = normalise_label(typed)
        case = adverts.get(row.get("id", ""))
        if case is None:
            problems.append(f"{row.get('title')}: not in adverts.jsonl")
        elif label is None:
            problems.append(f"{row.get('title')}: {typed!r} isn't entry, experienced or unclear")
        else:
            cases.append({**case, "label": label})
    return cases, problems


def job_from_case(case: dict[str, Any]) -> Job:
    return Job(
        source=case["source"],
        source_id=case["id"],
        title=case["title"],
        company=case["company"],
        location=case["location"],
        url=case["url"],
        description=case["description"],
        salary=case["salary"],
        contract=case["contract"],
        level=case["level"],
        excerpt_only=case["excerpt_only"],
        tags=list(case["tags"]),
    )


def score(rows: list[dict[str, Any]]) -> dict[str, tuple[int, int]]:
    """(matches, out of) for each measure. "Positive" means needing experience, the thing the
    check is there to catch."""
    def count(items: list[dict[str, Any]], test: Any) -> tuple[int, int]:
        return sum(1 for row in items if test(row)), len(items)

    positives = [row for row in rows if row["label"] == VERDICT_NEEDS_EXPERIENCE]
    negatives = [row for row in rows if row["label"] == VERDICT_ENTRY]
    flagged = [row for row in rows if row["verdict"] == VERDICT_NEEDS_EXPERIENCE
               and row["label"] != VERDICT_UNCLEAR]
    return {
        "agreement": count(rows, lambda row: row["verdict"] == row["label"]),
        "recall": count(positives, lambda row: row["verdict"] == VERDICT_NEEDS_EXPERIENCE),
        "precision": count(flagged, lambda row: row["label"] == VERDICT_NEEDS_EXPERIENCE),
        "specificity": count(negatives, lambda row: row["verdict"] == VERDICT_ENTRY),
        "unclear": count(rows, lambda row: row["verdict"] == VERDICT_UNCLEAR),
    }


def wilson(matches: int, total: int, z: float = 1.96) -> tuple[float, float]:
    """95% interval for a share, so a small sample's figure isn't over-read."""
    if total == 0:
        return 0.0, 0.0
    share = matches / total
    centre = share + z * z / (2 * total)
    margin = z * math.sqrt(share * (1 - share) / total + z * z / (4 * total * total))
    scale = 1 + z * z / total
    return max(0.0, (centre - margin) / scale), min(1.0, (centre + margin) / scale)


def render_report(rows: list[dict[str, Any]], errors: list[dict[str, Any]],
                  spent: float | None) -> str:
    """The evaluation as Markdown: headline, measures, confusion table and disagreements."""
    measures = score(rows)
    agree, total = measures["agreement"]
    low, high = wilson(agree, total)
    answered_by = ", ".join(sorted({SOURCE_NAMES.get(str(row["model"]), str(row["model"]))
                                    for row in rows if row.get("model")}))
    cost = "unknown" if spent is None else (f"about ${spent:.2f}" if spent else "nothing")
    lines = [
        "# How often Claude agrees with you",
        "",
        f"Answers from {answered_by or 'nobody yet'} · {total + len(errors)} labelled adverts · "
        f"{total} judged · {len(errors)} not judged · new paid checks cost {cost}",
        "",
    ]
    if total:
        lines += [f"**Claude agreed with you on {agree} of {total} adverts ({agree / total:.0%}; "
                  f"with this many adverts the true rate is likely {low:.0%} to {high:.0%}).**",
                  ""]
    lines += [
        "| Measure | Result | What it means |",
        "|---|---|---|",
        f"| Agreement | {_share(*measures['agreement'])} | Claude's verdict matched your label |",
        f"| Catches (recall) | {_share(*measures['recall'])} | Of the adverts you marked "
        "experienced, how many Claude flagged |",
        f"| Right warnings (precision) | {_share(*measures['precision'])} | Of the adverts "
        "Claude flagged, how many you also marked experienced |",
        f"| Entry level kept (specificity) | {_share(*measures['specificity'])} | Of the "
        "adverts you marked entry, how many Claude also called entry level |",
        f"| \"Can't tell\" answers | {_share(*measures['unclear'])} | How often Claude said "
        "the advert didn't say enough |",
        "",
        "## By what Claude could read",
        "",
        "| What Claude saw | Adverts | Agreement |",
        "|---|---|---|",
    ]
    for name, excerpt in (("The full advert", False), ("Only the start of the advert", True)):
        group = [row for row in rows if row["excerpt_only"] == excerpt]
        lines.append(f"| {name} | {len(group)} | {_share(*score(group)['agreement'])} |")
    lines += ["", "## Your labels against Claude's answers", "",
              "| You said / Claude said | entry | experienced | unclear |", "|---|---|---|---|"]
    pairs = Counter((row["label"], row["verdict"]) for row in rows)
    for label in LABEL_NAMES:
        cells = " | ".join(str(pairs[(label, verdict)]) for verdict in LABEL_NAMES)
        lines.append(f"| {LABEL_NAMES[label]} | {cells} |")
    lines += ["", "## Where Claude disagreed with you", ""]
    disagreements = [row for row in rows if row["verdict"] != row["label"]]
    for row in disagreements:
        lines += [f"### {_one_line(row['title'])} ({_one_line(row['company'])}, {row['source']})",
                  "",
                  f"You: {LABEL_NAMES[row['label']]} · Claude: {LABEL_NAMES[row['verdict']]}, "
                  f"asks for {row['years']} years · “{_one_line(row['evidence'])}”",
                  "", f"Advert: {row['url']}", ""]
    if not disagreements:
        lines += ["None.", ""]
    if errors:
        lines += ["## Not judged", ""]
        lines += [f"- {_one_line(error['title'])}: {error['error']}" for error in errors]
        lines.append("")
    return "\n".join(lines)


def _case(job: Job) -> dict[str, Any]:
    return {"id": job_key(job), "title": job.title, "company": job.company,
            "location": job.location, "salary": job.salary, "contract": job.contract,
            "level": job.level, "source": job.source, "url": job.url,
            "description": job.description, "excerpt_only": job.excerpt_only,
            "tags": job.tags}


def _sees(job: Job) -> str:
    return "only the start of the advert" if job.excerpt_only else "the full advert"


def _markdown(jobs: list[Job]) -> str:
    lines = [
        "# Adverts to label",
        "",
        "For each advert, open the link and read the whole advert, then write `entry`, "
        "`experienced` or `unclear` in the `your_label` column of labels.csv. Label what the "
        "advert really asks for, even when Claude only gets the start of it: that shows what "
        "the missing text costs.",
        "",
    ]
    for number, job in enumerate(jobs, start=1):
        fence = "`" * max(3, _longest_backtick_run(job.description) + 1)
        lines += [f"## {number}. {_one_line(job.title)} ({_one_line(job.company)}, {job.source})",
                  "", f"Link: {job.url}", "", f"Claude sees: {_sees(job)}", "",
                  f"{fence}text", job.description or "(no description)", fence, ""]
    return "\n".join(lines)


def _read_label_rows(path: Path) -> list[dict[str, str]]:
    # Excel saves CSV as UTF-8 or, with plain "Save as CSV", in the Windows code page.
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            with path.open(encoding=encoding, newline="") as handle:
                return list(csv.DictReader(handle))
        except UnicodeDecodeError:
            continue
    raise ValueError(f"{path.name} isn't readable text; save it again as CSV UTF-8")


def _share(matches: int, total: int) -> str:
    return f"{matches} of {total} ({matches / total:.0%})" if total else "none to measure"


def _one_line(text: str) -> str:
    return " ".join(str(text).split())


def _longest_backtick_run(text: str) -> int:
    return max((len(run) for run in re.findall(r"`+", text)), default=0)
