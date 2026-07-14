"""Source registry.

Single place that knows the concrete set of signal adapters. The pipeline and
the LLM tool layer both go through here so that adding another source later is a
one-line change (import it + append it to ``_build_sources``).

Every adapter conforms to the ``Source`` ABC in ``base.py`` and never raises
from ``fetch`` — so callers can safely iterate the whole registry.

Two tiers of adapter exist:

  * **demand-mix** sources (the default) feed every analysis/exploration run
    via ``_fetch_all``;
  * **pressure-only** sources (``Source.pressure_only``: outcomes,
    postmortems) are excluded from that default mix and only consulted at
    pressure-test time as corroboration tools. ``get_sources()`` keeps its
    historical meaning (the demand mix); pass
    ``include_pressure_only=True`` to see everything.
"""

from __future__ import annotations

from ..schemas import SourceName
from .appreviews import AppReviewsSource
from .arxiv import ArxivSource
from .base import Source
from .github import GitHubSource
from .hackernews import HackerNewsSource
from .jobs import JobsSource
from .newsletters import NewsletterSource
from .outcomes import OutcomesSource
from .postmortems import PostmortemsSource
from .reddit import RedditSource
from .regulatory import RegulatorySource
from .stackexchange import StackExchangeSource


def _build_sources() -> list[Source]:
    """Instantiate the canonical ordered list of ALL adapters.

    Order is intentional: demand first (Reddit user pain, Hacker News Ask/Show,
    job postings as wallet-backed demand, low-star app reviews as paid-user
    pain), then the capability / why-now tailwinds (arXiv research momentum,
    GitHub adoption velocity, newsletter attention, regulatory shifts), and
    finally the pressure-only corroboration adapters (YC outcomes, post-mortem
    corpus). Callers that care about ordering (e.g. the source-telemetry strip
    in the UI) get a stable sequence.
    """
    return [
        RedditSource(),
        ArxivSource(),
        HackerNewsSource(),
        GitHubSource(),
        NewsletterSource(),
        JobsSource(),
        AppReviewsSource(),
        RegulatorySource(),
        StackExchangeSource(),
        OutcomesSource(),
        PostmortemsSource(),
    ]


def get_sources(include_pressure_only: bool = False) -> list[Source]:
    """Return a fresh list of registered sources.

    By default this is the *demand mix* — every adapter that should feed
    ``_fetch_all`` on a normal run. Pressure-only adapters (outcomes,
    postmortems) are excluded unless ``include_pressure_only=True``.

    A new list of freshly-constructed adapters is returned on each call so no
    per-run state can leak between analyses.
    """
    sources = _build_sources()
    if include_pressure_only:
        return sources
    return [s for s in sources if not s.pressure_only]


def get_source(name: SourceName) -> Source | None:
    """Look up a single adapter by its ``SourceName`` (pressure-only included).
    ``None`` if unknown."""
    for src in _build_sources():
        if src.name == name:
            return src
    return None
