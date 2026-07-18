"""Graceful shutdown — SSE streams close on a sentinel instead of hanging.

Pins the fix for the restart-hangs-on-open-connections problem: a subscriber's
async generator must RETURN when the store pushes the shutdown sentinel, and the
service's shutdown() must stop workers and close streams without raising.

Hermetic — no server, no network.
"""

from __future__ import annotations

import asyncio

import pytest

from app.autonomous.store import TreeStore


@pytest.mark.asyncio
async def test_subscriber_closes_on_shutdown_sentinel(tmp_path):
    store = TreeStore(path=str(tmp_path / "s.db"))
    gen = store.subscribe("proj-1")

    # Drive the generator to the point where it has registered its queue.
    async def first():
        return await gen.__anext__()

    task = asyncio.ensure_future(first())
    await asyncio.sleep(0.05)  # let subscribe() register before we close it

    store.close_all_subscribers()

    # The pending __anext__ must resolve to StopAsyncIteration (stream closed),
    # not hang forever (which is what blocked uvicorn's shutdown).
    with pytest.raises(StopAsyncIteration):
        await asyncio.wait_for(task, timeout=2)


@pytest.mark.asyncio
async def test_service_shutdown_is_quiet_on_an_idle_service(tmp_path):
    from app.autonomous.governor import UsageGovernor
    from app.autonomous.service import ExplorerService

    store = TreeStore(path=str(tmp_path / "s2.db"))
    service = ExplorerService(store, UsageGovernor())
    # No workers running — shutdown must still complete without raising.
    await asyncio.wait_for(service.shutdown(), timeout=3)
