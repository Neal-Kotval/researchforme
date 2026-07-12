"""Scout mode tests — the engine proposes ownable spaces (STRATEGY Phase 1 §5).

Fully hermetic: the wide trending fetch is stubbed with canned RawItems +
SourceReports (no network), and the LLM is either a hand-rolled fake (grounding
and avoid-list tests) or the fixture backend (degraded-fallback + endpoint
tests). Verifies the contract the UI relies on:

* candidates carry ONLY signals whose URLs come from the supplied input set
  (same grounding discipline as the synthesis evidence gate);
* an unusable / failing LLM degrades to deterministic token-cluster candidates
  flagged ``degraded=True`` — the endpoint never 500s;
* candidates matching the founder's avoid list are excluded.
"""

from __future__ import annotations

import json

import pytest

from app.schemas import RawItem, SourceName, SourceReport, SourceStatus


@pytest.fixture()
def fixture_env(tmp_path, monkeypatch):
    """Force the fixture LLM backend + a hermetic temp cache (no credentials)."""
    monkeypatch.setenv("LLM_BACKEND", "fixture")
    monkeypatch.setenv("CACHE_PATH", str(tmp_path / "test_cache.db"))
    for var in (
        "REDDIT_CLIENT_ID",
        "REDDIT_CLIENT_SECRET",
        "GITHUB_TOKEN",
        "ANTHROPIC_API_KEY",
    ):
        monkeypatch.delenv(var, raising=False)

    from app import cache as cache_mod
    from app import config as config_mod
    from app.llm import client as client_mod

    def _reset() -> None:
        config_mod.get_settings.cache_clear()
        cache_mod._cache = None
        client_mod._client = None

    _reset()
    yield
    _reset()


def _item(source: SourceName, id_: str, title: str, url: str) -> RawItem:
    return RawItem(source=source, id=id_, title=title, url=url, weight=0.9)


def _trending_items() -> list[RawItem]:
    return [
        _item(SourceName.HACKERNEWS, "hn1",
              "Ask HN: why is there no good local-first CRM for tradespeople?",
              "https://news.ycombinator.com/item?id=1"),
        _item(SourceName.HACKERNEWS, "hn2",
              "Show HN: an open-source scheduling tool for tradespeople",
              "https://news.ycombinator.com/item?id=2"),
        _item(SourceName.GITHUB, "gh1",
              "local-first sync engine hits 5k stars in a month",
              "https://github.com/example/localsync"),
        _item(SourceName.REDDIT, "rd1",
              "Tradespeople here: invoicing software is still terrible",
              "https://reddit.com/r/smallbusiness/abc"),
        _item(SourceName.NEWSLETTER, "nl1",
              "Crypto compliance startups are raising again",
              "https://example.com/crypto-compliance"),
        _item(SourceName.ARXIV, "ax1",
              "On-device speech models close the gap with cloud ASR",
              "https://arxiv.org/abs/2406.00001"),
    ]


def _reports() -> list[SourceReport]:
    return [
        SourceReport(name=name, status=SourceStatus.MOCK, item_count=2)
        for name in SourceName
    ]


@pytest.fixture()
def stub_trending(monkeypatch):
    """Stub the wide signal pass so no adapter touches the network."""
    from app.autonomous import scout

    items, reports = _trending_items(), _reports()

    async def _fake_fetch():
        return items, reports

    monkeypatch.setattr(scout, "_fetch_trending", _fake_fetch)
    return items


class _FakeClient:
    """LLM stub returning a fixed candidate array (with one ungrounded plant)."""

    def __init__(self, payload: list[dict]):
        self._payload = payload
        self.prompts: list[str] = []

    async def complete(self, prompt, **kwargs):
        from app.llm.client import LLMResult

        self.prompts.append(prompt)
        return LLMResult(text=json.dumps(self._payload), backend="fake")


def _llm_payload() -> list[dict]:
    return [
        {
            "domain": "Local-first field-service software for tradespeople",
            "rationale": "HN pain + a fast-growing sync engine point at the space.",
            "signals": [
                {"source": "hackernews", "title": "Ask HN: no good local-first CRM",
                 "url": "https://news.ycombinator.com/item?id=1"},
                {"source": "github", "title": "local-first sync engine",
                 "url": "https://github.com/example/localsync"},
                {"source": "hackernews", "title": "HALLUCINATED",
                 "url": "https://news.ycombinator.com/item?id=99999"},
            ],
            "suggested_sub_segments": ["plumbers", "electricians"],
        },
        {
            "domain": "Fully hallucinated space",
            "rationale": "No real signals back this.",
            "signals": [
                {"source": "reddit", "title": "made up",
                 "url": "https://reddit.com/r/fake/xyz"},
            ],
        },
        {
            "domain": "Crypto compliance tooling",
            "rationale": "Newsletter momentum around crypto compliance.",
            "signals": [
                {"source": "newsletter", "title": "Crypto compliance startups",
                 "url": "https://example.com/crypto-compliance"},
                {"source": "reddit", "title": "invoicing pain",
                 "url": "https://reddit.com/r/smallbusiness/abc"},
            ],
        },
    ]


async def test_scout_grounds_signals_and_drops_hallucinated(stub_trending):
    """Signals not in the input set are dropped; fully ungrounded candidates die."""
    from app.autonomous.scout import scout_spaces

    resp = await scout_spaces("", [], _FakeClient(_llm_payload()), "claude-haiku-4-5-20251001")

    allowed = {i.url for i in stub_trending}
    domains = [c.domain for c in resp.candidates]
    assert "Fully hallucinated space" not in domains
    assert any("tradespeople" in d for d in domains)
    for cand in resp.candidates:
        assert not cand.degraded
        assert 1 <= len(cand.signals) <= 4
        for sig in cand.signals:
            assert sig.url in allowed  # grounding gate
    grounded = next(c for c in resp.candidates if "tradespeople" in c.domain)
    assert len(grounded.signals) == 2  # the hallucinated third signal was dropped
    assert grounded.suggested_sub_segments == ["plumbers", "electricians"]
    # Telemetry: one report per registered source.
    assert len(resp.sources) == len(list(SourceName))
    assert resp.generated_at is not None


async def test_scout_avoid_list_excludes_matching_candidates(stub_trending):
    """Candidates matching an avoid term are filtered out."""
    from app.autonomous.scout import scout_spaces

    resp = await scout_spaces(
        "solo technical founder", ["crypto"], _FakeClient(_llm_payload()),
        "claude-haiku-4-5-20251001",
    )

    domains = [c.domain for c in resp.candidates]
    assert not any("crypto" in d.lower() for d in domains)
    assert any("tradespeople" in d for d in domains)


async def test_scout_degrades_to_token_clusters_on_llm_failure(stub_trending):
    """A broken LLM never breaks scout: deterministic clusters, degraded=True."""
    from app.autonomous.scout import scout_spaces

    class _BrokenClient:
        async def complete(self, prompt, **kwargs):
            raise RuntimeError("LLM down")

    resp = await scout_spaces("", [], _BrokenClient(), "claude-haiku-4-5-20251001")

    assert resp.candidates, "fallback must still propose something"
    assert len(resp.candidates) <= 5
    allowed = {i.url for i in stub_trending}
    for cand in resp.candidates:
        assert cand.degraded
        assert cand.rationale
        assert cand.signals, "even degraded candidates carry their signals"
        for sig in cand.signals:
            assert sig.url in allowed
    # 'tradespeople' appears in three item titles → it must cluster.
    assert any("tradespeople" in c.domain.lower() for c in resp.candidates)


def test_scout_endpoint_never_500s(fixture_env, stub_trending):
    """POST /api/projects/scout with the fixture backend: 200 + degraded output.

    The fixture LLM returns canned gap JSON that cannot pass the scout schema /
    grounding gate, so the endpoint must serve the deterministic fallback.
    """
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:
        r = client.post(
            "/api/projects/scout",
            json={"brief": "nights-and-weekends builder", "avoid": ["crypto"]},
        )
    assert r.status_code == 200
    body = r.json()
    assert body["candidates"], "degraded fallback must still return candidates"
    for cand in body["candidates"]:
        assert cand["degraded"] is True
        assert "crypto" not in cand["domain"].lower()
    assert len(body["sources"]) == len(list(SourceName))
    assert body["generated_at"]
