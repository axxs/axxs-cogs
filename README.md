# Whatsonin

A Red V3 cog that lists upcoming local events for a place. It pulls from Eventbrite city directories, public iCal feeds, and manually-added events, then merges everything into one compact Discord embed with native relative timestamps.

```text
[p]whatsonin hobart
[p]whatsonin sydney --days 14 --limit 5
```

No API key needed. Bundled region packs for Tasmania and Sydney work as soon as you load the cog. Admins can add their own places per guild from Discord.

## Install into Red

Add the repo and install:

```text
[p]repo add axxs-cogs <git-url>
[p]cog install axxs-cogs whatsonin
[p]load whatsonin
```

Red will pip-install the deps (`aiohttp`, `PyYAML`, `icalendar`) on its own.

To switch the default region pack (bot owner):

```text
[p]set whatsonin active_region sydney
```

That applies on the next command. Bundled packs are a read-only fallback. Per-guild places (below) always win.

## Commands

### Read commands (everyone)

| Command | Description |
|---------|-------------|
| `[p]whatsonin <place> [--days N] [--limit N]` | List upcoming events for a place |
| `[p]whatsoninregions` | List bundled region packs and the active default |
| `[p]whatsoninplaces` | List places resolvable in this guild (guild + pack) |
| `[p]whatsonindiag <place>` | Per-source breakdown of the last fetch: events, cache age, warnings |

### Per-guild config (admins with `manage_guild`)

Use the `whatsonin_admin` group (alias `wsa`) to add your own places without touching files.

| Command | Description |
|---------|-------------|
| `[p]wsa add <key> [display_name]` | Create a new guild place |
| `[p]wsa remove <key>` | Remove a guild place |
| `[p]wsa places` | List places configured in this guild |
| `[p]wsa source <key> add <kind> <spec>` | Add a source. Validates with one fetch before saving. |
| `[p]wsa source <key> remove <index>` | Remove a source by index |
| `[p]wsa source <key> list` | List sources on a place |
| `[p]wsa alias <key> add <alias>` | Add an alias |
| `[p]wsa alias <key> remove <alias>` | Remove an alias |
| `[p]wsa manual <key> add "<title>" <iso-datetime> [--venue X] [--url X] [--end ISO]` | Add a manually-entered event |
| `[p]wsa manual <key> remove <event-index>` | Remove a manual event |

### Bot owner

| Command | Description |
|---------|-------------|
| `[p]whatsonintest <kind> <spec>` | Test-fetch any source without saving it. Examples: `eventbrite australia--hobart`, `ics https://example.com/cal.ics` |

### Source kinds

| Kind | Spec | Example |
|------|------|---------|
| `eventbrite` | Eventbrite directory slug | `australia--hobart` |
| `ics` | Public iCalendar URL | `https://example.com/calendar.ics` |
| `manual` | Events entered via `wsa manual add` | (no URL; events live in config) |

A single place can have any mix of sources. Results are deduped by title + date + venue.

## Configuration

| Setting | Default | Description |
|---------|---------|-------------|
| `whatsonin.active_region` | `tasmania` | Default region pack. Used when a place isn't in this guild's config. |
| `whatsonin.default_limit` | 10 | Max events returned |
| `whatsonin.default_days` | 30 | Lookahead window in days |
| `whatsonin.enable_eventbrite` | true | Toggle the Eventbrite provider globally |
| `whatsonin.cache_ttl_seconds` | 600 | HTTP response cache TTL (0 disables caching) |
| `whatsonin.eventbrite_locale` | `en-AU,en;q=0.9` | Accept-Language header sent to Eventbrite |

## Region packs

See [`whatsonin/regions/README.md`](whatsonin/regions/README.md) for how to add places, find Eventbrite slugs, and contribute new regions.

Bundled packs:

- **tasmania**: Hobart, Launceston, Devonport, and more
- **sydney**: Sydney, Parramatta, Newtown, Bondi (example pack)

## Development

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest
```

There's a live test against real Eventbrite, gated by an env var:

```bash
EVENTBRITE_LIVE=1 .venv/bin/pytest tests/test_providers.py::test_live_eventbrite_returns_events
```

## Adding more data sources

Providers live under `whatsonin/providers/` and implement `EventProvider.fetch(source, *, days, limit)`. Register a new kind in `whatsonin/providers/__init__.py::PROVIDERS`. Three providers ship today (Eventbrite, ICS, manual). Adding a fourth, like a site-specific scraper for tasmania.events / everi.com.au, is one new class plus one registry entry.

## Limitations

Coverage depends on which sources you wire up. Eventbrite only lists ticketed events from its city directories. ICS coverage depends on whoever publishes the calendar. Manual events scale with how much you're willing to type. No public-API integrations yet for Meetup, Facebook events, or Ticketmaster.
