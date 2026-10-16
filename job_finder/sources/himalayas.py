"""Himalayas: a remote-only job board with a free, keyless search API.

API docs: https://himalayas.app/docs/remote-jobs-api
"""

from __future__ import annotations

from typing import Any

from job_finder.http_client import Politeness
from job_finder.models import SCOPE_REMOTE_BOARD, Job
from job_finder.sources.base import JobSource, SearchRequest, list_at
from job_finder.text import format_salary, html_to_text, parse_date, plain, safe_url


class HimalayasSource(JobSource):
    key = "himalayas"
    name = "Himalayas"
    homepage = "https://himalayas.app"
    # Himalayas refreshes its data once a day, so asking more often gains nothing.
    politeness = Politeness(min_interval=5.0, cache_hours=24, daily_budget=16)

    SEARCH_URL = "https://himalayas.app/jobs/api/search"

    def build_requests(self) -> list[SearchRequest]:
        return [
            SearchRequest(
                self.SEARCH_URL,
                {
                    "q": term,
                    "country": "GB",  # jobs open to UK residents, including worldwide ones
                    "seniority": "Entry-level,Mid-level",
                    "sort": "recent",
                },
                SCOPE_REMOTE_BOARD,
                f"'{term}' (remote, open to the UK)",
            )
            for term in self.settings.remote_terms
        ]

    def extract_items(self, data: Any) -> list[Any]:
        return list_at(data, "jobs")

    def parse_item(self, item: Any, scope: str) -> Job | None:
        title = plain(item.get("title"))
        url = safe_url(item.get("applicationLink")) or safe_url(item.get("guid"))
        if not title or not url:
            return None
        regions = _region_names(item.get("locationRestrictions"))
        seniority = item.get("seniority")
        level = (
            ", ".join(plain(value) for value in seniority)
            if isinstance(seniority, list)
            else plain(seniority)
        )
        return Job(
            source=self.name,
            source_id=str(item.get("guid") or url),
            title=title,
            company=plain(item.get("companyName")),
            location=f"Remote ({_short_list(regions)})",
            url=url,
            description=html_to_text(item.get("description")) or html_to_text(item.get("excerpt")),
            posted=parse_date(item.get("pubDate")),
            salary=format_salary(
                item.get("minSalary"),
                item.get("maxSalary"),
                plain(item.get("currency")),
                period=item.get("salaryPeriod"),
            ),
            contract=plain(item.get("employmentType")),
            scope=scope,
            remote_regions=", ".join(regions) or "Worldwide",
            level=level,
        )


def _region_names(value: Any) -> list[str]:
    """Country names from locationRestrictions (objects with a 'name', or plain strings)."""
    if not isinstance(value, list):
        return []
    names = []
    for entry in value:
        name = plain(entry.get("name") if isinstance(entry, dict) else entry)
        if name:
            names.append(name)
    return names


def _short_list(names: list[str], limit: int = 4) -> str:
    if not names:
        return "Worldwide"
    if len(names) <= limit:
        return ", ".join(names)
    return f"{', '.join(names[:limit])} +{len(names) - limit} more"
