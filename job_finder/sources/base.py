"""Shared plumbing for job sources: API keys, the request loop and error handling."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, ClassVar

from job_finder.config import Settings
from job_finder.http_client import FetchError, PoliteClient, Politeness, StopSource
from job_finder.models import Job

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class SearchRequest:
    url: str
    params: dict[str, Any]
    scope: str  # one of the SCOPE_* values in models.py
    label: str  # short description used in messages
    auth: tuple[str, str] | None = None


@dataclass
class SourceResult:
    name: str
    homepage: str
    jobs: list[Job] = field(default_factory=list)
    status: str = ""
    searched: bool = False  # False when skipped (no API key) or turned off
    covers_home_area: bool = False


def list_at(data: Any, key: str) -> list[Any]:
    """The list stored under `key` in a JSON object, or [] if the shape is unexpected."""
    value = data.get(key) if isinstance(data, dict) else None
    return value if isinstance(value, list) else []


class JobSource:
    """Subclasses describe one API (what to ask, how to read answers); run() does the rest."""

    key: ClassVar[str]  # name used in the [sources] section of config.toml
    name: ClassVar[str]  # name shown in reports
    homepage: ClassVar[str]
    signup_url: ClassVar[str] = ""
    env_vars: ClassVar[tuple[str, ...]] = ()  # API keys this source needs
    secret_params: ClassVar[tuple[str, ...]] = ()  # query parameters that carry API keys
    covers_home_area: ClassVar[bool] = False  # searches around home, not only remote jobs
    politeness: ClassVar[Politeness]

    def __init__(self, settings: Settings, client: PoliteClient) -> None:
        self.settings = settings
        self.client = client

    def build_requests(self) -> list[SearchRequest]:
        raise NotImplementedError

    def extract_items(self, data: Any) -> list[Any]:
        raise NotImplementedError

    def parse_item(self, item: Any, scope: str) -> Job | None:
        raise NotImplementedError

    def missing_keys(self) -> list[str]:
        return [name for name in self.env_vars if not self.settings.api_keys.get(name)]

    def run(self) -> SourceResult:
        result = SourceResult(self.name, self.homepage, covers_home_area=self.covers_home_area)
        missing = self.missing_keys()
        if missing:
            result.status = (
                f"skipped: add {' and '.join(missing)} to .env (get a key at {self.signup_url})"
            )
            log.info("%s: %s", self.name, result.status)
            return result

        searches = self.build_requests()
        result.searched = True
        log.info("Searching %s (%d %s)...", self.name, len(searches),
                 "search" if len(searches) == 1 else "searches")
        failures: list[str] = []
        stop_reason = ""
        unreadable = 0
        for number, search in enumerate(searches, start=1):
            log.info("  %s", _progress(number, len(searches), self.politeness.min_interval),
                     extra={"progress": True})
            try:
                data = self.client.get_json(
                    search.url,
                    politeness=self.politeness,
                    params=search.params,
                    auth=search.auth,
                    secret_params=self.secret_params,
                )
            except StopSource as exc:
                stop_reason = str(exc)
                break
            except FetchError as exc:
                failures.append(str(exc))
                log.debug("%s: %s failed: %s", self.name, search.label, exc)
                continue
            for item in self.extract_items(data):
                try:
                    job = self.parse_item(item, search.scope)
                except (AttributeError, KeyError, TypeError, ValueError):
                    job = None
                if job is None:
                    unreadable += 1
                else:
                    result.jobs.append(job)

        if unreadable:
            log.debug("%s: skipped %d listings without a title or link", self.name, unreadable)
        result.status = _describe(len(result.jobs), len(searches), failures, stop_reason)
        if failures or stop_reason:
            log.warning("%s: %s", self.name, result.status)
        else:
            log.info("  %s", result.status)
        return result


def _progress(number: int, total: int, interval: float) -> str:
    """'search 7 of 22, about 45 seconds left', shown on a line that updates in place."""
    text = f"search {number} of {total}"
    seconds_left = round((total - number) * interval)
    if seconds_left >= 5:
        text += f", about {seconds_left} seconds left"
    return text


def _describe(found: int, total: int, failures: list[str], stop_reason: str) -> str:
    text = f"{found} listing" + ("" if found == 1 else "s")
    notes = []
    if failures:
        notes.append(f"{len(failures)} of {total} searches failed: {failures[0]}")
    if stop_reason:
        notes.append(f"stopped early: {stop_reason}")
    return f"{text} ({'; '.join(notes)})" if notes else text
