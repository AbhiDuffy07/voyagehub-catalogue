"""The decisions of `nearby` (B18), as pure functions: which Commons files near a place may be its
photo, whether a file passes every photo check, and which Wikidata items may be extra places.

Nothing is guessed. A file or item that could belong to something else is skipped, and every skip
is counted under a reason so the report can show what the rules cost.
"""

from collections import Counter
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
