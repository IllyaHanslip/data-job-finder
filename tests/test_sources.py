"""Each source reads its API's answers correctly (canned answers, no network)."""

from __future__ import annotations

import dataclasses
import logging
from datetime import UTC, date, datetime

from job_finder.http_client import FetchError, StopSource
from job_finder.models import SCOPE_LOCAL, SCOPE_REMOTE_BOARD, SCOPE_UK_REMOTE
from job_finder.sources import (
    AdzunaSource,
    HimalayasSource,
    JobicySource,
    ReedSource,
    RemotiveSource,
)
from job_finder.text import html_to_text

ADZUNA_ANSWER = {
    "__CLASS__": "Adzuna::API::Response::JobSearchResults",
    "count": 2,
    "results": [
        {
            "__CLASS__": "Adzuna::API::Response::Job",
            "id": "4321",
            "title": "<strong>Graduate</strong> Data Analyst",
            "description": "Join our team &amp; use Python and SQL to build dashboards…",
            "redirect_url": "https://www.adzuna.co.uk/jobs/land/ad/4321",
            "created": "2026-10-10T09:15:00Z",
            "company": {"display_name": "Midland Analytics Ltd"},
            "location": {
                "display_name": "Coventry, West Midlands",
                "area": ["UK", "West Midlands", "Coventry"],
            },
            "latitude": 52.4066,
            "longitude": -1.5122,
            "salary_min": 28000,
            "salary_max": 32000,
            "salary_is_predicted": "0",
            "contract_type": "permanent",
            "contract_time": "full_time",
            "category": {"label": "IT Jobs", "tag": "it-jobs"},
        },
        {"id": "999", "title": "", "redirect_url": "https://www.adzuna.co.uk/jobs/land/ad/999"},
    ],
}

REED_ANSWER = {
    "results": [
        {
            "jobId": 56789012,
            "employerId": 1234,
            "employerName": "Northgate Insights",
            "employerProfileId": None,
            "employerProfileName": None,
            "jobTitle": "Junior Data Scientist",
            "locationName": "Bishop's Stortford",
            "minimumSalary": 30000.0,
            "maximumSalary": 34000.0,
            "currency": "GBP",
            "expirationDate": "23/11/2026",
            "date": "12/10/2026",
            "jobDescription": "Hybrid: 2 days in Bishop&#39;s Stortford. Python and statistics...",
            "applications": 4,
            "jobUrl": "https://www.reed.co.uk/jobs/junior-data-scientist/56789012",
        }
    ],
    "ambiguousLocations": [],
    "totalResults": 1,
}

REMOTIVE_ANSWER = {
    "0-legal-notice": "Remotive API Legal Notice",
    "job-count": 3,
    "jobs": [
        {
            "id": 111,
            "url": "https://remotive.com/remote-jobs/data/data-analyst-111",
            "title": "Data Analyst",
            "company_name": "Remote Co",
            "category": "Data Analysis",
            "tags": ["sql", "python"],
            "job_type": "full_time",
            "publication_date": "2026-10-09T10:00:00",
            "candidate_required_location": "UK, Europe",
            "salary": "£35k - £45k",
            "description": "<p>We need someone strong in <b>SQL</b> and Python.</p>",
        },
        {
            "id": 112,
            "url": "https://remotive.com/remote-jobs/data/analytics-engineer-112",
            "title": "Analytics Engineer",
            "company_name": "US Only Inc",
            "job_type": "contract",
            "publication_date": "2026-10-08T10:00:00",
            "candidate_required_location": "USA",
            "salary": "",
            "description": "<p>dbt and Looker.</p>",
        },
        {"id": 113, "url": "", "title": "No link"},
    ],
}

JOBICY_ANSWER = {
    "apiVersion": "2",
    "jobCount": 2,
    "jobs": [
        {
            "id": 201,
            "url": "https://jobicy.com/jobs/201-junior-data-analyst",
            "jobTitle": "Junior Data Analyst",
            "companyName": "Jobs Ltd",
            "jobIndustry": ["Data Science & Analytics"],
            "jobType": ["full-time"],
            "jobGeo": "UK",
            "jobLevel": "Entry",
            "jobExcerpt": "Short summary",
            "jobDescription": "<p>Use Excel and SQL.</p>",
            "pubDate": "2026-10-11 08:00:00",
            "salaryMin": 30000,
            "salaryMax": 38000,
            "salaryCurrency": "GBP",
            "salaryPeriod": "yearly",
        },
        {
            "id": 202,
            "url": "https://jobicy.com/jobs/202-data-scientist",
            "jobTitle": "Data Scientist",
            "companyName": "Old Format Co",
            "jobType": ["contract"],
            "jobGeo": "Anywhere",
            "jobLevel": "Midweight",
            "jobExcerpt": "Excerpt only",
            "pubDate": "2026-10-10 08:00:00",
            "annualSalaryMin": "70000",
            "annualSalaryMax": "90000",
            "salaryCurrency": "USD",
        },
    ],
}

HIMALAYAS_ANSWER = {
    "offset": 0,
    "limit": 20,
    "totalCount": 1,
    "jobs": [
        {
            "title": "Junior Data Analyst",
            "excerpt": "Help us understand our customers",
            "companyName": "Acme",
            "companySlug": "acme",
            "employmentType": "Full Time",
            "minSalary": 32000,
            "maxSalary": 40000,
            "salaryPeriod": "year",
            "seniority": ["Entry-level"],
            "currency": "GBP",
            "locationRestrictions": [
                {"alpha2": "GB", "name": "United Kingdom", "slug": "united-kingdom"},
                {"alpha2": "IE", "name": "Ireland", "slug": "ireland"},
            ],
            "description": "<p>Work with <em>Python</em> and statistics.</p>",
            "pubDate": int(datetime(2026, 10, 13, 9, tzinfo=UTC).timestamp() * 1000),
            "applicationLink": "https://himalayas.app/companies/acme/jobs/junior-data-analyst",
            "guid": "https://himalayas.app/companies/acme/jobs/junior-data-analyst",
        }
    ],
}


def one_search(settings):
    """Settings with a single near-home search phrase and no remote ones."""
    return dataclasses.replace(settings, local_terms=["data analyst"], remote_terms=[])


def test_adzuna_builds_local_and_uk_wide_remote_searches(settings, fake_client):
    searches = AdzunaSource(settings, fake_client([])).build_requests()
    assert len(searches) == len(settings.local_terms) + len(settings.remote_terms)
    local, remote = searches[0], searches[-1]
    assert local.scope == SCOPE_LOCAL
    assert local.params["what"] == "data scientist"
    assert (local.params["where"], local.params["distance"]) == ("Birmingham", 55)
    assert local.params["app_id"] == "test-app-id"
    assert remote.scope == SCOPE_UK_REMOTE
    assert remote.params["what"] == "machine learning remote"
    assert "where" not in remote.params


def test_adzuna_reads_results_and_marks_keys_as_secret(settings, fake_client):
    client = fake_client([ADZUNA_ANSWER])
    result = AdzunaSource(one_search(settings), client).run()
    assert client.calls[0]["secret_params"] == ("app_id", "app_key")
    assert result.searched and result.status == "1 listing"
    job = result.jobs[0]
    assert job.title == "Graduate Data Analyst"
    assert job.company == "Midland Analytics Ltd"
    assert job.location == "Coventry, West Midlands"
    assert job.posted == date(2026, 10, 10)
    assert job.salary == "£28,000 – £32,000"
    assert job.contract == "Permanent, Full time"
    assert (job.latitude, job.longitude) == (52.4066, -1.5122)
    assert job.description.startswith("Join our team & use Python")
    assert job.scope == SCOPE_LOCAL


def test_reed_uses_basic_auth_and_a_radius_in_miles(settings, fake_client):
    searches = ReedSource(settings, fake_client([])).build_requests()
    assert searches[0].auth == ("test-reed-key", "")
    assert searches[0].params["locationName"] == "Birmingham"
    assert searches[0].params["distanceFromLocation"] == 35  # 55 km, rounded up
    assert "locationName" not in searches[-1].params


def test_reed_reads_results(settings, fake_client):
    result = ReedSource(one_search(settings), fake_client([REED_ANSWER])).run()
    job = result.jobs[0]
    assert job.title == "Junior Data Scientist"
    assert job.company == "Northgate Insights"
    assert job.location == "Bishop's Stortford"
    assert job.posted == date(2026, 10, 12)
    assert job.salary == "£30,000 – £34,000"
    assert job.url == "https://www.reed.co.uk/jobs/junior-data-scientist/56789012"
    assert "Bishop's Stortford" in job.description
    assert job.excerpt_only  # search results only carry the start of each advert


def test_reed_fetches_full_adverts_for_claudes_check(settings, fake_client):
    full = "<p>" + "The full advert, with every requirement listed. " * 5 + "</p>"
    client = fake_client([{"jobDescription": full}])
    reed = ReedSource(settings, client)
    snippet = reed.parse_item(REED_ANSWER["results"][0], SCOPE_LOCAL)
    other_site = dataclasses.replace(snippet, source="Adzuna")
    assert reed.add_full_descriptions([snippet, other_site]) == 1
    assert len(client.calls) == 1  # the other site's advert is left alone
    assert client.calls[0]["url"] == "https://www.reed.co.uk/api/1.0/jobs/56789012"
    assert client.calls[0]["auth"] == ("test-reed-key", "")
    assert snippet.description == html_to_text(full)
    assert not snippet.excerpt_only


def test_remotive_reads_jobs(settings, fake_client):
    result = RemotiveSource(settings, fake_client([REMOTIVE_ANSWER])).run()
    assert [job.title for job in result.jobs] == ["Data Analyst", "Analytics Engineer"]
    job = result.jobs[0]
    assert job.scope == SCOPE_REMOTE_BOARD
    assert job.remote_regions == "UK, Europe"
    assert job.location == "Remote (UK, Europe)"
    assert job.contract == "Full time"
    assert job.posted == date(2026, 10, 9)
    assert job.description == "We need someone strong in SQL and Python."


def test_jobicy_reads_both_salary_field_styles(settings, fake_client):
    first, second = JobicySource(settings, fake_client([JOBICY_ANSWER])).run().jobs
    assert first.salary == "£30,000 – £38,000 per year"
    assert (first.level, first.contract, first.remote_regions) == ("Entry", "Full time", "UK")
    assert second.salary == "$70,000 – $90,000 per year"
    assert second.description == "Excerpt only"


def test_himalayas_reads_jobs(settings, fake_client):
    client = fake_client([HIMALAYAS_ANSWER])
    settings = dataclasses.replace(settings, remote_terms=["data analyst"])
    result = HimalayasSource(settings, client).run()
    assert client.calls[0]["params"]["country"] == "GB"
    job = result.jobs[0]
    assert job.posted == date(2026, 10, 13)
    assert job.remote_regions == "United Kingdom, Ireland"
    assert job.location == "Remote (United Kingdom, Ireland)"
    assert job.level == "Entry-level"
    assert job.salary == "£32,000 – £40,000 per year"
    assert job.url == "https://himalayas.app/companies/acme/jobs/junior-data-analyst"


def test_source_without_keys_is_skipped_without_any_request(settings, fake_client):
    client = fake_client([])
    result = AdzunaSource(dataclasses.replace(settings, api_keys={}), client).run()
    assert not result.searched
    assert result.status.startswith("skipped: add ADZUNA_APP_ID and ADZUNA_APP_KEY to .env")
    assert client.calls == []


def test_each_search_reports_progress(settings, fake_client, caplog):
    two = dataclasses.replace(settings, local_terms=["a", "b"], remote_terms=[])
    client = fake_client([ADZUNA_ANSWER, ADZUNA_ANSWER])
    with caplog.at_level(logging.INFO, logger="job_finder"):
        AdzunaSource(two, client).run()
    progress = [r.getMessage() for r in caplog.records if getattr(r, "progress", False)]
    assert progress == ["  search 1 of 2", "  search 2 of 2"]


def test_refusal_stops_the_remaining_searches(settings, fake_client):
    two = dataclasses.replace(settings, local_terms=["a", "b"], remote_terms=[])
    client = fake_client([StopSource("api.adzuna.com asked for fewer requests (HTTP 429)")])
    result = AdzunaSource(two, client).run()
    assert len(client.calls) == 1
    assert "stopped early" in result.status


def test_one_failed_search_does_not_stop_the_others(settings, fake_client):
    two = dataclasses.replace(settings, local_terms=["a", "b"], remote_terms=[])
    client = fake_client([FetchError("could not reach api.adzuna.com (Timeout)"), ADZUNA_ANSWER])
    result = AdzunaSource(two, client).run()
    assert len(client.calls) == 2
    assert len(result.jobs) == 1
    assert "1 of 2 searches failed" in result.status


def test_unexpected_answers_do_not_crash(settings, fake_client):
    odd = {"jobs": ["not a dict", {"title": None}, {"title": "Data Analyst", "url": "ftp://x"}]}
    assert RemotiveSource(settings, fake_client([odd])).run().jobs == []
    assert RemotiveSource(settings, fake_client([])).extract_items(None) == []
    assert RemotiveSource(settings, fake_client([])).extract_items({"jobs": "oops"}) == []
