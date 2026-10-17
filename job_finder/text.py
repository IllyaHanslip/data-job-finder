"""Helpers that turn raw API values into clean, readable text."""

from __future__ import annotations

import html
import math
import re
from datetime import UTC, date, datetime
from html.parser import HTMLParser
from typing import Any

_BLOCK_TAGS = frozenset(
    {
        "address", "article", "blockquote", "br", "dd", "div", "dl", "dt", "footer", "h1",
        "h2", "h3", "h4", "h5", "h6", "header", "hr", "li", "ol", "p", "pre", "section",
        "table", "td", "th", "tr", "ul",
    }
)
_HIDDEN_TAGS = frozenset({"script", "style", "noscript", "template"})

_CURRENCY_SYMBOLS = {"GBP": "£", "USD": "$", "EUR": "€"}
_PERIOD_WORDS = {
    "year": "year", "yearly": "year", "annual": "year", "annually": "year", "annum": "year",
    "per annum": "year", "month": "month", "monthly": "month", "week": "week",
    "weekly": "week", "day": "day", "daily": "day", "hour": "hour", "hourly": "hour",
}


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._hidden_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _HIDDEN_TAGS:
            self._hidden_depth += 1
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n• " if tag == "li" else "\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _HIDDEN_TAGS:
            self._hidden_depth = max(0, self._hidden_depth - 1)
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._hidden_depth:
            self.parts.append(data)


def html_to_text(value: Any) -> str:
    """Convert an HTML fragment (or plain text) into tidy text with one paragraph per line."""
    if value is None:
        return ""
    raw = str(value)
    if not raw.strip():
        return ""
    extractor = _TextExtractor()
    try:
        extractor.feed(raw)
        extractor.close()
        text = "".join(extractor.parts)
    except Exception:  # html.parser can still fail on badly broken markup
        text = html.unescape(re.sub(r"<[^>]*>", " ", raw))

    lines: list[str] = []
    bullet_waiting = False
    for line in text.splitlines():
        line = " ".join(line.split())
        if line == "•":
            bullet_waiting = True
            continue
        if not line:
            continue
        if bullet_waiting and not line.startswith("•"):
            line = f"• {line}"
        bullet_waiting = False
        lines.append(line)
    return "\n".join(lines)


def plain(value: Any) -> str:
    """Clean single-line text, for titles, company names and places."""
    return " ".join(html_to_text(value).split())


def shorten(text: str, limit: int) -> str:
    """Flatten whitespace and cut to about `limit` characters, ending on a whole word."""
    flat = " ".join(text.split())
    if len(flat) <= limit:
        return flat
    cut = flat[:limit]
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return cut.rstrip(" ,.;:–-") + "…"


def to_float(value: Any) -> float | None:
    """Read a number from an int, float or numeric string; None if it isn't one."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value.replace(",", "").strip()) if isinstance(value, str) else float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def parse_date(value: Any) -> date | None:
    """Understand the date formats the job APIs use; None if the value can't be read."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        seconds = value / 1000 if value > 10**11 else value  # Himalayas sends milliseconds
        try:
            return datetime.fromtimestamp(seconds, tz=UTC).date()
        except (OverflowError, OSError, ValueError):
            return None
    text = str(value).strip()
    if not text:
        return None
    if text.isdigit():
        return parse_date(int(text))
    try:
        return datetime.strptime(text, "%d/%m/%Y").date()  # Reed's format
    except ValueError:
        pass
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        pass
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def format_salary(
    minimum: Any, maximum: Any, currency: str = "GBP", period: Any = "", note: str = ""
) -> str:
    """Format a salary such as '£30,000 – £35,000 per year'; '' when no figures are given."""
    low, high = to_float(minimum), to_float(maximum)
    low = low if low is not None and low > 0 else None
    high = high if high is not None and high > 0 else None
    code = str(currency or "").strip().upper()
    prefix = _CURRENCY_SYMBOLS.get(code, f"{code} " if code else "")

    def money(amount: float) -> str:
        if amount >= 1000 or amount.is_integer():
            return f"{prefix}{amount:,.0f}"
        return f"{prefix}{amount:,.2f}"

    if low is not None and high is not None and high > low:
        text = f"{money(low)} – {money(high)}"
    else:
        single = low if low is not None else high
        if single is None:
            return ""
        text = money(single)
    period_text = str(period or "").strip().lower()
    unit = _PERIOD_WORDS.get(period_text, period_text)
    if unit:
        text += f" per {unit}"
    if note:
        text += f" ({note})"
    return text


def dig(data: Any, *keys: str) -> Any:
    """Follow nested dictionary keys, returning None if any level is missing."""
    for key in keys:
        if not isinstance(data, dict):
            return None
        data = data.get(key)
    return data


def spreadsheet_safe(value: str) -> str:
    """Stop spreadsheet programs from treating text copied from an advert as a formula."""
    return f"'{value}" if value.startswith(("=", "+", "-", "@", "\t", "\r")) else value


def safe_url(value: Any) -> str:
    """Return the value if it is an http(s) link, otherwise ''."""
    url = str(value or "").strip()
    return url if url.lower().startswith(("https://", "http://")) else ""
