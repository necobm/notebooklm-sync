"""Loading and validating a notebook's YAML source manifest."""

from __future__ import annotations

from pathlib import Path

import yaml

from .crawl import parse_rule
from .errors import ManifestError
from .matching import dedupe_entries
from .models import ManifestEntry, SyncPolicy

VALID_TYPES = frozenset({"url", "youtube", "text", "file"})


def parse_manifest(data: object, *, origin: str = "<manifest>") -> list[ManifestEntry]:
    """Validate a parsed manifest document into entries.

    Kept separate from file IO so it can be tested against literals.
    """
    if data is None:
        raise ManifestError(f"{origin} is empty")
    if not isinstance(data, dict):
        raise ManifestError(f"{origin} must be a mapping with a 'sources' key")

    raw_sources = data.get("sources")
    if raw_sources is None:
        raise ManifestError(f"{origin} has no 'sources' key")
    if not isinstance(raw_sources, list):
        raise ManifestError(f"{origin}: 'sources' must be a list")

    entries: list[ManifestEntry] = []
    for index, item in enumerate(raw_sources):
        where = f"{origin} sources[{index}]"

        # A bare string is a reasonable shorthand for a URL-only entry.
        if isinstance(item, str):
            entries.append(ManifestEntry(url=item, rule=parse_rule(item, where=where)))
            continue
        if not isinstance(item, dict):
            raise ManifestError(f"{where} must be a string or a mapping")

        url = item.get("url")
        if not url or not isinstance(url, str):
            raise ManifestError(f"{where} is missing a 'url'")

        type_ = item.get("type")
        if type_ is not None:
            if not isinstance(type_, str) or type_.lower() not in VALID_TYPES:
                valid = ", ".join(sorted(VALID_TYPES))
                raise ManifestError(f"{where}: invalid type {type_!r}. Valid values: {valid}")
            type_ = type_.lower()

        raw_policy = item.get("policy")
        policy = None
        if raw_policy is not None:
            try:
                policy = SyncPolicy(str(raw_policy).lower())
            except ValueError:
                valid = ", ".join(p.value for p in SyncPolicy)
                raise ManifestError(
                    f"{where}: invalid policy {raw_policy!r}. Valid values: {valid}"
                ) from None

        title = item.get("title")
        if title is not None and not isinstance(title, str):
            raise ManifestError(f"{where}: 'title' must be a string")

        rule = parse_rule(url, where=where)
        if rule is not None and title is not None:
            raise ManifestError(
                f"{where}: a crawl rule cannot carry a 'title' — it expands to many "
                "pages, and one title cannot name them all"
            )

        entries.append(ManifestEntry(url=url, title=title, type=type_, policy=policy, rule=rule))

    # normalize_url raises ManifestError for bad schemes, so this both de-duplicates
    # and validates every URL before the engine ever sees it.
    deduped, duplicates = dedupe_entries(entries)
    if duplicates:
        # Duplicates are a manifest smell, not an error — the user gets told, not blocked.
        for url in duplicates:
            print(f"warning: {origin} lists {url} more than once; keeping the first entry")
    return deduped


def load_manifest(path: str | Path) -> list[ManifestEntry]:
    """Read and validate the manifest at ``path``."""
    manifest_path = Path(path)
    if not manifest_path.exists():
        raise ManifestError(f"Manifest not found: {manifest_path}")
    try:
        raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ManifestError(f"Could not parse {manifest_path}: {exc}") from exc
    except OSError as exc:
        raise ManifestError(f"Could not read {manifest_path}: {exc}") from exc
    return parse_manifest(raw, origin=str(manifest_path))
