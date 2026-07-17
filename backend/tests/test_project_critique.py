"""Project-level adversarial critique — the red team, aimed at the assembled bet.

A gap gets seven lenses, web search, a score, and a falsification plan. The
project it becomes — the thing a founder commits a year to — used to get only a
synthesis pass that argues FOR itself. This module runs the same rigor against
the platform thesis.

Pinned here is the scoring calibration, which is deliberately NOT the gap's. A
rigorous red team of a real company weakens on nearly every lens (every genuine
business has honest concerns), so at gap constants six weakens (-54) score below
a single fatal kill (capped at 40) — a company with six manageable concerns
ranking below one with a fatal flaw. That inversion is the bug this fixes.

Hermetic — the scoring is pure; no LLM, no network.
"""

from __future__ import annotations

from app.autonomous.projectcritique import _score_project, _synthetic_gap
from app.autonomous.pressure import _assemble
from app.autonomous.schemas import LensVerdict

_SCORES = {
    "scores": {
        "demand_strength": 3,
        "competitive_openness": 3,
        "trend_tailwind": 4,
        "feasibility": 3,
        "willingness_to_pay": 3,
    },
    "novelty": 4,
}


def _gap():
    return _synthetic_gap("Test Platform", "an assembled thesis", _SCORES)


def _test(verdicts):
    return _assemble(
        [LensVerdict(lens=f"l{i}", verdict=v, argument="x") for i, v in enumerate(verdicts)],
        "deep",
    )


def test_a_kill_scores_worse_than_all_weakens():
    """The core inversion this calibration fixes.

    At the gap constants, six weakens (-54) beat a single kill (capped at 40) —
    so an honest-concerns-everywhere company ranked below a fatally-flawed one.
    A kill must be the worse outcome.
    """
    gap = _gap()
    one_kill = _score_project(gap, _test(["kills"] + ["weakens"] * 6))[0]
    all_weak = _score_project(gap, _test(["weakens"] * 7))[0]
    assert one_kill < all_weak


def test_all_weakens_is_low_but_not_dead():
    """Six honest concerns is 'unproven', not 'kill it' — the 0 that misled."""
    score = _score_project(_gap(), _test(["weakens"] * 6 + ["unmeasured"]))[0]
    assert 15 < score < 45


def test_all_survive_scores_high():
    """A thesis that beat every lens is genuinely strong."""
    assert _score_project(_gap(), _test(["survives"] * 7))[0] >= 90


def test_unmeasured_is_neutral_not_a_penalty():
    """An axis the red team couldn't settle must not be scored as our failure."""
    gap = _gap()
    with_unmeasured = _score_project(gap, _test(["survives"] * 3 + ["unmeasured"] * 4))[0]
    just_three = _score_project(gap, _test(["survives"] * 3))[0]
    # The four unmeasured axes add nothing and subtract nothing beyond being
    # fewer survivals — the score reflects only the three earned survivals.
    assert with_unmeasured == just_three


def test_synthetic_gap_is_company_shaped():
    """The scoring vehicle must read as a standalone company, not a feature."""
    gap = _gap()
    assert gap.company is not None
    assert gap.company.standalone is True
    assert gap.company.expansion_path  # non-empty → venture-scale fallback isn't punitive


def test_scores_clamp_to_valid_range():
    """Garbage LLM scores must not crash or escape 1..5."""
    gap = _synthetic_gap("X", "t", {"scores": {"demand_strength": 99}, "novelty": "bad"})
    assert 1 <= gap.scores.demand_strength <= 5
    assert 1 <= gap.novelty <= 5
