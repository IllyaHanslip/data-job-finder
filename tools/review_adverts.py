r"""Judge adverts in a Claude Code chat instead of through the paid API.

Run from the project folder, with check_entry_level = true under [claude]:

  .venv\Scripts\python.exe tools\review_adverts.py export
      Lists every advert in your latest results that hasn't been judged yet in
      output/review/to_review.md, each numbered R1, R2, ..., then the entry-level jobs
      judged earlier, numbered C1, C2, ... Reed adverts are fetched in full first, as the
      check would see them. No new searches are made.

  Claude Code then reads that file and writes its verdicts to output/review/verdicts.json,
  following the instructions at the top of the file. It can add picks, notes and a summary
  for the "Claude's review" box at the top of the report.

  .venv\Scripts\python.exe tools\review_adverts.py import
      Saves all of that, so find_jobs.py shows it from then on
      (run find_jobs.py --offline to rebuild today's report straight away).
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from job_finder.cli import _set_up_console  # noqa: E402
from job_finder.config import load_settings  # noqa: E402
from job_finder.entry_check import SAVED_FILE, EntryLevelChecker, latest_jobs  # noqa: E402
from job_finder.http_client import PoliteClient  # noqa: E402
from job_finder.review import (  # noqa: E402
    REVIEW_FILE,
    ImportResult,
    ReviewError,
    import_verdicts,
    write_review_list,
)
from job_finder.sources.reed import ReedSource  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Judge adverts in a Claude Code chat.")
    parser.add_argument("action", choices=["export", "import"])
    args = parser.parse_args(argv)
    _set_up_console(verbose=False)
    settings = load_settings()
    if not settings.check_entry_level:
        print("Turn the check on first: check_entry_level = true under [claude] "
              "(config.local.toml is a good place).")
        return 2
    today = date.today()
    folder = settings.output_dir / "review"
    saved = settings.cache_dir / SAVED_FILE
    review_file = settings.cache_dir / REVIEW_FILE
    try:
        if args.action == "import":
            result = import_verdicts(folder, saved, review_file, today)
            for problem in result.problems:
                print(f"Skipped: {problem}")
            print(f"Saved {_saved_text(result)}. Run find_jobs.py --offline to see them in the "
                  "report.")
            return 0
        jobs = latest_jobs(settings, today)
        if not jobs:
            print("There are no saved results yet: run find_jobs.py first.")
            return 2
        online = PoliteClient(settings.cache_dir, settings.user_agent)
        ReedSource(settings, online).add_full_descriptions(jobs)
        checker = EntryLevelChecker(settings, None, saved, today)
        waiting, earlier = write_review_list(jobs, checker, folder, review_file)
    except ReviewError as exc:
        print(exc)
        return 2
    print(f"{waiting} of {len(jobs)} adverts need judging, and {earlier} entry-level jobs "
          f"judged earlier are listed for picks and notes: {folder / 'to_review.md'}")
    return 0


def _saved_text(result: ImportResult) -> str:
    parts = [_count(result.verdicts, "verdict"), _count(result.picks, "pick"),
             _count(result.notes, "note")]
    if result.summary:
        parts.append("the summary")
    return ", ".join(parts[:-1]) + " and " + parts[-1]


def _count(number: int, word: str) -> str:
    return f"{number} {word}{'' if number == 1 else 's'}"


if __name__ == "__main__":
    raise SystemExit(main())
