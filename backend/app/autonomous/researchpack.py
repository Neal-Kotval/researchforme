"""Research Pack generation (Phase 4 H2, docs/strategy/phase234-build.md).

ONE strong-model call over a scored gap's payload → a markdown hand-off pack
with exactly four sections: interview scripts (opening with quotes from the
gap's LIVE evidence only — mock/canned quotes are filtered out before the model
ever sees them), a landing-page smoke-test plan, a bottoms-up sizing sketch
built from the demand signals, and a riskiest-assumption → cheapest-falsification
map.

Honest degrade (NOT degrade-to-canned): a research pack is a deliverable the
founder will act on, so a backend that can't produce a real one must say so.
If the LLM is unreachable or returns unusable output (the fixture backend's
canned gaps JSON, an empty string, missing sections), :func:`generate_research_pack`
raises :class:`ResearchPackUnavailable` and the router turns that into a 503
with an honest detail — never a fabricated pack.
"""

from __future__ import annotations

import json

from ..llm.client import ClaudeClient
from ..schemas import Evidence, Gap
from .intake import steering_context_block
from .schemas import Node, Project


class ResearchPackUnavailable(RuntimeError):
    """No usable pack could be generated — surface as an honest 503, never canned."""


# The four contract sections. Generation instructs these exact headings and
# validation requires (most of) them, so a canned/foreign output can't pass.
SECTION_HEADINGS = (
    "## 1. Interview scripts",
    "## 2. Landing-page smoke-test plan",
    "## 3. Bottoms-up sizing sketch",
    "## 4. Riskiest assumption → cheapest falsification",
)

_PACK_SYSTEM = """\
You write a founder's research pack for ONE validated market-gap candidate. Be
concrete and actionable — this is a working document, not a pitch. Quote ONLY
the evidence items supplied in the payload, verbatim, with their URLs; if no
evidence is supplied, write interview openers without quotes and say why. Never
invent quotes, people, numbers, or sources. Output ONLY markdown."""


def live_evidence(gap: Gap) -> list[Evidence]:
    """The gap's evidence items with live provenance — the ONLY quotable set.

    Mock/fixture-derived evidence (``live == False``) must never be quoted in a
    deliverable as if a real person said it (H2: "only live evidence; mock
    quotes excluded").
    """
    return [e for e in (gap.evidence or []) if e.live]


def build_pack_prompt(node: Node, project: Project) -> str:
    """The strong-model prompt for one gap node. Live evidence only."""
    gap = node.gap
    assert gap is not None  # guarded by the caller
    quotable = [
        {"source": e.source.value, "quote": e.quote, "url": e.url, "date": e.date}
        for e in live_evidence(gap)
    ]
    payload = {
        "title": node.title,
        "thesis": gap.thesis,
        "wedge": gap.wedge,
        "why_now": gap.why_now,
        "riskiest_assumption": gap.riskiest_assumption,
        "weakest_link": gap.weakest_link,
        "sub_segment": gap.sub_segment,
        "scores": gap.scores.model_dump(),
        "competitors": [c.model_dump() for c in gap.competitors],
        "company": gap.company.model_dump() if gap.company else None,
        "live_evidence": quotable,
        "viability": node.viability,
        "pressure_summary": node.pressure_test.summary if node.pressure_test else "",
        "self_critique": node.pressure_test.self_critique if node.pressure_test else "",
    }
    steering = steering_context_block(project)
    steer = f"\n\n{steering}" if steering else ""
    evidence_note = (
        "Quote the live_evidence items verbatim as interview openers."
        if quotable
        else "NO live evidence is available — do not quote anyone; open each "
        "script with the hypothesis instead and note the evidence deficit."
    )
    return (
        f"GAP CANDIDATE (validated at viability {node.viability}):\n"
        f"{json.dumps(payload, ensure_ascii=False)}{steer}\n\n"
        "Write the research pack as markdown with EXACTLY these four sections, "
        "using these exact headings:\n"
        f"{SECTION_HEADINGS[0]}\n"
        "Five customer-interview scripts. " + evidence_note + "\n"
        f"{SECTION_HEADINGS[1]}\n"
        "A concrete landing-page smoke test: headline, promise, CTA, traffic "
        "source, budget, and the pass/fail threshold.\n"
        f"{SECTION_HEADINGS[2]}\n"
        "A bottoms-up market-sizing sketch reasoned from the demand signals "
        "above — show the arithmetic and label every assumption as an assumption.\n"
        f"{SECTION_HEADINGS[3]}\n"
        "Map the riskiest assumptions to the cheapest experiment that would "
        "falsify each, ordered by cost.\n"
    )


def looks_like_pack(text: str) -> bool:
    """Does this output pass for a real research pack?

    Rejects empty output, JSON masquerading as markdown (the fixture backend's
    canned gaps array), and anything missing the contract sections (at least 3
    of the 4 exact headings must be present).
    """
    body = (text or "").strip()
    if not body:
        return False
    if body.startswith("{") or body.startswith("["):
        return False
    hits = sum(1 for h in SECTION_HEADINGS if h in body)
    return hits >= 3


async def generate_research_pack(
    node: Node, project: Project, client: ClaudeClient
) -> str:
    """ONE strong-model call → the markdown pack, or :class:`ResearchPackUnavailable`.

    Never returns canned content: an unreachable LLM or unusable output (the
    fixture backend included) raises with an honest reason for the 503.
    """
    if node.gap is None:
        raise ResearchPackUnavailable(
            "This node has no scored gap payload to build a pack from."
        )
    try:
        result = await client.complete(
            build_pack_prompt(node, project),
            system=_PACK_SYSTEM,
            max_turns=1,
            timeout=300,
            model=project.pressure_model,
        )
    except Exception as exc:  # noqa: BLE001 - honest 503, never a canned pack.
        raise ResearchPackUnavailable(
            f"No LLM backend could generate the pack ({type(exc).__name__})."
        ) from exc
    text = (result.text or "").strip()
    if not looks_like_pack(text):
        raise ResearchPackUnavailable(
            f"The '{result.backend}' backend returned no usable research pack — "
            "refusing to serve canned content."
        )
    return text
