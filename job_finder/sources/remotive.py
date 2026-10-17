"""Remotive: a remote-only job board with a free, keyless API.

API docs: https://github.com/remotive-com/remote-jobs-api
"""

from __future__ import annotations

from typing import Any

from job_finder.http_client import Politeness
from job_finder.models import SCOPE_REMOTE_BOARD, Job
from job_finder.sources.base import JobSource, SearchRequest, list_at
from job_finder.text import html_to_text, parse_date, plain, safe_url

_JOB_TYPES = {
    "full_time": "Full time",
    "part_time": "Part time",
    "contract": "Contract",
    "freelance": "Freelance",
    "internship": "Internship",
}


class RemotiveSource(JobSource):
    key = "remotive"
    name = "Remotive"
    homepage = "https://remotive.com"
    # Remotive asks for no more than 4 calls a day and blocks more than 2 a minute.
    politeness = Politeness(min_interval=35.0, cache_hours=6, daily_budget=4)

    URL = "https://remotive.com/api/remote-jobs"
    CATEGORIES = {
        "data": "Data and Analytics",
        "artificial-intelligence": "Artificial Intelligence",
    }

    def build_requests(self) -> list[SearchRequest]:
        return [
            SearchRequest(self.URL, {"category": slug}, SCOPE_REMOTE_BOARD, f"{name} category")
            for slug, name in self.CATEGORIES.items()
        ]

    def extract_items(self, data: Any) -> list[Any]:
        return list_at(data, "jobs")

    def parse_item(self, item: Any, scope: str) -> Job | None:
        title = plain(item.get("title"))
        url = safe_url(item.get("url"))
        if not title or not url:
            return None
        regions = plain(item.get("candidate_required_location"))
        job_type = item.get("job_type")
        return Job(
            source=self.name,
            source_id=str(item.get("id") or url),
            title=title,
            company=plain(item.get("company_name")),
            location=f"Remote ({regions})" if regions else "Remote",
            url=url,
            description=html_to_text(item.get("description")),
            posted=parse_date(item.get("publication_date")),
            salary=plain(item.get("salary")),
            contract=_JOB_TYPES.get(job_type, "") if isinstance(job_type, str) else "",
            scope=scope,
            remote_regions=regions,
        )
