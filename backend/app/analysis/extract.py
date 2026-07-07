"""Signal extraction.

Deterministic, LLM-free reduction of the raw items each adapter returned into
the three typed signal streams the synthesis step reasons over:

  * DemandSignal      - unmet-need / pain / complaint / interest, mined from
                        Reddit chatter and Hacker News Ask HN posts.
  * CapabilitySignal  - "what just became feasible / why now": arXiv publication
                        momentum, GitHub star-velocity (adoption), recent
                        newsletter attention, and high-point HN story momentum.
  * SupplySignal      - incumbent / existing-solution hints: names mined from
                        Reddit chatter, Show HN launches, the GitHub repos
                        themselves, and product names in newsletter headlines.

All per-stream strengths are min-max normalized into 0..1 so the synthesizer can
compare across sources. We keep only the ~40 strongest signals overall to hold
the LLM prompt to a sane size.

Everything here is pure given the fetched inputs - no network, no randomness.
"""

from __future__ import annotations

import re

from ..schemas import (
    CapabilitySignal,
    DemandSignal,
    ExtractedSignals,
    RawItem,
    SourceName,
    SupplySignal,
)
from ..sources.base import FetchResult
from .scope import Scope

# Overall budget on emitted signals so the synthesis prompt stays compact.
_MAX_TOTAL = 40
_MAX_DEMAND = 24
_MAX_CAPABILITY = 10
_MAX_SUPPLY = 8

# --------------------------------------------------------------------------- #
# Demand classification (shared by Reddit + Hacker News)                       #
# --------------------------------------------------------------------------- #
# Ordered patterns: first match wins the DemandSignal.kind. "wish" (a desired
# but missing product) is the most actionable, so it's checked first.
_WISH_RE = re.compile(
    r"(i wish (?:there|it|they)|someone should (?:build|make)|is there (?:an? )?(?:app|tool|software|service)"
    r"|does anyone know (?:of )?(?:an?|any) (?:app|tool|software)"
    r"|why is there no|why isn'?t there|why does no|there has to be (?:a )?better"
    r"|i(?:'d| would) pay)",
    re.IGNORECASE,
)
_COMPLAINT_RE = re.compile(
    r"(\b[12] ?star|\b[123]/5\b|hate (?:that|how|using)|nightmare|so frustrat|rant\b|can'?t believe)",
    re.IGNORECASE,
)
# Tag hints so the synthesizer / UI can cluster demand.
_TAG_HINTS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("pricing", re.compile(r"\b(price|pricing|expensive|cost|overpriced|too much|paywall)\b", re.I)),
    ("ux", re.compile(r"\b(clunky|confusing|ui|ux|interface|hard to use|unusable)\b", re.I)),
    ("integration", re.compile(r"\b(integrat|sync|api|export|import|zapier)\b", re.I)),
    ("support", re.compile(r"\b(support|customer service|no reply|ignored)\b", re.I)),
    ("reliability", re.compile(r"\b(bug|crash|broken|down|outage|slow|lag)\b", re.I)),
)

# Strip a leading "Show HN:" / "Ask HN:" marker off an HN title.
_HN_PREFIX_RE = re.compile(r"^\s*(?:show|ask)\s+hn:?\s*", re.I)

# --------------------------------------------------------------------------- #
# Supply (incumbent) mining                                                   #
# --------------------------------------------------------------------------- #
# A small lexicon of common horizontal SaaS incumbents that show up as the
# "thing I'm frustrated with" across many areas. Case-insensitive whole-word.
_KNOWN_VENDORS = frozenset(
    {
        "quickbooks", "xero", "freshbooks", "wave", "stripe", "paypal", "square",
        "shopify", "wix", "squarespace", "salesforce", "hubspot", "pipedrive",
        "mailchimp", "klaviyo", "notion", "evernote", "asana", "trello", "jira",
        "monday", "clickup", "airtable", "slack", "zoom", "calendly", "docusign",
        "excel", "sheets", "google sheets", "zapier", "zendesk", "intercom",
        "mint", "ynab", "venmo", "toggl", "harvest", "gusto", "adp", "workday",
        "canva", "figma", "adobe", "dropbox", "onedrive", "wordpress", "webflow",
        "myfitnesspal", "strava", "peloton", "duolingo",
    }
)

# Verb frames that tend to name an existing solution just before/after them,
# e.g. "switched from X", "tried X", "X is too expensive".
_SUPPLY_FRAME_RE = re.compile(
    r"\b(?:using|used|use|switched (?:from|to)|tried|paying for|stuck with|moved (?:from|to)|"
    r"instead of|alternative to|replace|competitor)\s+"
    r"([A-Z][A-Za-z0-9&.\-]+(?:\s+[A-Z][A-Za-z0-9&.\-]+)?)",
)
# Bare CamelCase / branded tokens (Notion, QuickBooks, FreshBooks, MailChimp).
_BRAND_TOKEN_RE = re.compile(r"\b([A-Z][a-z]+(?:[A-Z][a-z]+)+)\b")
# Domain mentions (something.com / .io / .app).
_DOMAIN_RE = re.compile(r"\b([a-z0-9][a-z0-9\-]{1,30}\.(?:com|io|app|co|ai|net|org))\b", re.I)

# Words that look like brands to the regex but never are - filtered out.
_SUPPLY_STOPWORDS = frozenset(
    {
        "i", "the", "it", "they", "you", "we", "my", "our", "this", "that",
        "there", "here", "app", "apps", "tool", "tools", "software", "one",
        "something", "anything", "everything", "someone", "anyone", "reddit",
        "google", "gmail",  # too generic to count as a niche incumbent
    }
)


# --------------------------------------------------------------------------- #
# Small numeric helpers                                                        #
# --------------------------------------------------------------------------- #
def _normalize(values: list[float]) -> list[float]:
    """Min-max scale a list into 0..1. All-equal -> all 1.0 (they're equally
    strong). Empty -> empty."""
    if not values:
        return []
    lo, hi = min(values), max(values)
    if hi <= lo:
        return [1.0 for _ in values]
    span = hi - lo
    return [round((v - lo) / span, 4) for v in values]


def _tags_for(text: str) -> list[str]:
    return [tag for tag, rx in _TAG_HINTS if rx.search(text)]


def _snippet(item: RawItem, limit: int = 240) -> str:
    """A readable quote: prefer title, append the start of the body."""
    title = (item.title or "").strip()
    body = (item.body or "").strip()
    if title and body:
        quote = f"{title} - {body}"
    else:
        quote = title or body
    quote = re.sub(r"\s+", " ", quote)
    return quote[:limit].rstrip()


def _interleave(*lists: list) -> list:
    """Round-robin merge several lists so each source gets fair representation."""
    out: list = []
    idx = 0
    while True:
        added = False
        for lst in lists:
            if idx < len(lst):
                out.append(lst[idx])
                added = True
        if not added:
            return out
        idx += 1


def _mine_names(text: str) -> list[str]:
    """Pull candidate product / incumbent names out of free text.

    Combines the known-vendor lexicon, verb-frame captures ("switched from X"),
    and CamelCase brand-token + domain matches. Returns raw (un-deduped) hits.
    """
    found: list[str] = []
    low = text.lower()
    for vendor in _KNOWN_VENDORS:
        if re.search(rf"\b{re.escape(vendor)}\b", low):
            found.append(vendor.title())
    for m in _SUPPLY_FRAME_RE.finditer(text):
        found.append(m.group(1).strip())
    found.extend(m.group(1) for m in _BRAND_TOKEN_RE.finditer(text))
    found.extend(m.group(1) for m in _DOMAIN_RE.finditer(text))
    return found


# --------------------------------------------------------------------------- #
# Demand extractors                                                            #
# --------------------------------------------------------------------------- #
def _demand_from_reddit(items: list[RawItem]) -> list[DemandSignal]:
    """Reddit posts -> pain/wish/complaint demand signals, strength = upvotes."""
    signals: list[DemandSignal] = []
    weights = [max(0.0, i.weight) for i in items]
    strengths = _normalize(weights)
    for item, strength in zip(items, strengths):
        blob = f"{item.title}\n{item.body}"
        if _WISH_RE.search(blob):
            kind = "wish"
        elif _COMPLAINT_RE.search(blob):
            kind = "complaint"
        else:
            kind = "pain"
        sub = item.meta.get("subreddit") or ""
        tags = _tags_for(blob)
        if sub:
            tags = [f"r/{sub}", *tags]
        signals.append(
            DemandSignal(
                source=SourceName.REDDIT,
                kind=kind,
                quote=_snippet(item),
                url=item.url,
                strength=strength,
                date=item.created,
                tags=tags,
            )
        )
    return signals


def _demand_from_hackernews(items: list[RawItem]) -> list[DemandSignal]:
    """Ask HN pain + need-phrased posts -> demand signals, strength = points.

    We treat any ``kind == "ask"`` item as demand, plus any Show/story item whose
    text reads as an explicit unmet need (wish/complaint phrasing). Points (a
    proxy for how many builders felt the same pain) drive the normalized strength.
    """
    candidates: list[RawItem] = []
    for item in items:
        blob = f"{item.title}\n{item.body}"
        if item.meta.get("kind") == "ask" or _WISH_RE.search(blob) or _COMPLAINT_RE.search(blob):
            candidates.append(item)

    points = [float(i.meta.get("points", i.weight) or 0.0) for i in candidates]
    strengths = _normalize(points)
    signals: list[DemandSignal] = []
    for item, strength in zip(candidates, strengths):
        blob = f"{item.title}\n{item.body}"
        if _WISH_RE.search(blob):
            kind = "wish"
        elif _COMPLAINT_RE.search(blob):
            kind = "complaint"
        elif item.meta.get("kind") == "ask":
            kind = "pain"
        else:
            kind = "interest"
        tags = ["hn", *_tags_for(blob)]
        signals.append(
            DemandSignal(
                source=SourceName.HACKERNEWS,
                kind=kind,
                quote=_snippet(item),
                url=item.url,
                strength=strength,
                date=item.created,
                tags=tags,
            )
        )
    return signals


# --------------------------------------------------------------------------- #
# Capability extractors                                                        #
# --------------------------------------------------------------------------- #
def _capability_from_arxiv(items: list[RawItem]) -> list[CapabilitySignal]:
    """arXiv papers -> capability tailwinds, momentum = recency-decayed weight."""
    signals: list[CapabilitySignal] = []
    momenta = [float(i.meta.get("momentum", i.weight) or 0.0) for i in items]
    strengths = _normalize(momenta)
    for item, momentum in zip(items, strengths):
        desc = _snippet(item, limit=280)
        cats = item.meta.get("categories") or []
        if cats:
            desc = f"[{', '.join(cats[:3])}] {desc}"
        signals.append(
            CapabilitySignal(
                source=SourceName.ARXIV,
                description=desc,
                url=item.url,
                momentum=momentum,
                date=item.created,
            )
        )
    return signals


def _capability_from_github(items: list[RawItem]) -> list[CapabilitySignal]:
    """GitHub repos -> adoption tailwind, momentum = star velocity.

    A cluster of recently-created repos accreting stars fast is hard evidence the
    tooling substrate for a product just got cheaper (a 'why now'). Star velocity
    (stars / age) is the momentum; we surface stars, language, and topics so the
    synthesizer can reason about what specifically became feasible.
    """
    signals: list[CapabilitySignal] = []
    velocities = [float(i.meta.get("star_velocity", i.weight) or 0.0) for i in items]
    strengths = _normalize(velocities)
    for item, momentum in zip(items, strengths):
        repo = item.meta.get("repo") or item.title
        stars = int(item.meta.get("stars") or 0)
        lang = item.meta.get("language")
        topics = item.meta.get("topics") or []
        body = re.sub(r"\s+", " ", (item.body or "").strip())[:200]
        prefix = f"[{lang}] " if lang else ""
        desc = f"{prefix}{repo} ({stars} stars): {body}".strip()
        if topics:
            desc = f"{desc} #{' #'.join(str(t) for t in topics[:4])}"
        signals.append(
            CapabilitySignal(
                source=SourceName.GITHUB,
                description=desc[:320],
                url=item.url,
                momentum=momentum,
                date=item.created,
            )
        )
    return signals


def _capability_from_newsletters(items: list[RawItem]) -> list[CapabilitySignal]:
    """Recent newsletter coverage -> attention / 'why now' tailwind.

    When curators converge on a shift in a short window, that's an attention
    tailwind. The adapter already weights items by recency, so we read the item
    weight directly as the momentum (freshest coverage floats to the top).
    """
    signals: list[CapabilitySignal] = []
    momenta = [max(0.0, float(i.weight)) for i in items]
    strengths = _normalize(momenta)
    for item, momentum in zip(items, strengths):
        feed_title = item.meta.get("feed_title") or ""
        prefix = f"[{feed_title}] " if feed_title else ""
        desc = f"{prefix}{_snippet(item, limit=260)}"
        signals.append(
            CapabilitySignal(
                source=SourceName.NEWSLETTER,
                description=desc[:320],
                url=item.url,
                momentum=momentum,
                date=item.created,
            )
        )
    return signals


def _capability_from_hackernews(items: list[RawItem]) -> list[CapabilitySignal]:
    """High-point HN stories -> topic momentum (a 'why now' the crowd pushed up).

    Only ``kind == "story"`` items qualify (Ask/Show carry demand/supply meaning
    instead). Points are the momentum: a link the community voted to the top is a
    proxy for "this topic is heating up right now".
    """
    stories = [i for i in items if i.meta.get("kind") == "story"]
    if not stories:
        return []
    points = [float(i.meta.get("points", i.weight) or 0.0) for i in stories]
    strengths = _normalize(points)
    signals: list[CapabilitySignal] = []
    for item, momentum in zip(stories, strengths):
        pts = int(item.meta.get("points") or 0)
        desc = f"HN momentum ({pts} pts): {_snippet(item, limit=260)}"
        signals.append(
            CapabilitySignal(
                source=SourceName.HACKERNEWS,
                description=desc[:320],
                url=item.url,
                momentum=momentum,
                date=item.created,
            )
        )
    return signals


# --------------------------------------------------------------------------- #
# Supply extractors                                                            #
# --------------------------------------------------------------------------- #
def _supply_from_reddit(items: list[RawItem]) -> list[SupplySignal]:
    """Mine incumbent / existing-solution names out of Reddit chatter.

    De-duped by lowercased name, keeping the first (highest-weighted) mention as
    the hint so the retained quote comes from the loudest post.
    """
    signals: list[SupplySignal] = []
    seen: set[str] = set()

    # Highest-weight items first so the retained hint quotes the loudest post.
    for item in sorted(items, key=lambda i: i.weight, reverse=True):
        blob = f"{item.title}\n{item.body}"
        for raw_name in _mine_names(blob):
            name = raw_name.strip().strip(".,)(").strip()
            key = name.lower()
            if not name or key in seen or key in _SUPPLY_STOPWORDS or len(name) < 2:
                continue
            seen.add(key)
            signals.append(
                SupplySignal(
                    source=SourceName.REDDIT,
                    name=name,
                    hint=_snippet(item, limit=200),
                    url=item.url,
                )
            )
            if len(signals) >= _MAX_SUPPLY:
                return signals
    return signals


def _supply_from_hackernews(items: list[RawItem]) -> list[SupplySignal]:
    """Show HN launches -> supply (someone already shipped in this space).

    The product name is the lead phrase of the title after the 'Show HN:' marker
    and before the first dash/colon separator.
    """
    signals: list[SupplySignal] = []
    seen: set[str] = set()
    shows = [i for i in items if i.meta.get("kind") == "show"]
    for item in sorted(shows, key=lambda i: i.weight, reverse=True):
        name = _HN_PREFIX_RE.sub("", item.title or "").strip()
        name = re.split(r"\s[-–—:]\s|[:–—]", name, maxsplit=1)[0].strip()
        name = name[:60].strip()
        key = name.lower()
        if not name or key in seen or len(name) < 2:
            continue
        seen.add(key)
        signals.append(
            SupplySignal(
                source=SourceName.HACKERNEWS,
                name=name,
                hint=_snippet(item, limit=200),
                url=item.url,
            )
        )
        if len(signals) >= _MAX_SUPPLY:
            break
    return signals


def _supply_from_github(items: list[RawItem]) -> list[SupplySignal]:
    """Each recent repo is itself an existing tool / incumbent in the space."""
    signals: list[SupplySignal] = []
    seen: set[str] = set()
    for item in sorted(items, key=lambda i: i.weight, reverse=True):
        repo = str(item.meta.get("repo") or item.title or "").strip()
        key = repo.lower()
        if not repo or key in seen:
            continue
        seen.add(key)
        stars = int(item.meta.get("stars") or 0)
        hint = (item.body or "").strip() or f"{stars} stars on GitHub"
        signals.append(
            SupplySignal(
                source=SourceName.GITHUB,
                name=repo,
                hint=hint[:200],
                url=item.url,
            )
        )
        if len(signals) >= _MAX_SUPPLY:
            break
    return signals


def _supply_from_newsletters(items: list[RawItem]) -> list[SupplySignal]:
    """Scan newsletter headlines for named products -> optional supply hints."""
    signals: list[SupplySignal] = []
    seen: set[str] = set()
    for item in sorted(items, key=lambda i: i.weight, reverse=True):
        title = item.title or ""
        for raw_name in _mine_names(title):
            name = raw_name.strip().strip(".,)(").strip()
            key = name.lower()
            if not name or key in seen or key in _SUPPLY_STOPWORDS or len(name) < 2:
                continue
            seen.add(key)
            signals.append(
                SupplySignal(
                    source=SourceName.NEWSLETTER,
                    name=name,
                    hint=_snippet(item, limit=200),
                    url=item.url,
                )
            )
            if len(signals) >= _MAX_SUPPLY:
                return signals
    return signals


# --------------------------------------------------------------------------- #
# Budget trimming                                                              #
# --------------------------------------------------------------------------- #
def _cap_demand(signals: list[DemandSignal]) -> list[DemandSignal]:
    return sorted(signals, key=lambda s: s.strength, reverse=True)[:_MAX_DEMAND]


def _cap_capability(signals: list[CapabilitySignal]) -> list[CapabilitySignal]:
    return sorted(signals, key=lambda s: s.momentum, reverse=True)[:_MAX_CAPABILITY]


def _cap_supply(signals: list[SupplySignal]) -> list[SupplySignal]:
    """De-dupe supply hints by lowercased name and keep the first _MAX_SUPPLY."""
    seen: set[str] = set()
    out: list[SupplySignal] = []
    for s in signals:
        key = s.name.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
        if len(out) >= _MAX_SUPPLY:
            break
    return out


def _items(fetched: dict[SourceName, FetchResult], name: SourceName) -> list[RawItem]:
    fr = fetched.get(name)
    return list(fr.items) if fr and fr.items else []


# --------------------------------------------------------------------------- #
# Public entry point                                                          #
# --------------------------------------------------------------------------- #
def extract_signals(
    area: str, scope: Scope, fetched: dict[SourceName, FetchResult]
) -> ExtractedSignals:
    """Reduce the fetched raw items into the three typed, normalized streams.

    Strengths are min-max normalized *within each source* (so a Reddit upvote
    count, HN points, GitHub star velocity, and newsletter recency all end up on
    the same 0..1 footing), then the overall set is trimmed toward ~40 strongest
    signals to keep the synthesis prompt tight.
    """
    reddit_items = _items(fetched, SourceName.REDDIT)
    arxiv_items = _items(fetched, SourceName.ARXIV)
    hn_items = _items(fetched, SourceName.HACKERNEWS)
    github_items = _items(fetched, SourceName.GITHUB)
    newsletter_items = _items(fetched, SourceName.NEWSLETTER)

    # --- demand: Reddit pain/wish/complaint + Hacker News Ask HN pain ------
    demand: list[DemandSignal] = []
    demand.extend(_demand_from_reddit(reddit_items))
    demand.extend(_demand_from_hackernews(hn_items))
    demand = _cap_demand(demand)

    # --- capability: arXiv momentum + GitHub adoption velocity + newsletter
    #     attention + high-point HN story momentum ---------------------------
    capability: list[CapabilitySignal] = []
    capability.extend(_capability_from_arxiv(arxiv_items))
    capability.extend(_capability_from_github(github_items))
    capability.extend(_capability_from_newsletters(newsletter_items))
    capability.extend(_capability_from_hackernews(hn_items))
    capability = _cap_capability(capability)

    # --- supply: incumbents from Reddit + Show HN launches + GitHub repos +
    #     newsletter product mentions. Round-robin so every source is heard. --
    supply = _cap_supply(
        _interleave(
            _supply_from_reddit(reddit_items),
            _supply_from_hackernews(hn_items),
            _supply_from_github(github_items),
            _supply_from_newsletters(newsletter_items),
        )
    )

    # --- overall ~40 budget: keep supply (few, high-value), then fill the
    #     remaining slots with the strongest demand then capability. --------
    budget = _MAX_TOTAL - len(supply)
    ranked_demand = sorted(demand, key=lambda s: s.strength, reverse=True)
    ranked_cap = sorted(capability, key=lambda s: s.momentum, reverse=True)

    kept_demand = ranked_demand[: max(0, budget)]
    remaining = max(0, budget - len(kept_demand))
    kept_cap = ranked_cap[:remaining]

    return ExtractedSignals(
        area=area,
        sub_segments=list(scope.sub_segments),
        keywords=list(scope.keywords),
        demand=kept_demand,
        capability=kept_cap,
        supply=supply,
    )
