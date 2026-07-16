"""The `unmeasured` verdict — not seeing a competitor must not pay.

The bug this pins: the gauntlet had three verdicts (survives/weakens/kills) and
a system prompt that (rightly) discouraged killing on absent signal. With no
neutral option, every axis the red team could not settle came back "survives"
— worth ``_SURVIVE_BONUS`` each. A standard run's lenses therefore handed a gap
a large bonus for questions nobody answered.

That is how a power-fleet-economics gap scored 83 while a rival had raised $68M
(NVIDIA's own venture arm among the investors) and shipped inside NVIDIA's Vera
Rubin DSX reference design. Nothing had checked; the score read as if everything
had.

Contract pinned here:

* ``unmeasured`` scores strictly between a kill and a survival — it is worth
  exactly zero, taking neither bonus nor penalty;
* an all-unmeasured gauntlet scores its base composite and no more;
* ``unmeasured`` never counts as support for a "high" confidence rating;
* the summary says UNTESTED, not SURVIVED, when most axes are open.

Hermetic — no LLM, no network.
"""

from __future__ import annotations

from app.autonomous.pressure import _assemble, score_viability
from app.autonomous.schemas import LensVerdict

from tests.test_autonomous import _tiny_gap


def _verdicts(*pairs: tuple[str, str]) -> list[LensVerdict]:
    return [
        LensVerdict(lens=lens, verdict=verdict, argument="because")
        for lens, verdict in pairs
    ]


def test_unmeasured_scores_zero_not_a_survival_bonus():
    """The regression: unmeasured must not pay what a survival pays."""
    gap = _tiny_gap()

    all_survive = _assemble(
        _verdicts(
            ("demand_mirage", "survives"),
            ("just_a_feature", "survives"),
            ("venture_scale", "survives"),
            ("incumbent_countermove", "survives"),
        ),
        "standard",
    )
    all_unmeasured = _assemble(
        _verdicts(
            ("demand_mirage", "unmeasured"),
            ("just_a_feature", "unmeasured"),
            ("venture_scale", "unmeasured"),
            ("incumbent_countermove", "unmeasured"),
        ),
        "standard",
    )

    survived_score, _ = score_viability(gap, all_survive)
    unmeasured_score, _ = score_viability(gap, all_unmeasured)

    assert unmeasured_score < survived_score, (
        "a gauntlet that settled nothing must not score like one that tried and failed"
    )


def test_unmeasured_takes_no_penalty_either():
    """It is an open question, not a hit — it must sit between weakens and survives."""
    gap = _tiny_gap()

    unmeasured, _ = score_viability(
        gap, _assemble(_verdicts(("demand_mirage", "unmeasured")), "light")
    )
    weakened, _ = score_viability(
        gap, _assemble(_verdicts(("demand_mirage", "weakens")), "light")
    )
    survived, _ = score_viability(
        gap, _assemble(_verdicts(("demand_mirage", "survives")), "light")
    )

    assert weakened < unmeasured < survived


def test_unmeasured_tally_is_reported_and_summary_says_untested():
    """A mostly-open gauntlet must not describe itself as SURVIVED."""
    test = _assemble(
        _verdicts(
            ("demand_mirage", "unmeasured"),
            ("just_a_feature", "unmeasured"),
            ("venture_scale", "unmeasured"),
            ("incumbent_countermove", "survives"),
        ),
        "standard",
    )
    assert test.unmeasured == 3
    assert test.survived == 1
    assert "UNTESTED" in test.summary
    assert "3 unmeasured" in test.summary


def test_unmeasured_does_not_earn_high_confidence():
    """Unsettled axes are not support — they must not lift confidence."""
    gap = _tiny_gap()
    _, confidence = score_viability(
        gap,
        _assemble(
            _verdicts(
                ("demand_mirage", "unmeasured"),
                ("just_a_feature", "unmeasured"),
                ("venture_scale", "unmeasured"),
                ("incumbent_countermove", "unmeasured"),
            ),
            "standard",
        ),
    )
    assert confidence != "high"


def test_incumbent_lens_runs_at_standard_rigor():
    """The lens that would have caught Emerald AI must run by default.

    It sat at priority 6 and so only ran at `deep` — meaning the default run
    never asked who already holds the beachhead.
    """
    from app.autonomous.pressure import _RIGOR_COUNT, _select_lenses

    keys = {lens["key"] for lens in _select_lenses("standard")}
    assert "incumbent_countermove" in keys
    assert _RIGOR_COUNT["standard"] >= 5
