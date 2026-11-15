"""Readiness (B12, the pure part): is a city ready for the app, and is it plannable or browse only.

A city is ready when it has a summary, a time zone, at least `min_hero_photos` hero photos, at least
`min_photographed_places` places with their own photo and at least `min_checked_blocks` checked
blocks. Ready and filling `min_plannable_days` whole days is `plannable`; ready otherwise is
`browse_only`. Publication follows readiness: a ready city publishes at once, a published city is
unpublished only after `unpublish_after_failed_runs` not-ready runs in a row.
"""

import tomllib
from dataclasses import dataclass
from pathlib import Path

from catalogue.errors import PipelineError
from catalogue.photos.select import _strings, _table
from catalogue.prices.pricing import _whole

READINESS_RULES_PATH = (
    Path(__file__).resolve().parent.parent.parent / "config" / "readiness_rules.toml"
)
FAILED_RULES = ("summary", "timezone", "hero_photo", "photographed_places", "checked_block")


@dataclass(frozen=True, slots=True)
class ReadinessRules:
    version: int
    eligible_types: tuple[str, ...]
    min_hero_photos: int
    min_photographed_places: int
    place_sections: tuple[str, ...]
    min_checked_blocks: int
    min_plannable_days: int
    unpublish_after_failed_runs: int


def load_readiness_rules(path: Path = READINESS_RULES_PATH) -> ReadinessRules:
    source = path.name
    try:
        document = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        raise PipelineError(f"{source} could not be read as a TOML file") from None
    version = document.get("readiness_rules_version")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise PipelineError(f"{source}: readiness_rules_version needs a whole number of at least 1")
    city = _table(document, "city", source)
    photos = _table(document, "photos", source)
    blocks = _table(document, "blocks", source)
    publish = _table(document, "publish", source)
    return ReadinessRules(
        version=version,
        eligible_types=tuple(_strings(city, "eligible_types", source)),
        min_hero_photos=_whole(photos, "min_hero_photos", source, minimum=0, maximum=100),
        min_photographed_places=_whole(
            photos, "min_photographed_places", source, minimum=0, maximum=1000
        ),
        place_sections=tuple(_strings(photos, "place_sections", source)),
        min_checked_blocks=_whole(blocks, "min_checked_blocks", source, minimum=1, maximum=1000),
        min_plannable_days=_whole(blocks, "min_plannable_days", source, minimum=1, maximum=100),
        unpublish_after_failed_runs=_whole(
            publish, "unpublish_after_failed_runs", source, minimum=1, maximum=100
        ),
    )


@dataclass(frozen=True, slots=True)
class CityFacts:
    has_summary: bool
    has_timezone: bool
    hero_photos: int
    photographed_places: int
    checked_blocks: int
    days_fillable: int


@dataclass(frozen=True, slots=True)
class Verdict:
    ready: bool
    tier: str  # plannable, browse_only, not_ready
    failed: tuple[str, ...]


def assess_city(facts: CityFacts, rules: ReadinessRules) -> Verdict:
    passed: dict[str, bool] = {
        "summary": facts.has_summary,
        "timezone": facts.has_timezone,
        "hero_photo": facts.hero_photos >= rules.min_hero_photos,
        "photographed_places": facts.photographed_places >= rules.min_photographed_places,
        "checked_block": facts.checked_blocks >= rules.min_checked_blocks,
    }
    failed = tuple(name for name in FAILED_RULES if not passed[name])
    if failed:
        return Verdict(False, "not_ready", failed)
    plannable = facts.days_fillable >= rules.min_plannable_days
    return Verdict(True, "plannable" if plannable else "browse_only", ())


@dataclass(frozen=True, slots=True)
class CityState:
    published: bool
    failed_runs: int


def decide_publication(
    previous: CityState | None, *, ready: bool, rules: ReadinessRules, has_slug: bool = True
) -> tuple[CityState, str]:
    """The city's new state and what happened: publish, keep, keep_pending_unpublish, unpublish,
    blocked_no_slug (ready, but the static page needs a slug and slugs are assigned only in a
    reviewed discover wave) or none."""
    was_published = previous is not None and previous.published
    if ready:
        if not was_published and not has_slug:
            return CityState(False, 0), "blocked_no_slug"
        return CityState(True, 0), ("keep" if was_published else "publish")
    if not was_published:
        return CityState(False, 0), "none"
    failed = previous.failed_runs + 1 if previous is not None else 1
    if failed >= rules.unpublish_after_failed_runs:
        return CityState(False, failed), "unpublish"
    return CityState(True, failed), "keep_pending_unpublish"
