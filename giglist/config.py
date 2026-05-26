from __future__ import annotations

from redbot.core import Config

from .regions import DEFAULT_REGION


def get_config(cog) -> Config:
    return Config.get_conf(cog, identifier=8273645190283746510, force_registration=True)


def register_config(config: Config) -> None:
    config.register_global(
        active_region=DEFAULT_REGION,
        default_limit=10,
        default_days=30,
        cache_ttl_seconds=600,
        enable_tasguide=True,
        enable_humanitix=True,
        enable_ticketmaster=True,
        ticketmaster_api_key="",
        ticketmaster_country="AU",
        scrape_locale="en-AU,en;q=0.9",
    )
