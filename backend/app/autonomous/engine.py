"""The expansion engine — frontier + node expansion (SPEC §4).

This is the *driver* that turns a domain into a tree. It owns three things:

1. **Node minting & priority.** ``make_node`` gives every node a *content-hash*
   id (a function of parent + kind + title), which is what makes dedup free
   (SPEC §11): two branches that hypothesize the same segment collapse onto the
   same id instead of re-exploring it. ``node_priority`` is the pure, no-LLM
   §4.1 heuristic that decides what the frontier works next.

2. **The frontier** — a max-priority queue (highest first) with id-dedup and
   ``remove`` support. Best-first search over this is what makes "come back to
   the good stuff" work instead of "come back to 4000 mediocre nodes".

3. **Expansion strategies (SPEC §4.2).**
   - ``expand_structural`` — a cheap-model LLM decomposition of a Domain/SubArea
     into 4-8 distinct child sub-areas (or Segments at depth ≥ 2). It DEGRADES to
     a deterministic ``scope_area``-keyword decomposition, so a tree still forms
     with *no* LLM at all.
   - ``expand_segment`` — reuses today's ``scope → fetch → extract → synthesize``
     pipeline verbatim and returns one ``GapCandidate`` node per synthesized gap,
     each carrying the full ``Gap`` object plus a compact evidence ``context``
     (stashed on ``rationale``) for the pressure tester.

Same degrade-don't-crash contract as the rest of the codebase: neither expansion
ever raises; on failure it returns fewer (or no) children and the loop moves on.
"""

from __future__ import annotations

import asyncio
import hashlib
import heapq
import re

from ..analysis.extract import extract_signals
from ..analysis.scope import _STOPWORDS, scope_area
from ..analysis.synthesize import _extract_json_array, synthesize
from ..llm.client import ClaudeClient
from ..schemas import Gap, SourceReport, SourceStatus
from ..sources.registry import get_sources
from .graveyard import graveyard_context_block
from .intake import steering_context_block
from .schemas import Node, NodeKind, Project
from .store import TreeStore


# --------------------------------------------------------------------------- #
# Node minting                                                                #
# --------------------------------------------------------------------------- #
def make_node(
    project_id: str,
    parent: Node | None,
    kind: NodeKind | str,
    title: str,
    rationale: str = "",
    keywords: list[str] | None = None,
    depth: int = 0,
) -> Node:
    """Mint a fresh ``Node`` with a deterministic, *content-hash* id.

    The id is a hash of ``(project_id, parent_id, kind, title)`` — never a
    timestamp or random value — so re-deriving the same node on a parallel branch
    yields the *same* id and the frontier / store dedup it for free (SPEC §11).
    Title is normalized (trimmed) before hashing so trivial whitespace differences
    don't fork a node.
    """
    kind = NodeKind(kind)
    parent_id = parent.id if parent is not None else None
    norm_title = (title or "").strip()
    seed = f"{project_id}|{parent_id or 'root'}|{kind.value}|{norm_title.lower()}"
    node_id = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:16]
    return Node(
        id=node_id,
        project_id=project_id,
        parent_id=parent_id,
        kind=kind,
        title=norm_title,
        rationale=rationale or "",
        keywords=[k for k in (keywords or []) if str(k).strip()],
        depth=depth,
    )


# --------------------------------------------------------------------------- #
# Frontier priority (SPEC §4.1) — a pure, no-LLM heuristic                     #
# --------------------------------------------------------------------------- #
# Kind-level base value: segments feed gap payoff, so they edge out plain
# sub-areas; the domain root starts highest so the tree opens up immediately.
_KIND_BASE: dict[NodeKind, float] = {
    NodeKind.DOMAIN: 1.0,
    NodeKind.SUBAREA: 0.8,
    NodeKind.SEGMENT: 0.9,
    NodeKind.GAP_CANDIDATE: 0.7,
    NodeKind.GAP: 0.5,
}
# Words in a node's rationale that hint the branch is already source-corroborated.
_DENSITY_HINTS = ("reddit", "arxiv", "hackernews", "github", "newsletter", "demand", "trend", "evidence")

# Marker keyword stamped on decomposition children whose title/keywords share no
# significant token with the root domain's scope (see ``_flag_off_domain``), and
# the priority penalty the frontier applies to them. Penalized, NOT dropped —
# contrarian adjacency is sometimes right; best-first just prefers on-domain.
_OFF_DOMAIN_KEYWORD = "off_domain"
_OFF_DOMAIN_PENALTY = 0.35


def node_priority(node: Node, parent: Node | None) -> float:
    """Blend the §4.1 factors into a single frontier priority (higher = sooner).

    Pure and cheap (recomputed on every push): no LLM, no I/O, no clock. It
    blends **parent promise** (a child under a strong branch is worth more),
    **novelty / uncrowdedness** (keyword-richness as a proxy for a distinct,
    well-specified branch), **signal density** (rationale already references
    corroboration), a **depth decay** (widen before tunneling), and the
    **pinned boost** (a user-pinned node jumps the queue).
    """
    base = _KIND_BASE.get(node.kind, 0.5)

    # Parent promise: a scored (gap) parent lends its viability; a structural
    # parent lends a bounded fraction of its own priority; the root is maximal.
    if parent is None:
        promise = 1.0
    elif parent.viability is not None:
        promise = parent.viability / 100.0
    else:
        promise = max(0.0, min(parent.priority, 2.0)) / 2.0

    # Novelty / uncrowdedness proxy: a richer, more specific keyword set reads as
    # a more distinct branch worth opening. The off-domain marker is bookkeeping,
    # not a real keyword, so it never counts toward novelty.
    real_keywords = [k for k in node.keywords if k.lower() != _OFF_DOMAIN_KEYWORD]
    novelty = min(len(real_keywords), 5) / 5.0

    # Signal-density proxy: reward branches whose rationale already cites sources.
    text = (node.rationale or "").lower()
    density = min(0.1 * sum(1 for h in _DENSITY_HINTS if h in text), 0.4)

    # Depth decay: a gentle "widen before tunneling" tiebreaker among nodes of
    # the same kind at different depths. Kept small on purpose so it never
    # inverts the kind ordering above — a SEGMENT must still edge out a plain
    # SUBAREA (per _KIND_BASE) so the frontier reaches the productive gap layer
    # instead of exhausting the node budget on structural breadth.
    decay = 0.06 * node.depth

    # Domain-drift penalty: a child flagged off the project's root domain sinks
    # below its on-domain siblings but stays explorable (never hard-dropped).
    drift = _OFF_DOMAIN_PENALTY if _OFF_DOMAIN_KEYWORD in {k.lower() for k in node.keywords} else 0.0

    # User boost: a pinned node leaps the queue (SPEC §4.1).
    boost = 1.0 if node.pinned else 0.0

    return round(base + 0.9 * promise + 0.5 * novelty + density - decay - drift + boost, 4)


# --------------------------------------------------------------------------- #
# The frontier — a max-priority queue with id-dedup                           #
# --------------------------------------------------------------------------- #
class Frontier:
    """Priority queue of expandable nodes, highest ``priority`` popped first.

    Backed by a min-heap over ``-priority`` with a monotonic tiebreaker so equal
    priorities pop in push order (stable). De-dups by node id: an id it has ever
    held is never re-accepted, which — combined with content-hash ids — stops the
    same branch being explored twice and prevents re-queue loops. ``remove`` is
    lazy: the heap entry is skipped when it surfaces.
    """

    def __init__(self) -> None:
        self._heap: list[tuple[float, int, str]] = []
        self._nodes: dict[str, Node] = {}
        self._seen: set[str] = set()
        self._counter = 0

    def push(self, node: Node) -> None:
        """Add ``node`` unless its id has already been seen (dedup)."""
        if node.id in self._seen:
            return
        self._seen.add(node.id)
        self._nodes[node.id] = node
        heapq.heappush(self._heap, (-node.priority, self._counter, node.id))
        self._counter += 1

    def push_all(self, nodes: list[Node]) -> None:
        for node in nodes:
            self.push(node)

    def pop(self) -> Node | None:
        """Pop the highest-priority live node, or ``None`` if the frontier is empty."""
        while self._heap:
            _, _, node_id = heapq.heappop(self._heap)
            node = self._nodes.pop(node_id, None)
            if node is not None:  # None -> already removed/consumed; skip.
                return node
        return None

    def remove(self, node_id: str) -> None:
        """Drop a node from the queue (its heap entry is skipped lazily on pop)."""
        self._nodes.pop(node_id, None)

    def __len__(self) -> int:
        return len(self._nodes)


# --------------------------------------------------------------------------- #
# Kind helpers & the root                                                     #
# --------------------------------------------------------------------------- #
def kind_is_structural(kind: NodeKind | str) -> bool:
    """True for decomposition nodes (Domain/SubArea/Segment), not gap leaves."""
    return NodeKind(kind) in (NodeKind.DOMAIN, NodeKind.SUBAREA, NodeKind.SEGMENT)


def root_node(project: Project) -> Node:
    """Build the DOMAIN root node for a project, priority pre-computed."""
    scope = scope_area(project.domain, project.sub_segments)
    steer_block = steering_context_block(project)
    rationale = f"Root domain exploration of '{project.domain}'."
    if steer_block:
        rationale += "\n" + steer_block
    node = make_node(
        project.id,
        None,
        NodeKind.DOMAIN,
        project.domain,
        rationale=rationale,
        keywords=list(scope.keywords),
        depth=0,
    )
    node.priority = node_priority(node, None)
    return node


# --------------------------------------------------------------------------- #
# Structural expansion (SPEC §4.2) — cheap-model decomposition + fallback      #
# --------------------------------------------------------------------------- #
_DECOMPOSE_SYSTEM = """\
You are the decomposition step of an autonomous market-gap explorer. Given a node
in an exploration tree, you break it into distinct, non-overlapping children a
founder could specialize in — cheaply and widely. You do NOT evaluate gaps here;
you only carve the space. Avoid the single obvious textbook split of the space:
at least 2 of your children must come from a non-obvious angle (a neglected
workflow, an underserved buyer or geography, a contrarian read of where the
market is going). Every child MUST remain inside the project's root domain as
stated by the user: an adjacent-industry analogy may inspire a child's framing,
but the child's actual market must still be the root domain. Be concrete.
Output ONLY the requested JSON array — no prose, no markdown fences.\
"""

# Ordinary-practitioner personas rotated deterministically per node (by hash of
# the node id) so sibling decompositions don't all converge on the same
# consensus carve-up. Deliberately mundane vantage points, not visionary ones.
_DECOMPOSE_PERSONAS: list[str] = [
    "a bookkeeper who lives inside this workflow every day and knows where the "
    "spreadsheets and workarounds pile up",
    "a plant manager responsible for keeping this running on the ground, "
    "skeptical of tools that ignore the shop floor",
    "an operator serving customers in an underserved geography where the "
    "mainstream tools don't quite fit",
    "a contrarian operator who thinks the mainstream approach to this market "
    "is dead and the real opportunities are elsewhere",
    "a front-line support lead who sees every complaint and edge case the "
    "product teams never hear about",
    "a small-firm owner who buys these tools out of their own pocket and "
    "abandons anything that takes a week to set up",
]


def _persona_for(node: Node) -> str:
    """Pick the decomposition persona for a node — deterministic by node id.

    Stable hash (not the salted builtin ``hash``) so reruns of the same tree
    rotate identically; distinct nodes land on different vantage points.
    """
    digest = hashlib.sha256((node.id or "").encode("utf-8")).digest()
    return _DECOMPOSE_PERSONAS[digest[0] % len(_DECOMPOSE_PERSONAS)]

# How many structural children to keep from one decomposition (SPEC: 4-8).
_MAX_STRUCTURAL_CHILDREN = 8
# Deterministic fallback fans out narrower to avoid a bushy no-signal tree.
_MAX_FALLBACK_CHILDREN = 3


def _child_kind_for(node: Node) -> tuple[NodeKind, int]:
    """Kind + depth of this node's structural children.

    Domain(0)→SubArea(1)→Segment(2+): children at depth ≥ 2 are SEGMENTs (which
    the segment expander, not this one, later runs the pipeline on).
    """
    child_depth = node.depth + 1
    child_kind = NodeKind.SUBAREA if child_depth < 2 else NodeKind.SEGMENT
    return child_kind, child_depth


def _decompose_prompt(
    node: Node, project: Project, child_label: str, graveyard: str = ""
) -> str:
    """User prompt handing the model the node + its context to carve up.

    ``graveyard`` is the pre-rendered S3 anti-portfolio block (spaces the
    founder already rejected across every project) — injected beside the
    steering block so decomposition stops re-proposing buried spaces.
    """
    lines = [f"ROOT DOMAIN: {project.domain}"]
    lines.append(
        "ANCHOR: every child MUST remain inside the ROOT DOMAIN above, no matter "
        "how deep this node sits in the tree. Adjacent-industry analogies are "
        "allowed only as framing — never as a child's actual market."
    )
    lines.append(
        f"YOUR VANTAGE POINT: carve this space as {_persona_for(node)}. "
        "Let that vantage point pick which children exist at all — not just "
        "the wording."
    )
    if project.sub_segments:
        lines.append(f"SUB-SEGMENTS OF INTEREST: {', '.join(project.sub_segments)}")
    steer_block = steering_context_block(project)
    if steer_block:
        lines.append(steer_block)
    if graveyard:
        lines.append(graveyard)
    lines.append(f"NODE TO DECOMPOSE ({node.kind.value}): {node.title}")
    if node.rationale:
        lines.append(f"WHY THIS BRANCH MIGHT MATTER: {node.rationale}")
    if node.keywords:
        lines.append(f"KEYWORDS: {', '.join(node.keywords)}")
    return (
        "\n".join(lines)
        + f"\n\nBreak the node above into 4-8 distinct, non-overlapping {child_label} "
        "a founder could specialize in. They must be *children* of it (not restatements) "
        "and must not overlap each other. Avoid the single obvious textbook split; at "
        "least 2 children must come from a non-obvious angle. For each, give a short "
        "specific title, a one-line rationale for why it might be an under-served gap, "
        "and 3 search keywords. For children born of a contrarian or against-consensus "
        'angle, include the literal keyword "contrarian" as an extra keyword.\n'
        'Also declare, for EACH child: "market" — one line naming who specifically '
        "buys this, and whether that buyer sits inside the ROOT DOMAIN — and "
        '"in_domain" — true only if the child\'s actual buyers are in the ROOT '
        "DOMAIN. Be honest: an adjacent-industry child must say in_domain: false.\n\n"
        "Return ONLY a JSON array (no prose, no fences) shaped like:\n"
        '[{"title": "...", "rationale": "one line", "keywords": ["a", "b", "c"], '
        '"market": "who specifically buys this", "in_domain": true}]'
    )


_DRIFT_TOKEN_RE = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9\-']*")


def _significant_drift_tokens(text: str) -> set[str]:
    """Lowercased content tokens for the drift check (stopwords/shorts dropped).

    Trailing-``s`` plurals are normalized so 'clinic' matches 'clinics' — naive
    on purpose; this is a cheap, deterministic guard, not NLP.
    """
    tokens: set[str] = set()
    for m in _DRIFT_TOKEN_RE.finditer(text or ""):
        tok = m.group(0).lower()
        if len(tok) <= 2 or tok in _STOPWORDS:
            continue
        if tok.endswith("s") and len(tok) > 3:
            tok = tok[:-1]
        tokens.add(tok)
    return tokens


def _root_domain_tokens(project: Project) -> set[str]:
    """Significant tokens of the root domain's scope — the drift yardstick.

    Uses the domain phrase plus ``scope_area`` keywords; derived DEFAULT
    sub-segments are deliberately excluded (their generic SMB tokens would let
    'freelancer cash flow' pass as on-domain for a clinic-software project).
    """
    scope = scope_area(project.domain, project.sub_segments)
    tokens: set[str] = set()
    for term in (project.domain, *scope.keywords):
        tokens |= _significant_drift_tokens(term)
    return tokens


def _drifts_off_domain(title: str, keywords: list[str], root_tokens: set[str]) -> bool:
    """True when a child shares ZERO significant tokens with the root scope.

    TIEBREAKER only (token overlap is not semantic drift — on-domain children
    with fresh vocabulary would false-positive): the primary signal is the
    child's own declared ``in_domain`` (see ``_declared_in_domain``); this
    cheap check decides when the declaration is missing/unparseable, and gates
    re-entry from under an ``off_domain`` parent. Flagged children are never
    dropped — ``node_priority`` just prefers on-domain branches.
    """
    if not root_tokens:
        return False
    child_tokens = _significant_drift_tokens(title)
    for kw in keywords:
        child_tokens |= _significant_drift_tokens(kw)
    if not child_tokens:
        return False  # nothing to judge — benefit of the doubt
    return not (child_tokens & root_tokens)


def _declared_in_domain(obj: dict) -> bool | None:
    """The child's own ``in_domain`` declaration, or None when missing/unusable.

    The decomposition prompt requires each child to state its buyer
    (``market``) and whether that buyer sits in the root domain (``in_domain``).
    Accepts a real boolean or an unambiguous true/false string; anything else
    returns None so the token tiebreaker decides instead.
    """
    value = obj.get("in_domain")
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        low = value.strip().lower()
        if low in ("true", "yes"):
            return True
        if low in ("false", "no"):
            return False
    return None


def _child_keywords(obj: dict, title: str) -> list[str]:
    """Pull keywords/tags off a decomposition object; derive from the title if bare."""
    kw = obj.get("keywords") or obj.get("tags") or []
    if isinstance(kw, str):
        kw = [k for k in kw.replace(",", " ").split() if k]
    keywords = [str(k).strip() for k in kw if str(k).strip()][:6]
    if not keywords:
        keywords = list(scope_area(title, []).keywords[:3])
    return keywords


def _children_from_raw(
    node: Node, project: Project, child_kind: NodeKind, child_depth: int, raw: list
) -> list[Node]:
    """Turn parsed decomposition objects into prioritized child nodes (dedup titles).

    Off-domain flagging (the ``off_domain`` marker keyword, which
    ``node_priority`` penalizes) is driven by the child's own ``in_domain``
    declaration — the model states its intent explicitly. The token-overlap
    check is a tiebreaker only, used when the declaration is missing or
    unparseable. A child under an ``off_domain`` parent inherits the marker
    unless it re-declares ``in_domain: true`` AND passes the token tiebreaker,
    so drift can't launder itself through a domain-titled parent. The no-LLM
    fallback path (``_fallback_children``) never flags, as before.
    """
    root_tokens = _root_domain_tokens(project)
    parent_off_domain = _OFF_DOMAIN_KEYWORD in (node.keywords or [])
    children: list[Node] = []
    seen: set[str] = set()
    for obj in raw:
        if len(children) >= _MAX_STRUCTURAL_CHILDREN:
            break
        if not isinstance(obj, dict):
            continue
        title = str(
            obj.get("title") or obj.get("name") or obj.get("segment") or ""
        ).strip()
        key = title.lower()
        if not title or key in seen or key == (node.title or "").strip().lower():
            continue
        seen.add(key)
        rationale = str(
            obj.get("rationale")
            or obj.get("why")
            or obj.get("thesis")
            or obj.get("description")
            or ""
        ).strip()
        keywords = _child_keywords(obj, title)
        declared = _declared_in_domain(obj)
        market = str(obj.get("market") or "").strip()
        # The tiebreaker judges the child's full stated identity: title,
        # keywords, and the declared buyer line (when present).
        tiebreak_off = _drifts_off_domain(
            f"{title} {market}".strip(), keywords, root_tokens
        )
        if parent_off_domain:
            # Inheritance: escape only by re-declaring in-domain AND passing
            # the token tiebreaker (no drift laundering).
            off = not (declared is True and not tiebreak_off)
        elif declared is not None:
            off = not declared  # the declaration is the primary signal
        else:
            off = tiebreak_off  # missing/unparseable -> tiebreaker decides
        if off:
            keywords = [*keywords, _OFF_DOMAIN_KEYWORD]
        child = make_node(
            project.id,
            node,
            child_kind,
            title,
            rationale=rationale,
            keywords=keywords,
            depth=child_depth,
        )
        child.priority = node_priority(child, node)
        children.append(child)
    return children


def _fallback_children(
    node: Node, project: Project, child_kind: NodeKind, child_depth: int
) -> list[Node]:
    """Deterministic, no-LLM decomposition from ``scope_area`` keywords/segments.

    Guarantees a tree still forms when the model is unavailable or unparseable:
    derive 2-3 distinct child facets from the node's own scope.
    """
    scope = scope_area(node.title or project.domain, project.sub_segments)
    parent_title = (node.title or "").strip().lower()

    ordered: list[str] = []
    seen: set[str] = set()
    for term in [*scope.keywords, *scope.sub_segments]:
        low = term.strip().lower()
        if not low or low in seen or low == parent_title:
            continue
        seen.add(low)
        ordered.append(term.strip())

    children: list[Node] = []
    for term in ordered[:_MAX_FALLBACK_CHILDREN]:
        title = term if child_kind is NodeKind.SUBAREA else f"{term} — {node.title}"
        child = make_node(
            project.id,
            node,
            child_kind,
            title,
            rationale=(
                f"Deterministic decomposition of '{node.title}' along "
                f"'{term}' (no-LLM fallback)."
            ),
            keywords=[term],
            depth=child_depth,
        )
        child.priority = node_priority(child, node)
        children.append(child)

    # Absolute floor: never return empty for a structural node.
    if not children:
        title = f"General {node.title}" if child_kind is NodeKind.SUBAREA else f"Core {node.title}"
        child = make_node(
            project.id,
            node,
            child_kind,
            title,
            rationale=f"Fallback single child of '{node.title}'.",
            keywords=list(scope.keywords[:3]),
            depth=child_depth,
        )
        child.priority = node_priority(child, node)
        children.append(child)
    return children


async def expand_structural(
    node: Node,
    project: Project,
    client: ClaudeClient,
    model: str,
    store: TreeStore | None = None,
) -> list[Node]:
    """Decompose a Domain/SubArea into child sub-areas (or Segments at depth ≥ 2).

    Cheap, wide LLM call (SPEC §4.2). DEGRADES to a deterministic
    ``scope_area``-keyword decomposition when the model is missing or its output
    is unparseable, so a tree always forms. Never raises.

    When a ``store`` handle is threaded in (the service passes its own), the S3
    graveyard block — spaces the founder already rejected across every project —
    is injected into the prompt beside the steering block. No store → no
    injection, and a graveyard failure degrades to none.
    """
    child_kind, child_depth = _child_kind_for(node)

    graveyard = ""
    if store is not None:
        try:
            graveyard = graveyard_context_block(store, project.domain)
        except Exception:  # noqa: BLE001 - the memory seam must never block expansion.
            graveyard = ""

    raw: list = []
    try:
        child_label = "sub-areas" if child_kind is NodeKind.SUBAREA else "concrete segments"
        result = await client.complete(
            _decompose_prompt(node, project, child_label, graveyard),
            system=_DECOMPOSE_SYSTEM,
            model=model,
            max_turns=1,
            timeout=120,
        )
        parsed = _extract_json_array(result.text) if result.text else None
        if parsed:
            raw = parsed
    except Exception:  # noqa: BLE001 - resilient: fall through to the fallback.
        raw = []

    children = _children_from_raw(node, project, child_kind, child_depth, raw)
    if not children:
        children = _fallback_children(node, project, child_kind, child_depth)
    return children


# --------------------------------------------------------------------------- #
# Segment expansion (SPEC §4.2) — reuse the existing analysis pipeline         #
# --------------------------------------------------------------------------- #
def _evidence_context(gap: Gap, reports: list[SourceReport]) -> str:
    """A compact grounding blurb the pressure tester attacks (stashed on rationale).

    Summarizes the gap's thesis / why-now / wedge, which sources fired live vs
    mock (confidence input), and a few evidence quotes — kept short on purpose.
    """
    parts: list[str] = [f"Thesis: {gap.thesis}"]
    if gap.why_now:
        parts.append(f"Why now: {gap.why_now}")
    if gap.wedge:
        parts.append(f"Wedge: {gap.wedge}")
    if gap.weakest_link:
        parts.append(f"Weakest link: {gap.weakest_link}")

    live = [r.name.value for r in reports if r.status is SourceStatus.LIVE]
    mock = [r.name.value for r in reports if r.status is SourceStatus.MOCK]
    if live:
        parts.append(f"Live sources: {', '.join(live)}")
    if mock:
        parts.append(f"Mock sources (cap confidence): {', '.join(mock)}")

    ev_lines: list[str] = []
    for e in (gap.evidence or [])[:4]:
        quote = " ".join((e.quote or "").split())
        if len(quote) > 160:
            quote = quote[:160] + "…"
        ev_lines.append(f"[{e.source.value}] {quote} ({e.url})")
    if ev_lines:
        parts.append("Evidence:\n" + "\n".join(ev_lines))
    return "\n".join(parts)


async def _fetch_all(node_title: str, scope) -> tuple[dict, list[SourceReport]]:
    """Fetch every source in parallel off the event loop; never raise."""
    sources = get_sources()
    results = await asyncio.gather(
        *(
            asyncio.to_thread(src.fetch, node_title, scope.keywords, scope.sub_segments)
            for src in sources
        ),
        return_exceptions=True,
    )
    fetched: dict = {}
    reports: list[SourceReport] = []
    ok: list[tuple] = []
    for src, res in zip(sources, results):
        if isinstance(res, Exception) or res is None:
            continue
        report = getattr(res, "report", None)
        if report is not None:
            reports.append(report)
        ok.append((src, res, report))

    # Fixture contamination gate. A degraded adapter (e.g. Reddit 403s) serves
    # its fixture corpus, whose content is unrelated to the run's domain — a
    # bookkeeping-SaaS fixture would otherwise land in the signal set of a
    # protein-model exploration and be reasoned over as real market evidence,
    # dragging demand scores toward a *false* zero. So once ANY source is live,
    # mock items are excluded from the corpus entirely. Their reports are still
    # returned, so the honest "Mock sources (cap confidence)" line and the
    # confidence cap downstream are unaffected — we drop the fake content, not
    # the fact that the source failed. When nothing is live (offline/test runs)
    # fixtures are kept, since a fixture run is then the explicit intent.
    any_live = any(
        r is not None and r.status is SourceStatus.LIVE for _, _, r in ok
    )
    for src, res, report in ok:
        if any_live and report is not None and report.status is SourceStatus.MOCK:
            continue
        fetched[src.name] = res
    return fetched, reports


async def expand_segment(
    node: Node, project: Project, client: ClaudeClient, model: str
) -> list[Node]:
    """Run the existing ``scope → fetch → extract → synthesize`` pipeline on a segment.

    Returns one ``GapCandidate`` node per synthesized gap, each with ``node.gap``
    set and a compact evidence ``context`` recorded on ``rationale`` for the
    pressure tester. Reuses the source adapters and ``synthesize`` verbatim —
    autonomous mode is a driver, not a rewrite. Never raises: degrades to no
    children if every step fails.
    """
    try:
        scope = scope_area(node.title, project.sub_segments)
        fetched, reports = await _fetch_all(node.title, scope)
        signals = extract_signals(node.title, scope, fetched)
        steering = steering_context_block(project)
        gaps, _backend, _warnings = await synthesize(
            signals, reports, client, model=model, steering=steering
        )
    except Exception:  # noqa: BLE001 - degrade to no children, never crash the loop.
        return []

    children: list[Node] = []
    for gap in gaps:
        child = make_node(
            project.id,
            node,
            NodeKind.GAP_CANDIDATE,
            gap.title,
            rationale=_evidence_context(gap, reports),
            keywords=list(gap.tags or [])[:6],
            depth=node.depth + 1,
        )
        child.gap = gap
        child.priority = node_priority(child, node)
        children.append(child)
    return children
