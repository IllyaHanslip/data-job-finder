"""Arbeitnow: UK jobs taken from employers' own job pages, through a free, keyless API.

API docs: https://www.arbeitnow.com/blog/job-board-api (the UK feed is at arbeitnow.co.uk)
"""

from __future__ import annotations

from typing import Any

from job_finder.filters import open_to_uk
from job_finder.geo import find_place
from job_finder.http_client import Politeness
from job_finder.models import SCOPE_UK_REMOTE, Job
from job_finder.sources.base import JobSource, SearchRequest, list_at
from job_finder.text import html_to_text, parse_date, plain, safe_url


class ArbeitnowSource(JobSource):
    key = "arbeitnow"
    name = "Arbeitnow"
    homepage = "https://www.arbeitnow.co.uk"
    # A free public API that asks not to be abused. It has no keyword search: it lists every
    # UK job, newest first, 100 a page and about 400 a day, so each run reads the newest few
    # days and the title filter keeps the relevant ones. Saved pages are reused for 12 h.
    politeness = Politeness(min_interval=3.0, cache_hours=12, daily_budget=30)

    URL = "https://www.arbeitnow.co.uk/api/job-board-api"
    PAGES = 10

    def build_requests(self) -> list[SearchRequest]:
        return [
            SearchRequest(self.URL, {"page": page}, SCOPE_UK_REMOTE, f"newest UK jobs, page {page}")
            for page in range(1, self.PAGES + 1)
        ]

    def extract_items(self, data: Any) -> list[Any]:
        return list_at(data, "data")

    def parse_item(self, item: Any, scope: str) -> Job | None:
        title = plain(item.get("title"))
        url = safe_url(item.get("url"))
        if not title or not url:
            return None
        location = plain(item.get("location"))
        # The UK feed carries a few jobs based elsewhere, such as "United States, India".
        if location and not (open_to_uk(location) or find_place(location)):
            return None
        job_types = item.get("job_types")
        return Job(
            source=self.name,
            source_id=str(item.get("slug") or url),
            title=title,
            company=plain(item.get("company_name")),
            location=location,
            url=url,
            description=html_to_text(item.get("description")),
            posted=parse_date(item.get("created_at")),
            contract=", ".join(plain(value) for value in job_types if plain(value))
            if isinstance(job_types, list) else "",
            scope=scope,  # remote, hybrid or on-site is read from the advert's wording
        )
