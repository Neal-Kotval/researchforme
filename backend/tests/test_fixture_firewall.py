"""The fixture firewall — canned gaps must never reach a real run.

``_via_fixture`` ignores the prompt entirely and returns the same canned
freelancer-finance gaps for ANY call. That output is *structurally valid* (a
JSON array of objects with "title"), so every downstream parser accepts it
happily — which is exactly why a leak is silent rather than loud.

This bit for real: an agent-sdk run whose ``decompose`` calls hit a rate limit
cascaded to fixtures and minted "Real-time quarterly tax autopilot for 1099
income" as a child of an MoE-training subarea, in three separate branches. The
run reported 0 gaps and no kills rather than an error.

Contract pinned here:

* a live run never *cascades into* fixtures — it raises when its real backends
  are exhausted, because a loud failure is recoverable and a silent fintech
  tree is not;
* an explicit ``LLM_BACKEND=fixture`` run still gets its fixtures, so the
  zero-dependency demo path keeps working.

Hermetic — no network, no LLM.
"""

from __future__ import annotations

import pytest

from app.llm.client import ClaudeClient
from app.llm.fixture_synthesis import FIXTURE_GAPS_JSON


@pytest.fixture()
def live_client(monkeypatch):
    """A client resolved to the subscription backend, as a real run is."""
    monkeypatch.setenv("LLM_BACKEND", "agent-sdk")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    from app.config import get_settings

    get_settings.cache_clear()
    return ClaudeClient()


@pytest.fixture()
def fixture_client(monkeypatch):
    """A client explicitly asked for fixtures — the demo path."""
    monkeypatch.setenv("LLM_BACKEND", "fixture")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    from app.config import get_settings

    get_settings.cache_clear()
    return ClaudeClient()


def test_live_run_never_cascades_into_fixtures(live_client):
    """The regression: 'fixture' must not be in a live run's fallback chain."""
    assert "fixture" not in live_client._fallback_order()


def test_explicit_fixture_run_still_serves_fixtures(fixture_client):
    """The demo path is not collateral damage."""
    assert fixture_client._fallback_order() == ["fixture"]
    assert fixture_client._via_fixture().text == FIXTURE_GAPS_JSON


async def test_live_run_raises_when_real_backends_fail(live_client, monkeypatch):
    """A live run that exhausts its backends fails LOUDLY.

    Before the fix this returned canned fintech gaps with backend='fixture'
    and every caller that ignored the flag treated them as real findings.
    """

    async def _boom(*args, **kwargs):
        raise RuntimeError("simulated rate limit")

    monkeypatch.setattr(live_client, "_via_agent_sdk", _boom)
    monkeypatch.setattr(live_client, "_via_cli", _boom)

    with pytest.raises(RuntimeError, match="All LLM backends failed"):
        await live_client.complete("decompose this MoE training subarea")
