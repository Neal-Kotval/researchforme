"""Small helpers shared by the Phase-3 source adapters.

The founding adapters (reddit/arxiv/hackernews/github/newsletters) each carry
private copies of these; the newer adapters (jobs/appreviews/regulatory/
outcomes/postmortems) import from here instead of growing five more copies.
Everything is dependency-free and never raises on malformed input.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from ..schemas import RawItem

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_ENTITIES = {
    "&amp;": "&",
    "&lt;": "<",
    "&gt;": ">",
    "&quot;": '"',
    "&#x27;": "'",
    "&#x2F;": "/",
    "&#39;": "'",
    "&nbsp;": " ",
}


def strip_html(text: str | None) -> str:
    """Strip HTML tags + decode the common entities. Whitespace-normalized."""
    if not text:
        return ""
    out = _TAG_RE.sub(" ", str(text))
    for ent, rep in _ENTITIES.items():
        out = out.replace(ent, rep)
    return _WS_RE.sub(" ", out).strip()


def coerce_dt(val) -> datetime | None:
    """Best-effort UTC-aware datetime from ISO strings / datetimes / epochs."""
    if val is None:
        return None
    if isinstance(val, datetime):
        return val if val.tzinfo else val.replace(tzinfo=timezone.utc)
    if isinstance(val, (int, float)):
        try:
            return datetime.fromtimestamp(float(val), tz=timezone.utc)
        except (ValueError, OSError, OverflowError):
            return None
    s = str(val).strip()
    if not s:
        return None
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def freshest(items: list[RawItem]) -> datetime | None:
    dates = [i.created for i in items if i.created is not None]
    return max(dates) if dates else None


def dedupe(items: list[RawItem]) -> list[RawItem]:
    seen: set[str] = set()
    out: list[RawItem] = []
    for i in items:
        if i.id in seen:
            continue
        seen.add(i.id)
        out.append(i)
    return out


def query_terms(area: str, keywords: list[str]) -> list[str]:
    """Ordered, case-insensitively de-duplicated search-term list."""
    terms: list[str] = []
    for t in [area, *keywords]:
        t = (t or "").strip()
        if t and t.lower() not in {x.lower() for x in terms}:
            terms.append(t)
    return terms
