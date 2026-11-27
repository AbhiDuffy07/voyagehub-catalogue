"""The shape of every published file (pydantic). `schemas/` holds the exported JSON Schemas."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SCHEMA_VERSION = 1
SLUG = r"^[a-z0-9]+(-[a-z0-9]+)*$"
Tier = Literal["plannable", "browse_only", "not_ready"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Credit(_Model):
    author: str | None
    licence: str
    licence_url: str | None
    source_url: str
    attribution: str
    history_url: str | None = None


class Photo(_Model):
    thumb_url: str
    thumb_width: int | None
    width: int | None
    height: int | None
    credit: Credit
    caption: str | None = None  # a city hero taken from a place names that place


class Text(_Model):
    text: str
    credit: Credit


class Timezone(_Model):
    name: str
    basis: str | None


class Place(_Model):
    key: str
    name: str
    kind: str | None  # None: a block stop whose listing is not in the listings file
    lat: float | None
    long: float | None
    summary: Text | None
    photo: Photo | None


class Stop(_Model):
    key: str | None
    name: str
    lat: float | None
    long: float | None
    minutes_min: int | None
    minutes_max: int | None


class Cost(_Model):
    basis: str | None
    complete: bool | None
    currency: str | None
    total_low_minor: int | None
    total_high_minor: int | None
    note: str | None
    transport: str | None


class CostPair(_Model):
    label: str
    foreigner: Cost
    resident: Cost


class Block(_Model):
    id: str
    slot: str
    title: str | None
    intro: str | None
    minutes: int | None
    stop_count: int | None
    walk_km: float | None
    day_trip: bool | None
    stops: list[Stop]
    cost: CostPair | None


class Day(_Model):
    id: str
    block_ids: list[str]
    cost: CostPair


class PriceBand(_Model):
    kind: str
    group: str
    basis: str
    currency: str
    low_minor: int | None
    high_minor: int | None
    median_minor: int | None
    label: str
    n_prices: int | None
    n_fresh: int | None
    n_free: int | None = None  # free prices counted beside the paid range (never in it)


class RideApp(_Model):
    name: str
    url: str


class RideApps(_Model):
    apps: list[RideApp]
    note: str


class GettingAround(_Model):
    excerpt: Text | None  # the Wikivoyage "Get around" section, shortened
    ride_apps: RideApps | None  # apps whose own site lists the city (names and links only)


class CityFile(_Model):
    schema_version: Literal[1]
    slug: str = Field(pattern=SLUG)
    title: str
    country_iso: str | None
    country_name: str | None  # the Wikivoyage country title, e.g. "India"
    tier: Tier
    summary: Text | None
    hero: Photo | None
    timezone: Timezone | None
    getting_around: GettingAround
    places: list[Place]
    blocks: list[Block]
    days: list[Day]
    price_bands: list[PriceBand]


class IndexCity(_Model):
    slug: str = Field(pattern=SLUG)
    title: str
    country_iso: str | None
    country_name: str | None
    tier: Tier
    hero: Photo | None
    file: str


class IndexShard(_Model):
    country_iso: str | None
    country_name: str | None
    file: str
    cities: int


class IndexFile(_Model):
    """Exactly one of `cities` (a flat index, also each shard file) or `shards` (the top file
    of a world-sized index, one entry per country file)."""

    schema_version: Literal[1]
    cities: list[IndexCity] | None = None
    shards: list[IndexShard] | None = None

    @model_validator(mode="after")
    def _one_of_cities_or_shards(self) -> "IndexFile":
        if (self.cities is None) == (self.shards is None):
            raise ValueError("an index has either cities or shards")
        return self


class AttributionEntry(_Model):
    kind: Literal["photo", "summary", "getting_around"]
    city: str
    subject: str
    author: str | None
    licence: str
    licence_url: str | None
    source_url: str
    attribution: str
    history_url: str | None = None


class Notice(_Model):
    attribution: str
    licence: str | None
    licence_url: str | None


class AttributionsPart(_Model):
    schema_version: Literal[1]
    entries: list[AttributionEntry]


class PartInfo(_Model):
    file: str
    entries: int
    first_city: str
    last_city: str


class AttributionsFile(_Model):
    schema_version: Literal[1]
    notices: list[Notice]
    parts: list[PartInfo]


class FileInfo(_Model):
    sha256: str
    bytes: int


class ManifestFile(_Model):
    schema_version: Literal[1]
    version: str
    dump_date: str
    country_filter: str | None
    generated_on: str
    readiness_rules_version: int | None
    blocks_rules_version: int | None
    counts: dict[str, int]
    files: dict[str, FileInfo]


SCHEMAS = {
    "city": CityFile,
    "index": IndexFile,
    "attributions": AttributionsFile,
    "attributions_part": AttributionsPart,
    "manifest": ManifestFile,
}
