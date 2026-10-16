"""Polite HTTP. Every API call in the program goes through PoliteClient.get_json, which:

* saves each answer to disk and reuses it for a few hours, so re-running costs nothing;
* gives each API a daily request budget below its published limit;
* spaces out requests to the same API;
* leaves an API alone for the rest of the run as soon as it answers 429 (too many
  requests) or 401/403 (access refused), instead of retrying;
* falls back to the last saved answer if a request fails.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import requests

from job_finder.storage import read_json, write_json

log = logging.getLogger(__name__)

RETRY_DELAYS = (5.0, 15.0)  # seconds to wait before the second and third attempts
TIMEOUT = (10.0, 30.0)  # seconds to connect, seconds to wait for the answer


class FetchError(Exception):
    """A request failed. Messages never contain API keys, so they are safe to print."""


class StopSource(FetchError):
    """The API refused us or a limit was reached: stop calling it for the rest of this run."""


@dataclass(frozen=True)
class Politeness:
    min_interval: float  # seconds between requests to the same API
    cache_hours: float  # how long a saved answer is reused instead of asking again
    daily_budget: int  # most requests sent to the API in one calendar day


class PoliteClient:
    def __init__(
        self,
        cache_dir: Path,
        user_agent: str,
        *,
        offline: bool = False,
        session: requests.Session | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.cache_dir = cache_dir
        self.offline = offline
        self._session = session if session is not None else requests.Session()
        self._session.headers.update({"User-Agent": user_agent, "Accept": "application/json"})
        self._sleep = sleep
        self._last_sent: dict[str, float] = {}
        self._stopped: dict[str, str] = {}
        self._warned_hosts: set[str] = set()
        self._usage_path = cache_dir / "request_counts.json"
        self._usage = self._load_usage()
        self.requests_sent = 0
        self.cache_hits = 0

    def get_json(
        self,
        url: str,
        *,
        politeness: Politeness,
        params: Mapping[str, Any] | None = None,
        auth: tuple[str, str] | None = None,
        secret_params: tuple[str, ...] = (),
    ) -> Any:
        """GET a JSON document, reusing a saved copy when it is recent enough."""
        host = urlsplit(url).hostname or "unknown-host"
        public_params = {k: v for k, v in (params or {}).items() if k not in secret_params}
        cache_file = self._cache_file(host, url, public_params)
        saved = self._read_cache(cache_file)

        if saved is not None:
            fetched_at, data = saved
            age_hours = (time.time() - fetched_at) / 3600
            if 0 <= age_hours < politeness.cache_hours:
                self.cache_hits += 1
                log.debug("Saved answer (%.1f h old): %s %s", age_hours, url, public_params)
                return data

        if self.offline:
            if saved is None:
                raise FetchError("offline mode, and nothing saved for this search yet")
            return saved[1]

        try:
            data = self._fetch(host, url, params, auth, politeness, public_params)
        except FetchError as exc:
            if saved is None:
                raise
            if host not in self._warned_hosts:
                self._warned_hosts.add(host)
                log.warning("%s. Using answers saved on an earlier run instead.", exc)
            return saved[1]

        self._save_cache(cache_file, url, public_params, data)
        return data

    def _fetch(
        self,
        host: str,
        url: str,
        params: Mapping[str, Any] | None,
        auth: tuple[str, str] | None,
        politeness: Politeness,
        public_params: Mapping[str, Any],
    ) -> Any:
        if host in self._stopped:
            raise StopSource(self._stopped[host])
        problem = f"could not reach {host}"
        for attempt in range(len(RETRY_DELAYS) + 1):
            if attempt:
                self._sleep(RETRY_DELAYS[attempt - 1])
            if self._used_today(host) >= politeness.daily_budget:
                self._stopped[host] = (
                    f"reached today's limit of {politeness.daily_budget} requests to {host} "
                    "(it resets tomorrow)"
                )
                raise StopSource(self._stopped[host])
            self._wait_turn(host, politeness.min_interval)
            self._count_request(host)
            log.debug("GET %s %s", url, public_params)
            try:
                response = self._session.get(url, params=params, auth=auth, timeout=TIMEOUT)
            except requests.RequestException as exc:
                # Only the error type is shown: the full message can contain the URL and keys.
                problem = f"could not reach {host} ({type(exc).__name__})"
                continue

            status = response.status_code
            if status == 429:
                self._stopped[host] = (
                    f"{host} asked for fewer requests (HTTP 429), "
                    "so it was left alone for the rest of this run"
                )
                raise StopSource(self._stopped[host])
            if status in (401, 403):
                self._stopped[host] = (
                    f"{host} refused access (HTTP {status}); "
                    "if it needs an API key, check the one in .env"
                )
                raise StopSource(self._stopped[host])
            if status >= 500:
                problem = f"{host} had a server problem (HTTP {status})"
                continue
            if status >= 400:
                raise FetchError(f"{host} rejected the request (HTTP {status})")
            try:
                return response.json()
            except ValueError:
                raise FetchError(f"{host} replied with something that is not JSON") from None
        raise FetchError(problem)

    def _wait_turn(self, host: str, interval: float) -> None:
        last = self._last_sent.get(host)
        if last is not None:
            remaining = interval - (time.monotonic() - last)
            if remaining > 0:
                self._sleep(remaining)
        self._last_sent[host] = time.monotonic()

    def _load_usage(self) -> dict[str, Any]:
        data = read_json(self._usage_path)
        if isinstance(data, dict) and isinstance(data.get("counts"), dict):
            return data
        return {"date": "", "counts": {}}

    def _used_today(self, host: str) -> int:
        today = date.today().isoformat()
        if self._usage.get("date") != today:
            self._usage = {"date": today, "counts": {}}
        count = self._usage["counts"].get(host, 0)
        return count if isinstance(count, int) else 0

    def _count_request(self, host: str) -> None:
        used = self._used_today(host)
        self._usage["counts"][host] = used + 1
        self.requests_sent += 1
        try:
            write_json(self._usage_path, self._usage)
        except OSError as exc:
            log.warning("Could not update the request counter (%s); carrying on.", exc)

    def _cache_file(self, host: str, url: str, public_params: Mapping[str, Any]) -> Path:
        key = json.dumps([url, sorted((str(k), str(v)) for k, v in public_params.items())])
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]
        return self.cache_dir / "http" / host / f"{digest}.json"

    @staticmethod
    def _read_cache(path: Path) -> tuple[float, Any] | None:
        payload = read_json(path)
        if not isinstance(payload, dict) or "data" not in payload:
            return None
        fetched_at = payload.get("fetched_at")
        if isinstance(fetched_at, bool) or not isinstance(fetched_at, (int, float)):
            return None
        return float(fetched_at), payload["data"]

    def _save_cache(
        self, path: Path, url: str, public_params: Mapping[str, Any], data: Any
    ) -> None:
        payload = {
            "fetched_at": time.time(),
            "url": url,
            "params": dict(public_params),
            "data": data,
        }
        try:
            write_json(path, payload)
        except OSError as exc:
            log.warning("Could not save a copy of an answer (%s); carrying on.", exc)
