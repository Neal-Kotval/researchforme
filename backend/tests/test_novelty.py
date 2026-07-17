"""Novelty-distance scan — placing a gap against the funded landscape, hermetically.

The module exists to catch the engine's core failure: proposing an idea that a
funded incumbent already occupies. These tests pin its contract with a FAKE client
(no network, no real web search, no LLM):

* a canned NoveltyScan JSON parses and validates into the typed result;
* a fixture backend yields None — a canned landscape must never be scored as real;
* a garbage / non-JSON answer degrades to None instead of raising;
* the "occupied" path (a funded incumbent named, low novelty) round-trips.

Hermetic — no LLM, no network.
"""

from __future__ import annotations

import asyncio

from app.autonomous.novelty import NoveltyScan, novelty_scan

from tests.test_autonomous import _tiny_gap


class _CannedResult:
    """Stand-in for LLMResult: only .text and .backend are read by the module."""

    def __init__(self, text: str, backend: str = "api") -> None:
        self.text = text
        self.backend = backend


class _FakeClient:
    """Returns a fixed answer without touching the network or web search.

    Asserts web=True is actually requested — the whole point of this module is the
    live-landscape search, so a caller that dropped the flag is a bug.
    """

    def __init__(self, text: str, backend: str = "api") -> None:
        self._text = text
        self._backend = backend
        self.last_web = None

    async def complete(self, prompt, *, system=None, model=None, web=False, **kw):
        self.last_web = web
        return _CannedResult(self._text, self._backend)


_OCCUPIED_JSON = """
Here is my assessment:
{
  "nearest_known": [
    {"name": "EdgeImpulse", "url": "https://edgeimpulse.com",
     "why_similar": "Already ships TinyML models to MCUs with an OTA pipeline.",
     "funded": "Series B, $34M"},
    {"name": "Latent AI", "url": "https://latentai.com",
     "why_similar": "Edge model optimization + deployment to constrained devices.",
     "funded": "Series A"}
  ],
  "novelty_0_100": 18,
  "verdict": "occupied",
  "rationale": "Edge Impulse already owns OTA model delivery to microcontrollers; this sits on top of them."
}
Let me know if you want more detail.
"""


def test_canned_scan_parses_and_validates():
    """A well-formed answer round-trips into a typed NoveltyScan, web=True."""
    client = _FakeClient(_OCCUPIED_JSON)
    scan = asyncio.run(novelty_scan(_tiny_gap(), client, "claude-opus-4-8"))

    assert isinstance(scan, NoveltyScan)
    assert client.last_web is True, "novelty must run web-enabled — it's a live-landscape search"
    assert scan.verdict == "occupied"
    assert scan.novelty_0_100 == 18
    assert len(scan.nearest_known) == 2
    assert scan.nearest_known[0].name == "EdgeImpulse"
    assert scan.nearest_known[0].funded == "Series B, $34M"
    assert "Edge Impulse" in scan.rationale


def test_fixture_backend_yields_none():
    """A fixture backend returns canned gaps for any prompt — it must NOT be scored."""
    client = _FakeClient(_OCCUPIED_JSON, backend="fixture")
    assert asyncio.run(novelty_scan(_tiny_gap(), client, "claude-opus-4-8")) is None


def test_unparseable_answer_degrades_to_none():
    """Prose with no JSON object must degrade, never raise."""
    client = _FakeClient("I could not find any comparable companies, sorry.")
    assert asyncio.run(novelty_scan(_tiny_gap(), client, "claude-opus-4-8")) is None


def test_invalid_object_degrades_to_none():
    """A JSON object missing the required score (or out of range) fails validation → None."""
    client = _FakeClient('{"verdict": "novel", "rationale": "no score here"}')
    assert asyncio.run(novelty_scan(_tiny_gap(), client, "claude-opus-4-8")) is None

    out_of_range = _FakeClient('{"novelty_0_100": 250, "verdict": "novel", "rationale": "x"}')
    assert asyncio.run(novelty_scan(_tiny_gap(), out_of_range, "claude-opus-4-8")) is None


def test_llm_exception_degrades_to_none():
    """Any error from the client is swallowed — a failed scan never breaks a run."""

    class _Boom:
        async def complete(self, *a, **k):
            raise RuntimeError("all backends failed")

    assert asyncio.run(novelty_scan(_tiny_gap(), _Boom(), "claude-opus-4-8")) is None


def test_nearest_known_capped_at_three():
    """The contract is 'up to 3' — a model that returns more is trimmed."""
    many = """{
      "nearest_known": [
        {"name": "A", "url": "", "why_similar": "", "funded": ""},
        {"name": "B", "url": "", "why_similar": "", "funded": ""},
        {"name": "C", "url": "", "why_similar": "", "funded": ""},
        {"name": "D", "url": "", "why_similar": "", "funded": ""},
        {"name": "E", "url": "", "why_similar": "", "funded": ""}
      ],
      "novelty_0_100": 40, "verdict": "adjacent", "rationale": "several neighbors"
    }"""
    scan = asyncio.run(novelty_scan(_tiny_gap(), _FakeClient(many), "claude-opus-4-8"))
    assert scan is not None
    assert len(scan.nearest_known) == 3
    assert [c.name for c in scan.nearest_known] == ["A", "B", "C"]
