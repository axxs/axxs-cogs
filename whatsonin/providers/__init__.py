from .base import EventProvider
from .eventbrite import EventbriteProvider
from .ics import IcsProvider
from .manual import ManualProvider

PROVIDERS: dict = {
    "eventbrite": EventbriteProvider,
    "ics": IcsProvider,
    "manual": ManualProvider,
}

__all__ = [
    "EventProvider",
    "EventbriteProvider",
    "IcsProvider",
    "ManualProvider",
    "PROVIDERS",
]
