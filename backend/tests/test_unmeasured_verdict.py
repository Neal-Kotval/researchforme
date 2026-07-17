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
    """It is an open question, not a hit — it must sit between weakens and survives.

    A real survives on a second lens keeps the test from being all-unmeasured, so
    the untested cap doesn't apply and we isolate the per-verdict delta.
    """
    gap = _tiny_gap()

    def score(second_verdict):
        return score_viability(
            gap,
            _assemble(
                _verdicts(("moat", "survives"), ("demand_mirage", second_verdict)),
                "light",
            ),
        )[0]

    unmeasured = score("unmeasured")
    weakened = score("weakens")
    survived = score("survives")

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


# --------------------------------------------------------------------------- #
# The LLM-free fallback must not let a gap grade its own exam                  #
# --------------------------------------------------------------------------- #
def test_heuristic_fallback_is_unmeasured_not_survives():
    """The regression: a gap the red team never ran scored 98.

    "Token Dispatch Silicon" came back with all three lenses reading "Heuristic
    fallback (no LLM verdict available)", an empty evidence list, and viability
    98 — 18 of those points awarded for surviving a test that never happened.
    The fallback can only read the gap's OWN scores, so it cannot test anything;
    it can only ask the defendant how the trial went.
    """
    from app.autonomous.pressure import _neutral_pressure_test

    gap = _tiny_gap()
    gap.scores.demand_strength = 5
    gap.scores.willingness_to_pay = 5
    test = _neutral_pressure_test(gap, "light")

    assert test.survived == 0, "a self-graded exam is not a survival"
    assert test.unmeasured == len(test.lenses)
    assert "UNTESTED" in test.summary


def test_heuristic_fallback_scores_no_higher_than_its_base():
    """No lens bonus may come from a gauntlet that never ran — and it's capped."""
    from app.autonomous.pressure import (
        _NOVELTY_DELTA,
        _UNTESTED_CAP,
        _neutral_pressure_test,
        score_viability as sv,
    )
    from app.analysis.rank import composite_score
    from app.schemas import Weights

    gap = _tiny_gap()
    viability, confidence = sv(gap, _neutral_pressure_test(gap, "light"))
    base = (composite_score(gap.scores, Weights()) - 1.0) / 4.0 * 100.0
    self_reported = base + _NOVELTY_DELTA.get(int(gap.novelty), 0.0)
    # The fallback (all-unmeasured) adds no survival bonus AND is capped as
    # untested: it can only be its own self-report, and never above the cap.
    assert viability <= self_reported + 1
    assert viability <= _UNTESTED_CAP
    assert confidence == "low"


def test_self_admitted_trap_still_kills_without_an_llm():
    """An admission against interest is real signal, not a self-grade."""
    from app.autonomous.pressure import _neutral_verdict

    gap = _tiny_gap()
    gap.empty_for_a_reason = True
    gap.empty_reason = "regulator forbids it"
    assert _neutral_verdict(gap, "empty_for_a_reason").verdict == "kills"


def test_fully_untested_gap_is_capped():
    """A red team that never ran can't certify a gap as strong.

    Observed live: gaps showing viability 77 with 0/3 real lenses — the LLM
    failed, every lens fell back to unmeasured, and viability was just the gap's
    OWN self-reported composite (which it rated 5/5/5/5/5). An untested number
    read as 'strong'. It's a candidate to test, not a validated bet.
    """
    from app.autonomous.pressure import (
        _UNTESTED_CAP,
        _assemble,
        score_viability,
    )
    from app.autonomous.schemas import LensVerdict

    gap = _tiny_gap()
    for k in ("demand_strength", "competitive_openness", "willingness_to_pay",
              "trend_tailwind", "feasibility"):
        setattr(gap.scores, k, 5)

    all_unmeasured = _assemble(
        [LensVerdict(lens=f"l{i}", verdict="unmeasured", argument="x") for i in range(3)],
        "light",
    )
    v, conf = score_viability(gap, all_unmeasured)
    assert v <= _UNTESTED_CAP, "an all-unmeasured red team must not score strong"
    assert conf == "low"

    # One real verdict lifts it out of the cap (it was actually tested).
    partly_tested = _assemble(
        [
            LensVerdict(lens="l0", verdict="survives", argument="held"),
            LensVerdict(lens="l1", verdict="unmeasured", argument="x"),
            LensVerdict(lens="l2", verdict="unmeasured", argument="x"),
        ],
        "light",
    )
    v2, _ = score_viability(gap, partly_tested)
    assert v2 > _UNTESTED_CAP, "a partially-tested gap is not capped as untested"
