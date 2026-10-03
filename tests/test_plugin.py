"""Behaviour tests for the Mastodon Stats plugin (all HTTP is faked)."""

from datetime import date

import pytest
import requests
from plugins.mastodon_stats import (
    MastodonStatsPlugin,
    format_change,
    format_count,
    format_last_post,
    parse_account,
    short_count,
)

from .conftest import TEST_TOKEN, FakeResponse, account

# ----------------------------------------------------------------------
# Pure helpers
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("@alice@example.social", ("alice", "example.social")),
        ("alice@Example.Social", ("alice", "example.social")),
        ("  @alice@example.social  ", ("alice", "example.social")),
        ("https://example.social/@alice", ("alice", "example.social")),
        ("example.social/@alice/", ("alice", "example.social")),
        ("@a_b.c-d@sub.example.co.uk", ("a_b.c-d", "sub.example.co.uk")),
        ("@alice@localhost:3000", ("alice", "localhost:3000")),
        ("alice", None),
        ("@alice", None),
        ("", None),
        (None, None),
        ("@alice@bad host", None),
        ("@al ice@example.social", None),
        ("@alice@example.social/path", None),
        ("@alice@-example.social", None),
    ],
)
def test_parse_account(text, expected):
    assert parse_account(text) == expected


@pytest.mark.parametrize(
    "value,expected",
    [(0, "0"), (999, "999"), (1000, "1.0K"), (12345, "12.3K"), (999_949, "999.9K"),
     (999_999, "1.0M"), (1_234_567, "1.2M"), (12_345_678, "12M"), (None, "")],
)
def test_short_count(value, expected):
    assert short_count(value) == expected


def test_format_count():
    assert format_count(1234567) == "1,234,567"
    assert format_count(None) == ""


@pytest.mark.parametrize("value,expected", [(12, "+12"), (-3, "-3"), (0, "0"), (None, "")])
def test_format_change(value, expected):
    assert format_change(value) == expected


@pytest.mark.parametrize(
    "value,expected",
    [("2026-10-01", "OCT 1"), ("2026-01-15T10:00:00.000Z", "JAN 15"), (None, ""), ("garbage", "")],
)
def test_format_last_post(value, expected):
    assert format_last_post(value) == expected


# ----------------------------------------------------------------------
# Fetching
# ----------------------------------------------------------------------


def test_fetch_success(plugin, instance):
    result = plugin.fetch_data()
    assert result.available, result.error
    d = result.data
    assert d["handle"] == "@ALICE@EXAMPLE.SOCIAL"
    assert d["username"] == "ALICE"
    assert d["instance"] == "EXAMPLE.SOCIAL"
    assert d["display_name"] == "ALICE EXAMPLE"  # custom emoji shortcode dropped
    assert d["followers"] == "12,345"
    assert d["following"] == "321"
    assert d["posts"] == "4,567"
    assert d["followers_short"] == "12.3K"
    assert d["posts_short"] == "4.6K"
    assert d["followers_count"] == 12345
    assert d["following_count"] == 321
    assert d["posts_count"] == 4567
    assert d["last_post"] == "OCT 1"
    # First check of the day: nothing has changed yet.
    assert d["followers_change"] == "0"
    assert d["followers_trend_tile"] == ""

    call = instance.calls[0]
    assert call["url"] == "https://example.social/api/v1/accounts/lookup"
    assert call["params"] == {"acct": "alice"}
    assert "Authorization" not in call["headers"]
    assert "FiestaBoard" in call["headers"]["User-Agent"]
    assert call["timeout"]


def test_access_token_is_sent_when_set(make_plugin, instance):
    instance.require_token = True
    result = make_plugin(access_token=TEST_TOKEN).fetch_data()
    assert result.available, result.error
    assert instance.calls[0]["headers"]["Authorization"] == f"Bearer {TEST_TOKEN}"


def test_blank_token_is_not_sent(make_plugin, instance):
    make_plugin(access_token="   ").fetch_data()
    assert "Authorization" not in instance.calls[0]["headers"]


def test_display_name_falls_back_to_username(plugin, instance):
    instance.accounts[("example.social", "alice")] = account(display_name="")
    assert plugin.fetch_data().data["display_name"] == "ALICE"


def test_no_last_post(plugin, instance):
    instance.accounts[("example.social", "alice")] = account(last_status_at=None)
    assert plugin.fetch_data().data["last_post"] == ""


def test_hidden_counts_show_empty(plugin, instance):
    instance.accounts[("example.social", "alice")] = account(followers_count=-1, following_count=-1)
    d = plugin.fetch_data().data
    assert d["followers"] == ""
    assert d["followers_short"] == ""
    assert d["followers_count"] is None
    assert d["following"] == ""
    assert d["followers_change"] == ""
    assert d["posts"] == "4,567"


# ----------------------------------------------------------------------
# Daily change
# ----------------------------------------------------------------------


def test_daily_change_counts_from_first_check_of_the_day(plugin, instance, today):
    plugin.fetch_data()
    instance.accounts[("example.social", "alice")] = account(followers_count=12357, statuses_count=4570)
    d = plugin.fetch_data().data
    assert d["followers_change"] == "+12"
    assert d["followers_trend_tile"] == "{66}"
    assert d["posts_change"] == "+3"
    assert d["following_change"] == "0"


def test_daily_change_goes_down(plugin, instance):
    plugin.fetch_data()
    instance.accounts[("example.social", "alice")] = account(followers_count=12340)
    d = plugin.fetch_data().data
    assert d["followers_change"] == "-5"
    assert d["followers_trend_tile"] == "{63}"


def test_new_day_starts_from_last_count_of_yesterday(plugin, instance, today):
    plugin.fetch_data()
    instance.accounts[("example.social", "alice")] = account(followers_count=12350)
    plugin.fetch_data()  # last check of 2 Oct: 12350
    today.value = date(2026, 10, 3)
    instance.accounts[("example.social", "alice")] = account(followers_count=12352)
    assert plugin.fetch_data().data["followers_change"] == "+2"


def test_daily_change_off(make_plugin, instance):
    p = make_plugin(show_daily_change=False)
    p.fetch_data()
    instance.accounts[("example.social", "alice")] = account(followers_count=12357)
    d = p.fetch_data().data
    assert d["followers_change"] == ""
    assert d["posts_change"] == ""
    assert d["followers_trend_tile"] == ""


def test_changing_account_resets_baseline(make_plugin, instance):
    p = make_plugin()
    p.fetch_data()
    instance.accounts[("other.example", "bob")] = account(username="bob", followers_count=50)
    p.config = {"enabled": True, "account": "@bob@other.example"}
    d = p.fetch_data().data
    assert d["handle"] == "@BOB@OTHER.EXAMPLE"
    assert d["followers_change"] == "0"


# ----------------------------------------------------------------------
# Errors
# ----------------------------------------------------------------------


def test_missing_account(make_plugin, instance):
    result = make_plugin(account="").fetch_data()
    assert not result.available
    assert "@username@instance" in result.error
    assert instance.calls == []


def test_bad_account(make_plugin, instance):
    result = make_plugin(account="alice").fetch_data()
    assert not result.available
    assert instance.calls == []


def test_not_found(make_plugin, instance):
    result = make_plugin(account="@nobody@example.social").fetch_data()
    assert not result.available
    assert "not found" in result.error.lower()


def test_suspended(plugin, instance):
    instance.error(410)
    assert "suspended" in plugin.fetch_data().error.lower()


def test_auth_required_without_token(plugin, instance):
    instance.require_token = True
    result = plugin.fetch_data()
    assert not result.available
    assert "access token" in result.error.lower()


@pytest.mark.parametrize("status", [401, 403])
def test_token_rejected(make_plugin, instance, status):
    instance.error(status)
    result = make_plugin(access_token=TEST_TOKEN).fetch_data()
    assert "rejected" in result.error.lower()


def test_server_error(plugin, instance):
    instance.error(503)
    assert "503" in plugin.fetch_data().error


def test_network_error(plugin, instance):
    instance.queue(requests.exceptions.ConnectionError("down"))
    result = plugin.fetch_data()
    assert not result.available
    assert "EXAMPLE.SOCIAL" in result.error.upper()


def test_bad_json(plugin, instance):
    instance.queue(FakeResponse(raw=b"<html>", status_code=200))
    assert not plugin.fetch_data().available


def test_unexpected_payload(plugin, instance):
    instance.queue(FakeResponse(["not", "an", "account"]))
    assert not plugin.fetch_data().available


def test_rate_limited_backs_off_and_keeps_last_data(plugin, instance, clock):
    assert plugin.fetch_data().available
    instance.error(429, headers={"Retry-After": "120"})
    result = plugin.fetch_data()
    # Last good data is kept while the instance asks us to wait.
    assert result.available
    assert result.data["followers"] == "12,345"
    calls = len(instance.calls)
    clock.now += 60
    assert plugin.fetch_data().available
    assert len(instance.calls) == calls  # still backing off: no request
    clock.now += 61
    plugin.fetch_data()
    assert len(instance.calls) == calls + 1


def test_rate_limited_without_data_reports_error(plugin, instance):
    instance.error(429, headers={"Retry-After": "not-a-number"})
    result = plugin.fetch_data()
    assert not result.available
    assert "rate" in result.error.lower()


def test_rate_limit_http_date_retry_after(plugin, instance):
    instance.error(429, headers={"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"})
    assert not plugin.fetch_data().available


# ----------------------------------------------------------------------
# Config validation
# ----------------------------------------------------------------------


def test_validate_config_ok(plugin):
    assert plugin.validate_config({"account": "@alice@example.social", "refresh_seconds": 900}) == []


def test_validate_config_missing_account(plugin):
    assert plugin.validate_config({}) != []


def test_validate_config_bad_account(plugin):
    errors = plugin.validate_config({"account": "alice"})
    assert any("@username@instance" in e for e in errors)


def test_validate_config_refresh_too_low(plugin):
    assert plugin.validate_config({"account": "@alice@example.social", "refresh_seconds": 10}) != []


def test_plugin_id(manifest_data):
    assert MastodonStatsPlugin(manifest_data).plugin_id == "mastodon_stats"


def test_example_values_fit_max_length(manifest_data):
    for name, spec in manifest_data["variables"]["simple"].items():
        if "max_length" in spec and not spec["example"].startswith("{"):
            assert len(spec["example"]) <= spec["max_length"], name
