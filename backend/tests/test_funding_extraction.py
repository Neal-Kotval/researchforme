"""Funding-round extraction (Phase 3 C1, last bullet).

An LLM-free extraction pass in ``analysis/extract.py`` over newsletter RawItems
matching funding-announcement patterns (raised / Series X / seed / $-amounts),
producing structured crowding hints {company, round_hint, space_tokens, url}
carried on ``ExtractedSignals.funding`` and surfaced in ``_signals_payload`` so
the pressure-test gap payload can see crowding evidence.

Guarantees covered:
* positive patterns: "X raises $NM Series A", "X lands $N.NM seed round";
* negative patterns: no funding language, and funding-ish verbs without an
  amount or round label ("raised concerns") produce nothing;
* provenance: hints mined from a MOCK newsletter feed carry live=False;
* payload: ``_signals_payload`` exposes ``funding_signals``.
"""

from __future__ import annotations

from app.analysis.extract import extract_signals
from app.analysis.scope import scope_area
from app.analysis.synthesize import _signals_payload
from app.schemas import (
    ExtractedSignals,
    FundingHint,
    RawItem,
    SourceName,
    SourceReport,
    SourceStatus,
)
from app.sources.base import FetchResult


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #
def _newsletter_fetch(items: list[RawItem], status: SourceStatus = SourceStatus.LIVE):
    return {
        SourceName.NEWSLETTER: FetchResult(
            items=items,
            report=SourceReport(name=SourceName.NEWSLETTER, status=status),
        )
    }


def _item(title: str, body: str = "", url: str = "https://example.com/post") -> RawItem:
    return RawItem(
        source=SourceName.NEWSLETTER, id=url, title=title, body=body, url=url, weight=1.0
    )


def _extract(items: list[RawItem], status: SourceStatus = SourceStatus.LIVE) -> ExtractedSignals:
    scope = scope_area("test area", [])
    return extract_signals("test area", scope, _newsletter_fetch(items, status))


# --------------------------------------------------------------------------- #
# Positive patterns                                                            #
# --------------------------------------------------------------------------- #
def test_series_round_announcement_extracted():
    signals = _extract(
        [
            _item(
                "Acme Robotics raises $12M Series A to automate warehouse picking",
                url="https://example.com/acme",
            )
        ]
    )
    assert len(signals.funding) == 1
    hint = signals.funding[0]
    assert isinstance(hint, FundingHint)
    assert hint.company == "Acme Robotics"
    assert "series a" in hint.round_hint.lower()
    assert "$12m" in hint.round_hint.lower()
    assert hint.url == "https://example.com/acme"
    assert "warehouse" in hint.space_tokens
    # company / verb / amount tokens never leak into the space tokens
    assert "acme" not in hint.space_tokens
    assert "raises" not in hint.space_tokens


def test_seed_round_announcement_extracted():
    signals = _extract(
        [_item("Fintechly lands $3.5M seed round for SMB bookkeeping automation")]
    )
    assert len(signals.funding) == 1
    hint = signals.funding[0]
    assert hint.company == "Fintechly"
    assert "seed" in hint.round_hint.lower()
    assert "bookkeeping" in hint.space_tokens


def test_secured_with_body_amount_extracted():
    signals = _extract(
        [
            _item(
                "Loopwell secures Series B funding",
                body="The company announced it secured $40 million to expand its "
                "elder-care scheduling platform.",
            )
        ]
    )
    assert len(signals.funding) == 1
    hint = signals.funding[0]
    assert hint.company == "Loopwell"
    assert "series b" in hint.round_hint.lower()


def test_hints_deduped_by_company():
    signals = _extract(
        [
            _item("Acme raises $5M seed round", url="https://example.com/1"),
            _item("Acme raises a $5M seed round from Foo Ventures", url="https://example.com/2"),
        ]
    )
    assert len(signals.funding) == 1


# --------------------------------------------------------------------------- #
# Negative patterns                                                            #
# --------------------------------------------------------------------------- #
def test_non_funding_newsletter_items_produce_nothing():
    signals = _extract(
        [
            _item("The state of AI agents in 2026"),
            _item("Why most productivity tools fail their users"),
        ]
    )
    assert signals.funding == []


def test_funding_verb_without_amount_or_round_is_ignored():
    # "raised" alone is not a funding announcement.
    signals = _extract([_item("Developers raised concerns about the new pricing model")])
    assert signals.funding == []


def test_non_newsletter_sources_do_not_feed_funding():
    scope = scope_area("test area", [])
    reddit_item = RawItem(
        source=SourceName.REDDIT, id="r1",
        title="Acme raises $12M Series A", body="", url="https://reddit.com/1", weight=1.0,
    )
    fetched = {
        SourceName.REDDIT: FetchResult(
            items=[reddit_item],
            report=SourceReport(name=SourceName.REDDIT, status=SourceStatus.LIVE),
        )
    }
    signals = extract_signals("test area", scope, fetched)
    assert signals.funding == []


# --------------------------------------------------------------------------- #
# Provenance                                                                   #
# --------------------------------------------------------------------------- #
def test_mock_newsletter_hints_carry_live_false():
    signals = _extract(
        [_item("Acme raises $5M seed round")], status=SourceStatus.MOCK
    )
    assert len(signals.funding) == 1
    assert signals.funding[0].live is False


def test_live_newsletter_hints_carry_live_true():
    signals = _extract([_item("Acme raises $5M seed round")])
    assert signals.funding[0].live is True


# --------------------------------------------------------------------------- #
# Payload seam                                                                 #
# --------------------------------------------------------------------------- #
def test_signals_payload_includes_funding():
    signals = _extract(
        [_item("Acme Robotics raises $12M Series A to automate warehouse picking")]
    )
    payload = _signals_payload(signals)
    assert "funding_signals" in payload
    assert len(payload["funding_signals"]) == 1
    entry = payload["funding_signals"][0]
    assert entry["company"] == "Acme Robotics"
    assert set(entry) == {"company", "round_hint", "space_tokens", "url", "live"}


def test_signals_payload_funding_empty_when_none():
    signals = _extract([_item("The state of AI agents in 2026")])
    payload = _signals_payload(signals)
    assert payload["funding_signals"] == []
