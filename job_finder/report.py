"""Write the results as an HTML page (to read) and a CSV file (for Excel or pandas)."""

from __future__ import annotations

import csv
import html
import logging
from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path

from job_finder.config import Settings
from job_finder.filters import (
    TAG_ENTRY,
    TAG_NEEDS_EXPERIENCE,
    TAG_SENIOR,
    TAG_TEACHING,
    WORK_NOT_STATED,
    FilterSummary,
    job_key,
)
from job_finder.models import (
    SCOPE_REMOTE_BOARD,
    SECTION_CHECK,
    SECTION_LOCAL,
    SECTION_REMOTE,
    VERDICT_ENTRY,
    VERDICT_NEEDS_EXPERIENCE,
    VERDICT_UNCLEAR,
    Job,
)
from job_finder.sources.base import SourceResult
from job_finder.text import safe_url, shorten, spreadsheet_safe

log = logging.getLogger(__name__)

SUMMARY_CHARS = 420
FULL_TEXT_CHARS = 6000
CSV_DESCRIPTION_CHARS = 1500
SECTION_LABELS = {SECTION_REMOTE: "Remote", SECTION_LOCAL: "Near home", SECTION_CHECK: "Check"}
CSV_COLUMNS = [
    "Section", "New today", "Title", "Company", "Location", "Work pattern", "Distance (km)",
    "Est. drive (min)", "Salary", "Contract", "Posted", "Source", "Also listed on", "Tags",
    "CV skills matched", "Claude verdict", "Experience asked (years)", "Claude's evidence",
    "Score", "Link", "Description",
]
CLAUDE_LABELS = {VERDICT_ENTRY: "entry level", VERDICT_NEEDS_EXPERIENCE: "needs experience",
                 VERDICT_UNCLEAR: "can't tell"}


def write_reports(
    jobs: list[Job],
    results: list[SourceResult],
    summary: FilterSummary,
    settings: Settings,
    generated_at: datetime,
    claude_status: str = "",
) -> tuple[Path, Path]:
    """Write today's HTML and CSV reports and return their paths."""
    out = settings.output_dir
    out.mkdir(parents=True, exist_ok=True)
    stem = f"jobs_{generated_at:%Y-%m-%d}"
    page = render_html(jobs, results, summary, settings, generated_at, claude_status)
    html_path = _write_with_fallback(
        out / f"{stem}.html", generated_at, lambda path: path.write_text(page, encoding="utf-8")
    )
    csv_path = _write_with_fallback(
        out / f"{stem}.csv", generated_at, lambda path: write_csv(jobs, path)
    )
    return html_path, csv_path


def _write_with_fallback(
    path: Path, generated_at: datetime, write: Callable[[Path], object]
) -> Path:
    """Write to `path`; if it is locked (e.g. open in Excel) use a time-stamped name instead."""
    try:
        write(path)
        return path
    except PermissionError:
        alternative = path.with_name(f"{path.stem}_{generated_at:%H%M%S}{path.suffix}")
        log.warning("%s is open in another program, so this run was saved as %s.",
                    path.name, alternative.name)
        write(alternative)
        return alternative


def write_csv(jobs: list[Job], path: Path) -> None:
    # utf-8-sig lets Excel show £ and accented characters correctly.
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(CSV_COLUMNS)
        for job in jobs:
            writer.writerow([
                SECTION_LABELS.get(job.section, job.section),
                "yes" if job.is_new else "",
                spreadsheet_safe(job.title),
                spreadsheet_safe(job.company),
                spreadsheet_safe(job.location),
                job.work_mode,
                "" if job.distance_km is None else job.distance_km,
                "" if job.drive_minutes is None else job.drive_minutes,
                spreadsheet_safe(job.salary),
                spreadsheet_safe(job.contract),
                job.posted.isoformat() if job.posted else "",
                job.source,
                ", ".join(job.also_on),
                ", ".join(job.tags),
                ", ".join(job.matched_skills),
                CLAUDE_LABELS.get(job.claude_verdict, ""),
                "" if job.claude_years is None else job.claude_years,
                spreadsheet_safe(job.claude_evidence),
                job.score,
                safe_url(job.url),
                spreadsheet_safe(shorten(job.description, CSV_DESCRIPTION_CHARS)),
            ])


def render_html(
    jobs: list[Job],
    results: list[SourceResult],
    summary: FilterSummary,
    settings: Settings,
    generated_at: datetime,
    claude_status: str = "",
) -> str:
    today = generated_at.date()
    home = settings.home.name
    by_section = {key: [job for job in jobs if job.section == key] for key in SECTION_LABELS}
    new_count = sum(job.is_new for job in jobs)
    local_searched = any(result.searched and result.covers_home_area for result in results)
    headline = (
        f"{len(jobs)} job{'' if len(jobs) == 1 else 's'} · {new_count} new today · "
        f"updated {generated_at:%A} {generated_at.day} {generated_at:%B %Y} at {generated_at:%H:%M}"
    )
    local_empty = (
        "No local matches this time."
        if local_searched
        else "Searching near home needs an Adzuna and/or Reed API key: see README.md."
    )
    sections = [
        _section_html(
            SECTION_REMOTE,
            "Remote, open to UK applicants",
            "Fully remote roles, plus UK adverts that mention remote or home working. "
            "Check each advert for any office days.",
            by_section[SECTION_REMOTE],
            today,
            "No remote matches this time.",
        ),
        _section_html(
            SECTION_LOCAL,
            f"Within about an hour of {home}",
            f"On-site, hybrid or unspecified roles within {settings.home.radius_km:.0f} km of "
            f"{home} in a straight line. Drive times are rough estimates.",
            by_section[SECTION_LOCAL],
            today,
            local_empty,
        ),
        _section_html(
            SECTION_CHECK,
            "Worth a quick check",
            "These mention remote working somewhere but look hybrid or office-based further "
            "away, or their location is unclear.",
            by_section[SECTION_CHECK],
            today,
            "Nothing to double-check.",
            collapsible=True,
        ),
    ]
    return "\n".join([
        "<!doctype html>",
        '<html lang="en">',
        "<head>",
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>Job search {today.isoformat()}</title>",
        f"<style>{_CSS}</style>",
        "</head>",
        "<body>",
        '<div class="wrap">',
        "<header>",
        f"<h1>Data &amp; maths jobs: remote or near {_e(home)}</h1>",
        f'<p class="summary">{_e(headline)}</p>',
        _sources_html(results, summary, claude_status),
        _APPLICATIONS,
        _controls(with_claude=any(job.claude_verdict for job in jobs)),
        "</header>",
        "<main>",
        *sections,
        "</main>",
        f"<footer>{_FOOTER}</footer>",
        "</div>",
        f"<script>{_SCRIPT}</script>",
        "</body>",
        "</html>",
    ])


def _sources_html(
    results: list[SourceResult], summary: FilterSummary, claude_status: str = ""
) -> str:
    items = "".join(
        f'<li><a href="{_e(result.homepage)}" target="_blank" rel="noopener noreferrer">'
        f"{_e(result.name)}</a>: {_e(result.status)}</li>"
        for result in results
    )
    if claude_status:
        items += f"<li>Claude's entry-level check: {_e(claude_status)}</li>"
    stats = (
        f"Checked {summary.received} listings and kept {summary.kept} "
        f"({summary.duplicates} duplicates merged)."
    )
    if summary.removed:
        removed = ", ".join(f"{count} {reason}" for reason, count in summary.removed.most_common())
        stats += f" Removed: {removed}."
    return (
        '<details class="sources"><summary>Sources and filtering</summary>'
        f"<ul>{items}</ul><p>{_e(stats)}</p></details>"
    )


def _section_html(
    key: str,
    heading: str,
    note: str,
    jobs: list[Job],
    today: date,
    empty_text: str,
    collapsible: bool = False,
) -> str:
    if jobs:
        cards = "\n".join(_card(job, today) for job in jobs)
    else:
        cards = f'<p class="empty">{_e(empty_text)}</p>'
    title = f'<h2>{_e(heading)} <span class="count" data-count-for="{key}">{len(jobs)}</span></h2>'
    body = f'<p class="note">{_e(note)}</p>\n{cards}'
    if collapsible:
        return f'<details class="section" id="{key}"><summary>{title}</summary>\n{body}</details>'
    return f'<section class="section" id="{key}">{title}\n{body}</section>'


def _site_link(name: str, url: str) -> str:
    """A site's name, linked to its own advert when there is a safe link for it."""
    link = safe_url(url)
    if not link:
        return _e(name)
    return f'<a href="{_e(link)}" target="_blank" rel="noopener noreferrer">{_e(name)}</a>'


def _card(job: Job, today: date) -> str:
    url = safe_url(job.url)
    badges = ['<span class="badge new">New</span>'] if job.is_new else []
    for tag in job.tags:
        style = {TAG_ENTRY: "entry", TAG_SENIOR: "warn", TAG_NEEDS_EXPERIENCE: "warn"}.get(tag, "")
        badges.append(f'<span class="badge {style}">{_e(tag)}</span>')
    if job.drive_minutes is not None and job.drive_minutes > 60:
        badges.append('<span class="badge warn">Over 1 hr?</span>')
    badges.append('<span class="badge applied" hidden></span>')  # filled in by _SCRIPT

    title = _e(job.title)
    if url:
        title = f'<a href="{_e(url)}" target="_blank" rel="noopener noreferrer">{title}</a>'
    parts = [
        f'<h3>{title}{"".join(badges)}</h3>',
        f'<p class="meta">{_e(_meta_line(job, today))}</p>',
    ]
    if job.claude_verdict:
        parts.append(f'<p class="claude">{_e(_claude_line(job))}</p>')
    summary = shorten(job.description, SUMMARY_CHARS)
    if summary:
        parts.append(f'<p class="desc">{_e(summary)}</p>')
    if len(job.description) > SUMMARY_CHARS + 40:
        parts.append(
            '<details class="more"><summary>Full description</summary>'
            f'<div class="full">{_e(job.description[:FULL_TEXT_CHARS])}</div></details>'
        )
    if job.matched_skills:
        parts.append(f'<p class="skills">Matches your CV: {_e(", ".join(job.matched_skills))}</p>')
    links = []
    if url:
        links.append(
            f'<a class="apply" href="{_e(url)}" target="_blank" rel="noopener noreferrer">'
            f"View and apply on {_e(job.source)} &rarr;</a>"
        )
    if job.also_on:
        sites = ", ".join(_site_link(name, job.also_urls.get(name, "")) for name in job.also_on)
        links.append(f'<span class="also">also listed on {sites}</span>')
    links.append('<button type="button" class="pill mark-applied">Mark as applied</button>')
    parts.append(f'<p class="links">{" · ".join(links)}</p>')
    # The page script remembers applications by this key, and lists them in "My applications"
    # using these details even after the job drops out of the results.
    attributes = (
        f'data-new="{int(job.is_new)}" data-entry="{int(TAG_ENTRY in job.tags)}" '
        f'data-needs-exp="{int(TAG_NEEDS_EXPERIENCE in job.tags)}" '
        f'data-teaching="{int(TAG_TEACHING in job.tags)}" data-applied="0" '
        f'data-key="{_e(job_key(job))}" data-title="{_e(job.title)}" '
        f'data-company="{_e(job.company)}" data-location="{_e(job.location)}" '
        f'data-url="{_e(url)}"'
    )
    return f'<article class="job" {attributes}>{"".join(parts)}</article>'


def _claude_line(job: Job) -> str:
    """Claude's verdict, and the words from the advert it was based on."""
    if job.claude_verdict == VERDICT_NEEDS_EXPERIENCE:
        asks = f"asks for {job.claude_years}+ years" if job.claude_years else "asks for experience"
        verdict = f"needs experience ({asks})"
    elif job.claude_verdict == VERDICT_ENTRY:
        verdict = "entry level"
    else:
        verdict = "can't tell from the advert"
    quote = f": “{job.claude_evidence}”" if job.claude_evidence else ""
    return f"Claude's check: {verdict}{quote}"


def _meta_line(job: Job, today: date) -> str:
    parts = [job.company] if job.company else []
    parts.append(job.location or "Location not given")
    if job.scope != SCOPE_REMOTE_BOARD:
        stated = job.work_mode != WORK_NOT_STATED
        parts.append(job.work_mode if stated else "Work pattern not stated")
    if job.distance_km is not None and job.drive_minutes is not None:
        parts.append(f"~{job.distance_km:.0f} km away (roughly {job.drive_minutes} min drive)")
    if job.salary:
        parts.append(job.salary)
    if job.contract:
        parts.append(job.contract)
    if job.posted:
        parts.append(f"posted {job.posted.day} {job.posted:%b %Y} ({_days_ago(job.posted, today)})")
    return " · ".join(parts)


def _days_ago(day: date, today: date) -> str:
    days = (today - day).days
    if days <= 0:
        return "today"
    if days == 1:
        return "yesterday"
    return f"{days} days ago"


def _e(value: object) -> str:
    return html.escape(str(value), quote=True)


def _controls(with_claude: bool) -> str:
    """The filter bar. The "needs experience" filter only appears when Claude's check ran."""
    claude = ('<label><input type="checkbox" id="filter-experienced"> Hide "needs '
              'experience"</label>\n') if with_claude else ""
    return (
        '<div class="controls" role="search">\n'
        '<input type="search" id="filter-text" aria-label="Filter jobs"\n'
        '  placeholder="Filter by word, company or place">\n'
        '<label><input type="checkbox" id="filter-new"> New today</label>\n'
        '<label><input type="checkbox" id="filter-entry"> Entry level only</label>\n'
        '<label><input type="checkbox" id="filter-teaching"> Hide teaching</label>\n'
        '<label><input type="checkbox" id="filter-applied"> Hide applied</label>\n'
        f"{claude}</div>"
    )

_APPLICATIONS = """<details class="applications">
<summary>My applications <span class="count" id="applied-count">0</span></summary>
<p class="note" id="applied-note">Jobs you mark as applied are listed here, newest first, even
after they drop out of the results. The list is saved in this browser only, so download a copy
now and then.</p>
<ul id="applied-list"></ul>
<p><button type="button" class="pill" id="applied-download" hidden>
Download as spreadsheet</button></p>
</details>"""

_FOOTER = (
    'Listings come from the official APIs of <a href="https://www.adzuna.co.uk">Adzuna</a>, '
    '<a href="https://www.reed.co.uk">Reed</a>, <a href="https://himalayas.app">Himalayas</a>, '
    '<a href="https://remotive.com">Remotive</a> and <a href="https://jobicy.com">Jobicy</a>, '
    "and every job links to the site it came from. This tool is not affiliated with or "
    "endorsed by any of them. Distances are straight-line and drive times are rough "
    "estimates, so check a route planner before applying."
)

_CSS = """
:root {
  --bg: #f5f6f8; --card: #ffffff; --text: #1c2330; --muted: #5a6474; --line: #dfe3e8;
  --accent: #1d5fbf; --accent-soft: #e7effb; --new: #0f7a45; --new-soft: #e2f4e9;
  --warn: #8a5300; --warn-soft: #fbf0d9; --tag: #4b5563; --tag-soft: #eceef2;
  --done: #5b3cc4; --done-soft: #efeafd;
  color-scheme: light;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #12151a; --card: #1b1f26; --text: #e6e9ee; --muted: #a2abb8; --line: #2b313b;
    --accent: #84b4ff; --accent-soft: #1e2a3c; --new: #66d39b; --new-soft: #16301f;
    --warn: #f0c36c; --warn-soft: #382c14; --tag: #c7ced8; --tag-soft: #2a303a;
    --done: #b9a4ff; --done-soft: #2a2340;
    color-scheme: dark;
  }
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--text);
  font: 15px/1.55 system-ui, -apple-system, "Segoe UI", Roboto, Arial, sans-serif; }
a { color: var(--accent); }
.wrap { max-width: 960px; margin: 0 auto; padding: 24px 16px 48px; }
h1 { font-size: 1.6rem; line-height: 1.25; margin: 0 0 4px; }
.summary { color: var(--muted); margin: 0 0 8px; }
details.sources { font-size: .86rem; color: var(--muted); margin: 0 0 12px; }
details.sources summary { cursor: pointer; }
details.sources ul { margin: 6px 0; padding-left: 20px; }
details.applications { font-size: .9rem; margin: 0 0 12px; }
details.applications > summary { cursor: pointer; font-weight: 600; }
details.applications ul { margin: 6px 0; padding-left: 22px; }
.controls { position: sticky; top: 0; z-index: 2; display: flex; flex-wrap: wrap;
  gap: 8px 18px; align-items: center; padding: 10px 0; background: var(--bg);
  border-bottom: 1px solid var(--line); }
.controls input[type=search] { flex: 1 1 260px; min-width: 0; padding: 8px 10px;
  font: inherit; color: var(--text); background: var(--card);
  border: 1px solid var(--line); border-radius: 8px; }
.controls label { white-space: nowrap; font-size: .92rem; }
.section { margin-top: 28px; }
.section h2 { display: inline; font-size: 1.2rem; }
details.section > summary { cursor: pointer; }
.count { display: inline-block; min-width: 1.8em; padding: 0 8px; margin-left: 6px;
  border-radius: 999px; background: var(--accent-soft); color: var(--accent);
  font-size: .85rem; text-align: center; }
.note { color: var(--muted); font-size: .9rem; margin: 6px 0 12px; }
.job { background: var(--card); border: 1px solid var(--line); border-radius: 10px;
  padding: 14px 16px; margin: 10px 0; }
.job[hidden] { display: none; }
.job h3 { margin: 0 0 4px; font-size: 1.05rem; line-height: 1.35; }
.job h3 a { text-decoration: none; }
.job h3 a:hover { text-decoration: underline; }
.badge { display: inline-block; margin-left: 6px; padding: 1px 8px; border-radius: 999px;
  font-size: .72rem; font-weight: 600; vertical-align: 2px;
  background: var(--tag-soft); color: var(--tag); }
.badge.new { background: var(--new-soft); color: var(--new); }
.badge.entry { background: var(--accent-soft); color: var(--accent); }
.badge.warn { background: var(--warn-soft); color: var(--warn); }
.badge.applied { background: var(--done-soft); color: var(--done); }
.badge[hidden] { display: none; }
.meta { margin: 0 0 8px; color: var(--muted); font-size: .88rem; }
.desc { margin: 0 0 6px; }
.more summary { cursor: pointer; color: var(--accent); font-size: .88rem; }
.full { white-space: pre-line; margin-top: 6px; font-size: .92rem; }
.skills { margin: 6px 0; font-size: .86rem; color: var(--muted); }
.claude { margin: 0 0 8px; font-size: .88rem; color: var(--muted); }
.links { margin: 8px 0 0; font-size: .92rem; }
.apply { font-weight: 600; }
.also { color: var(--muted); }
.pill { padding: 1px 10px; font: inherit; font-size: .85rem; color: var(--accent);
  background: var(--card); border: 1px solid var(--line); border-radius: 999px;
  cursor: pointer; }
.pill:hover { border-color: var(--accent); }
.mark-applied[aria-pressed="true"] { color: var(--muted); }
.empty { color: var(--muted); font-style: italic; }
footer { margin-top: 40px; font-size: .8rem; color: var(--muted); }
"""

_SCRIPT = """
(function () {
  var STORE = "data-job-finder.applied";  // shared by every report, so marks carry over
  var text = document.getElementById("filter-text");
  var onlyNew = document.getElementById("filter-new");
  var onlyEntry = document.getElementById("filter-entry");
  var hideTeaching = document.getElementById("filter-teaching");
  var hideApplied = document.getElementById("filter-applied");
  var hideExperienced = document.getElementById("filter-experienced");  // Claude's check only
  var list = document.getElementById("applied-list");
  var appliedCount = document.getElementById("applied-count");
  var note = document.getElementById("applied-note");
  var download = document.getElementById("applied-download");
  var FILTERS = "data-job-finder.filters";  // ticked filters, remembered between reports
  var boxes = [onlyNew, onlyEntry, hideTeaching, hideApplied, hideExperienced].filter(Boolean);
  var jobs = Array.prototype.slice.call(document.querySelectorAll(".job"));
  var haystacks = jobs.map(function (job) {
    var button = job.querySelector(".mark-applied");
    return job.textContent.replace(button.textContent, " ").toLowerCase();
  });
  var canSave = false;
  var applied = {};

  function update() {
    var words = text.value.toLowerCase().split(/\\s+/).filter(Boolean);
    jobs.forEach(function (job, i) {
      var show = words.every(function (w) { return haystacks[i].indexOf(w) !== -1; }) &&
        (!onlyNew.checked || job.dataset.new === "1") &&
        (!onlyEntry.checked || job.dataset.entry === "1") &&
        (!hideTeaching.checked || job.dataset.teaching !== "1") &&
        (!hideApplied.checked || job.dataset.applied !== "1") &&
        (!hideExperienced || !hideExperienced.checked || job.dataset.needsExp !== "1");
      job.hidden = !show;
    });
    document.querySelectorAll("[data-count-for]").forEach(function (badge) {
      var section = document.getElementById(badge.dataset.countFor);
      badge.textContent = section ? section.querySelectorAll(".job:not([hidden])").length : 0;
    });
  }

  // Applications are kept in the browser's localStorage. It can be missing or blocked (some
  // privacy settings), so every access is guarded and the page still works without it.
  function storageWorks() {
    try {
      window.localStorage.setItem(STORE + ".test", "1");
      window.localStorage.removeItem(STORE + ".test");
      return true;
    } catch (e) {
      return false;
    }
  }

  function loadApplied() {
    try {
      var saved = JSON.parse(window.localStorage.getItem(STORE) || "{}");
      return saved && typeof saved === "object" ? saved : {};
    } catch (e) {
      return {};
    }
  }

  function saveApplied() {
    try {
      window.localStorage.setItem(STORE, JSON.stringify(applied));
    } catch (e) {
      canSave = false;
      warnNotSaved();
    }
  }

  function warnNotSaved() {
    note.textContent = "This browser won't let the page save your marks, so they will be " +
      "forgotten when you close it. Download the list to keep a copy.";
  }

  function restoreFilters() {
    try {
      var saved = JSON.parse(window.localStorage.getItem(FILTERS) || "{}");
      boxes.forEach(function (box) { box.checked = saved[box.id] === true; });
    } catch (e) {
      return;  // storage blocked: start with nothing ticked
    }
  }

  function saveFilters() {
    try {
      var saved = JSON.parse(window.localStorage.getItem(FILTERS) || "{}");
      boxes.forEach(function (box) { saved[box.id] = box.checked; });
      window.localStorage.setItem(FILTERS, JSON.stringify(saved));
    } catch (e) {
      return;
    }
  }

  function pad(n) {
    return (n < 10 ? "0" : "") + n;
  }

  function isoToday() {
    var d = new Date();
    return d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate());
  }

  function showDate(iso, withYear) {
    var parts = String(iso).split("-");
    var d = new Date(+parts[0], +parts[1] - 1, +parts[2]);
    if (isNaN(d.getTime())) { return String(iso); }
    var options = { day: "numeric", month: "short" };
    if (withYear) { options.year = "numeric"; }
    return d.toLocaleDateString("en-GB", options);
  }

  function entries() {
    return Object.keys(applied)
      .map(function (key) { return applied[key]; })
      .filter(function (entry) { return entry && typeof entry === "object" && entry.date; })
      .sort(function (a, b) { return a.date < b.date ? 1 : a.date > b.date ? -1 : 0; });
  }

  function showCard(job) {
    var entry = applied[job.dataset.key];
    var badge = job.querySelector(".badge.applied");
    var button = job.querySelector(".mark-applied");
    job.dataset.applied = entry ? "1" : "0";
    badge.hidden = !entry;
    badge.textContent = entry ? "Applied " + showDate(entry.date, false) : "";
    button.textContent = entry ? "Undo applied" : "Mark as applied";
    button.setAttribute("aria-pressed", entry ? "true" : "false");
  }

  function showList() {
    var items = entries();
    appliedCount.textContent = items.length;
    list.textContent = "";
    items.forEach(function (entry) {
      var item = document.createElement("li");
      var linked = /^https?:\\/\\//i.test(entry.url || "");
      var title = document.createElement(linked ? "a" : "span");
      title.textContent = entry.title || "Untitled job";
      if (linked) {
        title.href = entry.url;
        title.target = "_blank";
        title.rel = "noopener noreferrer";
      }
      item.appendChild(title);
      var details = [entry.company, entry.location, "applied " + showDate(entry.date, true)];
      item.appendChild(document.createTextNode(" · " + details.filter(Boolean).join(" · ")));
      list.appendChild(item);
    });
    download.hidden = items.length === 0;
  }

  function refresh() {
    jobs.forEach(function (job) { showCard(job); });
    showList();
    update();
  }

  function toggle(job) {
    if (canSave) {
      applied = loadApplied();  // pick up marks made in another open report
    }
    var key = job.dataset.key;
    if (applied[key]) {
      delete applied[key];
    } else {
      applied[key] = {
        date: isoToday(),
        title: job.dataset.title,
        company: job.dataset.company,
        location: job.dataset.location,
        url: job.dataset.url
      };
    }
    if (canSave) {
      saveApplied();
    }
    refresh();
  }

  function csvCell(value) {
    var cell = value === undefined || value === null ? "" : String(value);
    if (/^[=+\\-@\\t\\r]/.test(cell)) {
      cell = "'" + cell;  // stop spreadsheets treating it as a formula
    }
    return '"' + cell.replace(/"/g, '""') + '"';
  }

  function downloadList() {
    var rows = [["Applied on", "Title", "Company", "Location", "Link"]];
    entries().forEach(function (entry) {
      rows.push([entry.date, entry.title, entry.company, entry.location, entry.url]);
    });
    var csv = rows.map(function (row) { return row.map(csvCell).join(","); }).join("\\r\\n");
    // The byte-order mark lets Excel show £ and accented letters correctly.
    var blob = new Blob(["\\ufeff" + csv + "\\r\\n"], { type: "text/csv;charset=utf-8" });
    var link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = "my_applications_" + isoToday() + ".csv";
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    setTimeout(function () { URL.revokeObjectURL(link.href); }, 1000);
  }

  jobs.forEach(function (job) {
    job.querySelector(".mark-applied").addEventListener("click", function () { toggle(job); });
  });
  [text, onlyNew, onlyEntry, hideTeaching, hideApplied, hideExperienced]
    .filter(Boolean)
    .forEach(function (el) {
      el.addEventListener("input", update);
      el.addEventListener("change", update);
    });
  boxes.forEach(function (box) { box.addEventListener("change", saveFilters); });
  download.addEventListener("click", downloadList);
  window.addEventListener("storage", function (event) {
    if (event.key === STORE && canSave) {
      applied = loadApplied();
      refresh();
    }
  });

  canSave = storageWorks();
  if (canSave) {
    applied = loadApplied();
  } else {
    warnNotSaved();
  }
  restoreFilters();
  refresh();
})();
"""
