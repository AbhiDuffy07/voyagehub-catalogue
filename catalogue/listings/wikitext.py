"""Read one Wikivoyage article's wikitext into sections, listings and markers.

Everything is kept raw: a field value is the wikitext of the parameter with surrounding spaces
trimmed, nothing is interpreted (prices and hours are B3b). An empty value is stored as an empty
string. A parameter this code does not know goes to `extra`, so nothing is dropped.
"""

import re
from collections.abc import Iterator
from dataclasses import dataclass, field

import mwparserfromhell
from mwparserfromhell.nodes import (
    ExternalLink,
    Heading,
    Tag,
    Template,
    Wikilink,
)
from mwparserfromhell.wikicode import Wikicode

KINDS = frozenset({"see", "do", "buy", "eat", "drink", "sleep", "go", "listing"})
FIELDS = (
    "name",
    "alt",
    "address",
    "directions",
    "lat",
    "long",
    "phone",
    "tollfree",
    "fax",
    "email",
    "url",
    "hours",
    "price",
    "checkin",
    "checkout",
    "wikidata",
    "wikipedia",
    "image",
    "content",
    "lastedit",
    "type",
)
MARKER_FIELDS = ("name", "type", "lat", "long", "wikidata")

STANDARD_SECTIONS = frozenset(
    {
        "understand",
        "history",
        "climate",
        "landscape",
        "culture",
        "orientation",
        "get_in",
        "get_around",
        "see",
        "do",
        "learn",
        "work",
        "buy",
        "eat",
        "drink",
        "sleep",
        "talk",
        "connect",
        "stay_safe",
        "stay_healthy",
        "respect",
        "cope",
        "go_next",
        "fees_and_permits",
        "cities",
        "regions",
        "districts",
        "other_destinations",
        "itineraries",
    }
)
DESTINATION_LISTS = frozenset({"cities", "regions", "districts", "other_destinations"})
_INVISIBLE_TAGS = frozenset({"ref", "gallery", "references", "imagemap", "noinclude"})
_REMOVED_LINK_PREFIXES = ("category:", "file:", "image:", "media:")
_NOT_ALNUM = re.compile(r"[^a-z0-9]+")
_BLANK_RUNS = re.compile(r"\n{3,}")


@dataclass(frozen=True, slots=True)
class RawListing:
    kind: str
    listing_type: str  # the `type=` of the generic {{listing}} template, else empty
    fields: dict[str, str]
    extra: dict[str, str]
    repeated_params: tuple[str, ...] = ()
    from_marker: bool = False  # built from a typed {{marker}}, so most fields are empty


@dataclass(frozen=True, slots=True)
class Section:
    heading: str  # empty for the lead
    key: str
    standard: bool
    kind: str  # lead, prose, destination_list
    text: str
    listings: list[RawListing] = field(default_factory=list)
    markers: list[dict[str, str]] = field(default_factory=list)

    @property
    def listing_count(self) -> int:
        return len(self.listings)


@dataclass(frozen=True, slots=True)
class PageContent:
    sections: list[Section]
    markers: list[dict[str, str]]
    geo: tuple[str, str] | None = None  # the page's own {{geo}}: (lat, long) as written, or None


def section_key(heading: str) -> str:
    return _NOT_ALNUM.sub("_", heading.lower()).strip("_")


def _template_name(template: Template) -> str:
    return str(template.name).strip().lower()


def _values(template: Template) -> tuple[dict[str, str], tuple[str, ...]]:
    values: dict[str, str] = {}
    repeated: list[str] = []
    for parameter in template.params:
        key = str(parameter.name).strip().lower()
        if key in values and key not in repeated:
            repeated.append(key)
        values[key] = str(parameter.value).strip()  # MediaWiki: the last one wins
    return values, tuple(repeated)


def _walk(code: Wikicode) -> Iterator[Template]:
    """Yield listing and marker templates. A listing is never searched inside, so a listing
    written in another listing's parameter is not counted twice."""
    for node in code.nodes:
        if isinstance(node, Template):
            name = _template_name(node)
            if name in KINDS or name == "marker":
                yield node
            else:
                for parameter in node.params:
                    yield from _walk(parameter.value)
        elif isinstance(node, Tag) and node.contents is not None:
            yield from _walk(node.contents)
        elif isinstance(node, Wikilink) and node.text is not None:
            yield from _walk(node.text)
        elif isinstance(node, ExternalLink) and node.title is not None:
            yield from _walk(node.title)


def _listing(template: Template) -> RawListing:
    kind = _template_name(template)
    values, repeated = _values(template)
    fields = {name: values.pop(name, "") for name in FIELDS}
    listing_type = fields["type"].lower() if kind == "listing" else ""
    return RawListing(kind, listing_type, fields, values, repeated)


def _marker(template: Template) -> dict[str, str]:
    values, _ = _values(template)
    return {name: values.get(name, "") for name in MARKER_FIELDS}


def marker_listing(marker: dict[str, str]) -> RawListing | None:
    """A typed marker (go, see, eat, ...) as a listing; None for untyped or other marker types."""
    kind = marker["type"].strip().lower()
    if kind not in KINDS or kind == "listing":
        return None
    fields = {name: "" for name in FIELDS}
    fields.update(name=marker["name"], lat=marker["lat"], long=marker["long"])
    fields["wikidata"] = marker["wikidata"]
    return RawListing(kind, "", fields, {}, (), from_marker=True)


def _heading_text(heading: Heading) -> str:
    return " ".join(heading.title.strip_code().split())


def _plain_text(code: Wikicode) -> str:
    """Plain text: links reduced to their text, listings and other templates gone, references,
    categories and files removed. Bullets become lines and sub-headings stay as lines."""
    for tag in [t for t in code.filter_tags() if str(t.tag).strip().lower() in _INVISIBLE_TAGS]:
        if code.contains(tag):  # a tag nested in a removed tag is already gone
            code.remove(tag)
    for link in [
        link
        for link in code.filter_wikilinks()
        if str(link.title).strip().lower().startswith(_REMOVED_LINK_PREFIXES)
    ]:
        # A link nested in another removed link (a file inside a file caption) is already gone.
        if code.contains(link):
            code.remove(link)
    stripped = code.strip_code(normalize=True, collapse=True)
    lines = [line.strip() for line in stripped.splitlines()]
    return _BLANK_RUNS.sub("\n\n", "\n".join(lines)).strip()


def _build_section(
    heading: str | None, nodes: list, markers: list[dict[str, str]]
) -> Section | None:
    code = Wikicode(nodes)
    listings: list[RawListing] = []
    own_markers: list[dict[str, str]] = []
    for template in _walk(code):
        if _template_name(template) == "marker":
            own_markers.append(_marker(template))
        else:
            listings.append(_listing(template))
    markers.extend(own_markers)
    text = _plain_text(code)
    if heading is None:
        if not text and not listings:
            return None
        return Section("", "lead", True, "lead", text, listings, own_markers)
    key = section_key(heading)
    kind = "destination_list" if key in DESTINATION_LISTS else "prose"
    return Section(heading, key, key in STANDARD_SECTIONS, kind, text, listings, own_markers)


def _page_geo(nodes: list) -> tuple[str, str] | None:
    """The first {{geo|lat|long}}: positional values 1 and 2 (a third is the zoom), or named."""
    for node in nodes:
        if isinstance(node, Template) and _template_name(node) == "geo":
            named = {str(p.name).strip().lower(): str(p.value).strip() for p in node.params}
            lat = named.get("lat", named.get("1", ""))
            long = named.get("long", named.get("2", ""))
            return lat, long
    return None


def parse_page(text: str) -> PageContent:
    """Split at level-2 headings; the text before the first one is the lead."""
    nodes = mwparserfromhell.parse(text).nodes
    markers: list[dict[str, str]] = []
    sections: list[Section] = []
    heading: str | None = None
    current: list = []
    for node in nodes:
        if isinstance(node, Heading) and node.level == 2:
            section = _build_section(heading, current, markers)
            if section is not None:
                sections.append(section)
            heading, current = _heading_text(node), []
        else:
            current.append(node)
    section = _build_section(heading, current, markers)
    if section is not None:
        sections.append(section)
    return PageContent(sections, markers, _page_geo(nodes))
