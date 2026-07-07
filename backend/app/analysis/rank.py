"""Composite scoring & ranking.

Pure, deterministic, network-free. Turns the LLM's per-gap 1..5 ``Scores`` into a
single user-weighted ``composite`` (0..5) and orders the gaps for the table /
2x2 in the UI.

The composite is a *weighted average*: because ``Weights.normalized()`` returns
weights that sum to 1.0, the dot product of those weights with the 1..5 scores
always lands back in the 1..5 band (and therefore inside the documented 0..5
range). Re-ranking is cheap, so the UI can spin the weight sliders and re-order
instantly without ever re-fetching or re-synthesizing.
"""

from __future__ import annotations

from ..schemas import Gap, RankedGap, Scores, Weights, SCORE_KEYS


def composite_score(scores: Scores, weights: Weights) -> float:
    """Weighted blend of the five 1..5 scores -> a single 0..5 attractiveness.

    ``weights.normalized()`` yields per-key weights summing to 1.0, so this is a
    weighted mean of values in 1..5 and is itself in 1..5 (⊂ 0..5). Rounded to
    3 decimals for stable display / comparison.
    """
    norm = weights.normalized()
    total = sum(norm[k] * getattr(scores, k) for k in SCORE_KEYS)
    return round(total, 3)


def rank_gaps(gaps: list[Gap], weights: Weights) -> list[RankedGap]:
    """Score every gap, sort most-attractive first, and assign 1-based ranks.

    The sort is descending by composite; ties preserve the synthesizer's original
    (already most-attractive-first) ordering, since Python's sort is stable.
    """
    scored = [(gap, composite_score(gap.scores, weights)) for gap in gaps]
    # Stable descending sort by composite; equal composites keep input order.
    scored.sort(key=lambda pair: pair[1], reverse=True)
    return [
        RankedGap(gap=gap, composite=composite, rank=i + 1)
        for i, (gap, composite) in enumerate(scored)
    ]
