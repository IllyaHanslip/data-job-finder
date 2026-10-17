"""Command-line entry point. Run `python find_jobs.py --help` for the options."""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import subprocess
import sys
import webbrowser
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from job_finder.config import ConfigError, Settings, load_settings
from job_finder.entry_check import check_jobs
from job_finder.filters import FilterSummary, process_jobs
from job_finder.history import SeenJobs
from job_finder.http_client import PoliteClient
from job_finder.models import SECTION_CHECK, SECTION_LOCAL, SECTION_REMOTE, Job
from job_finder.report import write_reports
from job_finder.review import REVIEW_FILE, apply_review
from job_finder.sources import ALL_SOURCES
from job_finder.sources.base import JobSource, SourceResult

log = logging.getLogger("job_finder")


@dataclass
class RunResult:
    jobs: list[Job]
    sources: list[SourceResult]
    summary: FilterSummary
    html_path: Path
    csv_path: Path
    claude_status: str = ""


def run(
    settings: Settings,
    client: PoliteClient,
    source_types: list[type[JobSource]],
    now: datetime,
) -> RunResult:
    """Query every enabled source, filter and label the jobs, and write the reports."""
    results: list[SourceResult] = []
    for source_type in source_types:
        source = source_type(settings, client)
        if not settings.sources.get(source.key, True):
            results.append(
                SourceResult(source.name, source.homepage, status="turned off in config.toml")
            )
            continue
        try:
            result = source.run()
        except Exception as exc:  # a bug or format change in one source must not stop the rest
            log.warning("%s failed unexpectedly (%s); carrying on without it.",
                        source.name, type(exc).__name__)
            log.debug("Details:", exc_info=True)
            result = SourceResult(
                source.name, source.homepage, status=f"failed unexpectedly ({type(exc).__name__})"
            )
        results.append(result)

    collected = [job for result in results for job in result.jobs]
    jobs, summary = process_jobs(collected, settings, now.date())
    try:
        claude_status = check_jobs(jobs, settings, client, now.date())
    except Exception as exc:  # the optional check must never cost you the report
        log.warning("Claude's check failed unexpectedly (%s); carrying on without it.",
                    type(exc).__name__)
        log.debug("Details:", exc_info=True)
        claude_status = f"failed unexpectedly ({type(exc).__name__})"
    # Notes from the last review in Claude Code, if there was one (review.py).
    review = apply_review(jobs, settings.cache_dir / REVIEW_FILE) if claude_status else None

    history = SeenJobs(settings.cache_dir / "seen_jobs.json")
    history.load()
    history.mark(jobs, now.date())
    history.save(now.date())

    html_path, csv_path = write_reports(jobs, results, summary, settings, now, claude_status,
                                        review)
    return RunResult(jobs, results, summary, html_path, csv_path, claude_status)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    _set_up_console(verbose=args.verbose)
    try:
        settings = load_settings(args.config)
    except ConfigError as exc:
        log.error("%s", exc)
        return 2

    client = PoliteClient(settings.cache_dir, settings.user_agent, offline=args.offline)
    if not args.offline:
        log.info(
            "A full search takes about 8 minutes (seconds if you ran it in the last few hours).\n"
            "Keep this window open: the report opens when it's finished.\n"
        )
    try:
        outcome = run(settings, client, ALL_SOURCES, datetime.now())
    except KeyboardInterrupt:
        log.warning("Stopped before the report was written.")
        return 130

    _print_summary(outcome, client, settings)
    if not args.no_browser:
        _open_report(outcome.html_path, settings.browser)
    return 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="find_jobs.py",
        description="Find entry-level data science, AI and maths jobs that are remote or near "
        "home, using official job APIs.",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="don't use the internet; rebuild the report from answers saved on earlier runs",
    )
    parser.add_argument(
        "--no-browser", action="store_true", help="don't open the report in your web browser"
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="show every request and saved answer used"
    )
    parser.add_argument(
        "--config", type=Path, help="use a different settings file (default: config.toml)"
    )
    return parser.parse_args(argv)


def _print_summary(outcome: RunResult, client: PoliteClient, settings: Settings) -> None:
    width = max((len(result.name) for result in outcome.sources), default=0)
    print("\nSources:")
    for result in outcome.sources:
        print(f"  {result.name:<{width}}  {result.status}")
    if outcome.claude_status:
        print(f"\nClaude's entry-level check: {outcome.claude_status}")

    summary = outcome.summary
    sections = Counter(job.section for job in outcome.jobs)
    new_today = sum(job.is_new for job in outcome.jobs)
    print(
        f"\nChecked {summary.received} listings and kept {summary.kept} "
        f"({summary.duplicates} duplicates merged)."
    )
    print(
        f"  {sections[SECTION_REMOTE]} remote | "
        f"{sections[SECTION_LOCAL]} within about an hour of {settings.home.name} | "
        f"{sections[SECTION_CHECK]} worth a quick check | {new_today} new today"
    )
    print(f"\nReport:      {outcome.html_path}")
    print(f"Spreadsheet: {outcome.csv_path}")
    print(
        f"Requests sent this run: {client.requests_sent} "
        f"(plus {client.cache_hits} answered from saved copies)"
    )


def _open_report(path: Path, browser: str) -> None:
    """Open the report in Chrome when that is the chosen browser, else the default browser."""
    uri = path.resolve().as_uri()
    if browser == "chrome":
        chrome = _find_chrome()
        if chrome is not None:
            try:
                # Without the DEVNULLs, Chrome's own log messages can appear in this window.
                subprocess.Popen(
                    [str(chrome), uri],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except OSError:
                log.debug("Could not start Chrome:", exc_info=True)
            else:
                log.info("\nOpening the report in Chrome.")
                return
        log.info("\nChrome wasn't found, so the report is opening in your default browser.")
    else:
        log.info("\nOpening the report in your browser.")
    webbrowser.open(uri)


def _find_chrome() -> Path | None:
    """Where Google Chrome (or Chromium) is installed, or None."""
    candidates = [
        Path(base) / "Google" / "Chrome" / "Application" / "chrome.exe"
        for base in (os.environ.get(name) for name in
                     ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"))
        if base
    ]
    candidates.append(Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"))
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
        found = shutil.which(name)
        if found:
            return Path(found)
    return None


class _ConsoleHandler(logging.StreamHandler):
    """Prints messages, and shows progress on one line that updates in place.

    Progress records (logged with extra={"progress": True}) only appear in a console window;
    when the output goes to a file or another program they are left out.
    """

    def __init__(self, stream) -> None:
        super().__init__(stream)
        self._progress_width = 0

    def emit(self, record: logging.LogRecord) -> None:
        try:
            if getattr(record, "progress", False):
                isatty = getattr(self.stream, "isatty", None)
                if isatty is not None and isatty():
                    text = self.format(record)
                    self.stream.write("\r" + text.ljust(self._progress_width))
                    self.stream.flush()
                    self._progress_width = len(text)
                return
            if self._progress_width:
                self.stream.write("\r" + " " * self._progress_width + "\r")
                self._progress_width = 0
        except Exception:
            self.handleError(record)
            return
        super().emit(record)


class _ConsoleFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        message = super().format(record)
        if record.levelno >= logging.ERROR:
            return f"Error: {message}"
        if record.levelno >= logging.WARNING:
            return f"Warning: {message}"
        return message


def _set_up_console(verbose: bool) -> None:
    for stream in (sys.stdout, sys.stderr):
        # Job titles can contain characters the Windows console can't show; print '?' instead.
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(errors="replace")
            except (OSError, ValueError):
                pass
    handler = _ConsoleHandler(sys.stdout)
    handler.setFormatter(_ConsoleFormatter("%(message)s"))
    log.handlers[:] = [handler]
    log.setLevel(logging.DEBUG if verbose else logging.INFO)
    log.propagate = False
    # urllib3's debug messages include full request URLs, which would reveal API keys.
    logging.getLogger("urllib3").setLevel(logging.WARNING)
