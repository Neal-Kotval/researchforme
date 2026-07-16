"""Semantic dedup — the synonym duplicates lexical matching cannot reach.

One live run of a single tree produced five names for one company:

    The Underwriters Lab for Reward Models          (44)
    The Underwriters Laboratories of RL Rewards     (38)
    The Rating Agency for RL Reward Functions       (38)
    Moody's for RL Environments                     (29)
    Consumer Reports for RL Environments            (23)

Pairwise token overlap is ~0, so `is_duplicate_title` cannot merge them without
also merging genuinely distinct ideas. Each cost a full Opus pressure test and a
slot in the shortlist.

Pinned here: the parse contract and — most importantly — that every failure mode
FAILS OPEN. A wrong merge deletes an idea the founder never learns existed; a
missed duplicate costs them a scroll. The asymmetry is deliberate.

Hermetic — the client is faked, no LLM, no network.
"""

from __future__ import annotations

import pytest

from app.autonomous.semdedup import _parse, semantic_duplicate_of

_POOL = [
    ("The Rollout Foundry — sell trajectories by the token", "Sell RL rollouts as a supply business."),
    ("Moody's for RL Environments", "An independent body that rates RL environments."),
    ("Egocentric Data Foundry for Physical AI", "Capture first-person data for world models."),
]


class _FakeResult:
    def __init__(self, text: str, backend: str = "agent-sdk") -> None:
        self.text = text
        self.backend = backend


class _FakeClient:
    def __init__(self, text: str, backend: str = "agent-sdk") -> None:
        self._text = text
        self._backend = backend
        self.calls = 0

    async def complete(self, prompt, **kwargs):  # noqa: ANN001, ANN003
        self.calls += 1
        return _FakeResult(self._text, self._backend)


class _BoomClient:
    async def complete(self, prompt, **kwargs):  # noqa: ANN001, ANN003
        raise RuntimeError("rate limited")


# --------------------------------------------------------------------------- #
# Parsing                                                                      #
# --------------------------------------------------------------------------- #
def test_parse_extracts_index():
    assert _parse('{"duplicate_of": 1, "why": "same rating body"}', 3) == 1


def test_parse_null_is_not_a_duplicate():
    assert _parse('{"duplicate_of": null, "why": "different artifact"}', 3) is None


def test_parse_survives_prose_and_fences():
    assert _parse('Sure!\n```json\n{"duplicate_of": 2, "why": "x"}\n```', 3) == 2


@pytest.mark.parametrize(
    "text",
    [
        "",
        "not json at all",
        '{"duplicate_of": "1"}',      # string, not int
        '{"duplicate_of": true}',      # bool is not an index
        '{"duplicate_of": 9}',         # out of range
        '{"duplicate_of": -1}',        # out of range
        '{"why": "no field"}',
    ],
)
def test_parse_garbage_keeps_the_gap(text):
    """Every unparseable answer must read as 'not a duplicate'."""
    assert _parse(text, 3) is None


# --------------------------------------------------------------------------- #
# The call                                                                     #
# --------------------------------------------------------------------------- #
async def test_detects_the_synonym_duplicate():
    client = _FakeClient('{"duplicate_of": 1, "why": "both rate environments"}')
    idx = await semantic_duplicate_of(
        "The Underwriters Lab for Reward Models",
        "An independent lab that certifies reward functions.",
        _POOL,
        client,
        "claude-haiku-4-5-20251001",
    )
    assert idx == 1  # Moody's for RL Environments


async def test_distinct_idea_is_kept():
    client = _FakeClient('{"duplicate_of": null, "why": "sells vs rates"}')
    idx = await semantic_duplicate_of(
        "The Sovereign Frontier Appliance", "A rack you can own.", _POOL, client, "m"
    )
    assert idx is None


async def test_llm_failure_fails_open():
    """The regression that matters: a dead model must not silently eat ideas."""
    idx = await semantic_duplicate_of("Anything", "Any thesis", _POOL, _BoomClient(), "m")
    assert idx is None


async def test_fixture_backend_is_never_trusted():
    """Fixture output is canned freelancer JSON, not a judgement about this gap."""
    client = _FakeClient('{"duplicate_of": 0, "why": "canned"}', backend="fixture")
    idx = await semantic_duplicate_of("Anything", "Any thesis", _POOL, client, "m")
    assert idx is None


async def test_empty_pool_and_empty_title_skip_the_call():
    """No pool means nothing to duplicate — don't spend a call to learn that."""
    client = _FakeClient('{"duplicate_of": 0}')
    assert await semantic_duplicate_of("A title", "t", [], client, "m") is None
    assert await semantic_duplicate_of("", "t", _POOL, client, "m") is None
    assert client.calls == 0


async def test_index_maps_back_through_the_pool_cap():
    """With a capped comparison window, the returned index must be absolute.

    The pool is truncated to the newest _MAX_EXISTING entries, so a raw index
    into that window points at the wrong idea in the full list.
    """
    from app.autonomous.semdedup import _MAX_EXISTING

    big = [(f"idea {i}", f"thesis {i}") for i in range(_MAX_EXISTING + 5)]
    client = _FakeClient('{"duplicate_of": 0, "why": "first in window"}')
    idx = await semantic_duplicate_of("candidate", "t", big, client, "m")
    # Window starts at len(big) - _MAX_EXISTING, so window index 0 is that item.
    assert idx == len(big) - _MAX_EXISTING
    assert big[idx][0] == f"idea {len(big) - _MAX_EXISTING}"
