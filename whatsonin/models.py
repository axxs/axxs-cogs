from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass(frozen=True)
class Source:
    kind: str
    spec: dict
    label: Optional[str] = None


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
