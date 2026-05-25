from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import ProviderResult, Source


class EventProvider(ABC):
    kind: str
    name: str  # legacy alias for log lines; equal to kind for new providers

    @abstractmethod
    async def fetch(
        self, source: Source, *, days: int, limit: int
    ) -> ProviderResult:
        ...
