from .base import (
    DEFAULT_CACHE_TTL,
    MAX_IN_PROGRESS_SPAN,
    CachedHTTPProvider,
    EventProvider,
    filter_events,
)
from .humanitix import HumanitixProvider
from .tasguide import TasguideProvider
from .ticketmaster import TicketmasterProvider

PROVIDERS: dict = {
    "tasguide": TasguideProvider,
    "humanitix": HumanitixProvider,
    "ticketmaster": TicketmasterProvider,
}

__all__ = [
    "EventProvider",
    "CachedHTTPProvider",
    "DEFAULT_CACHE_TTL",
    "MAX_IN_PROGRESS_SPAN",
    "filter_events",
    "TasguideProvider",
    "HumanitixProvider",
    "TicketmasterProvider",
    "PROVIDERS",
]
