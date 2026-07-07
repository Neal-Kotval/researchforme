"""Source registry.

Single place that knows the concrete set of signal adapters. The pipeline and
the LLM tool layer both go through here so that adding another source later is a
one-line change (import it + append it to ``_build_sources``).

Every adapter conforms to the ``Source`` ABC in ``base.py`` and never raises
from ``fetch`` — so callers can safely iterate the whole registry.
"""

from __future__ import annotations

from ..schemas import SourceName
from .arxiv import ArxivSource
from .base import Source
from .github import GitHubSource
from .hackernews import HackerNewsSource
from .newsletters import NewsletterSource
from .reddit import RedditSource


def _build_sources() -> list[Source]:
    """Instantiate the canonical ordered list of adapters.

    Order is intentional: demand first (Reddit user pain, then Hacker News
    Ask/Show), then the capability / why-now tailwinds (arXiv research momentum,
    GitHub adoption velocity, newsletter attention). Callers that care about
    ordering (e.g. the source-telemetry strip in the UI) get a stable sequence.
    """
    return [
        RedditSource(),
        ArxivSource(),
        HackerNewsSource(),
        GitHubSource(),
        NewsletterSource(),
    ]


def get_sources() -> list[Source]:
    """Return a fresh list of all registered sources.

    A new list of freshly-constructed adapters is returned on each call so no
    per-run state can leak between analyses.
    """
    return _build_sources()


def get_source(name: SourceName) -> Source | None:
    """Look up a single adapter by its ``SourceName``. ``None`` if unknown."""
    for src in _build_sources():
        if src.name == name:
            return src
    return None
