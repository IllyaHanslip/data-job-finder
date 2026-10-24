"""Claude's entry-level check and its evaluation, with a fake Claude (no network, no cost)."""

from __future__ import annotations

import csv
import dataclasses
import json
from datetime import date
from types import SimpleNamespace

import pytest

from job_finder.entry_check import EntryLevelChecker, advert_text, check_jobs
from job_finder.entry_eval import (
    job_from_case,
    load_cases,
    normalise_label,
    pick_adverts,
    render_report,
    score,
    wilson,
    write_label_sheet,
)
from job_finder.filters import TAG_ENTRY
from job_finder.http_client import PoliteClient
from job_finder.models import VERDICT_ENTRY, VERDICT_NEEDS_EXPERIENCE, VERDICT_UNCLEAR, Job
from job_finder.review import ReviewError, apply_review, import_verdicts, write_review_list

TODAY = date(2026, 10, 17)
NEEDS_THREE_YEARS = {"verdict": "needs_experience", "years_required": 3,
                     "evidence": "3+ years of commercial SQL experience"}


def advert(**changes) -> Job:
    fields = {
        "source": "Himalayas",
        "source_id": "1",
        "title": "Junior Data Analyst",
        "company": "Acme",
        "location": "Remote (UK)",
        "url": "https://example.com/1",
        "description": "We need 3+ years of commercial SQL experience.",
        "tags": [TAG_ENTRY],
    }
    fields.update(changes)
    return Job(**fields)


class FakeClaude:
    """Stands in for anthropic.Anthropic: answers by advert title and records each request."""

    def __init__(self, answers):
        self.answers = answers
        self.requests = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **request):
        self.requests.append(request)
        sent = request["messages"][0]["content"]
        answer = next(a for title, a in self.answers.items() if f"Title: {title}\n" in sent)
        usage = SimpleNamespace(input_tokens=1000, output_tokens=200)
        if isinstance(answer, str):  # a stop reason with no usable answer
            return SimpleNamespace(stop_reason=answer, content=[], model=request["model"],
                                   usage=usage)
        blocks = [SimpleNamespace(type="thinking", thinking=""),
                  SimpleNamespace(type="text", text=json.dumps(answer))]
        return SimpleNamespace(stop_reason="end_turn", content=blocks, model=request["model"],
                               usage=usage)


@pytest.fixture
def claude_settings(settings):
    return dataclasses.replace(settings, check_entry_level=True, max_claude_checks=200)


def checker(settings, client, tmp_path, **options):
    return EntryLevelChecker(settings, client, tmp_path / "answers.json", TODAY, **options)


def test_claude_gets_the_advert_and_its_answer_is_recorded(claude_settings, tmp_path):
    job = advert()
    client = FakeClaude({"Junior Data Analyst": NEEDS_THREE_YEARS})
    status = checker(claude_settings, client, tmp_path).check([job])
    assert (job.claude_verdict, job.claude_years) == (VERDICT_NEEDS_EXPERIENCE, 3)
    assert job.claude_evidence == "3+ years of commercial SQL experience"
    request = client.requests[0]
    assert request["model"] == "claude-opus-5-5"
    assert request["output_config"]["effort"] == "low"
    assert request["output_config"]["format"]["type"] == "json_schema"
    assert request["fallbacks"] == "default"
    assert "3+ years of commercial SQL" in request["messages"][0]["content"]
    assert status == "1 new advert checked for about $0.01, 0 from saved answers"


def test_saved_answers_are_reused_until_the_advert_changes(claude_settings, tmp_path):
    first = FakeClaude({"Junior Data Analyst": NEEDS_THREE_YEARS})
    checker(claude_settings, first, tmp_path).check([advert()])
    again, unchanged = FakeClaude({}), advert()
    status = checker(claude_settings, again, tmp_path).check([unchanged])
    assert again.requests == [] and unchanged.claude_verdict == VERDICT_NEEDS_EXPERIENCE
    assert status.endswith("1 from saved answers")
    edited = FakeClaude({"Junior Data Analyst": NEEDS_THREE_YEARS})
    checker(claude_settings, edited, tmp_path).check([advert(description="Now 2 years.")])
    assert len(edited.requests) == 1


def test_declined_or_cut_off_answers_are_reported_not_saved(claude_settings, tmp_path):
    jobs = [advert(title="Data Analyst A"), advert(title="Data Analyst B")]
    client = FakeClaude({"Data Analyst A": "refusal", "Data Analyst B": "max_tokens"})
    status = checker(claude_settings, client, tmp_path).check(jobs)
    assert [job.claude_verdict for job in jobs] == ["", ""]
    assert "2 couldn't be checked" in status


def test_a_run_checks_no_more_adverts_than_its_limit(claude_settings, tmp_path):
    limited = dataclasses.replace(claude_settings, max_claude_checks=1)
    client = FakeClaude({"Data Analyst A": NEEDS_THREE_YEARS, "Data Analyst B": NEEDS_THREE_YEARS})
    jobs = [advert(title="Data Analyst A"), advert(title="Data Analyst B")]
    status = checker(limited, client, tmp_path).check(jobs)
    assert len(client.requests) == 1
    assert "1 left for the next run" in status


def test_offline_runs_only_use_saved_answers(claude_settings, tmp_path):
    client = FakeClaude({})
    status = checker(claude_settings, client, tmp_path, offline=True).check([advert()])
    assert client.requests == []
    assert "1 left for the next run (offline)" in status


def test_claude_is_told_when_it_only_sees_the_start_of_an_advert():
    assert "only provides the start" in advert_text(advert(excerpt_only=True))
    assert "only provides the start" not in advert_text(advert())


def test_without_a_key_only_saved_verdicts_are_used(claude_settings):
    client = PoliteClient(claude_settings.cache_dir, "test-agent", offline=True)
    keyless = dataclasses.replace(claude_settings, api_keys={})
    status = check_jobs([advert()], keyless, client, TODAY)
    assert status.startswith("0 judged earlier, 1 not judged yet")
    off = dataclasses.replace(claude_settings, check_entry_level=False)
    assert check_jobs([advert()], off, client, TODAY) == ""


def review_round(settings, tmp_path, jobs, written):
    """Export a review list for these jobs, then import what Claude Code `written`."""
    saved, notes, folder = tmp_path / "answers.json", tmp_path / "notes.json", tmp_path / "review"
    reviewer = EntryLevelChecker(settings, None, saved, TODAY)
    listed = write_review_list(jobs, reviewer, folder, notes)
    (folder / "verdicts.json").write_text(json.dumps(written), encoding="utf-8")
    return listed, (folder / "to_review.md").read_text(encoding="utf-8"), import_verdicts(
        folder, saved, notes, TODAY)


def test_adverts_judged_in_claude_code_count_like_api_answers(claude_settings, tmp_path):
    saved, folder = tmp_path / "answers.json", tmp_path / "review"
    jobs = [advert(title="Data Analyst A"), advert(title="Data Analyst B")]
    verdicts = {"R1": {"verdict": "needs_experience", "years": 2, "evidence": "2 years"},
                "R2": {"verdict": "maybe"}, "R9": {"verdict": "entry_level"}}
    listed, review_list, result = review_round(claude_settings, tmp_path, jobs, verdicts)
    assert listed == (2, 0) and "## R2" in review_list
    assert result.verdicts == 1 and len(result.problems) == 2
    assert not (folder / "verdicts.json").exists()  # set aside once imported
    with pytest.raises(ReviewError):
        import_verdicts(folder, saved, tmp_path / "notes.json", TODAY)  # nothing left
    later = [advert(title="Data Analyst A"), advert(title="Data Analyst B")]
    status = EntryLevelChecker(claude_settings, None, saved, TODAY).check(later)
    assert (later[0].claude_verdict, later[0].claude_years) == (VERDICT_NEEDS_EXPERIENCE, 2)
    assert later[1].claude_verdict == ""
    assert status.startswith("1 judged earlier, 1 not judged yet")


def test_a_review_leaves_picks_notes_and_a_summary(claude_settings, tmp_path):
    def graduate_job():
        return advert(title="Graduate Analyst", description="Graduates welcome.")

    review_round(claude_settings, tmp_path, [graduate_job()],
                 {"R1": {"verdict": "entry_level", "years": 0, "evidence": "Graduates welcome"}})
    jobs = [graduate_job(), advert(title="Data Analyst B")]
    written = {"summary": ["Few graduate jobs today", " "],
               "R1": {"verdict": "needs_experience", "years": 3, "note": "Fees apply"},
               "C1": {"pick": "Remote, and welcomes graduates"}}
    listed, review_list, result = review_round(claude_settings, tmp_path, jobs, written)
    assert listed == (1, 1)  # B is new; the graduate job was judged entry level before
    assert "## C1" in review_list and "Graduates welcome" in review_list
    assert (result.verdicts, result.picks, result.notes, result.summary) == (1, 1, 1, True)

    later = [graduate_job(), advert(title="Data Analyst B")]
    review = apply_review(later, tmp_path / "notes.json")
    assert (review.written, review.summary) == (TODAY, ["Few graduate jobs today"])
    assert later[0].claude_pick == "Remote, and welcomes graduates"
    assert later[1].claude_note == "Fees apply"

    def snippet_job():  # no entry-level badge, and judged "can't tell" below
        return advert(title="Statistician", description="Only a snippet.", tags=[])

    review_round(claude_settings, tmp_path, [snippet_job()],
                 {"R1": {"verdict": "unclear", "pick": "Near home"}})
    listed, review_list, _ = review_round(claude_settings, tmp_path,
                                          [snippet_job(), graduate_job()], {})
    assert listed == (0, 2)  # a picked job is listed for correction whatever its verdict
    assert "Current pick: Near home" in review_list

    _, review_list, _ = review_round(claude_settings, tmp_path, later, {"C1": {"pick": ""}})
    assert "Current pick: Remote, and welcomes graduates" in review_list
    assert "Few graduate jobs today" in review_list  # the summary, to keep or replace
    apply_review(later, tmp_path / "notes.json")
    assert later[0].claude_pick == "" and later[1].claude_note == "Fees apply"


def test_labels_are_read_generously():
    assert normalise_label(" Entry ") == VERDICT_ENTRY
    assert normalise_label("entry-level") == VERDICT_ENTRY
    assert normalise_label("Experienced") == VERDICT_NEEDS_EXPERIENCE
    assert normalise_label("?") == VERDICT_UNCLEAR
    assert normalise_label("maybe") is None


def test_scores_count_agreement_and_catches():
    rows = [
        {"label": VERDICT_NEEDS_EXPERIENCE, "verdict": VERDICT_NEEDS_EXPERIENCE},
        {"label": VERDICT_NEEDS_EXPERIENCE, "verdict": VERDICT_UNCLEAR},
        {"label": VERDICT_ENTRY, "verdict": VERDICT_ENTRY},
        {"label": VERDICT_ENTRY, "verdict": VERDICT_NEEDS_EXPERIENCE},
    ]
    measures = score(rows)
    assert measures["agreement"] == (2, 4)
    assert measures["recall"] == (1, 2)
    assert measures["precision"] == (1, 2)
    assert measures["specificity"] == (1, 2)
    assert measures["unclear"] == (1, 4)
    low, high = wilson(2, 4)
    assert 0 < low < 0.5 < high < 1


def test_label_sheet_round_trip(tmp_path):
    sources = ["Reed", "Adzuna", "Himalayas"] * 3
    jobs = [advert(title=f"Data Analyst {n}", source=source, tags=[TAG_ENTRY] if n % 2 else [])
            for n, source in enumerate(sources)]
    picked = pick_adverts(jobs, 4)
    assert picked == pick_adverts(jobs, 4)  # the same pick every time
    assert len({job.source for job in picked}) >= 2
    write_label_sheet(picked, tmp_path)
    sheet = tmp_path / "labels.csv"
    with sheet.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    rows[0]["your_label"], rows[1]["your_label"] = "experienced", "nonsense"
    with sheet.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    cases, problems = load_cases(tmp_path)
    assert [case["label"] for case in cases] == [VERDICT_NEEDS_EXPERIENCE]
    assert len(problems) == 1 and "nonsense" in problems[0]
    assert job_from_case(cases[0]).description == picked[0].description


def test_report_lists_each_disagreement():
    rows = [
        {"label": VERDICT_ENTRY, "verdict": VERDICT_NEEDS_EXPERIENCE, "years": 2,
         "evidence": "at least 2 years", "title": "Junior Analyst", "company": "Acme",
         "source": "Reed", "url": "https://example.com/1", "excerpt_only": False},
        {"label": VERDICT_ENTRY, "verdict": VERDICT_ENTRY, "years": 0,
         "evidence": "Graduate scheme", "title": "Graduate Analyst", "company": "Acme",
         "source": "Adzuna", "url": "https://example.com/2", "excerpt_only": True},
    ]
    rows[0]["model"], rows[1]["model"] = "claude-code-review", "claude-opus-5-5"
    report = render_report(rows, [], 0.02)
    assert "Claude agreed with you on 1 of 2 adverts (50%" in report
    assert ("Answers from a review in a Claude Code chat (not the API check), claude-opus-5-5"
            in report)  # says which route produced the verdicts being measured
    disagreements = report.split("## Where Claude disagreed with you")[1]
    assert "### Junior Analyst (Acme, Reed)" in disagreements
    assert "Graduate Analyst" not in disagreements
