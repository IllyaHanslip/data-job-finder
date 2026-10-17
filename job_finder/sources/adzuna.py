"""Adzuna: a UK job search engine that gathers adverts from most job sites.

API docs: https://developer.adzuna.com (key required: 25 free trial calls, then £8 a month).
"""

from __future__ import annotations

from typing import Any

from job_finder.http_client import Politeness
from job_finder.models import SCOPE_LOCAL, SCOPE_UK_REMOTE, Job
from job_finder.sources.base import JobSource, SearchRequest, list_at
from job_finder.text import dig, format_salary, html_to_text, parse_date, plain, safe_url, to_float

_CONTRACT_WORDS = {
    "permanent": "Permanent",
    "contract": "Contract",
    "full_time": "Full time",
    "part_time": "Part time",
}


class AdzunaSource(JobSource):
    key = "adzuna"
    name = "Adzuna"
    homepage = "https://www.adzuna.co.uk"
    signup_url = "https://developer.adzuna.com/signup"
    env_vars = ("ADZUNA_APP_ID", "ADZUNA_APP_KEY")
    secret_params = ("app_id", "app_key")
    covers_home_area = True
    # New accounts get 25 trial calls; after that the £8-a-month plan allows 500 calls a day
    # and 5,000 a month. 100 a day keeps even daily use inside the monthly allowance.
    politeness = Politeness(min_interval=3.0, cache_hours=8, daily_budget=100)

    SEARCH_URL = "https://api.adzuna.com/v1/api/jobs/gb/search/1"

    def build_requests(self) -> list[SearchRequest]:
        s = self.settings
        common = {
            "app_id": s.api_keys["ADZUNA_APP_ID"],
            "app_key": s.api_keys["ADZUNA_APP_KEY"],
            "results_per_page": 50,
            "max_days_old": s.max_days_old,
            "sort_by": "date",
            "content-type": "application/json",
        }
        searches = [
            SearchRequest(
                self.SEARCH_URL,
                {**common, "what": term, "where": s.home.name, "distance": round(s.home.radius_km)},
                SCOPE_LOCAL,
                f"'{term}' near {s.home.name}",
            )
            for term in s.local_terms
        ]
        searches += [
            SearchRequest(
                self.SEARCH_URL,
                {**common, "what": f"{term} remote"},
                SCOPE_UK_REMOTE,
                f"'{term} remote' across the UK",
            )
            for term in s.remote_terms
        ]
        return searches

    def extract_items(self, data: Any) -> list[Any]:
        return list_at(data, "results")

    def parse_item(self, item: Any, scope: str) -> Job | None:
        title = plain(item.get("title"))
        url = safe_url(item.get("redirect_url"))
        if not title or not url:
            return None
        predicted = str(item.get("salary_is_predicted", "0")) == "1"
        contract = ", ".join(
            _CONTRACT_WORDS[value]
            for value in (item.get("contract_type"), item.get("contract_time"))
            if isinstance(value, str) and value in _CONTRACT_WORDS
        )
        return Job(
            source=self.name,
            source_id=str(item.get("id") or url),
            title=title,
            company=plain(dig(item, "company", "display_name")),
            location=plain(dig(item, "location", "display_name")),
            url=url,
            description=html_to_text(item.get("description")),
            posted=parse_date(item.get("created")),
            salary=format_salary(
                item.get("salary_min"),
                item.get("salary_max"),
                "GBP",
                note="Adzuna estimate" if predicted else "",
            ),
            contract=contract,
            latitude=to_float(item.get("latitude")),
            longitude=to_float(item.get("longitude")),
            excerpt_only=True,  # the API only sends the first 500 characters of each advert
            scope=scope,
        )
