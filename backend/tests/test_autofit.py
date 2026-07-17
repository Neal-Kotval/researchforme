"""Auto-fit — the librarian that clusters loose gaps into fundable projects.

Fully hermetic: the LLM is a hand-rolled fake returning a canned JSON array, or
a fixture-backend fake that yields nothing. No network, no real model. Verifies
the contract the caller relies on:

* a normal run maps each loose gap to a home (existing slug, or "" for a new
  cluster) and echoes back one :class:`FitProposal` per placed gap;
* hallucinated gap_ids and self/hallucinated "combinable_with" entries are
  scrubbed — a proposal can only reference gaps that were actually in the pool;
* every degraded path (fixture backend, empty input, an exploding client,
  garbage output) returns ``[]`` and never raises. A failed fit is a no-op.
"""

from __future__ import annotations

import json

import pytest

from app.autonomous.autofit import FitProposal, propose_fits


class _FakeClient:
    """LLM stub returning a fixed proposal array; records the prompt it saw."""

    def __init__(self, payload):
        self._payload = payload
        self.prompts: list[str] = []

    async def complete(self, prompt, **kwargs):
        from app.llm.client import LLMResult

        self.prompts.append(prompt)
        text = self._payload if isinstance(self._payload, str) else json.dumps(self._payload)
        return LLMResult(text=text, backend="fake")


class _FixtureClient:
    """Stands in for the degraded fixture backend — its output must be ignored."""

    async def complete(self, prompt, **kwargs):
        from app.llm.client import LLMResult

        return LLMResult(text="[]", backend="fixture")


class _ExplodingClient:
    async def complete(self, prompt, **kwargs):
        raise RuntimeError("provider overloaded")


def _gaps():
    return [
        ("g1", "Local-first CRM for tradespeople", "Field techs lose jobs to bad tools."),
        ("g2", "Offline invoicing for plumbers", "Same buyer, the money half of the job."),
        ("g3", "Carbon-accounting for shipping", "Freight emissions nobody can measure."),
    ]


def _projects():
    return [("field-svc", "Field-service software", "Tools for on-site trades.")]


@pytest.mark.asyncio
async def test_maps_each_gap_to_a_home():
    """A normal pass: fit into an existing project, and seed a new cluster."""
    payload = [
        {"gap_id": "g1", "target": "field-svc", "cluster_label": "Field-service software",
         "reason": "Same trades buyer as the project.", "combinable_with": ["g2"]},
        {"gap_id": "g2", "target": "field-svc", "cluster_label": "Field-service software",
         "reason": "The money half of the same job.", "combinable_with": ["g1"]},
        {"gap_id": "g3", "target": "", "cluster_label": "Freight emissions ledger",
         "reason": "Unrelated buyer and problem.", "combinable_with": []},
    ]
    client = _FakeClient(payload)
    out = await propose_fits(_gaps(), _projects(), client, model="cheap")

    assert len(out) == 3
    assert all(isinstance(p, FitProposal) for p in out)
    by_id = {p.gap_id: p for p in out}
    assert by_id["g1"].target == "field-svc"
    assert by_id["g3"].target == ""  # new cluster
    assert by_id["g3"].cluster_label == "Freight emissions ledger"
    assert by_id["g1"].combinable_with == ["g2"]


@pytest.mark.asyncio
async def test_hallucinated_ids_are_scrubbed():
    """A gap_id not in the pool is dropped; a bogus combinable_with is stripped."""
    payload = [
        {"gap_id": "g1", "target": "", "cluster_label": "X", "reason": "r",
         "combinable_with": ["g2", "ghost", "g1"]},  # self + ghost must go
        {"gap_id": "not-real", "target": "", "cluster_label": "Y", "reason": "r",
         "combinable_with": []},  # whole proposal must go
    ]
    out = await propose_fits(_gaps(), _projects(), client=_FakeClient(payload), model="cheap")

    assert [p.gap_id for p in out] == ["g1"]
    assert out[0].combinable_with == ["g2"]


@pytest.mark.asyncio
async def test_fixture_backend_degrades_to_empty():
    """The fixture backend is not a real answer — it must yield no proposals."""
    out = await propose_fits(_gaps(), _projects(), client=_FixtureClient(), model="cheap")
    assert out == []


@pytest.mark.asyncio
async def test_empty_input_is_a_noop():
    """No loose gaps means nothing to cluster — and no LLM call is made."""
    client = _FakeClient([])
    out = await propose_fits([], _projects(), client, model="cheap")
    assert out == []
    assert client.prompts == []  # short-circuited before the call


@pytest.mark.asyncio
async def test_client_exception_never_raises():
    """A failed fit must degrade to [], never break the caller's run."""
    out = await propose_fits(_gaps(), _projects(), client=_ExplodingClient(), model="cheap")
    assert out == []


@pytest.mark.asyncio
async def test_unparseable_output_degrades():
    """Prose the parser can't turn into an array yields no proposals."""
    out = await propose_fits(
        _gaps(), _projects(), client=_FakeClient("sorry, I can't help with that"),
        model="cheap",
    )
    assert out == []
