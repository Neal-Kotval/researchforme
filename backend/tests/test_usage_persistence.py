"""The usage governor must not forget on restart.

The ledger — _spent_total, _recent, and the rolling-24h _daily buckets — lived
in memory only. After a full day of real work the governor reported 8,642 tokens
against 3,051,952 actually spent across projects, because the process had been
restarted a handful of times.

That is not a cosmetic bug:

* the shared rolling-24h cap resets to zero on every restart, so it can never
  bind — the safety valve is bypassable by `kill -9`;
* `headroom()` therefore always answers "ample", which silently pins test rigor
  to its most expensive setting;
* the usage bar in the UI reports a fiction.

Pinned here: spend survives a governor restart, the window is honest, and the
table stays bounded.

Hermetic — real temp SQLite, no LLM, no network.
"""

from __future__ import annotations

import time

import pytest

from app.autonomous.governor import _DAILY_WINDOW, UsageGovernor
from app.autonomous.store import TreeStore


@pytest.fixture()
def store(tmp_path):
    return TreeStore(path=str(tmp_path / "usage.db"))


def test_spend_survives_a_restart(store):
    """The regression: a new process must not start believing it spent nothing."""
    g1 = UsageGovernor()
    g1.attach_store(store)
    g1.record_usage(120_000)
    g1.record_usage(80_000)
    assert g1.snapshot()["daily_spent"] == 200_000

    # Process dies, a fresh governor boots against the same store.
    g2 = UsageGovernor()
    g2.attach_store(store)
    assert g2.snapshot()["daily_spent"] == 200_000, (
        "a restarted governor must remember the last 24h — otherwise the daily "
        "cap resets before it can ever bind"
    )


def test_detached_governor_still_works(store):
    """No store attached = in-memory only. Tests and CLI paths rely on this."""
    g = UsageGovernor()
    g.record_usage(5_000)
    assert g.snapshot()["daily_spent"] == 5_000


def test_ledger_ignores_spend_older_than_the_window(store):
    """The 24h window is rolling, not cumulative-forever."""
    now = time.time()
    store.add_usage(now - _DAILY_WINDOW - 7200, 999_999)  # well outside
    store.add_usage(now - 60, 1_000)                      # inside
    g = UsageGovernor()
    g.attach_store(store)
    assert g.snapshot()["daily_spent"] == 1_000


def test_a_restarted_governor_still_enforces_a_cap(store):
    """The point of persisting: the cap must bind across a restart."""
    g1 = UsageGovernor()
    g1.attach_store(store)
    g1.set_policy(daily_cap_tokens=100_000)
    g1.record_usage(99_000)

    g2 = UsageGovernor()
    g2.attach_store(store)
    g2.set_policy(daily_cap_tokens=100_000)
    snap = g2.snapshot()
    assert snap["daily_spent"] == 99_000
    # Nearly exhausted — headroom must not read as "ample" just because the
    # process is new.
    from app.autonomous.schemas import Budget

    assert g2.headroom(Budget(), 99_000) != "ample"


def test_table_stays_bounded(store):
    """One row per hour bucket, pruned past the window — never per-call growth."""
    g = UsageGovernor()
    g.attach_store(store)
    for _ in range(50):
        g.record_usage(10)
    buckets = store.usage_buckets(0)
    assert len(buckets) <= 2, f"50 calls should fold into ~1 bucket, got {len(buckets)}"


def test_store_failures_never_break_metering(store, monkeypatch):
    """Metering runs after every LLM call — it must never raise into a run."""

    def _boom(*a, **k):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(store, "add_usage", _boom)
    monkeypatch.setattr(store, "usage_buckets", _boom)
    g = UsageGovernor()
    g.attach_store(store)      # rehydrate fails -> must not raise
    g.record_usage(1_000)      # persist fails -> must not raise
    assert g.snapshot()["daily_spent"] == 1_000  # in-memory accounting still correct
