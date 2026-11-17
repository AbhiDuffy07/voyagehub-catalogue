"""A seeded random sample of published city heroes, for a human to look at after a run.

Reads the built `site/v1/cities/*.json` (what the app will show), so it checks the real output and
needs no network. The same site and seed always give the same list. Each line has the city name and
the two links a reviewer needs: the picture and its source page. A hero with a caption is a place
photo standing in for the city's own.
"""

import json
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from catalogue.discover.output import write_text
from catalogue.errors import PipelineError


@dataclass(frozen=True, slots=True)
class HeroSample:
    cities: int
    with_hero: int
    picked: list[dict[str, Any]]

    @property
    def without_hero(self) -> int:
        return self.cities - self.with_hero


def sample_heroes(site_dir: Path, count: int, seed: int) -> HeroSample:
    folder = site_dir / "v1" / "cities"
    if not folder.is_dir():
        raise PipelineError("the built site folder v1/cities does not exist; run publish-static")
    heroes = []
    cities = 0
    for path in sorted(folder.glob("*.json")):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
            cities += 1
            hero = doc["hero"]
            if hero is not None:
                heroes.append(
                    {
                        "slug": doc["slug"],
                        "title": doc["title"],
                        "country_iso": doc["country_iso"],
                        "thumb_url": hero["thumb_url"],
                        "source_url": hero["credit"]["source_url"],
                        "caption": hero.get("caption"),
                    }
                )
        except (OSError, ValueError, KeyError, TypeError):
            raise PipelineError("a city file in the site is damaged; run publish-static") from None
    rng = random.Random(seed)  # noqa: S311 - a reproducible review sample, not security
    picked = sorted(rng.sample(heroes, min(count, len(heroes))), key=lambda p: p["slug"])
    return HeroSample(cities=cities, with_hero=len(heroes), picked=picked)


def write_sample(sample: HeroSample, path: Path, seed: int) -> None:
    ordered = sorted(sample.picked, key=lambda p: (p["country_iso"] or "~", p["slug"]))
    lines = [
        "# Hero photo review sample",
        "",
        f"{len(ordered)} of {sample.with_hero} heroes, picked at random with seed {seed} "
        f"({sample.cities} city files, {sample.without_hero} without a hero). "
        "Open the picture link and judge it; the source link has the author and licence.",
        "",
        "| Country | City | Picture | Source | Note |",
        "|---|---|---|---|---|",
    ]
    for p in ordered:
        note = f"place photo: {p['caption']}" if p["caption"] else ""
        lines.append(
            f"| {p['country_iso'] or '-'} | {p['title']} | {p['thumb_url']} | {p['source_url']} "
            f"| {note} |"
        )
    write_text(path, "\n".join(lines) + "\n")
