"""Header-driven adaptive rate limiter for Okta's management API.

Okta reports the org's real limits on every response:
  X-Rate-Limit-Limit / -Remaining / -Reset (reset = UTC epoch seconds).
We learn the actual ceiling at runtime (so Workforce multipliers and support
increases are picked up automatically), proactively pause when the remaining
count drops below a reserve headroom, and on 429 sleep precisely until reset.
This makes IdPVault a good citizen on the org's shared API budget.
"""
import logging
import time

log = logging.getLogger(__name__)

MAX_SINGLE_SLEEP = 120  # never block longer than this on one wait


class AdaptiveRateLimiter:
    def __init__(self, reserve_pct: float = 0.2, max_retries: int = 5):
        self.reserve_pct = max(0.0, min(0.9, reserve_pct))
        self.max_retries = max_retries
        self.calls = 0
        self.limit: int | None = None
        self.remaining: int | None = None
        self.reset: int | None = None

    def _observe(self, headers) -> None:
        def _int(name):
            v = headers.get(name)
            try:
                return int(v) if v is not None else None
            except (TypeError, ValueError):
                return None
        self.limit = _int("x-rate-limit-limit") or self.limit
        rem = _int("x-rate-limit-remaining")
        if rem is not None:
            self.remaining = rem
        rst = _int("x-rate-limit-reset")
        if rst:
            self.reset = rst

    def _pause_if_low(self) -> None:
        if self.limit and self.remaining is not None and self.reset:
            if self.remaining <= self.limit * self.reserve_pct:
                wait = max(0.0, self.reset - time.time()) + 1.0
                if wait > 0:
                    log.info("okta rate reserve reached (%s/%s left) — pausing %.1fs",
                             self.remaining, self.limit, wait)
                    time.sleep(min(wait, MAX_SINGLE_SLEEP))
                    self.remaining = None  # force re-read after the window rolls

    def request(self, do):
        """do: zero-arg callable returning an httpx.Response. Retries on 429."""
        resp = None
        for attempt in range(self.max_retries + 1):
            self._pause_if_low()
            self.calls += 1
            resp = do()
            self._observe(resp.headers)
            if resp.status_code != 429:
                return resp
            wait = max(1.0, (self.reset - time.time()) if self.reset else 2.0) + 1.0
            log.warning("okta 429 — backing off %.1fs (retry %d/%d)",
                        wait, attempt + 1, self.max_retries)
            time.sleep(min(wait, MAX_SINGLE_SLEEP))
        return resp
