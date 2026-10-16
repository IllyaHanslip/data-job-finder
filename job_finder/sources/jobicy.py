"""Jobicy: a remote-only job board with a free, keyless API.

API docs: https://jobicy.com/jobs-rss-feed
"""

from __future__ import annotations

from typing import Any

from job_finder.http_client import Politeness
from job_finder.models import SCOPE_REMOTE_BOARD, Job
from job_finder.sources.base import JobSource, SearchRequest, list_at
from job_finder.text import format_salary, html_to_text, parse_date, plain, safe_url


class JobicySource(JobSource):
    key = "jobicy"
    name = "Jobicy"
    homepage = "https://jobicy.com"
    # Jobicy asks integrations to poll at most once an hour and to cache responses.
    politeness = Politeness(min_interval=10.0, cache_hours=6, daily_budget=6)

    URL = "https://jobicy.com/api/v2/remote-jobs"

    def build_requests(self) -> list[SearchRequest]:
        return [
            SearchRequest(
                self.URL,
                {"industry": "data-science", "count": 100},
                SCOPE_REMOTE_BOARD,
                "Data Science & Analytics category",
            )
        ]

    def extract_items(self, data: Any) -> list[Any]:
        return list_at(data, "jobs")

    def parse_item(self, item: Any, scope: str) -> Job | None:
        title = plain(item.get("jobTitle"))
        url = safe_url(item.get("url"))
        if not title or not url:
            return None
        regions = plain(item.get("jobGeo"))
        # Older API versions used annualSalaryMin/Max instead of salaryMin/Max.
        period = item.get("salaryPeriod") or ("year" if "annualSalaryMin" in item else "")
        return Job(
            source=self.name,
            source_id=str(item.get("id") or url),
            title=title,
            company=plain(item.get("companyName")),
            location=f"Remote ({regions})" if regions else "Remote",
            url=url,
            description=html_to_text(item.get("jobDescription"))
            or html_to_text(item.get("jobExcerpt")),
            posted=parse_date(item.get("pubDate")),
            salary=format_salary(
                _first(item, "salaryMin", "annualSalaryMin"),
                _first(item, "salaryMax", "annualSalaryMax"),
                plain(item.get("salaryCurrency")),
                period=period,
            ),
            contract=_job_types(item.get("jobType")),
            scope=scope,
            remote_regions=regions,
            level=plain(item.get("jobLevel")),
        )


def _first(item: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = item.get(key)
        if value is not None and value != "":
            return value
    return None


def _job_types(value: Any) -> str:
    values = value if isinstance(value, list) else [value]
    words = [plain(v).replace("-", " ").capitalize() for v in values if v]
    return ", ".join(word for word in words if word)
