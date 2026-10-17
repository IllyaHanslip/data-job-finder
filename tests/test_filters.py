"""Filtering, labelling, de-duplication and ordering of jobs."""

from __future__ import annotations

import dataclasses
from datetime import date

from job_finder.config import Home
from job_finder.filters import (
    REASON_EXCLUDED,
    REASON_NOT_UK,
    REASON_OFF_TOPIC,
    REASON_SENIOR,
    REASON_TOO_OLD,
    TAG_ENTRY,
    TAG_NEEDS_EXPERIENCE,
    TAG_SENIOR,
    TAG_TEACHING,
    WORK_HYBRID,
    WORK_NOT_STATED,
    WORK_ON_SITE,
    WORK_REMOTE,
    KeywordSet,
    apply_claude_verdicts,
    detect_work_mode,
    open_to_uk,
    process_jobs,
)
from job_finder.models import (
    SCOPE_LOCAL,
    SCOPE_REMOTE_BOARD,
    SCOPE_UK_REMOTE,
    SECTION_CHECK,
    SECTION_LOCAL,
    SECTION_REMOTE,
    VERDICT_ENTRY,
    VERDICT_NEEDS_EXPERIENCE,
    Job,
)

TODAY = date(2026, 10, 14)


def make_job(**changes) -> Job:
    fields = {
        "source": "Adzuna",
        "source_id": "1",
        "title": "Data Analyst",
        "company": "Acme",
        "location": "Coventry",
        "url": "https://example.com/1",
        "posted": date(2026, 10, 12),
        "scope": SCOPE_LOCAL,
    }
    fields.update(changes)
    return Job(**fields)


def titles(jobs):
    return [job.title for job in jobs]


def test_keyword_set_wildcards_whole_words_and_hyphens():
    words = KeywordSet(["statistic*", "ai", "entry level", "scikit-learn"])
    assert words.search("Trainee Statistician")
    assert words.search("Head of AI")
    assert not words.search("Maintenance Engineer")  # "ai" inside a longer word
    assert words.search("An entry-level role")
    assert words.matches("We use scikit learn and statistical tests") == [
        "statistical",
        "scikit learn",
    ]


def test_detect_work_mode():
    assert detect_work_mode("Fully remote role, UK based") == WORK_REMOTE
    assert detect_work_mode("Home-based role covering the UK") == WORK_REMOTE
    assert detect_work_mode("Hybrid: 2 days a week in Cambridge, otherwise remote") == WORK_HYBRID
    assert detect_work_mode("This is an office-based position") == WORK_ON_SITE
    assert detect_work_mode("Remote sensing analyst in our Leeds office") == WORK_NOT_STATED


def test_open_to_uk():
    allowed = ["Worldwide", "UK", "Europe, UK", "EMEA", "Anywhere", "United Kingdom, Ireland",
               "CET +/- 3 hours", ""]
    blocked = ["USA", "Canada", "Ukraine", "LATAM", "USA only"]
    assert all(open_to_uk(text) for text in allowed)
    assert not any(open_to_uk(text) for text in blocked)


def test_unsuitable_jobs_are_removed(settings):
    jobs = [
        make_job(title="Graduate Data Analyst", source_id="keep"),
        make_job(title="Senior Data Scientist", source_id="senior"),
        make_job(title="Warehouse Operative", source_id="off-topic"),
        make_job(title="SOC Analyst", source_id="excluded"),
        make_job(title="Data Analyst", company="Old Co", posted=date(2026, 8, 1)),
        make_job(title="Data Analyst", company="US Co", scope=SCOPE_REMOTE_BOARD,
                 remote_regions="USA"),
    ]
    kept, summary = process_jobs(jobs, settings, TODAY)
    assert titles(kept) == ["Graduate Data Analyst"]
    assert (summary.received, summary.kept) == (6, 1)
    assert summary.removed == {
        REASON_SENIOR: 1,
        REASON_OFF_TOPIC: 1,
        REASON_EXCLUDED: 1,
        REASON_TOO_OLD: 1,
        REASON_NOT_UK: 1,
    }


def test_senior_roles_can_be_included(settings):
    with_senior = dataclasses.replace(settings, include_senior=True)
    kept, _ = process_jobs([make_job(title="Lead Data Scientist")], with_senior, TODAY)
    assert kept[0].tags == [TAG_SENIOR]


def test_duplicates_across_sites_are_merged(settings):
    adzuna = make_job(source="Adzuna", company="Acme Ltd", scope=SCOPE_UK_REMOTE,
                      description="short")
    reed = make_job(source="Reed", source_id="2", company="ACME Limited", scope=SCOPE_LOCAL,
                    description="a much longer description")
    kept, summary = process_jobs([adzuna, reed], settings, TODAY)
    assert len(kept) == 1 and summary.duplicates == 1
    assert kept[0].also_on == ["Reed"]
    assert kept[0].scope == SCOPE_LOCAL
    assert kept[0].description == "a much longer description"


def test_merged_duplicate_keeps_the_other_sites_own_link(settings):
    himalayas = make_job(source="Himalayas", url="https://himalayas.app/apply/1",
                         company="Acme Ltd", scope=SCOPE_REMOTE_BOARD,
                         remote_regions="UK", location="Remote (UK)")
    remotive = make_job(source="Remotive", source_id="2", url="https://remotive.com/job/2",
                        company="Acme Limited", scope=SCOPE_REMOTE_BOARD,
                        remote_regions="UK", location="Remote (UK)")
    kept, _ = process_jobs([himalayas, remotive], settings, TODAY)
    assert len(kept) == 1
    assert kept[0].url == "https://himalayas.app/apply/1"
    assert kept[0].also_urls == {"Remotive": "https://remotive.com/job/2"}


def test_jobs_are_grouped_into_sections(settings):
    jobs = [
        make_job(title="Data Scientist", company="Remote Co", scope=SCOPE_REMOTE_BOARD,
                 remote_regions="UK", location="Remote (UK)"),
        make_job(title="Data Analyst", company="Hub Co", location="Coventry",
                 description="Hybrid working from our office"),
        make_job(title="Insight Analyst", company="City Co", scope=SCOPE_UK_REMOTE,
                 location="London", description="Hybrid, remote 3 days a week"),
        make_job(title="Statistician", company="County Co", location="Warwickshire"),
        make_job(title="Machine Learning Engineer", company="Home Co", scope=SCOPE_UK_REMOTE,
                 location="Leeds", description="Fully remote"),
    ]
    kept, _ = process_jobs(jobs, settings, TODAY)
    assert {job.title: job.section for job in kept} == {
        "Data Scientist": SECTION_REMOTE,
        "Machine Learning Engineer": SECTION_REMOTE,
        "Data Analyst": SECTION_LOCAL,
        "Statistician": SECTION_LOCAL,
        "Insight Analyst": SECTION_CHECK,
    }
    coventry = next(job for job in kept if job.title == "Data Analyst")
    assert coventry.work_mode == WORK_HYBRID
    assert 24 < coventry.distance_km < 30
    assert coventry.drive_minutes is not None and coventry.drive_minutes < 40


def test_a_far_away_namesake_does_not_move_a_local_job(settings):
    # Reed gives no coordinates. Its radius search only finds jobs near home, so a "Truro"
    # (over 300 km from home) from that search must be another place, and its distance is
    # ignored; the same name from a UK-wide search is taken at face value.
    local = make_job(title="Data Analyst", company="Hub Co", source="Reed", location="Truro")
    uk_wide = make_job(title="Statistician", company="Kernow Co", source="Reed",
                       scope=SCOPE_UK_REMOTE, location="Truro")
    kept, _ = process_jobs([local, uk_wide], settings, TODAY)
    by_title = {job.title: job for job in kept}
    assert by_title["Data Analyst"].distance_km is None
    assert by_title["Data Analyst"].section == SECTION_LOCAL
    assert by_title["Statistician"].distance_km > 300


def test_a_local_job_in_a_home_town_missing_from_the_list_is_at_home(settings):
    village = Home(name="Upper Slaughter", latitude=51.9036, longitude=-1.7720, radius_km=55.0)
    at_home = dataclasses.replace(settings, home=village)
    local = make_job(location="Upper Slaughter, Gloucestershire")
    uk_wide = make_job(title="Statistician", company="Wold Co", scope=SCOPE_UK_REMOTE,
                       location="Upper Slaughter, Gloucestershire")
    kept, _ = process_jobs([local, uk_wide], at_home, TODAY)
    by_title = {job.title: job for job in kept}
    assert by_title["Data Analyst"].distance_km == 0
    assert by_title["Statistician"].distance_km is None  # a UK-wide search could mean anywhere


def test_claudes_verdict_overrules_the_title(settings):
    junior = make_job(title="Junior Data Analyst", company="Acme")
    plain = make_job(title="Data Analyst", company="Brook Co")
    kept, _ = process_jobs([junior, plain], settings, TODAY)
    by_title = {job.title: job for job in kept}
    assert TAG_ENTRY in by_title["Junior Data Analyst"].tags  # from the title alone
    by_title["Junior Data Analyst"].claude_verdict = VERDICT_NEEDS_EXPERIENCE
    by_title["Data Analyst"].claude_verdict = VERDICT_ENTRY
    apply_claude_verdicts(kept, TODAY)
    assert TAG_ENTRY not in by_title["Junior Data Analyst"].tags
    assert TAG_NEEDS_EXPERIENCE in by_title["Junior Data Analyst"].tags
    assert TAG_ENTRY in by_title["Data Analyst"].tags
    assert [job.title for job in kept] == ["Data Analyst", "Junior Data Analyst"]


def test_entry_level_and_cv_matches_rank_higher(settings):
    weaker = make_job(title="Data Analyst", company="A", description="Reporting in Excel")
    stronger = make_job(title="Graduate Data Analyst", company="B",
                        description="Python, pandas, statistics and machine learning")
    kept, _ = process_jobs([weaker, stronger], settings, TODAY)
    assert titles(kept) == ["Graduate Data Analyst", "Data Analyst"]
    assert TAG_ENTRY in kept[0].tags
    assert kept[0].matched_skills == ["python", "pandas", "machine learning", "statistics"]


def test_teaching_roles_are_tagged(settings):
    kept, _ = process_jobs([make_job(title="Maths Teacher")], settings, TODAY)
    assert TAG_TEACHING in kept[0].tags


def test_teaching_roles_rank_below_similar_data_roles(settings):
    tutor = make_job(title="A-Level Maths Tutor (Graduate)", company="Tutors")
    analyst = make_job(title="Graduate Insight Analyst", company="Insight Co")
    kept, _ = process_jobs([tutor, analyst], settings, TODAY)
    assert titles(kept) == ["Graduate Insight Analyst", "A-Level Maths Tutor (Graduate)"]
