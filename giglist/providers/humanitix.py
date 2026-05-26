from __future__ import annotations

from ..models import Source
from .base import CachedHTTPProvider
from .jsonld import parse_jsonld_events

# Humanitix is the secondary scrape source. Its public listing pages embed an
# `ItemList` of `Event` JSON-LD with the first ~5 events server-side rendered;
# the rest load via client-side infinite scroll. spec: {slug, category?}.
BASE_URL = "https://humanitix.com/au/events"


class HumanitixProvider(CachedHTTPProvider):
    kind = "humanitix"
    name = "humanitix"

    def _slug(self, source: Source) -> str:
        return (source.spec.get("slug") or "").strip("/")

    def _category(self, source: Source) -> str:
        return (source.spec.get("category") or "").strip("/")

    def _cache_key(self, source: Source) -> str:
        return f"{self._slug(source)}|{self._category(source)}"

    def _build_url(self, source: Source) -> str:
        slug = self._slug(source)
        category = self._category(source)
        url = f"{BASE_URL}/{slug}" if slug else BASE_URL
        if category:
            url += f"/{category}"
        return url

    def _parse(self, text: str):
        return parse_jsonld_events(text, "humanitix")
