r"""Pick adverts from your latest results for you to label, to measure the Claude check.

Run from the project folder:  .venv\Scripts\python.exe tools\make_label_sheet.py

Uses the job-site answers saved by your last run, so it makes no new searches. Reed adverts
are fetched in full, as the Claude check would see them. Writes output/eval/labels.csv (fill
in its your_label column), adverts.md (each advert's text) and adverts.jsonl (exactly what
the check is sent). Nothing is sent to Claude here.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from job_finder.cli import _set_up_console  # noqa: E402
from job_finder.config import load_settings  # noqa: E402
from job_finder.entry_check import latest_jobs  # noqa: E402
from job_finder.entry_eval import pick_adverts, write_label_sheet  # noqa: E402
from job_finder.http_client import PoliteClient  # noqa: E402
from job_finder.sources.reed import ReedSource  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Pick adverts for you to label.")
    parser.add_argument("--count", type=int, default=50, help="how many adverts (default 50)")
    parser.add_argument("--force", action="store_true",
                        help="replace an existing labels.csv, losing its labels")
    args = parser.parse_args(argv)
    _set_up_console(verbose=False)
    settings = load_settings()
    folder = settings.output_dir / "eval"
    if (folder / "labels.csv").exists() and not args.force:
        print(f"{folder / 'labels.csv'} already exists. Add --force to replace it (and the "
              "labels in it).")
        return 2

    print("Reading the results saved by your last run (no new searches)...")
    jobs = latest_jobs(settings, date.today())
    if not jobs:
        print("There are no saved results yet: run find_jobs.py first.")
        return 2
    picked = pick_adverts(jobs, args.count)
    online = PoliteClient(settings.cache_dir, settings.user_agent)
    ReedSource(settings, online).add_full_descriptions(picked)
    labels, readable, _ = write_label_sheet(picked, folder)
    print(f"\nPicked {len(picked)} adverts from {len(jobs)}.")
    print(f"1. Open {labels} in Excel.")
    print("2. For each advert, open its link and read the whole advert, then type entry, "
          "experienced or unclear in the your_label column. The advert text is also in "
          f"{readable}.")
    print("3. Save the file (keep it as CSV), then run tools\\evaluate_entry_check.py.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
