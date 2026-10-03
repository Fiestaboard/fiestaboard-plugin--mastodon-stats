"""Mastodon Stats plugin for FiestaBoard.

Shows the follower, following and post counts of any Mastodon account, and
optionally how much each has changed since the first check of the day.

No sign-in: the counts come from the public account lookup endpoint
(Mastodon 3.4.0+, https://docs.joinmastodon.org/methods/accounts/#lookup)::

    GET https://<instance>/api/v1/accounts/lookup?acct=<username>

which returns an Account entity with ``followers_count``, ``following_count``
and ``statuses_count``. Some instances (limited federation / authorized fetch
setups) refuse anonymous lookups; for those the user can paste an access token
(``read:accounts``) made on their own instance, sent as a bearer token.
"""

import logging
import re
import time
import unicodedata
from datetime import date, datetime
from email.utils import parsedate_to_datetime
from typing import Any, Dict, Optional, Tuple

import requests
from src.board_chars import BoardChars
from src.plugins.base import PluginBase, PluginResult

logger = logging.getLogger(__name__)

LOOKUP_PATH = "/api/v1/accounts/lookup"
USER_AGENT = "FiestaBoard Mastodon Stats Plugin (https://github.com/Fiestaboard/fiestaboard-plugin--mastodon-stats)"
REQUEST_TIMEOUT_SECONDS = 10
DEFAULT_RETRY_AFTER_SECONDS = 300
MAX_RETRY_AFTER_SECONDS = 3600
MIN_REFRESH_SECONDS = 300

TILE_UP = "{66}"  # green
TILE_DOWN = "{63}"  # red

ACCOUNT_HELP = "Set the account as @username@instance (for example @alice@mastodon.social)."

_USER_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.\-]*$")
_HOST_RE = re.compile(r"^(?=.{1,253}(?::\d{1,5})?$)[a-z0-9](?:[a-z0-9\-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9\-]*[a-z0-9])?)*(?::\d{1,5})?$")
_URL_RE = re.compile(r"^(?:https?://)?([^/@\s]+)/@([^/\s]+)/?$", re.IGNORECASE)
_SHORTCODE_RE = re.compile(r":[A-Za-z0-9_]+:")

COUNT_FIELDS = (("followers", "followers_count"), ("following", "following_count"), ("posts", "statuses_count"))
MONTHS = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")


class MastodonError(Exception):
    """A lookup failed; the message is shown to the user."""


def _today() -> date:
    return date.today()


def _monotonic() -> float:
    return time.monotonic()


# ----------------------------------------------------------------------
# Pure helpers
# ----------------------------------------------------------------------


def parse_account(text: Any) -> Optional[Tuple[str, str]]:
    """Split ``@user@instance`` (or a profile URL) into (username, instance)."""
    raw = str(text or "").strip()
    if not raw:
        return None
    match = _URL_RE.match(raw)
    if match:
        host, user = match.group(1), match.group(2)
    else:
        parts = raw.lstrip("@").split("@")
        if len(parts) != 2:
            return None
        user, host = parts
    host = host.lower()
    if not _USER_RE.match(user) or not _HOST_RE.match(host):
        return None
    return user, host


def board_safe(text: Any) -> str:
    """Uppercase *text* and keep only characters the board can show."""
    folded = unicodedata.normalize("NFKD", str(text or ""))
    kept = []
    for ch in folded.upper():
        if ch.isspace():
            kept.append(" ")
        elif ch not in "{}" and BoardChars.get_char_code(ch) is not None:
            kept.append(ch)
    return re.sub(r" {2,}", " ", "".join(kept)).strip()


def _count(value: Any) -> Optional[int]:
    """A count from the API, or None when missing or hidden (negative)."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def format_count(value: Optional[int]) -> str:
    return "" if value is None else f"{value:,}"


def short_count(value: Optional[int]) -> str:
    """12345 -> 12.3K, 1234567 -> 1.2M, 12345678 -> 12M."""
    if value is None:
        return ""
    if value < 1000:
        return str(value)
    if value < 999_950:
        return f"{value / 1000:.1f}K"
    if value < 9_950_000:
        return f"{value / 1_000_000:.1f}M"
    return f"{round(value / 1_000_000)}M"


def format_change(value: Optional[int]) -> str:
    if value is None:
        return ""
    return f"+{value}" if value > 0 else str(value)


def format_last_post(value: Any) -> str:
    """``2026-10-01`` -> ``OCT 1``."""
    try:
        day = date.fromisoformat(str(value)[:10])
    except ValueError:
        return ""
    return f"{MONTHS[day.month - 1]} {day.day}"


def _retry_after_seconds(header: Any) -> float:
    if header is None:
        return DEFAULT_RETRY_AFTER_SECONDS
    try:
        seconds = float(header)
    except (TypeError, ValueError):
        try:
            when = parsedate_to_datetime(str(header))
            seconds = (when - datetime.now(when.tzinfo)).total_seconds()
        except (TypeError, ValueError):
            return DEFAULT_RETRY_AFTER_SECONDS
    return max(1.0, min(seconds, MAX_RETRY_AFTER_SECONDS))


# ----------------------------------------------------------------------
# Plugin
# ----------------------------------------------------------------------


class MastodonStatsPlugin(PluginBase):
    """Follower, following and post counts for one Mastodon account."""

    def __init__(self, manifest: Dict[str, Any]):
        super().__init__(manifest)
        self._baseline_key: Optional[Tuple[str, str]] = None
        self._baseline_day: Optional[date] = None
        self._baseline: Dict[str, Optional[int]] = {}
        self._latest: Dict[str, Optional[int]] = {}
        self._last_data: Optional[Dict[str, Any]] = None
        self._backoff_until = 0.0

    @property
    def plugin_id(self) -> str:
        return "mastodon_stats"

    def validate_config(self, config: Dict[str, Any]) -> list:
        errors = []
        if parse_account(config.get("account")) is None:
            errors.append(ACCOUNT_HELP)
        refresh = config.get("refresh_seconds")
        if refresh is not None and (not isinstance(refresh, int) or refresh < MIN_REFRESH_SECONDS):
            errors.append(f"Refresh interval must be at least {MIN_REFRESH_SECONDS} seconds.")
        return errors

    def fetch_data(self) -> PluginResult:
        parsed = parse_account(self.config.get("account"))
        if parsed is None:
            return PluginResult(available=False, error=ACCOUNT_HELP)
        if parsed != self._baseline_key:
            self._reset(parsed)

        if _monotonic() < self._backoff_until:
            if self._last_data is not None:
                return PluginResult(available=True, data=self._last_data)
            return PluginResult(available=False, error=self._rate_limit_message(parsed[1]))

        try:
            body = self._lookup(*parsed)
        except MastodonError as exc:
            if _monotonic() < self._backoff_until and self._last_data is not None:
                return PluginResult(available=True, data=self._last_data)
            return PluginResult(available=False, error=str(exc))

        self._last_data = self._build(parsed, body)
        return PluginResult(available=True, data=self._last_data)

    # -- internals ------------------------------------------------------

    def _reset(self, key: Tuple[str, str]) -> None:
        self._baseline_key = key
        self._baseline_day = None
        self._baseline = {}
        self._latest = {}
        self._last_data = None
        self._backoff_until = 0.0

    @staticmethod
    def _rate_limit_message(host: str) -> str:
        return f"{host} is rate limiting requests. Trying again shortly."

    def _lookup(self, user: str, host: str) -> Dict[str, Any]:
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        token = str(self.config.get("access_token") or "").strip()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        url = f"https://{host}{LOOKUP_PATH}"
        try:
            response = requests.get(url, params={"acct": user}, headers=headers, timeout=REQUEST_TIMEOUT_SECONDS)
        except requests.exceptions.RequestException as exc:
            logger.warning("Mastodon lookup on %s failed: %s", host, exc)
            raise MastodonError(f"Could not reach {host}. Check the instance name and your connection.") from exc

        status = response.status_code
        if status == 404:
            raise MastodonError(f"Account @{user}@{host} not found. {ACCOUNT_HELP}")
        if status == 410:
            raise MastodonError(f"@{user}@{host} is suspended.")
        if status in (401, 403):
            if token:
                raise MastodonError(f"{host} rejected the access token ({status}). Create a new one with read:accounts.")
            raise MastodonError(
                f"{host} does not allow public lookups ({status}). Add an access token with read:accounts in settings."
            )
        if status == 429:
            self._backoff_until = _monotonic() + _retry_after_seconds(response.headers.get("Retry-After"))
            raise MastodonError(self._rate_limit_message(host))
        if status != 200:
            raise MastodonError(f"{host} returned an error ({status}). Trying again later.")
        try:
            body = response.json()
        except ValueError as exc:
            raise MastodonError(f"{host} sent a response that is not a Mastodon account.") from exc
        if not isinstance(body, dict):
            raise MastodonError(f"{host} sent a response that is not a Mastodon account.")
        return body

    def _changes(self, counts: Dict[str, Optional[int]]) -> Dict[str, Optional[int]]:
        today = _today()
        if self._baseline_day != today:
            # The day starts from the last count seen yesterday, so changes
            # made between yesterday's last check and today's first one count.
            self._baseline = dict(self._latest) if self._latest else dict(counts)
            self._baseline_day = today
        self._latest = dict(counts)
        changes = {}
        for name, value in counts.items():
            base = self._baseline.get(name)
            changes[name] = None if value is None or base is None else value - base
        return changes

    def _build(self, parsed: Tuple[str, str], body: Dict[str, Any]) -> Dict[str, Any]:
        user, host = parsed
        counts = {name: _count(body.get(field)) for name, field in COUNT_FIELDS}
        username = board_safe(body.get("username") or user)
        display = board_safe(_SHORTCODE_RE.sub("", str(body.get("display_name") or ""))) or username

        data: Dict[str, Any] = {
            "handle": f"@{username}@{board_safe(host)}",
            "username": username,
            "display_name": display,
            "instance": board_safe(host),
            "last_post": format_last_post(body.get("last_status_at")),
        }
        for name, value in counts.items():
            data[name] = format_count(value)
            data[f"{name}_short"] = short_count(value)
            data[f"{name}_count"] = value

        show_change = self.config.get("show_daily_change", True)
        changes = self._changes(counts)
        for name in counts:
            data[f"{name}_change"] = format_change(changes[name]) if show_change else ""
        trend = changes["followers"] if show_change else None
        data["followers_trend_tile"] = TILE_UP if trend and trend > 0 else TILE_DOWN if trend and trend < 0 else ""
        return data
