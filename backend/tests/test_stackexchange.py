"""Stack Exchange demand source — offline unit tests (no network).

The point of this adapter is unmet-demand sensing: an UNANSWERED, high-view
question outranks an answered one. We test the mapping/weighting directly and the
mock-degrade path, without hitting the API.
"""
import pytest

from app.schemas import SourceName, SourceStatus
from app.sources.stackexchange import StackExchangeSource


def _hit(qid, title, views, answers, answered, score=5, created=1_800_000_000):
    return {
        "question_id": qid, "title": title, "view_count": views,
        "answer_count": answers, "is_answered": answered, "score": score,
        "creation_date": created, "link": f"https://stackoverflow.com/q/{qid}",
        "tags": ["gpu"],
    }


def test_unanswered_question_outranks_an_answered_one_with_similar_views():
    s = StackExchangeSource()
    answered = s._to_raw_item(_hit(1, "answered pain", 3000, 3, True), "stackoverflow")
    unmet = s._to_raw_item(_hit(2, "unmet pain", 3000, 0, False), "stackoverflow")
    assert unmet.weight > answered.weight
    assert unmet.meta["kind"] == "unanswered_pain"


def test_maps_the_fields_a_gap_needs():
    s = StackExchangeSource()
    it = s._to_raw_item(_hit(9, "why is there no tool for X?", 1200, 0, False), "stackoverflow")
    assert it.source is SourceName.STACKEXCHANGE
    assert it.url.endswith("/q/9")
    assert it.meta["views"] == 1200 and it.meta["is_answered"] is False


def test_empty_title_is_dropped():
    assert StackExchangeSource()._to_raw_item(_hit(3, "", 10, 0, False), "stackoverflow") is None


def test_registered_in_the_demand_mix():
    from app.sources.registry import get_sources
    names = {s.name for s in get_sources()}
    assert SourceName.STACKEXCHANGE in names   # feeds normal runs, not pressure-only


def test_mock_degrade_reports_mock_not_crash(monkeypatch, tmp_path):
    """A blocked/failed fetch degrades to the fixture, flagged MOCK."""
    s = StackExchangeSource()
    res = s._mock(["gpu kernel"], note="test degrade")
    assert res.report.status is SourceStatus.MOCK
    assert res.report.item_count >= 1
