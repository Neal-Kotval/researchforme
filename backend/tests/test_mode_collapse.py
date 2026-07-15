"""Anti-mode-collapse: synthesis is told what's already proposed, and an exact
re-proposal is dropped even if the model ignores the instruction.

The observed failure was 23 of 35 gaps being the same 'eval/observability layer
for motion policies' idea. The prompt-level fix (forbid re-proposing) is the real
lever; the post-synthesis dedup is a belt for exact/reordered echoes.
"""
from app.analysis.synthesize import _build_user_prompt
from app.analysis.extract import ExtractedSignals
from app.autonomous.engine import _norm_title


def _signals():
    return ExtractedSignals(area="motion policies", sub_segments=[],
                            demand=[], capability=[], supply=[])


def test_avoid_titles_reach_the_prompt():
    prompt = _build_user_prompt(_signals(), [], "",
                                ["The Eval Layer for Motion Policies",
                                 "Langfuse for Motion Policies"])
    assert "ALREADY PROPOSED" in prompt
    assert "The Eval Layer for Motion Policies" in prompt
    # The instruction must forbid reworded near-duplicates, not just exact strings.
    assert "near-duplicate" in prompt
    assert "EMPTY array" in prompt


def test_no_avoid_titles_leaves_the_prompt_clean():
    assert "ALREADY PROPOSED" not in _build_user_prompt(_signals(), [], "", None)
    assert "ALREADY PROPOSED" not in _build_user_prompt(_signals(), [], "", [])


def test_norm_title_collapses_reorderings_and_framing_words():
    # Same words, different order + framing -> same key (caught by the belt).
    assert _norm_title("The Eval Layer for Motion Policies") == \
           _norm_title("Motion Policies Eval Platform")
    # Genuinely different ideas -> different keys (not falsely merged).
    assert _norm_title("Eval layer for motion policies") != \
           _norm_title("Data marketplace for surgical video")
