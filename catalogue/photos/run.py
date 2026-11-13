"""`photo-candidates` (B5): gather Wikimedia Commons photo candidates for destinations and places.

It reads the `enrich-wikidata` output (hash-checked against that run's manifest) and, for every
article and every see/do listing that has a Wikidata item, collects candidate files from two
sources, in the owner's order of preference:

1. the item's Wikidata image (P18). `enrich-wikidata` stores one image name per item and drops it
   when an item has several, so every item without a stored image is re-read from Wikidata; if it
   has several images, every distinct file becomes a candidate flagged `multiple_p18` and none is
   chosen;
2. only for subjects with no usable image from step 1: files with a Commons quality mark
   (Quality, Featured, Valued) in the item's Commons category (P373), at most `category_cap` per
   subject. Searching a category is one request, so the step is kept for the subjects that need it.

Each candidate keeps its Commons file, size, hotlink URLs, plain-text author, licence code, name and
link, and an attribution line. Nothing is scored or chosen here (that is B6), nothing is guessed (a
value Commons does not give stays empty and a flag says so), and nothing is downloaded: photos are
hotlinked. Local files only, written to `out/photos/<date>[-CC]/`.

A candidate is `usable` when its file exists, is a photo type, has an allowed licence, has an
author, and has a thumbnail URL. Coverage (how many destinations and see/do listings have at least
one usable candidate) is in the report; listings with no Wikidata item count against it.
"""

import hashlib
import json
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from catalogue.discover.output import _csv_text, write_manifest, write_text
from catalogue.enrich.inputs import _rows, _sha256, verify_enrich_output
from catalogue.errors import PipelineError
from catalogue.http import PoliteClient
from catalogue.listings.output import JsonlWriter
from catalogue.listings.run import _safe_cell, parse_dump_date
from catalogue.photos.commons import (
    CategoryFiles,
    FileInfo,
    fetch_file_info,
    search_marked_files,
)
from catalogue.photos.rules import PhotoRules, load_photo_rules
from catalogue.photos.wikidata import MediaClaims, fetch_media_claims

SCHEMA_VERSION = 1
LISTING_KINDS = frozenset({"see", "do"})
NO_COUNTRY = "(none)"
INPUT_FILES = ("destination_facts.jsonl", "listing_facts.jsonl")
OUTPUT_FILES = (
    "coverage_by_country.csv",
    "flags_by_country.csv",
    "licences.csv",
    "photo_candidates.jsonl",
    "photo_candidates_by_country.csv",
    "report.md",
)
# A candidate with any of these is kept, flagged, and never counted as usable.
BLOCKING_FLAGS = frozenset(
    {
        "file_missing",
        "licence_not_allowed",
        "licence_unknown",
        "no_author",
        "no_thumb_url",
        "not_an_image",
    }
)


@dataclass(frozen=True, slots=True)
class Subject:
    page_id: int
    ordinal: int  # 0 for an article, the listing's ordinal for a listing
    kind: str  # article or listing
    listing_key: str | None
    country_iso: str | None
    wikidata_id: str | None
    image: str | None  # the single P18 file name enrich-wikidata stored


@dataclass(slots=True)
class Candidate:
    """One file for one subject, with where it came from."""

    sources: set[str] = field(default_factory=set)
    rank: str | None = None
    flags: set[str] = field(default_factory=set)


@dataclass(frozen=True, slots=True)
class PhotoResult:
    output_dir: Path
    totals: dict[str, Any]

    def summary(self) -> str:
        totals = self.totals
        flagged = sum(totals["flags"].values())
        lines = [
            f"subjects: {totals['subjects']} (articles {totals['articles']}, "
            f"listings {totals['listings']}); candidate rows: {totals['rows']}; "
            f"distinct files: {totals['files']}",
            f"usable rows: {totals['usable']}; flag occurrences: {flagged}",
        ]
        coverage = totals["coverage"]["all"]
        for subject in ("articles", "listings"):
            entry = coverage[subject]
            lines.append(
                f"coverage {subject}: {entry['with_usable']} of {entry['total']} "
                f"({_percent(entry['with_usable'], entry['total'])}%)"
            )
        if totals["flags"]:
            lines.append(", ".join(f"{k} {v}" for k, v in totals["flags"].items()))
        return "\n".join(lines)


def _percent(part: int, whole: int) -> str:
    return f"{100 * part / whole:.1f}" if whole else "-"


# --- inputs -------------------------------------------------------------------------------------


def _subjects(folder: Path) -> list[Subject]:
    subjects: list[Subject] = []
    for row in _rows(folder / "destination_facts.jsonl"):
        try:
            page_id = row["wikivoyage_page_id"]
        except KeyError:
            raise PipelineError("destination_facts.jsonl has a damaged row") from None
        subjects.append(
            Subject(
                page_id,
                0,
                "article",
                None,
                row.get("tree_country_iso"),
                row.get("wikidata_id"),
                row.get("image") or None,
            )
        )
    for row in _rows(folder / "listing_facts.jsonl"):
        try:
            page_id, ordinal, key = row["page_id"], row["ordinal"], row["listing_key"]
            kind = row["listing_kind"]
        except KeyError:
            raise PipelineError("listing_facts.jsonl has a damaged row") from None
        if kind in LISTING_KINDS:
            subjects.append(
                Subject(
                    page_id,
                    ordinal,
                    "listing",
                    key,
                    row.get("country_iso"),
                    row.get("wikidata_id"),
                    row.get("image") or None,
                )
            )
    return sorted(subjects, key=lambda s: (s.page_id, s.ordinal))


def _listing_totals(enrich_folder: Path, listings_folder: Path) -> Counter:
    """See/do listings per country, from fetch-listings' own file. `listing_facts.jsonl` holds only
    listings that have an item or no usable point, so it cannot be the denominator. The file is
    accepted only when it is the one this enrich run read (manifest and file hashes)."""
    manifest_path = listings_folder / "manifest.json"
    if not manifest_path.is_file():
        raise PipelineError(f"manifest.json not found in {listings_folder}; run fetch-listings")
    try:
        enrich_manifest = json.loads((enrich_folder / "manifest.json").read_text(encoding="utf-8"))
        expected_manifest = enrich_manifest["listings_manifest_sha256"]
        listings_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        expected_listings = listings_manifest["outputs_sha256"]["listings.jsonl"]
    except (OSError, ValueError, KeyError, TypeError):
        raise PipelineError(
            "the fetch-listings or enrich-wikidata manifest.json is damaged; run them again"
        ) from None
    if hashlib.sha256(manifest_path.read_bytes()).hexdigest() != expected_manifest:
        raise PipelineError(
            "the fetch-listings manifest.json is not the one enrich-wikidata read; "
            "run enrich-wikidata again"
        )
    path = listings_folder / "listings.jsonl"
    if not path.is_file():
        raise PipelineError(f"listings.jsonl not found in {listings_folder}")
    if _sha256(path) != expected_listings:
        raise PipelineError("listings.jsonl does not match the fetch-listings manifest")
    totals: Counter = Counter()
    for row in _rows(path):
        try:
            kind, country = row["kind"], row["country_iso"]
        except KeyError:
            raise PipelineError("listings.jsonl has a damaged row") from None
        if kind in LISTING_KINDS:
            totals[country or NO_COUNTRY] += 1
    return totals


# --- candidates ---------------------------------------------------------------------------------


def _add(
    found: dict[str, Candidate], name: str, source: str, rank: str | None, *flags: str
) -> None:
    candidate = found.setdefault(f"File:{name}", Candidate())
    candidate.sources.add(source)
    candidate.flags.update(flags)
    if rank is not None:
        candidate.rank = rank


def _p18_candidates(subject: Subject, claims: Mapping[str, MediaClaims]) -> dict[str, Candidate]:
    """The subject's own Wikidata image(s), by `File:` title. Several are all kept, none chosen."""
    found: dict[str, Candidate] = {}
    media = claims.get(subject.wikidata_id or "")
    if subject.image:
        rank = dict(media.p18).get(subject.image) if media else None
        _add(found, subject.image, "wikidata_p18", rank)
    elif media and media.p18:
        extra = "multiple_p18" if len(media.p18) > 1 else "p18_after_facts"
        for name, rank in media.p18:
            _add(found, name, "wikidata_p18", rank, extra)
    return found


def _add_category_candidates(
    found: dict[str, Candidate], subject_flags: set[str], entry: CategoryFiles
) -> None:
    if entry.unusable:
        subject_flags.add("commons_category_unusable")
        return
    for title in entry.files:
        if title.startswith("File:"):
            _add(found, title.removeprefix("File:"), "commons_category", None)
    if entry.capped:
        for candidate in found.values():
            if "commons_category" in candidate.sources:
                candidate.flags.add("category_cap_reached")


def _merge_by_file(
    files: Mapping[str, Candidate], infos: Mapping[str, FileInfo]
) -> dict[str, tuple[str, Candidate]]:
    """One candidate per file Commons finally reached: two requested names (`_` or space, a
    redirect) for the same file are merged, keeping every source and flag."""
    merged: dict[str, tuple[str, Candidate]] = {}
    for requested in sorted(files):
        candidate = files[requested]
        final = infos[requested].title
        if final not in merged:
            merged[final] = (requested, Candidate(set(), candidate.rank))
        target = merged[final][1]
        target.sources |= candidate.sources
        target.flags |= candidate.flags
        if target.rank is None:
            target.rank = candidate.rank
    return merged


def _is_usable(candidate: Candidate, info: FileInfo, rules: PhotoRules) -> bool:
    return info.status == "ok" and not (candidate.flags | _flags_for(info, rules)) & BLOCKING_FLAGS


def _flags_for(info: FileInfo, rules: PhotoRules) -> set[str]:
    if info.status != "ok":
        return {"file_missing"}
    flags: set[str] = set()
    if info.licence_code is None:
        if not (info.licence_name and rules.licence_name_allowed(info.licence_name)):
            flags.add("licence_unknown")
    elif not rules.licence_allowed(info.licence_code):
        flags.add("licence_not_allowed")
    if info.author is None:
        flags.add("no_author")
    if info.mime not in rules.mime_types:
        flags.add("not_an_image")
    if info.thumb_url is None:
        flags.add("no_thumb_url")
    return flags


def _licence_ok(code: str, name: str, rules: PhotoRules) -> bool:
    return rules.licence_allowed(code) if code else rules.licence_name_allowed(name)


def _attribution(info: FileInfo) -> str | None:
    if info.status != "ok" or not info.author or not info.licence_name:
        return None
    return f"{info.author}, {info.licence_name}, via Wikimedia Commons"


def _row(
    subject: Subject,
    requested: str,
    candidate: Candidate,
    info: FileInfo,
    rules: PhotoRules,
    dump_date: str,
    read_at: str,
) -> dict[str, Any]:
    flags = candidate.flags | _flags_for(info, rules)
    ok = info.status == "ok"
    return {
        "schema_version": SCHEMA_VERSION,
        "subject": subject.kind,
        "page_id": subject.page_id,
        "ordinal": subject.ordinal,
        "listing_key": subject.listing_key,
        "country_iso": subject.country_iso,
        "wikidata_id": subject.wikidata_id,
        "commons_file": info.title.removeprefix("File:"),
        "requested_file": requested.removeprefix("File:"),
        "sources": sorted(candidate.sources),
        "p18_rank": candidate.rank,
        "description_url": info.description_url,
        "url": info.url,
        "thumb_url": info.thumb_url,
        "thumb_width": info.thumb_width,
        "width": info.width,
        "height": info.height,
        "mime": info.mime,
        "sha1": info.sha1,
        "author": info.author,
        "licence_code": info.licence_code,
        "licence_name": info.licence_name,
        "licence_url": info.licence_url,
        "attribution": _attribution(info),
        "quality_marks": list(info.marks),
        "usable": ok and not flags & BLOCKING_FLAGS,
        "flags": sorted(flags),
        "rules_version": rules.version,
        "dump_date": f"{dump_date[:4]}-{dump_date[4:6]}-{dump_date[6:]}",
        "read_at": read_at,
    }


# --- report -------------------------------------------------------------------------------------


def _coverage_entry(total: int, with_usable: int) -> dict[str, Any]:
    return {"total": total, "with_usable": with_usable}


def _report(totals: Mapping[str, Any], dump_date: str, rules: PhotoRules) -> str:
    lines = [
        f"# photo-candidates, dump {dump_date}",
        "",
        f"- subjects: {totals['subjects']} (articles {totals['articles']}, "
        f"see/do listings with facts {totals['listings']})",
        f"- subjects with a Wikidata item: {totals['with_item']}",
        f"- candidate rows: {totals['rows']} ({totals['usable']} usable); "
        f"distinct Commons files: {totals['files']}",
        f"- Commons categories searched (subjects with no usable image of their own): "
        f"{totals['categories_searched']}",
        f"- rules version: {rules.version}",
        "",
        "## Coverage: subjects with at least one usable candidate",
        "",
        "Usable = the file exists, is a photo type, has an allowed licence, an author and a "
        "thumbnail URL. Articles are every destination row of the enrich output; listings are "
        "every see/do listing of fetch-listings (listings without a Wikidata item count against "
        "coverage).",
        "",
    ]
    allc = totals["coverage"]["all"]
    for subject, label in (("articles", "Destinations"), ("listings", "See/do listings")):
        entry = allc[subject]
        lines.append(
            f"- {label}: {entry['with_usable']} of {entry['total']} "
            f"({_percent(entry['with_usable'], entry['total'])}%)"
        )
    lines += ["", "## Flags (candidate rows and subjects)", ""]
    lines += [f"- `{flag}`: {count}" for flag, count in totals["flags"].items()] or ["- none"]
    lines += [
        "",
        "## Sources",
        "",
        *(f"- `{name}`: {count} rows" for name, count in totals["sources"].items()),
        "",
        "Files per licence are in `licences.csv`; the allow-list and the reason for it are in "
        "`config/photo_rules.toml`.",
        "",
    ]
    return "\n".join(lines)


# --- the job ------------------------------------------------------------------------------------


def run_photo_candidates(
    client: PoliteClient,
    *,
    enrich_folder: Path,
    listings_folder: Path,
    out_dir: Path,
    dump_date: str,
    today: date,
    country_iso: str | None = None,
    rules: PhotoRules | None = None,
) -> PhotoResult:
    parse_dump_date(dump_date)
    rules = rules if rules is not None else load_photo_rules()
    country_filter = country_iso.upper() if country_iso else None
    manifest_sha, input_hashes = verify_enrich_output(enrich_folder, country_filter, INPUT_FILES)
    listing_totals = _listing_totals(enrich_folder, listings_folder)
    subjects = _subjects(enrich_folder)

    qids = {s.wikidata_id for s in subjects if s.wikidata_id}
    claims = fetch_media_claims(client, qids)

    # Owner's order: the item's own image first. Category search is one request per category and
    # is the expensive step, so it only runs for subjects that have no usable image yet.
    gathered = {s: (_p18_candidates(s, claims), set[str]()) for s in subjects}
    for s in subjects:
        media = claims.get(s.wikidata_id or "")
        if media is not None and media.category_conflict:
            gathered[s][1].add("multiple_commons_category")
    infos = fetch_file_info(client, {t for files, _ in gathered.values() for t in files}, rules)
    fallback = {
        s: media.commons_category
        for s in subjects
        if (media := claims.get(s.wikidata_id or "")) is not None
        and media.commons_category
        and not any(_is_usable(c, infos[t], rules) for t, c in gathered[s][0].items())
    }
    categories = search_marked_files(client, set(fallback.values()), rules)
    for s, category in fallback.items():
        _add_category_candidates(gathered[s][0], gathered[s][1], categories[category])
    infos.update(
        fetch_file_info(
            client,
            {t for files, _ in gathered.values() for t in files if t not in infos},
            rules,
        )
    )
    titles = set(infos)

    read_at = today.isoformat()
    target = out_dir / (f"{dump_date}-{country_filter}" if country_filter else dump_date)
    rows_by_country: Counter = Counter()
    flag_counts: Counter = Counter()
    by_country_flag: Counter = Counter()
    sources: Counter = Counter()
    usable_subjects: set[tuple[int, int]] = set()
    covered: Counter = Counter()
    articles_total: Counter = Counter()
    licences: Counter = Counter()
    usable_rows = 0
    row_count = 0
    with JsonlWriter(target / "photo_candidates.jsonl") as out:
        for subject in subjects:
            files, subject_flags = gathered[subject]
            country = subject.country_iso or NO_COUNTRY
            plural = f"{subject.kind}s"
            if subject.kind == "article":
                articles_total[country] += 1
            for flag in subject_flags:
                flag_counts[flag] += 1
                by_country_flag[(country, plural, flag)] += 1
            has_usable = False
            for _final, (title, candidate) in sorted(_merge_by_file(files, infos).items()):
                info = infos[title]
                row = _row(subject, title, candidate, info, rules, dump_date, read_at)
                out.write(row)
                row_count += 1
                rows_by_country[(country, plural)] += 1
                usable_rows += row["usable"]
                has_usable = has_usable or row["usable"]
                for name in row["sources"]:
                    sources[name] += 1
                for flag in row["flags"]:
                    flag_counts[flag] += 1
                    by_country_flag[(country, plural, flag)] += 1
            if has_usable and (subject.page_id, subject.ordinal) not in usable_subjects:
                usable_subjects.add((subject.page_id, subject.ordinal))
                covered[(country, plural)] += 1
        for info in infos.values():
            if info.status == "ok":
                licences[(info.licence_code or "", info.licence_name or "")] += 1

    country_names = sorted(
        {c for c in articles_total} | {c for c in listing_totals} | {c for c, _ in covered}
    )
    coverage: dict[str, dict[str, dict[str, int]]] = {"all": {}, "by_country": {}}
    coverage_rows: list[list[Any]] = []
    for plural, totals_by_country in (("articles", articles_total), ("listings", listing_totals)):
        total_all = with_all = 0
        for country in country_names:
            total, with_usable = totals_by_country[country], covered[(country, plural)]
            if not total and not with_usable:
                continue
            total_all += total
            with_all += with_usable
            coverage["by_country"].setdefault(country, {})[plural] = _coverage_entry(
                total, with_usable
            )
            coverage_rows.append(
                [_safe_cell(country), plural, total, with_usable, _percent(with_usable, total)]
            )
        coverage["all"][plural] = _coverage_entry(total_all, with_all)
        coverage_rows.append(["(all)", plural, total_all, with_all, _percent(with_all, total_all)])

    write_text(
        target / "coverage_by_country.csv",
        _csv_text(
            ["country_iso", "subject", "total", "with_usable_candidate", "percent"], coverage_rows
        ),
    )
    write_text(
        target / "photo_candidates_by_country.csv",
        _csv_text(
            ["country_iso", "subject", "candidate_rows"],
            [[_safe_cell(c), s, n] for (c, s), n in sorted(rows_by_country.items())],
        ),
    )
    write_text(
        target / "flags_by_country.csv",
        _csv_text(
            ["country_iso", "subject", "flag", "count"],
            [[_safe_cell(c), s, f, n] for (c, s, f), n in sorted(by_country_flag.items())],
        ),
    )
    write_text(
        target / "licences.csv",
        _csv_text(
            ["licence_code", "licence_name", "files", "allowed"],
            [
                [
                    _safe_cell(code),
                    _safe_cell(name),
                    count,
                    "yes" if _licence_ok(code, name, rules) else "no",
                ]
                for (code, name), count in sorted(licences.items(), key=lambda kv: (-kv[1], kv[0]))
            ],
        ),
    )
    totals = {
        "subjects": len(subjects),
        "articles": sum(s.kind == "article" for s in subjects),
        "listings": sum(s.kind == "listing" for s in subjects),
        "with_item": sum(1 for s in subjects if s.wikidata_id),
        "rows": row_count,
        "usable": usable_rows,
        "files": len(titles),
        "categories_searched": len(set(fallback.values())),
        "flags": dict(sorted(flag_counts.items())),
        "sources": dict(sorted(sources.items())),
        "coverage": coverage,
    }
    write_text(target / "report.md", _report(totals, dump_date, rules))
    write_manifest(
        target / "manifest.json",
        {
            "schema_version": SCHEMA_VERSION,
            "photo_rules_version": rules.version,
            "dump_date": dump_date,
            "country_filter": country_filter,
            "generated_on": read_at,
            "enrich_manifest_sha256": manifest_sha,
            "inputs_sha256": input_hashes,
            "totals": totals,
            "outputs_sha256": {
                name: hashlib.sha256((target / name).read_bytes()).hexdigest()
                for name in OUTPUT_FILES
            },
        },
    )
    return PhotoResult(target, totals)
