"""PoliteClient behaviour, using a fake session so nothing goes over the network."""

from __future__ import annotations

import json
import time

import pytest
import requests

from job_finder.http_client import FetchError, PoliteClient, Politeness, StopSource

URL = "https://api.example.com/search"
QUICK = Politeness(min_interval=0, cache_hours=6, daily_budget=10)


class FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        if self._payload is None:
            raise ValueError("not JSON")
        return self._payload


class FakeSession:
    def __init__(self, *responses):
        self.headers = {}
        self.responses = list(responses)
        self.calls = []

    def get(self, url, params=None, auth=None, timeout=None):
        self.calls.append({"url": url, "params": params, "auth": auth, "timeout": timeout})
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def make_client(tmp_path, *responses, offline=False):
    session = FakeSession(*responses)
    sleeps = []
    client = PoliteClient(
        tmp_path, "test-agent", offline=offline, session=session, sleep=sleeps.append
    )
    return client, session, sleeps


def age_saved_answers(tmp_path, hours):
    for path in (tmp_path / "http").rglob("*.json"):
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["fetched_at"] = time.time() - hours * 3600
        path.write_text(json.dumps(payload), encoding="utf-8")


def test_identifies_itself(tmp_path):
    _, session, _ = make_client(tmp_path)
    assert session.headers["User-Agent"] == "test-agent"


def test_repeat_request_is_answered_from_the_saved_copy(tmp_path):
    client, session, _ = make_client(tmp_path, FakeResponse(200, {"jobs": [1]}))
    assert client.get_json(URL, politeness=QUICK, params={"q": "data"}) == {"jobs": [1]}
    assert client.get_json(URL, politeness=QUICK, params={"q": "data"}) == {"jobs": [1]}
    assert len(session.calls) == 1
    assert (client.requests_sent, client.cache_hits) == (1, 1)


def test_api_keys_are_never_written_to_disk(tmp_path):
    client, _, _ = make_client(tmp_path, FakeResponse(200, {"ok": True}))
    client.get_json(
        URL,
        politeness=QUICK,
        params={"app_key": "SUPER-SECRET", "what": "data"},
        secret_params=("app_key",),
    )
    saved = "".join(path.read_text(encoding="utf-8") for path in tmp_path.rglob("*.json"))
    assert "SUPER-SECRET" not in saved
    assert "data" in saved


def test_too_many_requests_stops_calling_that_api(tmp_path):
    client, session, _ = make_client(tmp_path, FakeResponse(429))
    with pytest.raises(StopSource, match="HTTP 429"):
        client.get_json(URL, politeness=QUICK, params={"q": "one"})
    with pytest.raises(StopSource):
        client.get_json(URL, politeness=QUICK, params={"q": "two"})
    assert len(session.calls) == 1


def test_rejected_key_stops_calling_that_api(tmp_path):
    client, session, _ = make_client(tmp_path, FakeResponse(401))
    with pytest.raises(StopSource, match="check the one in .env"):
        client.get_json(URL, politeness=QUICK)
    assert len(session.calls) == 1


def test_server_errors_are_retried_after_a_pause(tmp_path):
    client, session, sleeps = make_client(
        tmp_path, FakeResponse(503), FakeResponse(200, {"ok": 1})
    )
    assert client.get_json(URL, politeness=QUICK) == {"ok": 1}
    assert len(session.calls) == 2
    assert sleeps == [5.0]


def test_failure_falls_back_to_an_older_saved_copy(tmp_path):
    client, _, _ = make_client(tmp_path, FakeResponse(200, {"old": True}))
    client.get_json(URL, politeness=QUICK)
    age_saved_answers(tmp_path, hours=10)

    error = requests.ConnectionError("network down")
    client, session, sleeps = make_client(tmp_path, error, error, error)
    assert client.get_json(URL, politeness=QUICK) == {"old": True}
    assert len(session.calls) == 3
    assert sleeps == [5.0, 15.0]


def test_failure_without_a_saved_copy_is_reported(tmp_path):
    error = requests.Timeout("slow")
    client, _, _ = make_client(tmp_path, error, error, error)
    with pytest.raises(FetchError, match=r"could not reach api\.example\.com \(Timeout\)"):
        client.get_json(URL, politeness=QUICK)


def test_daily_budget_is_respected(tmp_path):
    budget = Politeness(min_interval=0, cache_hours=6, daily_budget=2)
    client, session, _ = make_client(
        tmp_path, FakeResponse(200, {}), FakeResponse(200, {}), FakeResponse(200, {})
    )
    client.get_json(URL, politeness=budget, params={"page": 1})
    client.get_json(URL, politeness=budget, params={"page": 2})
    with pytest.raises(StopSource, match="today's limit"):
        client.get_json(URL, politeness=budget, params={"page": 3})
    assert len(session.calls) == 2


def test_daily_budget_survives_a_restart(tmp_path):
    budget = Politeness(min_interval=0, cache_hours=6, daily_budget=1)
    client, _, _ = make_client(tmp_path, FakeResponse(200, {}))
    client.get_json(URL, politeness=budget, params={"page": 1})

    client, session, _ = make_client(tmp_path, FakeResponse(200, {}))
    with pytest.raises(StopSource):
        client.get_json(URL, politeness=budget, params={"page": 2})
    assert session.calls == []


def test_requests_to_the_same_api_are_spaced_out(tmp_path):
    spaced = Politeness(min_interval=3, cache_hours=6, daily_budget=10)
    client, _, sleeps = make_client(tmp_path, FakeResponse(200, {}), FakeResponse(200, {}))
    client.get_json(URL, politeness=spaced, params={"page": 1})
    client.get_json(URL, politeness=spaced, params={"page": 2})
    assert len(sleeps) == 1
    assert 2.9 < sleeps[0] <= 3


def test_offline_mode_only_uses_saved_copies(tmp_path):
    client, _, _ = make_client(tmp_path, FakeResponse(200, {"saved": 1}))
    client.get_json(URL, politeness=QUICK)
    age_saved_answers(tmp_path, hours=100)

    client, session, _ = make_client(tmp_path, offline=True)
    assert client.get_json(URL, politeness=QUICK) == {"saved": 1}
    with pytest.raises(FetchError, match="offline"):
        client.get_json(URL, politeness=QUICK, params={"q": "never fetched"})
    assert session.calls == []


def test_answer_that_is_not_json_is_an_error(tmp_path):
    client, _, _ = make_client(tmp_path, FakeResponse(200, None))
    with pytest.raises(FetchError, match="not JSON"):
        client.get_json(URL, politeness=QUICK)
