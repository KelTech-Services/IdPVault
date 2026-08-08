"""The live config cache must honour the operator's refresh interval.

REGRESSION GUARD: get_live_export used a hardcoded 120-second TTL and ignored
`state_poll_minutes` entirely. An operator who set the interval to 30 minutes
still got a full provider export every time they navigated back to a tenant
Overview - the exact thing the setting exists to prevent.
"""
import time

import pytest

from app.core import livestate as ls


@pytest.fixture(autouse=True)
def _clean_cache():
    ls._live_cache.clear()
    yield
    ls._live_cache.clear()


@pytest.mark.parametrize("minutes,expect_s", [
    (30, 1800),   # Eric's setting
    (60, 3600),
    (15, 900),    # the default
    (1, 60),
])
def test_ttl_follows_the_setting(monkeypatch, minutes, expect_s):
    monkeypatch.setattr(ls, "_ttl_minutes", lambda: minutes)
    assert ls._live_ttl_s() == expect_s


def test_polling_off_still_caches(monkeypatch):
    """0 means "do not poll on a schedule", NOT "re-fetch on every click"."""
    monkeypatch.setattr(ls, "_ttl_minutes", lambda: 0)
    assert ls._live_ttl_s() == ls._LIVE_CACHE_FALLBACK_TTL_S
    assert ls._live_ttl_s() > 0


def _boom(*a, **k):
    raise AssertionError("hit the provider when the cache was warm")


def test_warm_cache_is_served_without_touching_the_provider(monkeypatch):
    monkeypatch.setattr(ls, "_ttl_minutes", lambda: 30)
    # Anything that would reach a provider blows up if it is reached.
    monkeypatch.setattr("app.models.db.SessionLocal", _boom)
    ls._live_cache[7] = (time.monotonic(), {"apps": [{"id": "a"}]})
    assert ls.get_live_export(7) == {"apps": [{"id": "a"}]}


def test_a_stale_entry_is_not_served(monkeypatch):
    monkeypatch.setattr(ls, "_ttl_minutes", lambda: 1)   # 60s TTL
    ls._live_cache[7] = (time.monotonic() - 120, {"apps": []})
    with pytest.raises(Exception):
        ls.get_live_export(7)   # must fall through and try to fetch


def test_force_bypasses_a_warm_entry(monkeypatch):
    monkeypatch.setattr(ls, "_ttl_minutes", lambda: 30)
    ls._live_cache[7] = (time.monotonic(), {"apps": []})
    with pytest.raises(Exception):
        ls.get_live_export(7, force=True)


def test_an_entry_written_by_the_poll_is_reused_by_the_explorer(monkeypatch):
    """poll_tenant already exports the whole tenant for its drift summary.
    That export seeds the same cache the Explorer reads, so opening an
    Overview costs one provider export rather than two."""
    monkeypatch.setattr(ls, "_ttl_minutes", lambda: 30)
    monkeypatch.setattr("app.models.db.SessionLocal", _boom)
    ls._live_cache[3] = (time.monotonic(), {"groups": [{"id": "g1"}]})
    assert ls.get_live_export(3)["groups"][0]["id"] == "g1"
