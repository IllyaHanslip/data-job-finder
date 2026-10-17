"""Reed: one of the UK's largest job boards.

API docs: https://www.reed.co.uk/developers/jobseeker (free key required).
"""

from __future__ import annotations

import logging
import math
from typing import Any

from job_finder.http_client import FetchError, Politeness, StopSource
from job_finder.models import SCOPE_LOCAL, SCOPE_UK_REMOTE, Job
from job_finder.sources.base import JobSource, SearchRequest, list_at
from job_finder.text import format_salary, html_to_text, parse_date, plain, safe_url

log = logging.getLogger(__name__)

KM_PER_MILE = 1.609344


class ReedSource(JobSource):
    key = "reed"
    name = "Reed"
    homepage = "https://www.reed.co.uk"
    signup_url = "https://www.reed.co.uk/developers/jobseeker"
    env_vars = ("REED_API_KEY",)
    covers_home_area = True
    # Reed publishes no rate limit, so stay well clear of anything that could look like abuse.
    politeness = Politeness(min_interval=3.0, cache_hours=8, daily_budget=150)

    SEARCH_URL = "https://www.reed.co.uk/api/1.0/search"
    DETAILS_URL = "https://www.reed.co.uk/api/1.0/jobs/{}"
    # Only used by the Claude check. A job's details hardly change, so a saved copy is reused
    # for a month. Reed's daily count is shared with the searches: at most 250 a day in all.
    details_politeness = Politeness(min_interval=3.0, cache_hours=24 * 30, daily_budget=250)

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
            excerpt_only=True,  # search results only carry the start of each advert
            scope=scope,
        )

    def add_full_descriptions(self, jobs: list[Job]) -> int:
        """Swap Reed's search excerpts for the full adverts, so Claude can read them."""
        wanted = [job for job in jobs
                  if job.source == self.name and job.excerpt_only and job.source_id.isdigit()]
        if not wanted or self.missing_keys():
            return 0
        log.info("Getting the full text of %d Reed adverts for Claude...", len(wanted))
        auth = (self.settings.api_keys["REED_API_KEY"], "")
        added = 0
        for number, job in enumerate(wanted, start=1):
            log.info("  %d of %d", number, len(wanted), extra={"progress": True})
            try:
                details = self.client.get_json(self.DETAILS_URL.format(job.source_id),
                                               politeness=self.details_politeness, auth=auth)
            except StopSource as exc:
                log.warning("Reed: %s", exc)
                break
            except FetchError:
                continue
            text = html_to_text(details.get("jobDescription")) if isinstance(details, dict) else ""
            if len(text) > len(job.description):
                job.description, job.excerpt_only = text, False
                added += 1
        log.info("  %d full adverts", added)
        return added
