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
_DAILY_WINDOW = 86400.0      # rolling 24h window for the shared daily cap
_DAILY_BUCKET = 3600.0       # bucket width for the daily meter (memory-bounded)
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

# Rate bands (tokens/min) for the qualitative gauge when no cap is set.
_RATE_BANDS: tuple[tuple[int, str], ...] = (
    (2_000, "low"), (8_000, "medium"), (20_000, "high"),
)


def _usage_level(ratio: float | None, rate_per_min: int) -> str:
    """A ``claude usage``-style band: low / medium / high / heavy.

    When a usage cap is set we band by how close spend is to it (``ratio``); with
    no cap, we fall back to the raw token *rate* so the gauge still reads sensibly.
    """
    if ratio is not None:
        if ratio < 0.40:
            return "low"
        if ratio < 0.70:
            return "medium"
        if ratio < 0.90:
            return "high"
        return "heavy"
    for threshold, label in _RATE_BANDS:
        if rate_per_min < threshold:
            return label
    return "heavy"


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
            # Aggressive by default so a sprinting run keeps many agents busy;
            # override with AP_MAX_CONCURRENCY. The rate-limit backoff (§6.1) is
            # the real safety valve, so we can afford to fan out wide.
            env = os.getenv("AP_MAX_CONCURRENCY")
            if env and env.isdigit() and int(env) > 0:
                max_concurrency = int(env)
            else:
                # Fan out wide by default — the rate-limit backoff (§6.1) is the
                # real safety valve, so a big semaphore just means idle slots when
                # the backend is slow, not overload. Clamp raised 12 → 32.
                max_concurrency = max(8, min(32, (os.cpu_count() or 4) * 3))
        self._max_concurrency = max_concurrency
        self._sem = asyncio.Semaphore(max_concurrency)
        self._lock = threading.Lock()

        # Rolling spend + rate accounting (global across projects).
        self._spent_total = 0                              # lifetime, for reporting
        self._recent: deque[tuple[float, int]] = deque()   # (ts, tokens) in window
        self._limit_hits: deque[float] = deque()           # 429 timestamps in window
        # Rolling 24h spend for the shared daily cap, in hourly buckets so memory
        # stays bounded (≤25 buckets) instead of growing per-call over a long run.
        self._daily: deque[list[float]] = deque()          # [bucket_start_ts, tokens]

        # Exponential backoff memory (SPEC §6.1).
        self._consecutive_limits = 0
        self._backoff_until = 0.0

        # Dynamic usage shaping: a global rolling-24h token cap and a target
        # percent of it (e.g. 0.95) that all projects cooperatively stay under.
        # As spend approaches cap*pct the governor curbs, then pauses — so a run
        # automatically shapes itself around a usage limit you set.
        self._global_daily_cap: int | None = None
        self._usage_limit_pct: float = 1.0

        # Durable ledger, set by attach_store(). None = in-memory only, which is
        # the pre-restart-amnesia behaviour and fine for tests.
        self._store = None

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
        # balanced: three-quarters of the cap (raised from half), at least one —
        # so several projects still each fan out wide without a project starving.
        return max(1, (cap * 3) // 4)

    # ----------------------------------------------------------------- #
    # Metering & rate-limit feedback                                    #
    # ----------------------------------------------------------------- #
    def attach_store(self, store) -> None:  # noqa: ANN001 - avoid an import cycle
        """Give the governor durable memory and rehydrate its 24h window.

        Without this the ledger is in-memory only, so every process restart
        silently resets it: after a full day of work the governor reported 8,642
        tokens against 3,051,952 actually spent. A rolling-24h cap that forgets
        on restart can never bind, and ``headroom`` therefore always answered
        "ample" — which silently pinned test rigor and made the usage bar
        fiction. Idempotent; safe to call on every boot.
        """
        self._store = store
        now = time.time()
        try:
            buckets = store.usage_buckets(now - _DAILY_WINDOW)
        except Exception:  # noqa: BLE001 - a broken ledger must not stop boot
            return
        with self._lock:
            self._daily = deque([list(b) for b in buckets])
            restored = int(sum(b[1] for b in self._daily))
            # spent_total is "lifetime for reporting"; the durable 24h window is
            # the honest floor for it after a restart.
            self._spent_total = max(self._spent_total, restored)

    def record_usage(self, tokens: int) -> None:
        """Meter ``tokens`` of spend (called after every LLM call)."""
        if tokens <= 0:
            return
        now = time.time()
        with self._lock:
            self._spent_total += tokens
            self._recent.append((now, tokens))
            self._add_daily(now, tokens)
            self._prune(now)
            # A successful call after the backoff window has passed clears the
            # consecutive-limit memory so we don't back off forever.
            if now >= self._backoff_until:
                self._consecutive_limits = 0
            bucket = now - (now % _DAILY_BUCKET)
        # Persist OUTSIDE the lock: SQLite has its own, and holding both invites
        # a deadlock on a path that runs after every single LLM call.
        #
        # Guarded even though TreeStore.add_usage swallows its own errors: this
        # runs after EVERY LLM call, and metering must never be able to raise
        # into a live expansion. Defence in depth — the store handle is injected,
        # so "it never raises" is a promise about today's implementation, not a
        # property of this call site. Losing a token from the ledger is cheap;
        # losing a node to a metering exception is not.
        store = getattr(self, "_store", None)
        if store is None:
            return
        try:
            store.add_usage(bucket, float(tokens))
            store.prune_usage(now - _DAILY_WINDOW - _DAILY_BUCKET)
        except Exception:  # noqa: BLE001 - metering must never break a run
            pass

    def set_policy(
        self, *, daily_cap_tokens: int | None = None, limit_pct: float | None = None
    ) -> None:
        """Set the global usage-shaping policy: a rolling-24h cap and a % target.

        ``daily_cap_tokens`` is the shared "100%" reference; ``limit_pct`` (0.05..1)
        is the fraction of it the fleet aims to stay under. Passing 0/None for the
        cap clears it (unbounded). Idempotent and thread-safe.
        """
        with self._lock:
            if daily_cap_tokens is not None:
                self._global_daily_cap = daily_cap_tokens if daily_cap_tokens > 0 else None
            if limit_pct is not None:
                self._usage_limit_pct = max(0.05, min(1.0, float(limit_pct)))

    def _effective_cap(self) -> int | None:
        """The shaped ceiling = global daily cap × limit%. Caller holds the lock."""
        if not self._global_daily_cap:
            return None
        return int(self._global_daily_cap * self._usage_limit_pct)

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

    def _add_daily(self, now: float, tokens: int) -> None:
        """Fold ``tokens`` into the current hourly bucket. Caller holds lock."""
        bucket = now - (now % _DAILY_BUCKET)
        if self._daily and self._daily[-1][0] == bucket:
            self._daily[-1][1] += tokens
        else:
            self._daily.append([bucket, float(tokens)])

    def _daily_spent(self, now: float) -> int:
        """Tokens metered in the trailing 24h, pruning stale buckets. Holds lock."""
        horizon = now - _DAILY_WINDOW
        while self._daily and self._daily[0][0] < horizon:
            self._daily.popleft()
        return int(sum(b[1] for b in self._daily))

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
            # Measure the shared daily cap against a *rolling 24h* spend, not the
            # process-lifetime total, so a long-lived server doesn't falsely
            # exhaust the "daily" cap and never recover (audit HIGH #3).
            day = self._daily_spent(time.time())
            fracs.append(
                max(0.0, (budget.daily_cap_tokens - day) / budget.daily_cap_tokens)
            )
        # Global usage-shaping cap (× limit%): the fleet-wide ceiling the user set.
        eff = self._effective_cap()
        if eff:
            day = self._daily_spent(time.time())
            fracs.append(max(0.0, (eff - day) / eff))
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

    def snapshot(self) -> dict:
        """A clean, real global usage snapshot for the always-on usage bar (SPEC §6/§10.3).

        Everything here is a *measured* number the governor already tracks — no
        guessing: lifetime + rolling-24h spend, the current token rate (tokens in
        the trailing 60s), the shared mode, whether we're in rate-limit backoff and
        for how long, recent 429s, and the shared concurrency ceiling. The UI bar
        is driven by this, so several tabs show one honest, cooperating meter.
        """
        now = time.time()
        with self._lock:
            self._prune(now)
            rate = int(sum(tok for _, tok in self._recent))  # tokens/60s ≈ /min
            in_backoff = now < self._backoff_until
            day = self._daily_spent(now)
            eff = self._effective_cap()
            ratio = (day / eff) if eff else None
            return {
                "spent_total": self._spent_total,
                "daily_spent": day,
                "rate_per_min": rate,
                "mode": self._mode.value,
                "in_backoff": in_backoff,
                "backoff_remaining_s": max(0.0, self._backoff_until - now) if in_backoff else 0.0,
                "recent_limits": len(self._limit_hits),
                "max_concurrency": self._max_concurrency,
                # Usage-shaping policy + derived gauge fields.
                "daily_cap": self._global_daily_cap,
                "limit_pct": self._usage_limit_pct,
                "effective_cap": eff,
                "usage_ratio": ratio,  # 0..1+ against the effective cap (None = uncapped)
                "usage_level": _usage_level(ratio, rate),
                "projected_24h": rate * 1440,  # tokens/day if the current rate holds
            }

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
    """Return the process-wide governor shared by every project.

    On first creation it registers its :meth:`note_rate_limit` with the LLM
    client's observer hook, so real 429 / ``retry-after`` signals from live calls
    actually drive the backoff (SPEC §6.1) instead of the meter running blind.
    """
    global _GOVERNOR
    if _GOVERNOR is None:
        with _GOVERNOR_LOCK:
            if _GOVERNOR is None:
                _GOVERNOR = UsageGovernor()
                try:
                    from ..llm.client import register_rate_limit_listener

                    register_rate_limit_listener(_GOVERNOR.note_rate_limit)
                except Exception:  # noqa: BLE001 - wiring must never break startup
                    pass
    return _GOVERNOR
