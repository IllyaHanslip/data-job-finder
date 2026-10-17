r"""Measure how often the Claude entry-level check agrees with your labels.

Run from the project folder once output/eval/labels.csv has labels in it:
    .venv\Scripts\python.exe tools\evaluate_entry_check.py        (says what it would cost)
    .venv\Scripts\python.exe tools\evaluate_entry_check.py --yes  (sends the adverts)

It uses the same check, model and saved answers as find_jobs.py, so adverts already checked
cost nothing. Writes output/eval/report.md, plus results.jsonl and errors.jsonl.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
import tempfile
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from job_finder.cli import _set_up_console  # noqa: E402
from job_finder.config import load_settings  # noqa: E402
from job_finder.entry_check import (  # noqa: E402
    SIGN_UP_URL,
    EntryLevelChecker,
    advert_text,
    estimate_cost,
)
from job_finder.entry_eval import job_from_case, load_cases, render_report, score  # noqa: E402
from job_finder.filters import job_key  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Measure the Claude check against your labels.")
    parser.add_argument("--yes", action="store_true", help="go ahead and send adverts to Claude")
    parser.add_argument("--fresh", action="store_true",
                        help="ask again even for adverts that already have a saved answer")
    args = parser.parse_args(argv)
    _set_up_console(verbose=False)
    settings = load_settings()
    folder = settings.output_dir / "eval"
    if not (folder / "labels.csv").exists():
        print("There's no label sheet yet: run tools\\make_label_sheet.py first.")
        return 2
    cases, problems = load_cases(folder)
    for problem in problems:
        print(f"Skipped {problem}")
    if not cases:
        print(f"No labels yet: fill in the your_label column of {folder / 'labels.csv'}.")
        return 2
    key = settings.api_keys.get("ANTHROPIC_API_KEY", "")
    if not key:
        print(f"Add ANTHROPIC_API_KEY to .env first (get a key at {SIGN_UP_URL}).")
        return 2
    try:
        import anthropic
    except ImportError:
        print("The anthropic package is missing: run pip install -r requirements.txt")
        return 2

    jobs = [job_from_case(case) for case in cases]
    settings = dataclasses.replace(
        settings, max_claude_checks=max(len(jobs), settings.max_claude_checks)
    )
    saved = settings.cache_dir / "claude_verdicts.json"
    if args.fresh:
        saved = Path(tempfile.mkdtemp()) / "claude_verdicts.json"
    client = anthropic.Anthropic(api_key=key, max_retries=3, timeout=120.0)
    checker = EntryLevelChecker(settings, client, saved, date.today())
    to_ask = [job for job in jobs if checker.saved_answer(job) is None]
    estimate = estimate_cost(to_ask, settings.claude_model)
    cost = f"roughly ${estimate:.2f} (an estimate)" if estimate is not None else "cost unknown"
    print(f"{len(jobs)} labelled adverts: {len(jobs) - len(to_ask)} already have a saved "
          f"answer, {len(to_ask)} to send to {settings.claude_model}, {cost}.")
    if to_ask and not args.yes:
        print("Nothing has been sent. Run again with --yes to go ahead.")
        return 0

    checker.check(jobs)
    rows, errors = [], []
    for case, job in zip(cases, jobs, strict=True):
        answer = checker.saved_answer(job)
        if answer is None:
            reason = checker.failures.get(job_key(job)) or checker.stop_reason or "not checked"
            errors.append({"prompt_id": case["id"], "title": case["title"], "error": reason})
            continue
        rows.append({
            "prompt_id": case["id"],
            "prompt": advert_text(job),
            "tags": [case["source"], "excerpt" if case["excerpt_only"] else "full advert"],
            "status": "ok",
            "expected": case["label"],
            "grade": {"agree": float(answer["verdict"] == case["label"])},
            "model": answer.get("model"),
            "usage": {"input_tokens": answer.get("input_tokens", 0),
                      "output_tokens": answer.get("output_tokens", 0)},
            "label": case["label"],
            "verdict": answer["verdict"],
            "years": answer.get("years"),
            "evidence": answer.get("evidence", ""),
            "title": case["title"],
            "company": case["company"],
            "source": case["source"],
            "url": case["url"],
            "excerpt_only": case["excerpt_only"],
        })
    _write_jsonl(folder / "results.jsonl", rows)
    _write_jsonl(folder / "errors.jsonl", errors)
    report = folder / "report.md"
    report.write_text(render_report(rows, errors, settings.claude_model, settings.claude_effort,
                                    checker.spent), encoding="utf-8")
    agree, total = score(rows)["agreement"]
    if total:
        print(f"\nClaude agreed with you on {agree} of {total} adverts ({agree / total:.0%}).")
    if checker.checked:
        spent = f"about ${checker.spent:.2f}" if checker.spent is not None else "an unknown sum"
        print(f"{checker.checked} new checks cost {spent}.")
    print(f"Full report: {report}")
    return 0


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
