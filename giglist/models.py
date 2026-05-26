from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass(frozen=True)
class Source:
    kind: str
    spec: dict
    label: Optional[str] = None
    # 'local' (the place's actual geographic narrowing — e.g. humanitix
    # `au--tas--devonport`, ticketmaster `city=Devonport`) vs 'statewide'
    # (sources that can't be city-narrowed — e.g. tasguide's `/category/music`,
    # ticketmaster `dmaId=707`). The cog uses this to render a "no
    # <Place>-specific gigs; showing wider listings" note when all local
    # sources came up empty.
    scope: str = "local"


@dataclass(frozen=True)
class Place:
    key: str
    display_name: str
    sources: tuple = ()
    aliases: tuple = ()


@dataclass
class Event:
    title: str
    start: Optional[datetime]
    end: Optional[datetime]
    venue: Optional[str]
    url: Optional[str]
    source: str
    description: Optional[str] = None
    # Tagged by the cog after each provider fetch ('local' | 'statewide')
    # so the renderer can group events into sections without re-reading
    # diag. None when an Event is constructed outside the cog pipeline
    # (e.g. in tests) — the renderer treats that as un-sectioned.
    scope: Optional[str] = None

    def dedupe_key(self) -> tuple:
        title = " ".join(self.title.lower().split())
        venue = " ".join((self.venue or "").lower().split())
        date_key = self.start.date().isoformat() if self.start else ""
        return title, date_key, venue


@dataclass
class ProviderResult:
    events: list[Event]
    warnings: list[str]
    error: Optional[str] = None
