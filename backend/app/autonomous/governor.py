"""Usage-aware orchestration — the shared :class:`UsageGovernor` (SPEC §6).

The governor treats the Claude subscription as a metered resource so several
autonomous projects can run for hours without stampeding a rate limit. It is a
*global* singleton: one shared concurrency semaphore, one rolling token meter,
and one backoff memory cooperate across every project tab.

Everything here is pure Python — no LLM, no network. The engine calls
:meth:`UsageGovernor.gate` at the top of every frontier iteration, so ``gate``
is deliberately cheap and never raises. The authoritative live signal is the
rate-limit feedback the caller pushes in via :meth:`note_rate_limit` (a 429 or a
``retry-after``); everything else is derived from the token spend the caller
meters through :meth:`record_usage`.

Policy sketch (SPEC §6.2)::

    headroom = f(remaining_budget, recent_429s, daily_cap_left)
    ample -> SPRINTING : full concurrency, deep pressure tests
    tight -> CURBING   : concurrency 1, cheapest lenses, brief throttle
    none  -> PAUSED    : idle / exponential backoff, caller should not work
"""

from __future__ import annotations

import asyncio
import os
import threading
import time
from collections import deque
from contextlib import asynccontextmanager

from .schemas import Budget, ExplorerMode

# --------------------------------------------------------------------------- #
# Tunables. Times are seconds. The pace dial (eco < balanced < sprint) shifts  #
# how aggressively the governor spends: eco curbs early and paces itself even  #
# with headroom; sprint pushes concurrency and rigor to the ceiling.           #
# --------------------------------------------------------------------------- #
_RATE_WINDOW = 60.0          # rolling window for token-rate + 429 accounting
_BACKOFF_BASE = 1.0          # first backoff after a rate limit (seconds)
_BACKOFF_CAP = 60.0          # exponential backoff ceiling
_IDLE_WAIT = 1.0             # bounded idle when paused with no explicit backoff

# Fraction of budget remaining below which a pace enters "tight". Eco curbs
# early to protect the subscription; sprint runs right up to the edge.
_TIGHT_THRESHOLD: dict[str, float] = {"eco": 0.25, "balanced": 0.12, "sprint": 0.05}

# Inter-step delay while SPRINTING (ample). Eco stays slow on purpose.
_SPRINT_DELAY: dict[str, float] = {"eco": 0.5, "balanced": 0.05, "sprint": 0.0}

# Inter-step delay while CURBING (tight): a light throttle to ease off the limit.
_CURB_DELAY: dict[str, float] = {"eco": 2.0, "balanced": 1.0, "sprint": 0.5}


class UsageGovernor:
    """Shared, self-throttling budget/concurrency governor.

    A single instance is shared by every project (see :func:`get_governor`). The
    concurrency semaphore is global so N autonomous runs cooperate instead of
    stampeding the limit; the token meter and backoff memory are likewise global
    (a daily cap is a shared resource), while per-project budgets are supplied on
    each call.
    """

    def __init__(self, max_concurrency: int | None = None) -> None:
        if max_concurrency is None:
            max_concurrency = max(1, min(8, (os.cpu_count() or 4) - 2))
        self._max_concurrency = max_concurrency
        self._sem = asyncio.Semaphore(max_concurrency)
        self._lock = threading.Lock()

        # Rolling spend + rate accounting (global across projects).
        self._spent_total = 0
        self._recent: deque[tuple[float, int]] = deque()   # (ts, tokens) in window
        self._limit_hits: deque[float] = deque()           # 429 timestamps in window

        # Exponential backoff memory (SPEC §6.1).
        self._consecutive_limits = 0
        self._backoff_until = 0.0

        self._mode = ExplorerMode.PAUSED

    # ----------------------------------------------------------------- #
    # Concurrency                                                       #
    # ----------------------------------------------------------------- #
    @asynccontextmanager
    async def slot(self):
        """Acquire the shared concurrency semaphore for the duration of a block.

        Usage::

            async with governor.slot():
                await expand(node)
        """
        await self._sem.acquire()
        try:
            yield
        finally:
            self._sem.release()

    def concurrency_for(self, budget: Budget) -> int:
        """Advisory number of expansions to run in parallel for this project's pace.

        The hard ceiling is the shared semaphore; this just tells a project how
        wide to fan out given its pace dial.
        """
        cap = self._max_concurrency
        pace = budget.pace
        if pace == "eco":
            return 1
        if pace == "sprint":
            return cap
        # balanced: roughly half the cap, at least one.
        return max(1, cap // 2)

    # ----------------------------------------------------------------- #
    # Metering & rate-limit feedback                                    #
    # ----------------------------------------------------------------- #
    def record_usage(self, tokens: int) -> None:
        """Meter ``tokens`` of spend (called after every LLM call)."""
        if tokens <= 0:
            return
        now = time.time()
        with self._lock:
            self._spent_total += tokens
            self._recent.append((now, tokens))
            self._prune(now)
            # A successful call after the backoff window has passed clears the
            # consecutive-limit memory so we don't back off forever.
            if now >= self._backoff_until:
                self._consecutive_limits = 0

    def note_rate_limit(self, retry_after: float | None = None) -> None:
        """Record a rate-limit signal (429 / ``retry-after``) and widen backoff.

        Consecutive hits grow the backoff exponentially (capped). An explicit
        ``retry_after`` from the server is honoured verbatim when larger.
        """
        now = time.time()
        with self._lock:
            self._consecutive_limits += 1
            self._limit_hits.append(now)
            self._prune(now)
            if retry_after is not None and retry_after > 0:
                wait = float(retry_after)
            else:
                wait = min(
                    _BACKOFF_CAP,
                    _BACKOFF_BASE * (2 ** (self._consecutive_limits - 1)),
                )
            self._backoff_until = max(self._backoff_until, now + wait)

    def _prune(self, now: float) -> None:
        """Drop rate/limit samples older than the rolling window. Caller holds lock."""
        horizon = now - _RATE_WINDOW
        while self._recent and self._recent[0][0] < horizon:
            self._recent.popleft()
        while self._limit_hits and self._limit_hits[0] < horizon:
            self._limit_hits.popleft()

    # ----------------------------------------------------------------- #
    # Policy                                                            #
    # ----------------------------------------------------------------- #
    def headroom(self, budget: Budget, spent: int) -> str:
        """Classify available headroom as ``"ample" | "tight" | "none"`` (SPEC §6.2).

        Blends: whether we're in a rate-limit backoff, how much of the budget
        (per-project ``max_tokens`` and the shared ``daily_cap_tokens``) is left,
        and whether we've seen recent 429s.
        """
        now = time.time()
        with self._lock:
            self._prune(now)
            in_backoff = now < self._backoff_until
            recent_limits = len(self._limit_hits)
            remaining = self._remaining_fraction(budget, spent)

        if in_backoff or remaining <= 0.0:
            return "none"
        if recent_limits > 0:
            return "tight"
        if remaining < _TIGHT_THRESHOLD.get(budget.pace, 0.12):
            return "tight"
        return "ample"

    def _remaining_fraction(self, budget: Budget, spent: int) -> float:
        """Smallest fraction of budget left across the active caps (1.0 = uncapped).

        ``max_tokens`` is per project (``spent``); ``daily_cap_tokens`` is shared,
        so it's measured against the global meter. Caller holds the lock.
        """
        fracs: list[float] = []
        if budget.max_tokens and budget.max_tokens > 0:
            fracs.append(max(0.0, (budget.max_tokens - spent) / budget.max_tokens))
        if budget.daily_cap_tokens and budget.daily_cap_tokens > 0:
            fracs.append(
                max(0.0, (budget.daily_cap_tokens - self._spent_total) / budget.daily_cap_tokens)
            )
        if not fracs:
            return 1.0
        return min(fracs)

    def rigor_for(self, budget: Budget, spent: int) -> str:
        """Pick a :data:`TestRigor` (``deep`` / ``standard`` / ``light``) for now.

        Deep (all 5 lenses) only when we have ample headroom *and* the user is
        sprinting; standard with headroom otherwise; light whenever we're curbing
        or paused (SPEC §5's budget-scaled depth).
        """
        hr = self.headroom(budget, spent)
        if hr != "ample":
            return "light"
        if budget.pace == "sprint":
            return "deep"
        return "standard"

    def mode(self) -> ExplorerMode:
        """The current global mode as of the last :meth:`gate` call."""
        with self._lock:
            return self._mode

    async def gate(self, budget: Budget, spent: int) -> ExplorerMode:
        """Throttle the frontier loop for one iteration and return the current mode.

        Cheap and safe to call every iteration. On ``none`` headroom it sleeps out
        the backoff (or a bounded idle) and returns :attr:`ExplorerMode.PAUSED` —
        the caller should idle and re-check its stop conditions. On ``tight`` it
        applies a brief curb delay; on ``ample`` a pace-scaled sprint delay.
        """
        hr = self.headroom(budget, spent)
        now = time.time()

        if hr == "none":
            self._set_mode(ExplorerMode.PAUSED)
            with self._lock:
                backoff_until = self._backoff_until
            wait = (backoff_until - now) if backoff_until > now else _IDLE_WAIT
            wait = max(0.0, min(wait, _BACKOFF_CAP))
            if wait > 0:
                await asyncio.sleep(wait)
            return ExplorerMode.PAUSED

        if hr == "tight":
            self._set_mode(ExplorerMode.CURBING)
            delay = _CURB_DELAY.get(budget.pace, 1.0)
            if delay > 0:
                await asyncio.sleep(delay)
            return ExplorerMode.CURBING

        self._set_mode(ExplorerMode.SPRINTING)
        delay = _SPRINT_DELAY.get(budget.pace, 0.05)
        if delay > 0:
            await asyncio.sleep(delay)
        return ExplorerMode.SPRINTING

    def _set_mode(self, mode: ExplorerMode) -> None:
        with self._lock:
            self._mode = mode


# --------------------------------------------------------------------------- #
# Global singleton                                                            #
# --------------------------------------------------------------------------- #
_GOVERNOR: UsageGovernor | None = None
_GOVERNOR_LOCK = threading.Lock()


def get_governor() -> UsageGovernor:
    """Return the process-wide governor shared by every project."""
    global _GOVERNOR
    if _GOVERNOR is None:
        with _GOVERNOR_LOCK:
            if _GOVERNOR is None:
                _GOVERNOR = UsageGovernor()
    return _GOVERNOR
