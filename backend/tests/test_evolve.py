"""The evolutionary operators — crossover, mutation, and parent selection.

Pins the contract of ``app.autonomous.evolve``: both operators emit a VALID
``Gap`` (so offspring flow through the same scoring gauntlet as any synthesized
gap), tag their lineage, degrade to ``None`` on a fixture backend or bad output,
and the selection helpers bias toward promising + novel + distant parents.

Hermetic — a fake client, no LLM, no network.
"""

from __future__ import annotations

import json

import pytest

from app.autonomous.evolve import (
    MUTATION_STRATEGIES,
    crossover_gaps,
    mutate_gap,
    select_crossover_pairs,
    select_mutation_targets,
)

from tests.test_autonomous import _tiny_gap


def _gap_json(title: str = "Fused idea") -> str:
    """A minimal but schema-valid Gap object, as the operator would emit it."""
    return json.dumps(
        [
            {
                "title": title,
                "thesis": "A genuinely non-obvious hybrid bet.",
                "scores": {
                    "demand_strength": 4,
                    "competitive_openness": 4,
                    "trend_tailwind": 4,
                    "feasibility": 3,
                    "willingness_to_pay": 4,
                },
                "wedge": "Land the one buyer both parents pointed at.",
                "riskiest_assumption": "The two insights actually compound.",
                "weakest_link": "Integration complexity.",
                "novelty": 5,
                "sub_segment": "edge inference ops",
                "tags": ["hybrid"],
            }
        ]
    )


class _FakeClient:
    """Returns a fixed text/backend, and records the last system prompt seen."""

    def __init__(self, text: str, backend: str = "api") -> None:
        self._text = text
        self.backend = backend
        self.last_system = None
        self.last_prompt = None

    async def complete(self, prompt, **kwargs):  # noqa: ANN001
        self.last_system = kwargs.get("system", "")
        self.last_prompt = prompt
        from types import SimpleNamespace

        return SimpleNamespace(text=self._text, backend=self.backend)


@pytest.mark.asyncio
async def test_crossover_emits_a_valid_tagged_gap():
    client = _FakeClient(_gap_json("Telemetry-as-a-datatype for agents"))
    child = await crossover_gaps(_tiny_gap("a"), _tiny_gap("b"), client, "m")
    assert child is not None
    assert child.title == "Telemetry-as-a-datatype for agents"
    assert "crossover" in child.tags  # lineage stamped for the tree/UI.


@pytest.mark.asyncio
async def test_mutation_applies_a_known_strategy_and_tags_it():
    client = _FakeClient(_gap_json("Sell to the payer, not the user"))
    child = await mutate_gap(_tiny_gap(), "invert_buyer", client, "m")
    assert child is not None
    assert "mutation" in child.tags
    assert "mut:invert_buyer" in child.tags
    # The strategy text must actually reach the model.
    assert "INVERT THE BUYER" in client.last_prompt


@pytest.mark.asyncio
async def test_unknown_mutation_strategy_is_a_noop():
    client = _FakeClient(_gap_json())
    assert await mutate_gap(_tiny_gap(), "not_a_real_strategy", client, "m") is None


@pytest.mark.asyncio
async def test_operators_refuse_a_fixture_backend():
    """A fixture backend returns canned text — it cannot fuse or twist anything."""
    fix = _FakeClient(_gap_json(), backend="fixture")
    assert await crossover_gaps(_tiny_gap("a"), _tiny_gap("b"), fix, "m") is None
    assert await mutate_gap(_tiny_gap(), "contrarian", fix, "m") is None


@pytest.mark.asyncio
async def test_empty_array_means_no_worthwhile_offspring():
    """The crossover prompt allows [] when two parents can't fuse into more."""
    client = _FakeClient("[]")
    assert await crossover_gaps(_tiny_gap("a"), _tiny_gap("b"), client, "m") is None


@pytest.mark.asyncio
async def test_unparseable_output_degrades_to_none():
    client = _FakeClient("I could not think of a fusion, sorry.")
    assert await crossover_gaps(_tiny_gap("a"), _tiny_gap("b"), client, "m") is None


def test_crossover_selection_prefers_distant_high_fitness_pairs():
    a = _tiny_gap("gpu kernels")
    a.title = "Kernel autotuner"
    b = _tiny_gap("protein folding")
    b.title = "Protein design copilot"
    c = _tiny_gap("gpu kernels")
    c.title = "Kernel profiler"  # near-duplicate topic of a
    pairs = select_crossover_pairs([(a, 70), (b, 68), (c, 40)], k=1)
    assert pairs, "a non-trivial pool must yield at least one pair"
    titles = {pairs[0][0].title, pairs[0][1].title}
    # The strongest parent (a) should pair with the DISTANT b, not the near-dup c.
    assert "Kernel autotuner" in titles and "Protein design copilot" in titles


def test_mutation_targeting_is_weakest_link_aware():
    crowded = _tiny_gap()
    crowded.weakest_link = "The space is crowded with incumbents."
    small = _tiny_gap()
    small.weakest_link = "The niche is too small to scale."
    picks = dict(
        (g.weakest_link, strat)
        for g, strat in select_mutation_targets([(crowded, 60), (small, 58)], k=2)
    )
    assert picks["The space is crowded with incumbents."] == "contrarian"
    assert picks["The niche is too small to scale."] == "escalate"


def test_every_named_strategy_has_a_move():
    # No strategy key can be dangling — mutate_gap looks each one up.
    assert set(MUTATION_STRATEGIES) >= {
        "invert_buyer", "invert_model", "adjacent_need",
        "contrarian", "escalate", "sharpen_wedge",
    }
