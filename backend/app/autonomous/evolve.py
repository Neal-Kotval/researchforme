"""Evolutionary idea operators — crossover and mutation (the creativity engine).

The tree, on its own, only DECOMPOSES (splits a space) and SYNTHESIZES (turns a
leaf into scored gaps). Both are top-down and convergent, so the tree lands on
the ideas systematic enumeration finds — the obvious ones, which are already
occupied. It has no way to reach a NON-obvious idea, because it never combines
or twists anything.

These operators are the missing moves, borrowed from evolutionary search (the
same shape as DeepMind's FunSearch/AlphaEvolve and Sakana's AI Scientist —
generate-and-test with an LLM as the variation operator):

* CROSSOVER — fuse two parent gaps into a hybrid whose strength comes from the
  combination, reaching ideas neither parent nor decomposition could.
* MUTATION — twist one parent along a creative axis (invert the buyer, flip the
  model, chase the adjacent need, take the contrarian read, escalate the
  ambition) to escape its own weakest link.

Offspring are ordinary ``Gap`` objects: they flow through the SAME pressure test,
scoring, and dedup as any synthesized gap, so a bad recombination dies in the
gauntlet like anything else. Selection biases toward promising AND novel parents
so the population diversifies instead of collapsing (quality-diversity, not pure
hill-climbing).

Every operator degrades safely: any error, an unparseable answer, or a fixture
backend yields no offspring (returns None / []). A failed mutation must never
break a run.
"""

from __future__ import annotations

import logging

from ..analysis.synthesize import _extract_json_array, _validate_gaps
from ..llm.client import ClaudeClient
from ..schemas import Gap

logger = logging.getLogger("gapfinder.evolve")

# The offspring must be a valid Gap. This is the synthesis output contract in
# compact form — kept here (not imported) so the operator prompts are
# self-contained and can foreground the ONE field each operator cares about.
_GAP_CONTRACT = """\
Return ONLY a top-level JSON array with EXACTLY ONE Gap object (no prose, no
fences), or an empty array [] if you cannot produce something genuinely stronger.
The object:
{
  "title": "short punchy name",
  "thesis": "one sentence: the bet in a breath",
  "easy_explain": "3-5 plain sentences for a smart outsider: what's broken today, who feels it, what this does, why it's worth money. No jargon.",
  "company": {"product": "...", "icp": "the specific first buyer", "business_model": "how it earns + pricing shape", "expansion_path": "the arc that fits THIS business", "moat": "the durable, compounding advantage", "standalone": true, "standalone_reason": "why it's a company not a feature"},
  "scores": {"demand_strength": 1-5, "competitive_openness": 1-5, "trend_tailwind": 1-5, "feasibility": 1-5, "willingness_to_pay": 1-5},
  "competitors": [{"name": "", "url": "", "positioning": "", "segment": "", "price_tier": "free|$|$$|$$$|enterprise", "weakness": "the blind spot this idea exploits"}],
  "wedge": "a CONCRETE first move — what you build/prove/sell FIRST, to whom, and what you deliberately do NOT do yet",
  "riskiest_assumption": "the one belief that, if false, kills this — stated so ~10 calls could test it",
  "weakest_link": "the softest score or biggest doubt, named",
  "why_now": "the dated recent shift that makes this buildable/urgent now",
  "novelty": 1-5,
  "sub_segment": "which sub-segment this serves",
  "tags": ["", ""]
}
Do NOT invent evidence URLs — leave evidence out; the red team will verify. Score
honestly: a hybrid that isn't actually stronger than its parents should score low
or not be produced at all."""

# Mutation strategies — the creative moves. Each is a distinct way to reach a
# less-obvious idea, chosen deliberately to escape the "obvious idea is occupied"
# trap by attacking a different axis of the parent.
MUTATION_STRATEGIES: dict[str, str] = {
    "invert_buyer": (
        "INVERT THE BUYER. Sell to the OTHER side of the transaction, or to the "
        "stakeholder the parent ignored — the payer instead of the user, the "
        "supplier instead of the buyer, the regulator instead of the regulated. "
        "Often the same insight is far more valuable to the party the original "
        "overlooked."
    ),
    "invert_model": (
        "INVERT THE BUSINESS MODEL. Flip the shape: services→productized software, "
        "a tool→a two-sided marketplace, a one-time sale→an owned recurring "
        "dataset, sell-the-thing→give-it-away-and-monetize-the-exhaust. Keep the "
        "underlying insight; change how it captures value into something more "
        "defensible or higher-ceiling."
    ),
    "adjacent_need": (
        "CHASE THE ADJACENT UNMET NEED. The parent exposes a deeper or neighboring "
        "problem it doesn't itself solve — the thing the buyer actually loses sleep "
        "over, one step upstream or downstream. Go build for THAT, using the "
        "parent's insight as the way in."
    ),
    "contrarian": (
        "TAKE THE CONTRARIAN READ. Assume the parent's core market assumption is "
        "WRONG — the consensus everyone shares about this space is a blind spot. "
        "What is the idea if the opposite is true? The best non-obvious ideas live "
        "exactly where the mainstream is confidently wrong."
    ),
    "escalate": (
        "ESCALATE THE AMBITION. The parent may be a feature or a niche; find the "
        "venture-scale version — the platform, standard, network, or "
        "system-of-record it becomes if it wins, where the moat COMPOUNDS with "
        "scale (data flywheel, network effect, switching cost, infrastructure "
        "others build on)."
    ),
    "sharpen_wedge": (
        "SHARPEN THE WEDGE. The parent is too broad. Narrow to the single "
        "sharpest, most-defensible entry point — the one buyer, one workflow, one "
        "acute pain where you can win decisively and expand from strength."
    ),
}

_CROSSOVER_SYSTEM = """\
You are the CROSSOVER operator in an evolutionary startup-idea engine. You are
given TWO parent market-gap ideas. Produce ONE offspring that genuinely FUSES
their strongest elements into a NON-OBVIOUS hybrid.

WHAT MAKES A REAL CROSSOVER (not a mashup):
- The offspring's strength comes from the COMBINATION — a shared buyer both
  parents point to, a moat that only exists when both insights compound, a wedge
  that works precisely because it serves both needs at once. "Idea A with a
  feature of B bolted on" is a FAILURE.
- It should be MORE defensible or MORE novel than either parent alone. If fusing
  them doesn't produce something stronger, return [] — a forced merge is worse
  than none.
- Prefer fusions that reach a compounding moat (data, network, workflow lock-in)
  neither parent had by itself.

Reason ONLY from the two parents given. Do not invent facts or competitors.
""" + "\n" + _GAP_CONTRACT

_MUTATION_SYSTEM = """\
You are the MUTATION operator in an evolutionary startup-idea engine. You are
given ONE parent market-gap idea and ONE mutation strategy. Produce ONE offspring
that twists the parent along that strategy to reach a LESS-OBVIOUS, potentially
more-defensible idea.

RULES:
- Apply the strategy for real — the offspring must be genuinely DIFFERENT from
  the parent (a true twist, not a reword or a scope tweak).
- Aim the mutation to ESCAPE the parent's weakest_link — the twist should attack
  exactly what made the parent weak (occupied, undifferentiated, small, or
  demand-unproven).
- Keep the parent's genuine insight; change the axis the strategy names.

Reason ONLY from the parent given. Do not invent facts or competitors.
""" + "\n" + _GAP_CONTRACT


def _gap_brief(gap: Gap, label: str) -> str:
    """Compact parent description for an operator prompt."""
    c = gap.company
    lines = [
        f"===== {label}: {gap.title} =====",
        f"thesis: {gap.thesis}",
        f"wedge: {gap.wedge}",
        f"why_now: {gap.why_now}",
        f"riskiest_assumption: {gap.riskiest_assumption}",
        f"weakest_link: {gap.weakest_link}",
        f"moat: {c.moat if c else '(none stated)'}",
        f"buyer: {c.icp if c else '(none stated)'}",
        f"scores: demand={gap.scores.demand_strength} open={gap.scores.competitive_openness} "
        f"trend={gap.scores.trend_tailwind} feas={gap.scores.feasibility} wtp={gap.scores.willingness_to_pay} "
        f"novelty={gap.novelty}",
    ]
    return "\n".join(lines)


async def _run_operator(
    system: str, prompt: str, client: ClaudeClient, model: str
) -> Gap | None:
    """Shared operator call: one strong-model pass → a single validated Gap or None."""
    try:
        result = await client.complete(
            prompt, system=system, model=model, max_turns=1, timeout=120,
            temperature=0.9,  # higher sampling — variation operators want divergence
        )
    except Exception:  # noqa: BLE001 - a failed operator must never break a run
        return None
    if getattr(result, "backend", "") == "fixture":
        return None
    raw = _extract_json_array(result.text or "")
    if not raw:
        return None
    gaps = _validate_gaps(raw, warnings=[], known_urls=None)
    return gaps[0] if gaps else None


async def crossover_gaps(
    parent_a: Gap, parent_b: Gap, client: ClaudeClient, model: str,
    steering: str = "",
) -> Gap | None:
    """Fuse two parent gaps into one hybrid offspring, or None if they can't fuse."""
    prompt = (
        (f"FOUNDER STEERING:\n{steering}\n\n" if steering.strip() else "")
        + "Fuse these two ideas into one stronger, non-obvious hybrid.\n\n"
        + _gap_brief(parent_a, "PARENT A") + "\n\n"
        + _gap_brief(parent_b, "PARENT B")
    )
    child = await _run_operator(_CROSSOVER_SYSTEM, prompt, client, model)
    if child is not None:
        child.tags = list({*(child.tags or []), "crossover"})
    return child


async def mutate_gap(
    parent: Gap, strategy: str, client: ClaudeClient, model: str,
    steering: str = "",
) -> Gap | None:
    """Twist one parent along a mutation strategy into an offspring, or None."""
    move = MUTATION_STRATEGIES.get(strategy)
    if move is None:
        return None
    prompt = (
        (f"FOUNDER STEERING:\n{steering}\n\n" if steering.strip() else "")
        + f"MUTATION STRATEGY — {move}\n\n"
        + "Apply that strategy to this idea:\n\n"
        + _gap_brief(parent, "PARENT")
    )
    child = await _run_operator(_MUTATION_SYSTEM, prompt, client, model)
    if child is not None:
        child.tags = list({*(child.tags or []), "mutation", f"mut:{strategy}"})
    return child


# --------------------------------------------------------------------------- #
# Selection — which parents breed. Quality-diversity: bias toward promising AND #
# novel, so the population diversifies rather than converging on one winner.    #
# --------------------------------------------------------------------------- #
_STOP = {"the", "a", "an", "for", "of", "and", "to", "in", "on", "with", "as", "by"}


def _keyword_set(gap: Gap) -> set[str]:
    """A gap's rough topic signature — tags plus meaningful title/sub-segment tokens.

    Used only to measure DISTANCE between two parents so crossover pairs the ones
    with the least overlap (distant crosses reach further). Cheap and lexical; it
    just needs to rank overlap, not be exact."""
    words = f"{gap.title} {gap.sub_segment or ''}".lower().replace("/", " ")
    toks = {w.strip(".,:—-()") for w in words.split()}
    toks = {w for w in toks if len(w) > 3 and w not in _STOP}
    return toks | {t.lower() for t in (gap.tags or [])}


def _parent_fitness(gap: Gap, viability: int | None) -> float:
    """Rank a gap as a parent: viability if scored, plus a novelty kicker."""
    base = float(viability if viability is not None else 45)
    return base + 4.0 * (int(gap.novelty) - 3)  # reward non-obvious parents


def select_crossover_pairs(
    scored: list[tuple[Gap, int | None]], k: int
) -> list[tuple[Gap, Gap]]:
    """Pick up to k DIVERSE pairs to cross. Pairs the strongest parents with ones
    they share few tags/keywords with — distant crosses reach further."""
    ranked = sorted(scored, key=lambda g: -_parent_fitness(g[0], g[1]))
    top = ranked[: max(2, min(len(ranked), 2 * k + 2))]
    pairs: list[tuple[Gap, Gap]] = []
    used: set[tuple[str, str]] = set()
    for i, (a, _) in enumerate(top):
        a_kw = _keyword_set(a)
        # partner = the most DISTANT (fewest shared keywords) among the rest.
        best = None
        best_overlap = 1e9
        for j, (b, _) in enumerate(top):
            if i == j:
                continue
            key = tuple(sorted((a.title.lower(), b.title.lower())))
            if key in used:
                continue
            overlap = len(a_kw & _keyword_set(b))
            if overlap < best_overlap:
                best_overlap, best = overlap, (b, key)
        if best is not None:
            pairs.append((a, best[0]))
            used.add(best[1])
        if len(pairs) >= k:
            break
    return pairs


def select_mutation_targets(
    scored: list[tuple[Gap, int | None]], k: int
) -> list[tuple[Gap, str]]:
    """Pick up to k (gap, strategy) mutations. Weakest-link-aware: a promising gap
    with a soft spot gets the strategy most likely to escape it."""
    ranked = sorted(scored, key=lambda g: -_parent_fitness(g[0], g[1]))
    strategies = list(MUTATION_STRATEGIES.keys())
    out: list[tuple[Gap, str]] = []
    for idx, (gap, _) in enumerate(ranked[:k]):
        wl = (gap.weakest_link or "").lower()
        if "competit" in wl or "crowd" in wl or "occupied" in wl or "incumbent" in wl:
            strat = "contrarian"          # differentiated escape from a crowd
        elif "small" in wl or "niche" in wl or "ceiling" in wl or "scale" in wl:
            strat = "escalate"            # raise the ceiling
        elif "demand" in wl or "pay" in wl or "wtp" in wl or "willing" in wl:
            strat = "invert_buyer"        # find the party that actually pays
        elif "feature" in wl or "thin" in wl or "wrapper" in wl or "moat" in wl:
            strat = "invert_model"        # find a defensible shape
        else:
            strat = strategies[idx % len(strategies)]
        out.append((gap, strat))
    return out
