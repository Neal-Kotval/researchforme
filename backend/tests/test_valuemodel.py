"""Value modeling degrades honestly and only certifies a costed number.

``model_value`` is a bonus lens: it prices the status quo a gap attacks and
derives an annual ROI with the formula shown. Like every operator here it must
NEVER break a run — a fixture backend, an exception, or output that won't
validate all have to fall back to ``None``. And a real value model must round-
trip the analyst's costed fields (status quo, drivers, the formula-bearing
annual number, the assumptions it rests on).

Hermetic — no LLM, no network. A fake client stands in for the model.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from app.autonomous.schemas import Project, SteeringContext
from app.autonomous.valuemodel import ValueModel, model_value

from tests.test_autonomous import _tiny_gap

_CANNED = {
    "status_quo": (
        "A firmware team hand-patches each MCU over a bench cable, one board at a "
        "time, then re-flashes the whole image when a model changes."
    ),
    "current_cost": (
        "~6 engineer-hours/week reflashing across a 40-board fleet; at a $75 "
        "loaded rate that is ~$450/wk, plus ~2 bricked boards/quarter at $300 each."
    ),
    "cost_drivers": [
        "full-image reflash instead of a delta",
        "manual per-board bench time",
        "rework from bricked boards",
    ],
    "solution_delta": (
        "OTA delta-diffing removes ~70% of the reflash labor and most bricking by "
        "shipping only the changed model bytes."
    ),
    "annual_value": (
        "6 hrs/wk x $75/hr x 48 wks x 0.70 automatable = ~$15,120/yr labor, plus "
        "~$2,400/yr avoided rework = ~$17,500/yr per typical buyer."
    ),
    "assumptions": [
        "$75/hr loaded engineer rate",
        "40-board fleet, ~weekly model changes",
        "70% of reflash labor is automatable",
    ],
    "confidence": "medium",
}


class _FakeClient:
    """Returns a canned ValueModel JSON object as a live (non-fixture) backend."""

    def __init__(self, text: str, backend: str = "api") -> None:
        self._text = text
        self._backend = backend
        self.calls: list[dict] = []

    async def complete(self, prompt, system=None, model=None, max_turns=1,
                        timeout=0, **kwargs):
        self.calls.append({"prompt": prompt, "system": system, "model": model})
        return SimpleNamespace(text=self._text, backend=self._backend)


def _project() -> Project:
    return Project(
        id="p1",
        domain="on-device inference",
        steering=SteeringContext(brief="Ex-firmware founder, sells to TinyML teams."),
    )


async def test_model_value_parses_a_costed_annual_number():
    """A real value model round-trips the analyst's fields, formula included."""
    client = _FakeClient(json.dumps(_CANNED))
    vm = await model_value(_tiny_gap(), _project(), client, "claude-opus-4-8")

    assert isinstance(vm, ValueModel)
    assert vm.confidence == "medium"
    assert "15,120" in vm.annual_value and "x" in vm.annual_value  # the formula survived
    assert len(vm.cost_drivers) == 3
    assert "70% of reflash labor is automatable" in vm.assumptions
    assert client.calls, "the strong-model call must have been made"


async def test_model_value_accepts_a_fenced_object():
    """Models love ```json fences and a bare object (no array) — both must parse."""
    fenced = "```json\n" + json.dumps(_CANNED) + "\n```"
    vm = await model_value(_tiny_gap(), _project(), _FakeClient(fenced), "m")
    assert isinstance(vm, ValueModel)
    assert vm.status_quo.startswith("A firmware team")


async def test_fixture_backend_yields_none():
    """The fixture backend must degrade to None — never a fabricated ROI."""
    client = _FakeClient(json.dumps(_CANNED), backend="fixture")
    assert await model_value(_tiny_gap(), _project(), client, "m") is None


async def test_unparseable_output_yields_none():
    """Prose with no JSON object degrades to None, not an exception."""
    client = _FakeClient("I could not model this — insufficient information.")
    assert await model_value(_tiny_gap(), _project(), client, "m") is None


async def test_invalid_shape_yields_none():
    """Valid JSON that isn't a ValueModel (missing required fields) → None."""
    client = _FakeClient(json.dumps({"annual_value": "$10k"}))
    assert await model_value(_tiny_gap(), _project(), client, "m") is None


async def test_llm_exception_yields_none():
    """A raising client must never break a run — swallowed to None."""

    class _Boom:
        async def complete(self, *a, **k):
            raise RuntimeError("backend on fire")

    assert await model_value(_tiny_gap(), _project(), _Boom(), "m") is None
