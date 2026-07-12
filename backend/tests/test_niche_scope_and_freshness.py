"""Task 4 regressions: niche query construction + Reddit freshness floor.

(a) ``scope_area`` must not dilute an out-of-vocab niche area's search keywords
    with generic default-segment tokens ("freelancers", "small businesses", ...).
(b) The Reddit adapter's live/keyless mapping must drop posts older than the
    configured freshness floor (default 18 months), mirroring HN's cutoff.
No network involved anywhere here.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.analysis.scope import scope_area
from app.sources.reddit import RedditSource

_GENERIC_SMB_TOKENS = {"freelancers", "small", "businesses", "growing", "teams"}

_NICHE_AREA = "compliance tooling for independent pharmacies"


# --------------------------------------------------------------------------- #
# Scope: niche keywords stay niche                                             #
# --------------------------------------------------------------------------- #
def test_out_of_vocab_area_keywords_keep_niche_phrase_and_tokens():
    scope = scope_area(_NICHE_AREA, [])
    lowered = [k.lower() for k in scope.keywords]
    # The full multi-word phrase stays the primary query...
    assert lowered[0] == _NICHE_AREA
    # ...and its significant tokens survive as secondaries.
    for tok in ("compliance", "tooling", "independent", "pharmacies"):
        assert tok in lowered


def test_out_of_vocab_area_keywords_exclude_generic_smb_tokens():
    scope = scope_area(_NICHE_AREA, [])
    lowered = {k.lower() for k in scope.keywords}
    assert not (lowered & _GENERIC_SMB_TOKENS), (
        f"generic SMB tokens leaked into search keywords: {lowered & _GENERIC_SMB_TOKENS}"
    )


def test_default_segments_still_frame_the_audience():
    # The SMB defaults remain as sub_segment FRAMING even though they no longer
    # feed the search keywords.
    scope = scope_area(_NICHE_AREA, [])
    assert scope.sub_segments == ["freelancers", "small businesses", "growing teams"]


def test_caller_supplied_segments_still_inject_keyword_tokens():
    scope = scope_area(_NICHE_AREA, ["hospital pharmacists"])
    lowered = {k.lower() for k in scope.keywords}
    assert "pharmacists" in lowered


# --------------------------------------------------------------------------- #
# Reddit: freshness floor on the live-path item mapping                        #
# --------------------------------------------------------------------------- #
def _post(created: datetime, title: str = "I wish there was a tool for this") -> dict:
    return {
        "id": "abc",
        "title": title,
        "selftext": "so frustrating",
        "permalink": "/r/test/comments/abc/post/",
        "created_utc": created.timestamp(),
        "ups": 250,
        "num_comments": 12,
        "subreddit": "test",
    }


def test_reddit_drops_posts_older_than_freshness_floor():
    src = RedditSource()
    three_years_old = datetime.now(timezone.utc) - timedelta(days=3 * 365)
    assert src._to_raw_item(_post(three_years_old)) is None


def test_reddit_keeps_recent_posts():
    src = RedditSource()
    recent = datetime.now(timezone.utc) - timedelta(days=30)
    item = src._to_raw_item(_post(recent))
    assert item is not None
    assert item.created is not None


def test_reddit_keeps_dateless_posts():
    # Missing created_utc must not trip the floor (weight already handles it).
    src = RedditSource()
    post = _post(datetime.now(timezone.utc))
    post.pop("created_utc")
    assert src._to_raw_item(post) is not None


def test_reddit_mock_path_exempt_from_floor():
    # Fixture posts may carry old dates; the offline pipeline must keep working.
    src = RedditSource()
    result = src._fetch_mock(["compliance"], note="test")
    assert result.items, "mock fixture should still yield items"
