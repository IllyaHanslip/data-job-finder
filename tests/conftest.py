"""Shared test fixtures. No test touches the network: every API answer is canned."""

from __future__ import annotations

import logging

import pytest

from job_finder.config import Home, Settings

FAKE_KEYS = {
    "ADZUNA_APP_ID": "test-app-id",
    "ADZUNA_APP_KEY": "test-app-key",
    "REED_API_KEY": "test-reed-key",
}


class FakeClient:
    """Stands in for PoliteClient: hands back canned answers and records every call."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.calls = []

    def get_json(self, url, *, politeness, params=None, auth=None, secret_params=()):
        self.calls.append(
            {"url": url, "params": dict(params or {}), "auth": auth, "secret_params": secret_params}
        )
        answer = self.answers.pop(0) if self.answers else {}
        if isinstance(answer, Exception):
            raise answer
        return answer


@pytest.fixture
def settings(tmp_path) -> Settings:
    """Fixed settings, so the tests don't depend on edits to config.toml."""
    return Settings(
        home=Home(name="Birmingham", latitude=52.4862, longitude=-1.8904, radius_km=55.0),
        max_days_old=30,
        include_senior=False,
        local_terms=["data scientist", "data analyst", "statistics"],
        remote_terms=["data scientist", "machine learning"],
        title_keywords=[
            "data scien*", "data analy*", "analyst", "machine learning", "statistic*",
            "maths", "mathemat*", "insight*",
        ],
        exclude_title_keywords=["security", "soc", "recruit*"],
        senior_keywords=["senior", "lead", "head", "principal", "manager"],
        entry_level_keywords=["graduate", "junior", "trainee", "entry level"],
        cv_skills=["python", "pandas", "machine learning", "statistic*", "excel"],
        sources={},
        user_agent="test-agent",
        api_keys=dict(FAKE_KEYS),
        data_dir=tmp_path,
    )


@pytest.fixture
def fake_client():
    return FakeClient


@pytest.fixture(autouse=True)
def _restore_logging():
    logger = logging.getLogger("job_finder")
    handlers, level, propagate = logger.handlers[:], logger.level, logger.propagate
    yield
    logger.handlers[:] = handlers
    logger.setLevel(level)
    logger.propagate = propagate
