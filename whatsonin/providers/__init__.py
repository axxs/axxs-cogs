from .base import EventProvider
from .eventbrite import EventbriteProvider
from .ics import IcsProvider
from .manual import ManualProvider
from .rss import RssProvider

PROVIDERS: dict = {
    "eventbrite": EventbriteProvider,
    "ics": IcsProvider,
    "manual": ManualProvider,
    "rss": RssProvider,
}

__all__ = [
    "EventProvider",
    "EventbriteProvider",
    "IcsProvider",
    "ManualProvider",
    "RssProvider",
    "PROVIDERS",
]
