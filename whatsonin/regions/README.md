# Region packs

Region packs are the bundled defaults that ship with the cog. Each region is a folder containing a `places.yaml` file. Admins can also add per-guild places from Discord (see the main README); packs are the read-only fallback for anything a guild hasn't customised.

## Bundled regions

| Region | Description |
|--------|-------------|
| `tasmania` | Tasmania, Australia (default) |
| `sydney` | Greater Sydney, Australia (example pack) |

Switch the active pack (bot owner):

```text
[p]set whatsonin active_region sydney
```

The change applies on the next `[p]whatsonin` command. List the available packs from Discord with `[p]whatsoninregions`.

## Adding a region

1. Make a folder: `whatsonin/regions/<region-name>/`
2. Add `places.yaml` using the new schema:

```yaml
places:
  - key: london
    display_name: London
    aliases: [ldn]
    sources:
      - kind: eventbrite
        spec: { slug: united-kingdom--london }

  - key: manchester
    display_name: Manchester
    sources:
      - kind: eventbrite
        spec: { slug: united-kingdom--manchester }
```

The old single-slug form still works for back-compat:

```yaml
places:
  - key: london
    display_name: London
    eventbrite_slug: united-kingdom--london
    aliases: [ldn]
```

Set `active_region` to your folder name (without path), then verify each source before publishing:

```text
[p]whatsonintest eventbrite united-kingdom--london
[p]whatsonintest ics https://example.org/london-events.ics
```

## Finding an Eventbrite slug

Eventbrite doesn't expose a public search API for third-party apps, so you'll need to copy slugs from their website.

### Method 1: browser URL

1. Open [Eventbrite](https://www.eventbrite.com/) and search for events in your city.
2. Open the city's "things to do" or events directory page.
3. Copy the slug from the URL path after `/d/`:

| URL | Slug |
|-----|------|
| `https://www.eventbrite.com/d/australia--hobart/events/` | `australia--hobart` |
| `https://www.eventbrite.com/d/united-kingdom--london/events/` | `united-kingdom--london` |
| `https://www.eventbrite.com/d/ca--san-francisco/events/` | `ca--san-francisco` |

The cog fetches `https://www.eventbrite.com/d/{slug}/events/`. Some cities have an alternate URL shape like `/d/australia/hobart/`. Prefer the `--` form when both exist, and confirm with `[p]whatsonintest`.

### Method 2: directory sitemap

Browse [Eventbrite's local events directory](https://www.eventbrite.com/directory/sitemap/) for country and city listings.

### Tips

- Verify every slug with `[p]whatsonintest eventbrite <slug>` before adding it to a pack.
- Disambiguate ambiguous names (`Queenstown` should be `australia--queenstown-tas` to avoid New Zealand's).
- Smaller towns often have no Eventbrite directory page. Map suburbs to a nearby city slug if needed (e.g. Sandy Bay to Hobart).
- Eventbrite only lists ticketed events. Mix in an ICS source on the same place for broader coverage.

## Contributing a region

Pull requests adding new `whatsonin/regions/<name>/places.yaml` files are welcome. Please include:

- Verified Eventbrite slugs (tested with `whatsonintest`)
- Common aliases for local nicknames
- A short note in the PR describing the geographic coverage
