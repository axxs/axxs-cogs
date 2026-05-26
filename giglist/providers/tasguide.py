from __future__ import annotations

from ..models import Source
from .base import CachedHTTPProvider
from .jsonld import parse_jsonld_events

# Tasguide aggregates Tasmanian gigs and emits one schema.org `Event` JSON-LD
# block per event on its listing pages (≈50 per /category/music page). It is
# the primary grassroots source — Hobart pubs/clubs/community gigs that
# Eventbrite, Ticketmaster, etc. miss.
BASE_URL = "https://tasguide.com.au"
DEFAULT_PATH = "category/music"  # tasguide.com.au/music 302s here


class TasguideProvider(CachedHTTPProvider):
    kind = "tasguide"
    name = "tasguide"

    def _normalised_path(self, source: Source) -> str:
        path = source.spec.get("path") if source.spec else None
        return (path or DEFAULT_PATH).strip("/")

    def _cache_key(self, source: Source) -> str:
        return self._normalised_path(source)

    def _build_url(self, source: Source) -> str:
        return f"{BASE_URL}/{self._normalised_path(source)}"

    def _parse(self, text: str):
        return parse_jsonld_events(text, "tasguide")
