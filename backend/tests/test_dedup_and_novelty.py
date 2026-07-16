"""Near-duplicate detection + the obviousness penalty.

Two mode-collapse defects, both observed in live runs:

* **Near-duplicates ate slots.** ``_norm_title`` compared stemless token sets for
  exact equality and called itself "deliberately aggressive". It wasn't: real
  duplicates differ by a synonym or a plural and sailed through. One run scored
  "The Verifiable-Environment Factory" and "Verifier & Environment Foundry for
  domains beyond math and code" at 91 apiece — same idea, two branches, two Opus
  pressure tests, two starred slots in a top-10.

* **Novelty was decorative.** It was collected 1..5, shown to the red team, used
  as a moat proxy in the LLM-free fallback — and left out of ``SCORE_KEYS``, so
  it moved viability by nothing. With no incentive to be original, the engine
  converged on one shape (an observability/dev-tool layer) across protein
  models, surgical robotics, GPU kernels, and RL post-training alike.

Hermetic — no LLM, no network.
"""

from __future__ import annotations

import pytest

from app.autonomous.engine import _title_tokens, is_duplicate_title
from app.autonomous.pressure import _assemble, score_viability
from app.autonomous.schemas import LensVerdict

from tests.test_autonomous import _tiny_gap


# --------------------------------------------------------------------------- #
# Near-duplicate detection                                                     #
# --------------------------------------------------------------------------- #
def test_real_observed_duplicate_pairs_are_caught():
    """The exact pairs that survived the old exact-match test.

    Both scored identically in live runs (67/62 and 87/87) and each ate a slot
    plus an Opus pressure test.
    """
    pairs = [
        (
            "Reward-Function CI — adversarial pre-flight testing for verifiers",
            "Verifier CI — pre-flight fuzzing for RLVR reward functions",
        ),  # Jaccard 0.600
        (
            "Power-First Capacity Planner for the AI Buildout",
            "Stranded-Power Capacity Planning for AI Factories",
        ),  # Jaccard 0.500 — exactly at the threshold, deliberately
    ]
    for first, second in pairs:
        assert is_duplicate_title(second, [first]), f"missed duplicate: {second!r}"


@pytest.mark.xfail(
    reason=(
        "KNOWN LIMIT: synonym duplicates are not reachable by title tokens. "
        "'Verifiable-Environment FACTORY' vs 'Verifier & Environment FOUNDRY' "
        "share only 'environment' -> Jaccard 0.071, and their tags overlap only "
        "0.2. Both scored 91 and both starred in a live run, so this is a real "
        "miss, not a hypothetical. Catching it needs embeddings or an LLM "
        "same-idea check; no lexical threshold separates it from genuinely "
        "distinct ideas without false-merging them. Documented rather than "
        "papered over."
    ),
    strict=True,
)
def test_synonym_duplicates_are_a_known_miss():
    assert is_duplicate_title(
        "Verifier & Environment Foundry for domains beyond math and code",
        ["The Verifiable-Environment Factory (picks-and-shovels for RL post-training)"],
    )


def test_distinct_ideas_are_not_merged():
    """A false merge costs a real idea — the threshold must not be greedy."""
    existing = ["The Verifiable-Environment Factory for RL post-training"]
    distinct = [
        "Moody's for Model Benchmarks — the contamination-proof rating agency",
        "Power-First Capacity Planner for the AI Buildout",
        "Accelerator Residual-Value & Depreciation Engine",
        "Self-verification observability for long-horizon agents",
    ]
    for title in distinct:
        assert not is_duplicate_title(title, existing), f"false merge: {title!r}"


def test_stemming_collapses_plural_and_gerund():
    assert _title_tokens("Verifier Testing") == _title_tokens("Verifiers Tested")


def test_empty_and_unknown_titles_are_never_duplicates():
    assert not is_duplicate_title("", ["anything"])
    assert not is_duplicate_title("Something New", [])
    assert not is_duplicate_title("Something New", [""])


# --------------------------------------------------------------------------- #
# Novelty now moves the score                                                  #
# --------------------------------------------------------------------------- #
def _clean_test():
    return _assemble(
        [LensVerdict(lens="demand_mirage", verdict="survives", argument="holds")],
        "light",
    )


def test_obvious_idea_scores_below_original_one():
    """The regression: novelty used to change nothing at all."""
    obvious = _tiny_gap()
    obvious.novelty = 1
    original = _tiny_gap()
    original.novelty = 5

    obvious_score, _ = score_viability(obvious, _clean_test())
    original_score, _ = score_viability(original, _clean_test())

    assert obvious_score < original_score


def test_novelty_three_is_neutral():
    """Mid novelty must not silently shift every historical score."""
    from app.autonomous.pressure import _NOVELTY_DELTA

    assert _NOVELTY_DELTA[3] == 0.0


# --------------------------------------------------------------------------- #
# Corroboration no longer inflates viability                                   #
# --------------------------------------------------------------------------- #
def test_evidence_against_a_gap_does_not_raise_its_score():
    """The bug: +2/evidence item regardless of which way the evidence cut.

    A red team that documented seven funded competitors handed the gap it was
    attacking the full +10 corroboration bonus.
    """
    from app.schemas import Evidence, SourceName

    gap = _tiny_gap()
    damning = Evidence(
        source=SourceName.ARXIV,
        url="https://arxiv.org/abs/0000.00000",
        quote="the space is crowded with funded competitors",
        live=True,
    )
    bare = _assemble(
        [LensVerdict(lens="moat", verdict="weakens", argument="incumbents ahead")],
        "light",
    )
    with_evidence = _assemble(
        [
            LensVerdict(
                lens="moat",
                verdict="weakens",
                argument="incumbents ahead",
                evidence=[damning, damning, damning],
            )
        ],
        "light",
    )

    bare_score, _ = score_viability(gap, bare)
    evidenced_score, _ = score_viability(gap, with_evidence)

    assert evidenced_score == bare_score, (
        "citing evidence must not change viability — it is a confidence signal"
    )


def test_corroboration_still_lifts_confidence():
    """Removing the viability bonus must not break the confidence path."""
    from app.schemas import Evidence, SourceName

    gap = _tiny_gap()
    live_ev = [
        Evidence(
            source=SourceName.ARXIV,
            url=f"https://arxiv.org/abs/0000.0000{i}",
            quote="real",
            live=True,
        )
        for i in range(3)
    ]
    _, bare_conf = score_viability(
        gap,
        _assemble(
            [LensVerdict(lens="moat", verdict="survives", argument="holds")], "deep"
        ),
    )
    _, rich_conf = score_viability(
        gap,
        _assemble(
            [
                LensVerdict(
                    lens="moat", verdict="survives", argument="holds", evidence=live_ev
                )
            ],
            "deep",
        ),
    )
    order = {"low": 0, "medium": 1, "high": 2}
    assert order[rich_conf] >= order[bare_conf]
