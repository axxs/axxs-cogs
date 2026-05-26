from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

import yaml

from .models import Place, Source

DEFAULT_REGION = "tasmania"


def normalize_name(text: str) -> str:
    """Collapse case, whitespace, dashes, and underscores. The same function
    is applied to user input and to place keys/aliases so the two sides match."""
    text = text.strip().lower()
    text = re.sub(r"[\s_\-]+", " ", text)
    return text.strip()


class RegionNotFoundError(ValueError):
    pass


def default_regions_dir() -> Path:
    return Path(__file__).resolve().parent / "regions"


def list_regions(regions_dir: Optional[Path] = None) -> list[str]:
    root = regions_dir or default_regions_dir()
    if not root.is_dir():
        return []
    return sorted(
        path.name
        for path in root.iterdir()
        if path.is_dir() and (path / "places.yaml").is_file()
    )


def load_region(
    region: str, regions_dir: Optional[Path] = None
) -> tuple[dict[str, Place], dict[str, str]]:
    root = regions_dir or default_regions_dir()
    path = root / region / "places.yaml"
    if not path.is_file():
        available = ", ".join(list_regions(root)) or "(none)"
        raise RegionNotFoundError(
            f"Region '{region}' not found. Available regions: {available}"
        )

    with path.open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}

    places: dict[str, Place] = {}
    aliases: dict[str, str] = {}

    for entry in data.get("places", []):
        key = normalize_name(str(entry["key"]))
        display_name = str(entry["display_name"]).strip()
        sources = _sources_from_entry(entry, place_key=key, path=path)
        alias_tuple = tuple(
            normalize_name(str(a)) for a in (entry.get("aliases") or [])
        )

        places[key] = Place(
            key=key,
            display_name=display_name,
            sources=tuple(sources),
            aliases=alias_tuple,
        )
        for alias in alias_tuple:
            aliases[alias] = key

    if not places:
        raise RegionNotFoundError(f"Region '{region}' has no places defined in {path}")

    return places, aliases


def _sources_from_entry(entry: dict, *, place_key: str, path: Path) -> list:
    if "sources" not in entry:
        raise RegionNotFoundError(
            f"Place '{place_key}' in {path} has no 'sources'."
        )
    out = []
    for s in entry["sources"]:
        kind = str(s["kind"]).strip()
        spec = dict(s.get("spec") or {})
        label = s.get("label")
        scope = str(s.get("scope") or "local").strip().lower()
        out.append(Source(kind=kind, spec=spec, label=label, scope=scope))
    return out
