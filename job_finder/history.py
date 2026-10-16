"""Remember when each job was first seen, so the report can highlight new ones."""

from __future__ import annotations

import logging
from datetime import date, timedelta
from pathlib import Path

from job_finder.filters import job_key
from job_finder.models import Job
from job_finder.storage import read_json, write_json

log = logging.getLogger(__name__)


class SeenJobs:
    def __init__(self, path: Path, keep_days: int = 120) -> None:
        self.path = path
        self.keep_days = keep_days
        self._first_seen: dict[str, str] = {}

    def load(self) -> None:
        data = read_json(self.path)
        if isinstance(data, dict):
            self._first_seen = {str(key): str(value) for key, value in data.items()}

    def mark(self, jobs: list[Job], today: date) -> None:
        """Flag jobs first seen today (re-running on the same day keeps them flagged)."""
        stamp = today.isoformat()
        for job in jobs:
            job.is_new = self._first_seen.setdefault(job_key(job), stamp) == stamp

    def save(self, today: date) -> None:
        cutoff = (today - timedelta(days=self.keep_days)).isoformat()
        recent = {key: day for key, day in self._first_seen.items() if day >= cutoff}
        try:
            write_json(self.path, recent)
        except OSError as exc:
            log.warning("Could not save the list of seen jobs (%s).", exc)
