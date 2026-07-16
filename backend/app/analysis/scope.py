"""Scope derivation.

Turns a free-text ``area`` (and optional caller-supplied ``sub_segments``) into
a small, deterministic, network-free ``Scope``: a handful of query keywords the
source adapters can search on, plus 2-4 sub-segments to focus the hunt.

Everything here is pure and reproducible — the same input always yields the same
Scope, which keeps the ingest cache keys stable across runs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# --------------------------------------------------------------------------- #
# Static, hand-tuned vocab                                                     #
# --------------------------------------------------------------------------- #
# Words that carry no search signal — dropped before we build keywords.
_STOPWORDS = frozenset(
    {
        "the", "a", "an", "and", "or", "for", "to", "of", "in", "on", "with",
        "at", "by", "from", "as", "is", "are", "be", "that", "this", "it",
        "app", "apps", "tool", "tools", "software", "platform", "solution",
        "solutions", "service", "services", "system", "systems", "market",
        "industry", "space", "product", "products", "stuff", "thing", "things",
    }
)

# Light synonym / adjacency expansion. Kept intentionally small and generic so
# it nudges recall without hallucinating a whole taxonomy. Matched on lowercased
# single tokens found in the area.
_SYNONYMS: dict[str, tuple[str, ...]] = {
    "invoice": ("billing", "invoicing"),
    "invoicing": ("billing", "invoice"),
    "billing": ("invoicing", "payments"),
    "payment": ("payments", "billing"),
    "payments": ("payment", "billing"),
    "budget": ("budgeting", "expense"),
    "budgeting": ("budget", "expense tracking"),
    "expense": ("expenses", "spending"),
    "finance": ("financial", "money"),
    "financial": ("finance", "money"),
    "fitness": ("workout", "exercise"),
    "workout": ("fitness", "training"),
    "nutrition": ("diet", "meal planning"),
    "diet": ("nutrition", "meal planning"),
    "scheduling": ("calendar", "booking"),
    "calendar": ("scheduling", "booking"),
    "booking": ("scheduling", "reservations"),
    "crm": ("customer relationship", "sales pipeline"),
    "sales": ("pipeline", "outreach"),
    "marketing": ("advertising", "campaigns"),
    "email": ("inbox", "newsletter"),
    "notes": ("note taking", "knowledge management"),
    "productivity": ("workflow", "task management"),
    "task": ("tasks", "todo"),
    "project": ("projects", "project management"),
    "hiring": ("recruiting", "recruitment"),
    "recruiting": ("hiring", "talent"),
    "hr": ("human resources", "people ops"),
    "payroll": ("compensation", "wages"),
    "inventory": ("stock", "warehouse"),
    "logistics": ("shipping", "fulfillment"),
    "ecommerce": ("online store", "retail"),
    "retail": ("ecommerce", "storefront"),
    "education": ("learning", "edtech"),
    "learning": ("education", "courses"),
    "healthcare": ("health", "medical"),
    "mental": ("wellness", "therapy"),
    "wellness": ("wellbeing", "mental health"),
    "legal": ("compliance", "contracts"),
    "contract": ("contracts", "agreements"),
    "insurance": ("coverage", "claims"),
    "real": ("property", "housing"),
    "estate": ("property", "housing"),
    "travel": ("trips", "itinerary"),
    "food": ("restaurant", "meal"),
    "restaurant": ("hospitality", "dining"),
    "freelance": ("freelancer", "solopreneur"),
    "freelancer": ("freelance", "solopreneur"),
    "startup": ("startups", "founders"),
    "developer": ("developers", "engineering"),
    "data": ("analytics", "dashboards"),
    "analytics": ("data", "reporting"),
    "ai": ("machine learning", "automation"),
    "automation": ("workflow", "no-code"),
    "security": ("cybersecurity", "privacy"),
    "privacy": ("data protection", "security"),
    "climate": ("sustainability", "carbon"),
    "energy": ("power", "utilities"),
    "gaming": ("games", "esports"),
    "social": ("community", "networking"),
    "content": ("creator", "media"),
    "creator": ("creators", "content"),
    "video": ("streaming", "media"),
    "music": ("audio", "streaming"),
    "pet": ("pets", "animal care"),
    "parenting": ("family", "childcare"),
    "senior": ("elderly", "aging"),
}

# Default audience segments, chosen deterministically by which cue words appear
# in the area. Each tuple is a small, distinct set of 3 segments.
_CONSUMER_CUES = frozenset(
    {
        "personal", "consumer", "consumers", "family", "families", "home",
        "fitness", "nutrition", "diet", "wellness", "mental", "parenting",
        "pet", "pets", "travel", "hobby", "dating", "gaming", "music",
        "senior", "student", "students", "kids", "food",
    }
)
_DEV_CUES = frozenset(
    {
        "developer", "developers", "engineering", "api", "apis", "devops",
        "data", "ml", "cloud", "code", "coding",
        "security", "cybersecurity", "analytics",
    }
)
# Heavy/frontier spaces, where the buyer is an operator, a lab, or a state — not
# an indie developer. Checked BEFORE the dev cues: "ai infrastructure" is not a
# thing you sell to indie developers, and scoping it that way silently caps
# every idea beneath it at hobby scale.
_FRONTIER_CUES = frozenset(
    {
        "ai", "frontier", "silicon", "chip", "chips", "semiconductor", "fab",
        "foundry", "wafer", "accelerator", "gpu", "datacenter", "data-center",
        "interconnect", "power", "grid", "energy", "reactor", "nuclear",
        "launch", "space", "satellite", "robotics", "biotech", "pharma",
        "defense", "manufacturing", "industrial", "infrastructure", "training",
        "model", "models", "compute",
    }
)

_CONSUMER_SEGMENTS = ("individual consumers", "busy families", "power users")
_DEV_SEGMENTS = ("indie developers", "engineering teams", "technical startups")
_SMB_SEGMENTS = ("freelancers", "small businesses", "growing teams")
_FRONTIER_SEGMENTS = (
    "frontier labs and large model builders",
    "infrastructure operators and hyperscalers",
    "enterprises and governments buying capability",
)
# Scale-neutral fallback. The old default was _SMB_SEGMENTS with the rationale
# "where most under-served software gaps live" — which quietly decided, before
# synthesis ran, that every unrecognized domain sells to freelancers and small
# businesses. "Semiconductor packaging" and "launch vehicles" both landed there.
# A fallback must not name a size.
_GENERIC_SEGMENTS = (
    "the primary buyer in this space",
    "adjacent buyers",
    "upstream suppliers",
)

_TOKEN_RE = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9\-']*")


@dataclass
class Scope:
    """The focused search surface for one analysis run."""

    sub_segments: list[str]
    keywords: list[str]


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #
def _tokenize(text: str) -> list[str]:
    """Lowercase word tokens, order-preserving, punctuation stripped."""
    return [m.group(0).lower() for m in _TOKEN_RE.finditer(text or "")]


def _dedupe_keep_order(items: list[str]) -> list[str]:
    """Case-insensitive de-dupe that preserves first-seen order & casing."""
    out: list[str] = []
    seen: set[str] = set()
    for it in items:
        it = it.strip()
        if not it:
            continue
        low = it.lower()
        if low in seen:
            continue
        seen.add(low)
        out.append(it)
    return out


def _significant_tokens(area: str) -> list[str]:
    """Content tokens of the area with stopwords / very short words removed."""
    return [t for t in _tokenize(area) if t not in _STOPWORDS and len(t) > 2]


def _derive_sub_segments(area: str, tokens: list[str]) -> list[str]:
    """Pick 3 plausible audience sub-segments from area cue words (heuristic).

    Order matters. Frontier cues are checked before dev cues because "ai
    infrastructure" hits both, and scoping it to indie developers caps every
    downstream idea at hobby scale before synthesis has said a word. The
    fallback is deliberately size-neutral: this function runs with no LLM and no
    steering, so a wrong guess here is invisible and unfalsifiable — it just
    quietly decides who the buyer is.
    """
    token_set = set(tokens)
    if token_set & _CONSUMER_CUES:
        return list(_CONSUMER_SEGMENTS)
    if token_set & _FRONTIER_CUES:
        return list(_FRONTIER_SEGMENTS)
    if token_set & _DEV_CUES:
        return list(_DEV_SEGMENTS)
    return list(_GENERIC_SEGMENTS)


def _expand_synonyms(tokens: list[str]) -> list[str]:
    """Flatten the light synonym map for whichever tokens matched."""
    out: list[str] = []
    for t in tokens:
        for syn in _SYNONYMS.get(t, ()):  # empty tuple if no match
            out.append(syn)
    return out


# --------------------------------------------------------------------------- #
# Public entry point                                                          #
# --------------------------------------------------------------------------- #
def scope_area(area: str, sub_segments: list[str]) -> Scope:
    """Normalize ``area`` into a searchable ``Scope``.

    - Keywords: the whole area phrase first (best single search term), then its
      significant tokens, then light synonym expansions, then any words drawn
      from caller-supplied sub-segments (derived defaults frame the audience
      but are never used as search terms). De-duplicated and capped to 4-8 terms.
    - Sub-segments: caller-supplied ones win; otherwise 3 are derived from cue
      words in the area via ``_derive_sub_segments``.

    Fully deterministic and network-free.
    """
    area = (area or "").strip()
    tokens = _significant_tokens(area)

    # ---- sub-segments --------------------------------------------------- #
    provided = _dedupe_keep_order([s for s in (sub_segments or []) if s and s.strip()])
    segments = provided if provided else _derive_sub_segments(area, tokens)

    # ---- keywords ------------------------------------------------------- #
    candidates: list[str] = []
    if area:
        candidates.append(area)          # the full phrase is the strongest term
    candidates.extend(tokens)            # individual content words
    candidates.extend(_expand_synonyms(tokens))  # light recall expansion

    # Pull the meaningful tokens out of caller-supplied sub-segments so the
    # query reflects who we're hunting for. Derived DEFAULT segments are kept
    # for audience framing only — injecting their generic tokens (e.g.
    # "freelancers", "small businesses") would dilute a niche area query into
    # generic SMB chatter.
    if provided:
        for seg in segments:
            candidates.extend(
                t for t in _tokenize(seg) if t not in _STOPWORDS and len(t) > 2
            )

    keywords = _dedupe_keep_order(candidates)

    # Guarantee a floor of 4 terms when the area is thin: fall back to raw area
    # tokens (incl. short ones) so a two-word area still yields usable queries.
    if len(keywords) < 4:
        for t in _tokenize(area):
            if len(keywords) >= 4:
                break
            if t not in {k.lower() for k in keywords}:
                keywords.append(t)

    # Cap at 8 — the adapters themselves only take the first ~6 anyway, and a
    # tighter list keeps the OR-query focused.
    keywords = keywords[:8]

    return Scope(sub_segments=segments, keywords=keywords)
