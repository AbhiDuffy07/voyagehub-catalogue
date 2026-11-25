"""The decisions of `nearby` (B18), as pure functions: which Commons files near a place may be its
photo, whether a file passes every photo check, and which Wikidata items may be extra places.

Nothing is guessed. A file or item that could belong to something else is skipped, and every skip
is counted under a reason so the report can show what the rules cost.
"""

import math
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from catalogue.listings.quality import distance_km
from catalogue.nearby.rules import NearbyRules
from catalogue.photos.commons import FileInfo
from catalogue.photos.rules import PhotoRules
from catalogue.photos.run import BLOCKING_FLAGS, _flags_for
from catalogue.photos.select import SelectionRules, name_words, normalise_file_name
from catalogue.publish.build import photo_of
from catalogue.publish.hero import HeroRules

Point = tuple[float, float]


@dataclass(frozen=True, slots=True)
class GeoHit:
    """One Commons file the geosearch returned: its title (with `File:`), its own point and its
    distance in metres from the place's point."""

    title: str
    point: Point
    distance_m: float


@dataclass(frozen=True, slots=True)
class OtherPlace:
    """Another place of the same city, for the ambiguity test."""

    point: Point
    words: frozenset[str]


@dataclass(frozen=True, slots=True)
class Accepted:
    hit: GeoHit
    named: bool


def file_words(title: str, selection: SelectionRules) -> frozenset[str]:
    stem = normalise_file_name(title).rsplit(".", 1)[0]
    return frozenset(name_words(stem, selection.category_min_word, selection.generic_words))


def place_words(name: str, selection: SelectionRules) -> frozenset[str]:
    return frozenset(name_words(name, selection.category_min_word, selection.generic_words))


def accept_hits(
    hits: Iterable[GeoHit],
    subject_words: frozenset[str],
    others: Iterable[OtherPlace],
    rules: NearbyRules,
    selection: SelectionRules,
    hero: HeroRules,
    skipped: Counter,
) -> list[Accepted]:
    """The files that may show this place, best first (named, then nearest, then title).

    - A file further than `search_radius_m` is not looked at.
    - A file name with a drop or collage word (maps, logos, montages) is skipped.
    - A file whose name shares a word with the place name is "named". Any other file is accepted
      only inside `name_free_radius_m`, and only when that circle holds at most
      `max_unnamed_files` unnamed files (a busy point is someone's copied coordinate).
    - A file whose point is not clearly nearer to this place than to another place of the city
      (by `ambiguity_margin_m`) is skipped, unless it is named for this place and not for the
      other one.
    """
    others = list(others)
    looked: list[tuple[GeoHit, bool]] = []
    for hit in hits:
        if hit.distance_m > rules.search_radius_m:
            skipped["far"] += 1
            continue
        stem = normalise_file_name(hit.title).rsplit(".", 1)[0]
        if hero.name_pattern.search(stem):
            skipped["name_word"] += 1
            continue
        looked.append((hit, bool(file_words(hit.title, selection) & subject_words)))
    unnamed_close = sum(
        1 for hit, named in looked if not named and hit.distance_m <= rules.name_free_radius_m
    )
    busy = unnamed_close > rules.max_unnamed_files
    accepted: list[Accepted] = []
    for hit, named in looked:
        if not named:
            if rules.name_free_radius_m == 0 or hit.distance_m > rules.name_free_radius_m:
                skipped["no_name_match"] += 1
                continue
            if busy:
                skipped["busy_point"] += 1
                continue
        words = file_words(hit.title, selection)
        ambiguous = False
        for other in others:
            other_m = distance_km(hit.point, other.point) * 1000
            if other_m < hit.distance_m + rules.ambiguity_margin_m and (
                not named or words & other.words
            ):
                ambiguous = True
                break
        if ambiguous:
            skipped["ambiguous"] += 1
            continue
        accepted.append(Accepted(hit, named))
    accepted.sort(key=lambda a: (not a.named, a.hit.distance_m, a.hit.title))
    return accepted


def photo_problem(
    info: FileInfo,
    photo: Mapping[str, Any],
    photo_rules: PhotoRules,
    selection: SelectionRules,
    hero: HeroRules,
) -> str | None:
    """Why a file may not be a place photo, else None: every check B5, B6 and publish make, plus
    the collage words and the hero override list (an unasked-for photo gets the strictest test).
    `photo` is the file in the published photo shape (PHOTO_FIELDS)."""
    blocking = _flags_for(info, photo_rules) & BLOCKING_FLAGS
    if blocking:
        return sorted(blocking)[0]
    name = normalise_file_name(info.title)
    if name in hero.rejected:
        return "rejected_list"
    if info.mime not in selection.mime_types:
        return "not_an_image"
    if hero.name_pattern.search(name.rsplit(".", 1)[0]):
        return "name_word"
    width, height = info.width, info.height
    if not isinstance(width, int) or not isinstance(height, int) or width <= 0 or height <= 0:
        return "no_size"
    if width / height > selection.max_aspect:
        return "panorama"
    if max(width, height) < selection.min_long_side:
        return "below_gate"
    if photo_of({"photo": dict(photo)}, Counter(), hero) is None:
        return "credit_or_author"
    return None


@dataclass(frozen=True, slots=True)
class KnownPlace:
    """A place the city already has (a listing) or was just given (an extra place)."""

    point: Point
    words: frozenset[str]
    qid: str | None


def same_place(
    point: Point, words: frozenset[str], qid: str | None, known: KnownPlace, rules: NearbyRules
) -> bool:
    if qid is not None and known.qid == qid:
        return True
    km = distance_km(point, known.point)
    return km <= rules.any_name_km or (km <= rules.same_place_km and bool(words & known.words))


def nearest_city(point: Point, centres: Mapping[int, Point]) -> int | None:
    """The city whose centre is nearest (ties broken by the lower page id)."""
    best: tuple[float, int] | None = None
    for pid, centre in centres.items():
        candidate = (distance_km(point, centre), pid)
        if best is None or candidate < best:
            best = candidate
    return None if best is None else best[1]


class CentreIndex:
    """City centres in a 0.1-degree grid, so "which city centre is nearest" looks only at centres
    that could be nearer than the city's own (a world run has about 22,000 centres). Gives exactly
    the answer of `nearest_city`, ties included."""

    CELL = 0.1  # degrees
    CELLS_AROUND = round(360 / CELL)

    def __init__(self, centres: Mapping[int, Point]) -> None:
        self._centres = dict(centres)
        self._grid: dict[tuple[int, int], list[int]] = defaultdict(list)
        for pid, (lat, lon) in self._centres.items():
            self._grid[self._cell(lat, lon)].append(pid)

    def _cell(self, lat: float, lon: float) -> tuple[int, int]:
        return math.floor(lat / self.CELL), math.floor(lon / self.CELL) % self.CELLS_AROUND

    def nearest(self, point: Point, max_km: float, own: int) -> int | None:
        """The nearest centre to `point`, which is within `max_km` of the centre of city `own`
        (any nearer centre is then within that distance too). Falls back to a full scan when the
        search box would be too wide (near a pole, or the point is far from its own centre)."""
        radius = distance_km(point, self._centres[own]) + 1e-9
        if radius > max_km:
            return nearest_city(point, self._centres)
        dlat = radius / 111.0 + self.CELL
        top = abs(point[0]) + dlat
        if top >= 89.9:  # the circle may wrap round the pole: no longitude box holds it
            return nearest_city(point, self._centres)
        dlon = radius / (111.0 * math.cos(math.radians(top))) + self.CELL
        if dlon >= 90:
            return nearest_city(point, self._centres)
        low_lat, _ = self._cell(point[0] - dlat, 0.0)
        high_lat, _ = self._cell(point[0] + dlat, 0.0)
        first_lon = math.floor((point[1] - dlon) / self.CELL)
        last_lon = math.floor((point[1] + dlon) / self.CELL)
        nearby = {
            pid
            for row in range(low_lat, high_lat + 1)
            for col in range(first_lon, last_lon + 1)
            for pid in self._grid.get((row, col % self.CELLS_AROUND), ())
        }
        return nearest_city(point, {pid: self._centres[pid] for pid in nearby})
