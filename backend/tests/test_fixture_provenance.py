"""Tests for fixture provenance: fake URLs + honest fetch-time MOCK status.

The landmine: fixtures used to attach FABRICATED content to REAL, resolvable
identifiers (e.g. genuine arXiv IDs like 2502.09033), so a silent live->fixture
degrade produced gaps citing real papers that say something completely
different. These tests pin the defusal:

* every URL in every source fixture is non-resolvable by construction
  (reserved example.org hosts, invalid ``0000.*`` arXiv IDs, out-of-range HN
  objectIDs); reddit's synthetic ``1a2b3c*`` permalinks are already fake;
* an adapter that degrades to fixture mid-fetch reports MOCK for THAT fetch
  (health reflects config capability; fetch reports reflect reality);
* ``extract_signals``' per-item ``live`` stamping (eabae99) consumes that
  fetch-time status, so fixture-served arXiv items yield ``live=False``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from app.analysis.extract import extract_signals
from app.analysis.scope import scope_area
from app.schemas import SourceName, SourceStatus
from app.sources.arxiv import ArxivSource

_FIXTURES = Path("app/sources/fixtures")

# Hosts that can never resolve to real content: IANA-reserved example.org (and
# subdomains). arXiv links are additionally allowed only with an invalid
# ``0000.*`` ID; reddit permalinks only with the synthetic ``1a2b3c*`` post ids.
_FAKE_ARXIV_RE = re.compile(r"^https?://arxiv\.org/abs/0000\.\d+$")
_FAKE_REDDIT_RE = re.compile(r"^https?://(www\.)?reddit\.com/r/\w+/comments/1a2b3c")
_URL_RE = re.compile(r"https?://[^\s\"<>]+")


def _is_defused(url: str) -> bool:
    host = url.split("/")[2] if "//" in url else ""
    if host == "example.org" or host.endswith(".example.org"):
        return True
    if _FAKE_ARXIV_RE.match(url) or _FAKE_REDDIT_RE.match(url):
        return True
    # HN item pages are minted by the adapter from objectID, not the fixture;
    # the fixture itself must not carry any other real host.
    return False


@pytest.fixture()
def hermetic_env(tmp_path, monkeypatch):
    """Temp cache + settings reset so fetches never touch a shared cache."""
    monkeypatch.setenv("CACHE_PATH", str(tmp_path / "test_cache.db"))

    from app import cache as cache_mod
    from app import config as config_mod

    def _reset() -> None:
        config_mod.get_settings.cache_clear()
        cache_mod._cache = None

    _reset()
    yield
    _reset()


# --------------------------------------------------------------------------- #
# Every fixture URL is non-resolvable                                          #
# --------------------------------------------------------------------------- #
def test_all_fixture_urls_are_non_resolvable():
    offenders: list[str] = []
    for path in sorted(_FIXTURES.glob("*.json")):
        for url in _URL_RE.findall(path.read_text()):
            if not _is_defused(url.rstrip('",')):
                offenders.append(f"{path.name}: {url}")
    assert not offenders, (
        "fixtures must never carry real, resolvable URLs (fabricated content "
        f"on real IDs is a citation landmine): {offenders}"
    )


def test_arxiv_fixture_ids_are_unmistakably_fake():
    rows = json.loads((_FIXTURES / "arxiv.json").read_text())
    assert rows, "arXiv fixture must not be empty"
    for row in rows:
        assert str(row["id"]).startswith("0000."), row["id"]
        assert _FAKE_ARXIV_RE.match(row["link"]), row["link"]


def test_hackernews_fixture_object_ids_are_out_of_range():
    payload = json.loads((_FIXTURES / "hackernews.json").read_text())
    for hit in payload["hits"]:
        # 99xxxxxxxx is far beyond any real HN item id, so the minted
        # news.ycombinator.com/item?id= URL can never resolve to a real story.
        assert hit["objectID"].startswith("99"), hit["objectID"]
        assert len(hit["objectID"]) == 10


# --------------------------------------------------------------------------- #
# Degraded fetch is loud: MOCK status + live=False stamping                    #
# --------------------------------------------------------------------------- #
def _degraded_arxiv_fetch(monkeypatch):
    def _boom(self, terms, max_results, months_back):
        raise ConnectionError("network unreachable (test)")

    monkeypatch.setattr(ArxivSource, "_fetch_live", _boom)
    return ArxivSource().fetch("physical therapy clinics", ["physical therapy"], [])


def test_degraded_arxiv_fetch_reports_mock(hermetic_env, monkeypatch):
    result = _degraded_arxiv_fetch(monkeypatch)
    assert result.report.status is SourceStatus.MOCK
    assert "fixture" in (result.report.note or "")
    assert result.items, "the fixture must still feed the pipeline"
    for item in result.items:
        assert _FAKE_ARXIV_RE.match(item.url), item.url


def test_fixture_served_arxiv_items_stamp_live_false(hermetic_env, monkeypatch):
    result = _degraded_arxiv_fetch(monkeypatch)
    scope = scope_area("physical therapy clinics", [])
    signals = extract_signals(
        "physical therapy clinics", scope, {SourceName.ARXIV: result}
    )
    arxiv_caps = [s for s in signals.capability if s.source is SourceName.ARXIV]
    assert arxiv_caps, "arXiv fixture rows must surface as capability signals"
    for sig in arxiv_caps:
        assert sig.live is False
        assert _FAKE_ARXIV_RE.match(sig.url), sig.url
