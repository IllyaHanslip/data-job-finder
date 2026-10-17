"""Report files, settings loading and complete runs, using fake sources (no network)."""

from __future__ import annotations

import csv
import dataclasses
import html
import io
import logging
import os
from datetime import datetime, timedelta

import pytest

from job_finder import cli, config
from job_finder.config import DEFAULT_CONFIG_PATH, ConfigError, load_settings
from job_finder.filters import TAG_NEEDS_EXPERIENCE, FilterSummary, job_key
from job_finder.http_client import PoliteClient, Politeness
from job_finder.models import SCOPE_LOCAL, SECTION_LOCAL, VERDICT_NEEDS_EXPERIENCE, Job
from job_finder.report import _write_with_fallback, write_reports
from job_finder.sources.base import JobSource, SourceResult

NOW = datetime(2026, 10, 14, 9, 30)


def sample_job(**changes) -> Job:
    fields = {
        "source": "Reed",
        "source_id": "7",
        "title": "Graduate Data Analyst",
        "company": "Hub Co",
        "location": "Coventry",
        "url": "https://www.reed.co.uk/jobs/7",
        "description": "Python and statistics. " * 40,
        "posted": NOW.date(),
        "scope": SCOPE_LOCAL,
        "section": SECTION_LOCAL,
    }
    fields.update(changes)
    return Job(**fields)


class FakeSource(JobSource):
    key = "fake"
    name = "Fake board"
    homepage = "https://example.com"
    covers_home_area = True
    politeness = Politeness(min_interval=0, cache_hours=1, daily_budget=1)

    def run(self):
        jobs = [sample_job(), sample_job(source_id="8", title="Head of Data Science")]
        return SourceResult(self.name, self.homepage, jobs=jobs, status="2 listings",
                            searched=True, covers_home_area=True)


class BrokenSource(JobSource):
    key = "broken"
    name = "Broken board"
    homepage = "https://example.org"
    politeness = Politeness(min_interval=0, cache_hours=1, daily_budget=1)

    def run(self):
        raise RuntimeError("the API changed its format")


def offline_client(settings):
    return PoliteClient(settings.cache_dir, "test-agent", offline=True)


def test_report_escapes_untrusted_text(settings):
    nasty = sample_job(
        title='<script>alert("x")</script>',
        company='=HYPERLINK("http://evil.example")',
        url="javascript:alert(1)",
    )
    html_path, csv_path = write_reports([nasty], [], FilterSummary(), settings, NOW)
    page = html_path.read_text(encoding="utf-8")
    assert '<script>alert("x")</script>' not in page
    assert "&lt;script&gt;" in page
    assert "javascript:" not in page
    with csv_path.open(encoding="utf-8-sig", newline="") as handle:
        row = next(csv.DictReader(handle))
    assert row["Company"].startswith("'=")
    assert row["Link"] == ""


def test_cards_carry_what_the_applied_button_needs(settings):
    job = sample_job(title='Data "Quant" Analyst', company="Hub & Co Ltd")
    html_path, _ = write_reports([job], [], FilterSummary(), settings, NOW)
    page = html_path.read_text(encoding="utf-8")
    assert 'class="pill mark-applied"' in page
    assert 'id="filter-applied"' in page and 'id="applied-list"' in page
    assert f'data-key="{html.escape(job_key(job))}"' in page
    assert 'data-title="Data &quot;Quant&quot; Analyst"' in page
    assert 'data-company="Hub &amp; Co Ltd"' in page
    assert 'data-url="https://www.reed.co.uk/jobs/7"' in page
    assert 'id="filter-experienced"' not in page  # only shown when Claude's check ran


def test_card_shows_claudes_check(settings):
    job = sample_job(claude_verdict=VERDICT_NEEDS_EXPERIENCE, claude_years=2,
                     claude_evidence="Minimum 2 years <b>SQL</b>", tags=[TAG_NEEDS_EXPERIENCE])
    html_path, csv_path = write_reports([job], [], FilterSummary(), settings, NOW,
                                        "1 new advert checked")
    page = html_path.read_text(encoding="utf-8")
    assert "Claude&#x27;s check: needs experience (asks for 2+ years)" in page
    assert "&lt;b&gt;SQL&lt;/b&gt;" in page and "<b>SQL</b>" not in page
    assert 'data-needs-exp="1"' in page and 'id="filter-experienced"' in page
    assert "Claude's entry-level check: 1 new advert checked" in page
    with csv_path.open(encoding="utf-8-sig", newline="") as handle:
        row = next(csv.DictReader(handle))
    assert (row["Claude verdict"], row["Experience asked (years)"]) == ("needs experience", "2")


def test_without_an_api_key_the_check_waits_for_a_review(settings):
    turned_on = dataclasses.replace(settings, check_entry_level=True)
    outcome = cli.run(turned_on, offline_client(turned_on), [FakeSource], NOW)
    assert "1 not judged yet" in outcome.claude_status


def test_card_links_every_site_a_job_was_found_on(settings):
    job = sample_job(
        source="Himalayas",
        url="https://himalayas.app/apply/1",
        also_on=["Remotive", "Odd site"],
        also_urls={"Remotive": "https://remotive.com/job/2", "Odd site": "javascript:alert(1)"},
    )
    html_path, _ = write_reports([job], [], FilterSummary(), settings, NOW)
    page = html_path.read_text(encoding="utf-8")
    assert 'href="https://himalayas.app/apply/1"' in page
    assert '<a href="https://remotive.com/job/2"' in page
    assert "javascript:" not in page  # an unsafe link is dropped, the name stays
    assert "Odd site" in page


def test_locked_file_is_saved_under_a_new_name(tmp_path):
    attempts = []

    def write(path):
        attempts.append(path.name)
        if len(attempts) == 1:
            raise PermissionError("open in Excel")
        path.write_text("ok", encoding="utf-8")

    saved = _write_with_fallback(tmp_path / "jobs_2026-10-14.csv", NOW, write)
    assert saved.name == "jobs_2026-10-14_093000.csv"
    assert attempts == ["jobs_2026-10-14.csv", "jobs_2026-10-14_093000.csv"]


def test_run_writes_reports_and_survives_a_broken_source(settings):
    outcome = cli.run(settings, offline_client(settings), [FakeSource, BrokenSource], NOW)
    assert [job.title for job in outcome.jobs] == ["Graduate Data Analyst"]  # Head of... is senior
    assert outcome.sources[1].status == "failed unexpectedly (RuntimeError)"
    page = outcome.html_path.read_text(encoding="utf-8")
    assert "Graduate Data Analyst" in page and "Broken board" in page
    assert outcome.csv_path.exists()


def test_jobs_are_new_only_on_the_day_they_are_first_seen(settings):
    client = offline_client(settings)
    first = cli.run(settings, client, [FakeSource], NOW)
    later_today = cli.run(settings, client, [FakeSource], NOW + timedelta(hours=2))
    tomorrow = cli.run(settings, client, [FakeSource], NOW + timedelta(days=1))
    assert first.jobs[0].is_new and later_today.jobs[0].is_new
    assert not tomorrow.jobs[0].is_new


def test_disabled_source_is_not_called(settings):
    off = dataclasses.replace(settings, sources={"broken": False})
    outcome = cli.run(off, offline_client(off), [BrokenSource], NOW)
    assert outcome.sources[0].status == "turned off in config.toml"


def test_main_runs_end_to_end(settings, monkeypatch, capsys):
    monkeypatch.setattr(cli, "load_settings", lambda path=None: settings)
    monkeypatch.setattr(cli, "ALL_SOURCES", [FakeSource])
    assert cli.main(["--no-browser", "--offline"]) == 0
    printed = capsys.readouterr().out
    assert "Fake board" in printed
    assert "Report:" in printed


def test_report_opens_in_chrome_when_it_is_installed(monkeypatch, tmp_path):
    launched, opened = [], []
    monkeypatch.setattr(cli, "_find_chrome", lambda: tmp_path / "chrome.exe")
    monkeypatch.setattr(cli.subprocess, "Popen", lambda args, **kwargs: launched.append(args))
    monkeypatch.setattr(cli.webbrowser, "open", opened.append)
    report = tmp_path / "jobs.html"
    cli._open_report(report, "chrome")
    assert launched == [[str(tmp_path / "chrome.exe"), report.resolve().as_uri()]]
    assert opened == []


def test_report_falls_back_to_the_default_browser(monkeypatch, tmp_path):
    opened = []
    monkeypatch.setattr(cli, "_find_chrome", lambda: None)
    monkeypatch.setattr(cli.webbrowser, "open", opened.append)
    report = tmp_path / "jobs.html"
    cli._open_report(report, "chrome")
    cli._open_report(report, "default")
    assert opened == [report.resolve().as_uri()] * 2


class FakeConsole(io.StringIO):
    def isatty(self):
        return True


def console_record(message, progress=False):
    record = logging.makeLogRecord({"msg": message, "levelno": logging.INFO,
                                    "levelname": "INFO"})
    if progress:
        record.progress = True
    return record


def test_progress_updates_one_line_in_a_console_window():
    console = FakeConsole()
    handler = cli._ConsoleHandler(console)
    handler.setFormatter(cli._ConsoleFormatter("%(message)s"))
    for record in (console_record("search 1 of 2", progress=True),
                   console_record("search 2 of 2", progress=True),
                   console_record("2 listings")):
        handler.handle(record)
    assert console.getvalue() == (
        "\rsearch 1 of 2\rsearch 2 of 2" + "\r" + " " * 13 + "\r" + "2 listings\n"
    )


def test_progress_is_left_out_when_output_is_not_a_console():
    output = io.StringIO()
    handler = cli._ConsoleHandler(output)
    handler.handle(console_record("search 1 of 2", progress=True))
    handler.handle(console_record("2 listings"))
    assert output.getvalue() == "2 listings\n"


def test_main_explains_a_broken_config_file(tmp_path, capsys):
    bad = tmp_path / "bad.toml"
    bad.write_text("[home\nname = 1", encoding="utf-8")
    assert cli.main(["--config", str(bad), "--no-browser"]) == 2
    assert "not valid TOML" in capsys.readouterr().out


def test_shipped_config_file_is_valid():
    settings = load_settings()
    assert settings.home.name
    assert settings.local_terms and settings.title_keywords


def test_local_settings_file_overrides_the_shared_one(tmp_path, monkeypatch):
    shared = tmp_path / "config.toml"
    shared.write_text(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    local = tmp_path / "config.local.toml"
    local.write_text('[home]\nname = "Coventry"\nlatitude = 52.4066\nlongitude = -1.5122\n',
                     encoding="utf-8")
    monkeypatch.setattr(config, "DEFAULT_CONFIG_PATH", shared)
    monkeypatch.setattr(config, "LOCAL_CONFIG_PATH", local)
    settings = load_settings()
    assert (settings.home.name, settings.home.latitude) == ("Coventry", 52.4066)
    assert settings.home.radius_km == 55  # not in the local file, so taken from config.toml
    assert load_settings(shared).home.name == "Birmingham"  # a file chosen with --config
    # is used exactly as it is


def test_wrong_type_in_config_gives_a_clear_message(tmp_path):
    text = DEFAULT_CONFIG_PATH.read_text(encoding="utf-8")
    path = tmp_path / "config.toml"
    path.write_text(text.replace("max_days_old = 30", 'max_days_old = "30"'), encoding="utf-8")
    with pytest.raises(ConfigError, match="max_days_old in config.toml should be a number"):
        load_settings(path)


def test_browser_setting_is_read_and_checked(tmp_path):
    text = DEFAULT_CONFIG_PATH.read_text(encoding="utf-8")
    path = tmp_path / "config.toml"
    path.write_text(text.replace('browser = "chrome"', ""), encoding="utf-8")
    assert load_settings(path).browser == "default"  # optional: older files still work
    path.write_text(text.replace('browser = "chrome"', 'browser = "netscape"'), encoding="utf-8")
    with pytest.raises(ConfigError, match='browser in config.toml should be "chrome" or'):
        load_settings(path)


def test_claude_settings_are_optional_and_checked(tmp_path):
    text = DEFAULT_CONFIG_PATH.read_text(encoding="utf-8")
    path = tmp_path / "config.toml"
    path.write_text(text[:text.index("[claude]")] + text[text.index("[network]"):],
                    encoding="utf-8")
    assert load_settings(path).check_entry_level is False  # older files keep the check off
    path.write_text(text.replace('effort = "low"', 'effort = "extreme"'), encoding="utf-8")
    with pytest.raises(ConfigError, match="effort in config.toml should be"):
        load_settings(path)


def test_missing_setting_gives_a_clear_message(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[home]\nname = "Birmingham"\n', encoding="utf-8")
    with pytest.raises(ConfigError, match="missing 'latitude'"):
        load_settings(path)


def test_env_file_saved_as_utf16_is_understood(tmp_path):
    name = "JOB_FINDER_TEST_KEY"
    os.environ.pop(name, None)
    env_file = tmp_path / ".env"
    env_file.write_bytes(f"{name}=abc123\n".encode("utf-16"))
    try:
        config._load_env_file(env_file)
        assert os.environ[name] == "abc123"
    finally:
        os.environ.pop(name, None)
