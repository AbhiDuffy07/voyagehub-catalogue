"""The plain records B8 passes around."""

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class Stop:
    """One see/do listing with a trusted point, ready to be put in an area and a block."""

    key: str  # listing_key
    page_id: int
    ordinal: int
    owner_id: int  # the city page the listing belongs to (districts roll up to it)
    owner_slug: str
    owner_title: str
    name: str
    lat: float
    lon: float
    section: str  # see or do
    kind: str | None
    unesco: bool
    has_photo: bool
    hours: dict[str, Any] | None  # parsed hours with a real schedule, else None (unknown)
    source: str = "listing"  # listing, or article for a sub-article that is a sight
    has_summary: bool = False  # a B4 Wikipedia summary exists
    wikidata: str = ""  # the item id, used only to avoid listing one sight twice


@dataclass(slots=True)
class Area:
    number: int  # 1.. in creation order, per owner
    members: list[Stop]
    lat: float
    lon: float

    @property
    def anchor(self) -> Stop:
        return self.members[0]


@dataclass(frozen=True, slots=True)
class BlockStop:
    stop: Stop
    position: int  # 1..
    minutes_min: int
    minutes_max: int
    duration_basis: str  # kind or default
    hop_minutes: float  # estimated travel from the previous stop (0 for the first)
    hop_km: float  # estimated distance of that hop (straight line x detour factor)
    hop_method: str  # walk or road ("" for the first stop)
    hours_known: bool


@dataclass(slots=True)
class Block:
    owner_id: int
    owner_slug: str
    area_numbers: list[int]
    slot: str  # morning, afternoon or full_day
    day_trip: bool
    stops: list[BlockStop]
    minutes: int  # visit midpoints plus estimated hops
    walk_km: float  # estimated, on foot
    hours_unknown_count: int
    photo_count: int
    transfer_minutes: float = 0.0  # day trips: estimated road time from the city's centre
    transfer_km: float = 0.0
    transfer_method: str = ""
    slot_basis: str = "kind"  # kind, alternate (either half-day), opposite or trip
    title: str = ""
    intro: str = ""
    block_id: str = ""
    flags: list[str] = field(default_factory=list)
