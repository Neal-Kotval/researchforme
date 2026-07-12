"""Pluggable source-adapter contract.

Adding a fourth signal later = write one class implementing `Source` and
register it. The pipeline and the LLM tools both consume this interface, so
nothing else needs to change.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field

from ..schemas import RawItem, SourceName, SourceReport


@dataclass
class FetchResult:
    """What every adapter returns from `fetch`."""

    items: list[RawItem] = field(default_factory=list)
    report: SourceReport | None = None


class Source(abc.ABC):
    """One signal source (Reddit, arXiv, Hacker News, GitHub, newsletters, ...)."""

    name: SourceName

    @property
    @abc.abstractmethod
    def live(self) -> bool:
        """True if real credentials/access are present. False -> mock/degraded."""

    @abc.abstractmethod
    def fetch(self, area: str, keywords: list[str], sub_segments: list[str]) -> FetchResult:
        """Pull raw items for the given scope. Must never raise: on any error,
        return a FetchResult whose report explains the degraded status."""

    # A short, human description used in the LLM tool manifest.
    description: str = ""

    # Pressure-only adapters (e.g. outcomes, postmortems) are excluded from the
    # default demand mix (`registry.get_sources()` / pipeline `_fetch_all`) and
    # only consulted at pressure-test time as corroboration tools.
    pressure_only: bool = False
