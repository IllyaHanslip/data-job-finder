"""Load settings from config.toml, and API keys from .env or environment variables."""

from __future__ import annotations

import io
import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.toml"
LOCAL_CONFIG_PATH = PROJECT_ROOT / "config.local.toml"  # your own settings, never committed
ENV_PATH = PROJECT_ROOT / ".env"
API_KEY_NAMES = ("ADZUNA_APP_ID", "ADZUNA_APP_KEY", "REED_API_KEY", "ANTHROPIC_API_KEY")
BROWSERS = ("chrome", "default")
EFFORTS = ("low", "medium", "high", "xhigh", "max")


class ConfigError(Exception):
    """config.toml or .env is missing, unreadable or contains a value of the wrong type."""


@dataclass(frozen=True)
class Home:
    name: str
    latitude: float
    longitude: float
    radius_km: float


@dataclass(frozen=True)
class Settings:
    home: Home
    max_days_old: int
    include_senior: bool
    local_terms: list[str]
    remote_terms: list[str]
    title_keywords: list[str]
    exclude_title_keywords: list[str]
    senior_keywords: list[str]
    entry_level_keywords: list[str]
    cv_skills: list[str]
    sources: dict[str, bool]
    user_agent: str
    api_keys: dict[str, str]
    data_dir: Path  # cache, history and reports are written under this folder
    browser: str = "default"  # which browser opens the report: one of BROWSERS
    # The optional Claude entry-level check ([claude] in config.toml)
    check_entry_level: bool = False
    claude_model: str = "claude-opus-5-5"
    claude_effort: str = "low"  # one of EFFORTS
    max_claude_checks: int = 200

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / ".cache"

    @property
    def output_dir(self) -> Path:
        return self.data_dir / "output"


def load_settings(config_path: Path | None = None) -> Settings:
    path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
    raw = _read_toml(path)
    if config_path is None and LOCAL_CONFIG_PATH.exists():
        raw = _overlay(raw, _read_toml(LOCAL_CONFIG_PATH))

    _load_env_file(ENV_PATH)

    home = _section(raw, "home")
    search = _section(raw, "search")
    filters = _section(raw, "filters")
    sources = _section(raw, "sources")
    network = _section(raw, "network")
    report = _section(raw, "report")
    claude = _section(raw, "claude")

    return Settings(
        home=Home(
            name=_text(home, "home", "name"),
            latitude=_number(home, "home", "latitude", -90, 90),
            longitude=_number(home, "home", "longitude", -180, 180),
            radius_km=_number(home, "home", "radius_km", 1, 300),
        ),
        max_days_old=int(_number(search, "search", "max_days_old", 1, 365)),
        include_senior=_flag(search, "search", "include_senior_roles"),
        local_terms=_text_list(search, "search", "local_terms"),
        remote_terms=_text_list(search, "search", "remote_terms"),
        title_keywords=_text_list(filters, "filters", "title_keywords"),
        exclude_title_keywords=_text_list(filters, "filters", "exclude_title_keywords"),
        senior_keywords=_text_list(filters, "filters", "senior_keywords"),
        entry_level_keywords=_text_list(filters, "filters", "entry_level_keywords"),
        cv_skills=_text_list(filters, "filters", "cv_skills"),
        sources={name: _flag(sources, "sources", name) for name in sources},
        user_agent=_text(network, "network", "user_agent"),
        api_keys={name: os.environ.get(name, "").strip() for name in API_KEY_NAMES},
        data_dir=PROJECT_ROOT,
        # Optional, so settings files written before it existed still work.
        browser=_choice(report, "report", "browser", BROWSERS, default="default"),
        **_claude_settings(claude),
    )


def _claude_settings(table: dict[str, Any]) -> dict[str, Any]:
    """The optional [claude] section. Settings files without it keep the check off."""
    if not table:
        return {}
    return {
        "check_entry_level": _flag(table, "claude", "check_entry_level"),
        "claude_model": _text(table, "claude", "model"),
        "claude_effort": _choice(table, "claude", "effort", EFFORTS, default="low"),
        "max_claude_checks": int(_number(table, "claude", "max_checks_per_run", 0, 5000)),
    }


def _read_toml(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except FileNotFoundError:
        raise ConfigError(f"Config file not found: {path}") from None
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path.name} is not valid TOML ({exc}).") from None


def _overlay(shared: dict[str, Any], local: dict[str, Any]) -> dict[str, Any]:
    """Settings in config.local.toml replace the same ones in config.toml, section by section."""
    merged = dict(shared)
    for name, value in local.items():
        if isinstance(value, dict) and isinstance(merged.get(name), dict):
            merged[name] = {**merged[name], **value}
        else:
            merged[name] = value
    return merged


def _load_env_file(path: Path) -> None:
    """Load KEY=value lines into the environment without overriding variables already set."""
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise ConfigError(f"Could not read {path.name}: {exc}") from None
    # Windows PowerShell's `>` saves UTF-16, and Notepad can add a UTF-8 byte-order mark.
    encoding = "utf-16" if raw.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig"
    try:
        text = raw.decode(encoding)
    except UnicodeDecodeError:
        raise ConfigError(f"{path.name} is not plain text; re-save it as UTF-8.") from None
    load_dotenv(stream=io.StringIO(text), override=False)


def _section(raw: dict[str, Any], name: str) -> dict[str, Any]:
    value = raw.get(name, {})
    if not isinstance(value, dict):
        raise ConfigError(f"[{name}] in config.toml should be a section.")
    return value


def _require(table: dict[str, Any], section: str, key: str) -> Any:
    if key not in table:
        raise ConfigError(f"config.toml is missing '{key}' in the [{section}] section.")
    return table[key]


def _text(table: dict[str, Any], section: str, key: str) -> str:
    value = _require(table, section, key)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"[{section}] {key} in config.toml should be text in quotes.")
    return value.strip()


def _number(table: dict[str, Any], section: str, key: str, low: float, high: float) -> float:
    value = _require(table, section, key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"[{section}] {key} in config.toml should be a number.")
    if not low <= value <= high:
        raise ConfigError(f"[{section}] {key} in config.toml should be between {low} and {high}.")
    return float(value)


def _flag(table: dict[str, Any], section: str, key: str) -> bool:
    value = _require(table, section, key)
    if not isinstance(value, bool):
        raise ConfigError(f"[{section}] {key} in config.toml should be true or false.")
    return value


def _choice(
    table: dict[str, Any], section: str, key: str, options: tuple[str, ...], default: str
) -> str:
    value = table.get(key, default)
    if not isinstance(value, str) or value.strip().lower() not in options:
        allowed = " or ".join(f'"{option}"' for option in options)
        raise ConfigError(f"[{section}] {key} in config.toml should be {allowed}.")
    return value.strip().lower()


def _text_list(table: dict[str, Any], section: str, key: str) -> list[str]:
    value = _require(table, section, key)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ConfigError(f"[{section}] {key} in config.toml should be a list of quoted phrases.")
    return [item.strip() for item in value if item.strip()]
