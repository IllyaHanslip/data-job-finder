"""Reed: one of the UK's largest job boards.

API docs: https://www.reed.co.uk/developers/jobseeker (free key required).
"""

from __future__ import annotations

import math
from typing import Any

from job_finder.http_client import Politeness
from job_finder.models import SCOPE_LOCAL, SCOPE_UK_REMOTE, Job
from job_finder.sources.base import JobSource, SearchRequest, list_at
from job_finder.text import format_salary, html_to_text, parse_date, plain, safe_url

KM_PER_MILE = 1.609344


class ReedSource(JobSource):
    key = "reed"
    name = "Reed"
    homepage = "https://www.reed.co.uk"
    signup_url = "https://www.reed.co.uk/developers/jobseeker"
    env_vars = ("REED_API_KEY",)
    covers_home_area = True
    # Reed publishes no rate limit, so stay well clear of anything that could look like abuse.
    politeness = Politeness(min_interval=3.0, cache_hours=8, daily_budget=100)

    SEARCH_URL = "https://www.reed.co.uk/api/1.0/search"

    def build_requests(self) -> list[SearchRequest]:
        s = self.settings
        auth = (s.api_keys["REED_API_KEY"], "")  # Reed takes the key as a basic-auth username
        radius_miles = max(1, math.ceil(s.home.radius_km / KM_PER_MILE))
        searches = [
            SearchRequest(
                self.SEARCH_URL,
                {
                    "keywords": term,
                    "locationName": s.home.name,
                    "distanceFromLocation": radius_miles,
                    "resultsToTake": 100,
                },
                SCOPE_LOCAL,
                f"'{term}' within {radius_miles} miles of {s.home.name}",
                auth,
            )
            for term in s.local_terms
        ]
        searches += [
            SearchRequest(
                self.SEARCH_URL,
                {"keywords": f"{term} remote", "resultsToTake": 100},
                SCOPE_UK_REMOTE,
                f"'{term} remote' across the UK",
                auth,
            )
            for term in s.remote_terms
        ]
        return searches

    def extract_items(self, data: Any) -> list[Any]:
        return list_at(data, "results")

    def parse_item(self, item: Any, scope: str) -> Job | None:
        title = plain(item.get("jobTitle"))
        url = safe_url(item.get("jobUrl"))
        if not title or not url:
            return None
        return Job(
            source=self.name,
            source_id=str(item.get("jobId") or url),
            title=title,
            company=plain(item.get("employerName")),
            location=plain(item.get("locationName")),
            url=url,
            description=html_to_text(item.get("jobDescription")),
            posted=parse_date(item.get("date")),
            salary=format_salary(
                item.get("minimumSalary"), item.get("maximumSalary"), item.get("currency") or "GBP"
            ),
            scope=scope,
        )
