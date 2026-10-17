r"""Judge adverts in a Claude Code chat instead of through the paid API.

Run from the project folder, with check_entry_level = true under [claude]:

  .venv\Scripts\python.exe tools\review_adverts.py export
      Lists every advert in your latest results that hasn't been judged yet in
      output/review/to_review.md, each numbered R1, R2, ... Reed adverts are fetched in
      full first, as the check would see them. No new searches are made.

  Claude Code then reads that file and writes its verdicts to output/review/verdicts.json,
  following the instructions at the top of the file.

  .venv\Scripts\python.exe tools\review_adverts.py import
      Saves those verdicts, so find_jobs.py shows them from then on
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
from job_finder.review import ReviewError, import_verdicts, write_review_list  # noqa: E402
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
    try:
        if args.action == "import":
            added, problems = import_verdicts(folder, saved, today)
            for problem in problems:
                print(f"Skipped: {problem}")
            print(f"Saved {added} verdicts. Run find_jobs.py --offline to see them in the report.")
            return 0
        jobs = latest_jobs(settings, today)
        if not jobs:
            print("There are no saved results yet: run find_jobs.py first.")
            return 2
        online = PoliteClient(settings.cache_dir, settings.user_agent)
        ReedSource(settings, online).add_full_descriptions(jobs)
        waiting = write_review_list(jobs, EntryLevelChecker(settings, None, saved, today), folder)
    except ReviewError as exc:
        print(exc)
        return 2
    print(f"{waiting} of {len(jobs)} adverts need judging: {folder / 'to_review.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
