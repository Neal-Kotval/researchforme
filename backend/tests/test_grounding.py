"""Evidence grounding + per-evidence provenance (anti-fabrication seam).

Covers the four audit findings this seam fixes:

* per-item provenance: mock-source signals carry ``live=False`` so they can
  never masquerade as live data downstream;
* the grounding gate: a live-LLM Evidence.url NOT present in the mined signals
  (or tool fetches) is dropped and counted, and a gap stripped of all evidence
  is kept but flagged ``grounded=False``;
* the relaxed output contract: 0-8 gaps / 1-6 evidence items — returning zero
  gaps on thin evidence is honest behavior, not a failure to paper over;
* fixture honesty: canned FIXTURE_GAPS_JSON output is unmistakably marked
  (warning + per-gap 'fixture' tag) and exempt from the grounding gate so the
  zero-dependency demo path still produces gaps.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from app.analysis.scope import scope_area
from app.analysis.extract import extract_signals
from app.analysis.synthesize import (
    SYSTEM_PROMPT,
    build_corroboration_tools,
    synthesize,
)
from app.llm.client import LLMResult
from app.llm.fixture_synthesis import FIXTURE_GAPS_JSON
from app.schemas import (
    DemandSignal,
    ExtractedSignals,
    RawItem,
    SourceName,
    SourceReport,
    SourceStatus,
)
from app.sources.base import FetchResult


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #
class StubClient:
    """Minimal ClaudeClient stand-in: returns a fixed text/backend pair."""

    def __init__(self, text: str, backend: str = "api") -> None:
        self._text = text
        self._backend = backend

    async def complete(self, prompt, **kwargs) -> LLMResult:  # noqa: ANN001
        return LLMResult(text=self._text, backend=self._backend)


def _signals(url: str = "https://www.reddit.com/r/x/comments/1/known/") -> ExtractedSignals:
    return ExtractedSignals(
        area="test area",
        demand=[
            DemandSignal(
                source=SourceName.REDDIT,
                kind="pain",
                quote="a real mined quote",
                url=url,
                strength=0.8,
                live=True,
            )
        ],
    )


def _gap_json(evidence: list[dict]) -> str:
    return json.dumps(
        [
            {
                "title": "Test gap",
                "thesis": "one line",
                "scores": {
                    "demand_strength": 4,
                    "competitive_openness": 3,
                    "trend_tailwind": 3,
                    "feasibility": 4,
                    "willingness_to_pay": 3,
                },
                "evidence": evidence,
                "wedge": "wedge",
                "riskiest_assumption": "assumption",
                "weakest_link": "link",
            }
        ]
    )


_KNOWN = "https://www.reddit.com/r/x/comments/1/known/"
_HALLUCINATED = "https://www.reddit.com/r/x/comments/9/made_up/"


# --------------------------------------------------------------------------- #
# Per-item provenance through extraction                                       #
# --------------------------------------------------------------------------- #
def test_signals_carry_live_provenance():
    """extract_signals stamps live=False on mock-source signals, True on live."""
    scope = scope_area("test area", [])
    reddit_item = RawItem(
        source=SourceName.REDDIT, id="r1", title="I hate that this is broken",
        body="so frustrating", url="https://reddit.com/r/x/1", weight=10.0,
    )
    hn_item = RawItem(
        source=SourceName.HACKERNEWS, id="h1", title="Ask HN: tool for X?",
        body="", url="https://news.ycombinator.com/item?id=1", weight=5.0,
        meta={"kind": "ask", "points": 5},
    )
    fetched = {
        SourceName.REDDIT: FetchResult(
            items=[reddit_item],
            report=SourceReport(name=SourceName.REDDIT, status=SourceStatus.MOCK),
        ),
        SourceName.HACKERNEWS: FetchResult(
            items=[hn_item],
            report=SourceReport(name=SourceName.HACKERNEWS, status=SourceStatus.LIVE),
        ),
    }
    signals = extract_signals("test area", scope, fetched)
    reddit_sigs = [d for d in signals.demand if d.source == SourceName.REDDIT]
    hn_sigs = [d for d in signals.demand if d.source == SourceName.HACKERNEWS]
    assert reddit_sigs and all(d.live is False for d in reddit_sigs)
    assert hn_sigs and all(d.live is True for d in hn_sigs)


# --------------------------------------------------------------------------- #
# The grounding gate (live-LLM output)                                         #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_grounding_gate_drops_hallucinated_evidence():
    text = _gap_json(
        [
            {"source": "reddit", "url": _KNOWN, "quote": "real"},
            {"source": "reddit", "url": _HALLUCINATED, "quote": "fabricated"},
        ]
    )
    gaps, backend, warnings = await synthesize(_signals(_KNOWN), [], StubClient(text))
    assert backend == "api"
    assert len(gaps) == 1
    gap = gaps[0]
    assert [e.url for e in gap.evidence] == [_KNOWN]
    assert gap.evidence[0].live is True
    assert gap.evidence_dropped == 1
    assert gap.grounded is True
    assert any("ungrounded" in w for w in warnings)


@pytest.mark.asyncio
async def test_gap_with_all_ungrounded_evidence_is_flagged():
    text = _gap_json([{"source": "reddit", "url": _HALLUCINATED, "quote": "fab"}])
    gaps, _backend, warnings = await synthesize(_signals(_KNOWN), [], StubClient(text))
    assert len(gaps) == 1
    gap = gaps[0]
    assert gap.evidence == []
    assert gap.evidence_dropped == 1
    assert gap.grounded is False
    assert gap.title == "Test gap"  # other fields survive
    assert any("ungrounded" in w for w in warnings)


@pytest.mark.asyncio
async def test_empty_array_is_honest_not_fixture_fallback():
    """A live model returning [] on thin evidence must NOT trigger fixtures."""
    gaps, backend, warnings = await synthesize(_signals(), [], StubClient("[]"))
    assert gaps == []
    assert backend == "api"
    assert not any("FIXTURE" in w for w in warnings)
    assert any("zero gaps" in w for w in warnings)


# --------------------------------------------------------------------------- #
# Fixture honesty + fixture-mode exemption                                     #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_fixture_backend_exempt_from_gate_and_marked():
    client = StubClient(FIXTURE_GAPS_JSON, backend="fixture")
    gaps, backend, warnings = await synthesize(_signals(), [], client)
    assert backend == "fixture"
    assert gaps, "fixture-mode runs must still produce gaps"
    # exempt from the grounding drop: canned evidence survives intact...
    assert all(g.evidence for g in gaps if not g.empty_for_a_reason)
    # ...but every gap is unmistakably marked as canned.
    assert all("fixture" in g.tags for g in gaps)
    assert all(e.live is False for g in gaps for e in g.evidence)
    assert any("FIXTURE" in w for w in warnings)


# --------------------------------------------------------------------------- #
# Relaxed output contract                                                      #
# --------------------------------------------------------------------------- #
def test_output_contract_relaxed():
    assert "0-8" in SYSTEM_PROMPT
    assert "4-8" not in SYSTEM_PROMPT
    assert "3-6" not in SYSTEM_PROMPT
    assert "1-6" in SYSTEM_PROMPT


# --------------------------------------------------------------------------- #
# Corroboration tools feed the grounded-URL set                                #
# --------------------------------------------------------------------------- #
def test_corroboration_tools_record_fetched_urls(monkeypatch):
    fetched_url = "https://www.reddit.com/r/x/comments/2/tool_hit/"

    class FakeSource:
        def fetch(self, area, keywords, sub_segments):  # noqa: ANN001
            return FetchResult(
                items=[
                    RawItem(
                        source=SourceName.REDDIT, id="t1", title="hit",
                        url=fetched_url, weight=1.0,
                    )
                ],
                report=SourceReport(name=SourceName.REDDIT, status=SourceStatus.LIVE),
            )

    from app.sources import registry

    monkeypatch.setattr(registry, "get_source", lambda name: FakeSource())

    sink: dict[str, bool] = {}
    tools = build_corroboration_tools("test area", [], url_sink=sink)
    reddit_tool = next(t for t in tools if t.name == "search_reddit")
    out = asyncio.run(reddit_tool.handler({"query": "pain"}))
    assert fetched_url in out
    assert sink.get(fetched_url.rstrip("/")) is True
