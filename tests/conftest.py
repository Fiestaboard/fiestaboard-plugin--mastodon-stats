"""Shared fixtures for the Mastodon Stats plugin tests.

``mastodon_api`` patches ``requests.get`` with a fake Mastodon instance that
answers ``GET /api/v1/accounts/lookup`` with an Account entity shaped like the
real one. No test touches the network. All names are invented.
"""

import json
from collections import deque
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pytest
from plugins.mastodon_stats import MastodonStatsPlugin

MANIFEST_PATH = Path(__file__).parent.parent / "manifest.json"
TEST_TOKEN = "test_access_token"


def account(**overrides):
    """An Account entity as /api/v1/accounts/lookup returns it."""
    body = {
        "id": "109000000000000001",
        "username": "alice",
        "acct": "alice",
        "display_name": "Alice Example :blobcat:",
        "locked": False,
        "bot": False,
        "created_at": "2022-11-01T00:00:00.000Z",
        "note": "<p>Test account</p>",
        "url": "https://example.social/@alice",
        "followers_count": 12345,
        "following_count": 321,
        "statuses_count": 4567,
        "last_status_at": "2026-10-01",
        "emojis": [],
        "fields": [],
    }
    body.update(overrides)
    return body


class FakeResponse:
    def __init__(self, body=None, status_code=200, headers=None, raw=None):
        self._body = body
        self.status_code = status_code
        self.headers = headers or {}
        self._raw = raw

    def json(self):
        if self._raw is not None:
            raise ValueError("not json")
        return self._body


class FakeInstance:
    """Answers requests.get the way a Mastodon instance would."""

    def __init__(self):
        self.accounts = {("example.social", "alice"): account()}
        self.overrides = deque()
        self.calls = []
        self.require_token = False

    def queue(self, response):
        """Return (or raise) *response* for the next request instead."""
        self.overrides.append(response)

    def error(self, status, message="test", headers=None):
        self.queue(FakeResponse({"error": message}, status, headers))

    def __call__(self, url, params=None, headers=None, timeout=None):
        self.calls.append({"url": url, "params": params, "headers": headers, "timeout": timeout})
        if self.overrides:
            response = self.overrides.popleft()
            if isinstance(response, Exception):
                raise response
            return response
        host = url.split("/")[2]
        assert url == f"https://{host}/api/v1/accounts/lookup", url
        if self.require_token and "Authorization" not in (headers or {}):
            return FakeResponse({"error": "This API requires an authenticated user"}, 401)
        body = self.accounts.get((host, params["acct"]))
        if body is None:
            return FakeResponse({"error": "Record not found"}, 404)
        return FakeResponse(body)


class Today:
    """A controllable stand-in for the local date."""

    def __init__(self):
        self.value = date(2026, 10, 2)

    def __call__(self):
        return self.value


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def manifest_data():
    with open(MANIFEST_PATH) as f:
        return json.load(f)


@pytest.fixture(autouse=True)
def today():
    t = Today()
    with patch("plugins.mastodon_stats._today", side_effect=t):
        yield t


@pytest.fixture(autouse=True)
def clock():
    c = Clock()
    with patch("plugins.mastodon_stats._monotonic", side_effect=c):
        yield c


@pytest.fixture
def instance():
    fake = FakeInstance()
    with patch("plugins.mastodon_stats.requests.get", side_effect=fake):
        yield fake


@pytest.fixture
def make_plugin(manifest_data):
    def factory(**config):
        p = MastodonStatsPlugin(manifest_data)
        p.config = {"enabled": True, "account": "@alice@example.social", **config}
        return p

    return factory


@pytest.fixture
def plugin(make_plugin):
    return make_plugin()
