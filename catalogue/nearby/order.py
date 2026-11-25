"""The order in which `nearby` works through short cities, so a run that stops at its time budget
has helped the cities closest to ready.

First the cities that already have a summary (without one a city cannot become ready this run,
whatever nearby adds), then the ones missing the fewest photographed places, then the ones that
already have a hero photo (an extra place's Wikidata image can still give one), then page id, so
the order is the same on every run over the same inputs.
"""

from collections.abc import Collection, Iterable, Mapping

from catalogue.readiness.rules import ReadinessRules


def city_order(
    short: Iterable[int],
    places: Mapping[int, int],
    hero: Mapping[int, int],
    has_summary: Collection[int],
    rules: ReadinessRules,
) -> list[int]:
    def key(pid: int) -> tuple[bool, int, bool, int]:
        return (
            pid not in has_summary,
            rules.min_photographed_places - places.get(pid, 0),
            hero.get(pid, 0) < rules.min_hero_photos,
            pid,
        )

    return sorted(set(short), key=key)
