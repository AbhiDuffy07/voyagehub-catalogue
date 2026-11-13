"""`photo-select` (B6): one main photo per subject from B5's candidates, by rule.

Offline: no network, no settings. It reads the `photo-candidates` output (hash-checked against that
run's manifest) and the enrich facts B5 read (checked against the hashes in B5's manifest), and
writes `out/photo_selection/<date>[-CC]/`. The ranking itself is `catalogue.photos.select`.

Every subject (every article, and every see/do listing with a facts row) gets one row: the chosen
photo with its full credit, or `photo: null` and a flag saying why (the app then shows the city
photo). The report says how many subjects the size gate costs; above 5% of the covered subjects it
opens with a warning, because the rules are not frozen until that has been looked at.
"""

import hashlib
import json
import random
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from catalogue.discover.output import _csv_text, write_manifest, write_text
from catalogue.enrich.inputs import _rows, _sha256, verify_enrich_output
from catalogue.errors import PipelineError
from catalogue.listings.output import JsonlWriter
from catalogue.listings.run import _safe_cell, parse_dump_date
from catalogue.photos.run import INPUT_FILES, NO_COUNTRY, Subject, _percent, _subjects
from catalogue.photos.select import (
    Choice,
    SelectionRules,
    choose,
    load_rejected_files,
    load_selection_rules,
    name_words,
)

SCHEMA_VERSION = 1
GATE_LIMIT_PERCENT = 5.0
SAMPLE_SIZES = {"p18": 10, "other_tier": 10, "lowest_score": 5, "narrow": 5}
OUTPUT_FILES = (
    "flags_by_country.csv",
    "lost_to_gate.csv",
    "photo_selection.jsonl",
    "report.md",
    "selection_by_country.csv",
    "spot_check_sample.csv",
)
PHOTO_FIELDS = (
    "commons_file",
    "description_url",
    "url",
    "thumb_url",
    "thumb_width",
    "width",
    "height",
    "mime",
    "sha1",
    "author",
    "licence_code",
    "licence_name",
    "licence_url",
    "attribution",
    "quality_marks",
    "sources",
    "p18_rank",
)


@dataclass(frozen=True, slots=True)
class SelectResult:
    output_dir: Path
    totals: dict[str, Any]

    def summary(self) -> str:
        t = self.totals
        gate = t["gate"]
        lines = [
            f"subjects: {t['subjects']}; selected: {t['selected']} of {t['subjects']} "
            f"(portrait {t['portrait']}, hero quality {t['hero_quality']})",
            f"size gate: removes {gate['lost']} of {gate['covered']} covered subjects "
            f"({gate['percent']}%)"
            + ("; OVER THE 5% LINE, not frozen" if gate["over_limit"] else ""),
        ]
        if t["tiers"]:
            lines.append("tiers: " + ", ".join(f"{k} {v}" for k, v in t["tiers"].items()))
        if t["flags"]:
            lines.append(", ".join(f"{k} {v}" for k, v in t["flags"].items()))
        return "\n".join(lines)


# --- inputs -------------------------------------------------------------------------------------


def _verify_photos(
    folder: Path, country_filter: str | None
) -> tuple[str, dict[str, str], dict[str, Any]]:
    """Check the B5 folder: manifest, country filter and candidates hash. Returns the manifest's
    own hash, the enrich input hashes B5 recorded and B5's coverage denominators."""
    path = folder / "manifest.json"
    if not path.is_file():
        raise PipelineError(f"manifest.json not found in {folder}; run photo-candidates first")
    damaged = "the photo-candidates manifest.json is damaged; run photo-candidates again"
    try:
        manifest_bytes = path.read_bytes()
        manifest = json.loads(manifest_bytes)
        recorded_country = manifest["country_filter"]
        outputs = manifest["outputs_sha256"]
        inputs = manifest["inputs_sha256"]
        by_country = manifest["totals"]["coverage"]["by_country"]
    except (OSError, ValueError, KeyError, TypeError):
        raise PipelineError(damaged) from None
    if (
        not isinstance(outputs, dict)
        or not isinstance(inputs, dict)
        or not isinstance(by_country, dict)
    ):
        raise PipelineError(damaged)
    if recorded_country != country_filter:
        raise PipelineError(
            "photo-candidates was run for a different country filter; use the same --country"
        )
    candidates = folder / "photo_candidates.jsonl"
    if not candidates.is_file():
        raise PipelineError(f"photo_candidates.jsonl not found in {folder}")
    if _sha256(candidates) != outputs.get("photo_candidates.jsonl"):
        raise PipelineError("photo_candidates.jsonl does not match the photo-candidates manifest")
    return hashlib.sha256(manifest_bytes).hexdigest(), inputs, by_country


def _subject_words(folder: Path, rules: SelectionRules) -> dict[tuple[int, int], frozenset[str]]:
    """Name words of each subject itself: a destination's title, Wikidata label and English
    Wikipedia title; a listing's label, English Wikipedia title and own key (not its city's)."""
    words: dict[tuple[int, int], frozenset[str]] = {}
    size, generic = rules.category_min_word, rules.generic_words
    for row in _rows(folder / "destination_facts.jsonl"):
        text = " ".join(str(row.get(k) or "") for k in ("title", "label", "enwiki_title"))
        words[(row["wikivoyage_page_id"], 0)] = frozenset(name_words(text, size, generic))
    for row in _rows(folder / "listing_facts.jsonl"):
        key = str(row.get("listing_key") or "").split(":", 1)[-1].replace("-", " ")
        text = " ".join([str(row.get("label") or ""), str(row.get("enwiki_title") or ""), key])
        words[(row["page_id"], row["ordinal"])] = frozenset(name_words(text, size, generic))
    return words


def _candidates_by_subject(
    folder: Path, subjects: Mapping[tuple[int, int], Subject]
) -> dict[tuple[int, int], list[dict[str, Any]]]:
    grouped: dict[tuple[int, int], list[dict[str, Any]]] = {}
    for row in _rows(folder / "photo_candidates.jsonl"):
        try:
            key = (row["page_id"], row["ordinal"])
            for field in ("commons_file", "sources", "quality_marks", "flags", "usable"):
                row[field]
        except KeyError:
            raise PipelineError("photo_candidates.jsonl has a damaged row") from None
        if key not in subjects:
            raise PipelineError(
                "photo_candidates.jsonl names a subject that is not in the enrich output"
            )
        grouped.setdefault(key, []).append(row)
    return grouped


# --- output rows --------------------------------------------------------------------------------


def _row(
    subject: Subject, choice: Choice, rules: SelectionRules, dump_date: str, read_at: str
) -> dict[str, Any]:
    chosen = choice.chosen
    return {
        "schema_version": SCHEMA_VERSION,
        "subject": subject.kind,
        "page_id": subject.page_id,
        "ordinal": subject.ordinal,
        "listing_key": subject.listing_key,
        "country_iso": subject.country_iso,
        "wikidata_id": subject.wikidata_id,
        "photo": {f: chosen.get(f) for f in PHOTO_FIELDS} if chosen else None,
        "tier": choice.tier,
        "score": choice.score,
        "long_side": choice.long_side,
        "hero_quality": choice.hero_quality if chosen else None,
        "portrait": choice.portrait if chosen else None,
        "alternatives": choice.alternatives,
        "candidates": choice.candidates,
        "flags": sorted(choice.flags),
        "rules_version": rules.version,
        "approved_by": f"auto:{rules.version}" if chosen else None,
        "dump_date": f"{dump_date[:4]}-{dump_date[4:6]}-{dump_date[6:]}",
        "read_at": read_at,
    }


def _spot_sample(rows: list[dict[str, Any]], rules: SelectionRules) -> list[list[Any]]:
    """30 selected rows chosen with a fixed seed: P18 picks, other tiers, the lowest scores and the
    ones that only just pass the size gate. No row appears twice."""
    chosen = sorted((r for r in rows if r["photo"]), key=lambda r: (r["page_id"], r["ordinal"]))
    rng = random.Random(rules.version)  # noqa: S311 - a reproducible sample, not security
    pools = {
        "p18": [r for r in chosen if r["tier"] == "p18"],
        "other_tier": [r for r in chosen if r["tier"] != "p18"],
        "lowest_score": sorted(chosen, key=lambda r: (r["score"], r["page_id"], r["ordinal"])),
        "narrow": [r for r in chosen if r["long_side"] < rules.min_long_side * 1.25],
    }
    taken: set[tuple[int, int]] = set()
    sample: list[list[Any]] = []
    for bucket in ("p18", "other_tier", "narrow", "lowest_score"):
        pool = [r for r in pools[bucket] if (r["page_id"], r["ordinal"]) not in taken]
        if bucket == "lowest_score":
            picks = pool[: SAMPLE_SIZES[bucket]]
        else:
            picks = rng.sample(pool, min(SAMPLE_SIZES[bucket], len(pool)))
        for r in picks:
            taken.add((r["page_id"], r["ordinal"]))
            photo = r["photo"]
            sample.append(
                [
                    bucket,
                    r["subject"],
                    r["page_id"],
                    r["ordinal"],
                    _safe_cell(r["country_iso"] or ""),
                    r["tier"],
                    r["score"],
                    r["long_side"],
                    _safe_cell(photo["commons_file"]),
                    photo["description_url"],
                    photo["thumb_url"],
                ]
            )
    return sample


def _gate_stats(covered: int, lost: int) -> dict[str, Any]:
    """The gate cost. "Over 5%" is tested on the exact ratio; only the shown percent rounds."""
    return {
        "covered": covered,
        "lost": lost,
        "percent": round(100 * lost / covered, 1) if covered else 0.0,
        "over_limit": lost * 100 > GATE_LIMIT_PERCENT * covered,
    }


def _report(totals: Mapping[str, Any], dump_date: str, rules: SelectionRules) -> str:
    gate = totals["gate"]
    lines = [f"# photo-select, dump {dump_date}", ""]
    if gate["over_limit"]:
        lines.append(
            f"**WARNING: the size gate removes {gate['lost']} of {gate['covered']} covered "
            f"subjects ({gate['percent']}%), over the {GATE_LIMIT_PERCENT:g}% line. The rules "
            "are not frozen "
            "until this has been looked at.**"
        )
        lines.append("")
    lines += [
        f"- subjects: {totals['subjects']} (articles {totals['articles']}, "
        f"see/do listings with facts {totals['listings']})",
        f"- selected: {totals['selected']} (portrait {totals['portrait']}, hero quality "
        f"{totals['hero_quality']})",
        f"- rules version: {rules.version} (long side at least {rules.min_long_side} px, portrait "
        f"allowed, hero quality {rules.hero_long_side} px)",
        "",
        "## Coverage: subjects with a selected photo",
        "",
        "Denominators are photo-candidates' own: every destination row, and every see/do "
        "listing of "
        "fetch-listings (listings without a Wikidata item count against coverage).",
        "",
    ]
    for subject, label in (("articles", "Destinations"), ("listings", "See/do listings")):
        entry = totals["coverage"]["all"][subject]
        lines.append(
            f"- {label}: {entry['selected']} of {entry['total']} "
            f"({_percent(entry['selected'], entry['total'])}%)"
        )
    lines += [
        "",
        "## What the size gate costs",
        "",
        f"- covered subjects (a candidate survived the drops): {gate['covered']}",
        f"- removed by the size gate (every surviving candidate under {rules.min_long_side} px): "
        f"{gate['lost']} ({gate['percent']}%); listed in `lost_to_gate.csv`",
        "",
        "## Tiers of the selected photos",
        "",
        *([f"- `{k}`: {v}" for k, v in totals["tiers"].items()] or ["- none"]),
        "",
        "## Flags (subjects)",
        "",
        *([f"- `{k}`: {v}" for k, v in totals["flags"].items()] or ["- none"]),
        "",
        "`spot_check_sample.csv` is a seeded sample of 30 selected rows for the author's "
        "own review.",
        "",
    ]
    return "\n".join(lines)


# --- the job ------------------------------------------------------------------------------------


def run_photo_select(
    *,
    photos_folder: Path,
    enrich_folder: Path,
    out_dir: Path,
    dump_date: str,
    today: date,
    country_iso: str | None = None,
    rules: SelectionRules | None = None,
    rejected: frozenset[str] | None = None,
) -> SelectResult:
    parse_dump_date(dump_date)
    rules = rules if rules is not None else load_selection_rules()
    rejected = rejected if rejected is not None else load_rejected_files()
    country_filter = country_iso.upper() if country_iso else None
    manifest_sha, recorded_inputs, denominators = _verify_photos(photos_folder, country_filter)
    _, input_hashes = verify_enrich_output(enrich_folder, country_filter, INPUT_FILES)
    if input_hashes != recorded_inputs:
        raise PipelineError(
            "the enrich-wikidata output is not the one photo-candidates read; "
            "run photo-candidates again"
        )
    subjects = _subjects(enrich_folder)
    by_key = {(s.page_id, s.ordinal): s for s in subjects}
    candidates = _candidates_by_subject(photos_folder, by_key)
    subject_words = _subject_words(enrich_folder, rules)

    read_at = today.isoformat()
    target = out_dir / (f"{dump_date}-{country_filter}" if country_filter else dump_date)
    rows: list[dict[str, Any]] = []
    selected: Counter = Counter()
    subject_count: Counter = Counter()
    flag_counts: Counter = Counter()
    by_country_flag: Counter = Counter()
    tiers: Counter = Counter()
    lost: list[list[Any]] = []
    covered = portrait = hero = 0
    with JsonlWriter(target / "photo_selection.jsonl") as out:
        for subject in subjects:
            key = (subject.page_id, subject.ordinal)
            choice = choose(candidates.get(key, []), rules, rejected, subject_words[key])
            row = _row(subject, choice, rules, dump_date, read_at)
            out.write(row)
            rows.append(row)
            country, plural = subject.country_iso or NO_COUNTRY, f"{subject.kind}s"
            subject_count[(country, plural)] += 1
            covered += choice.covered
            if choice.chosen is not None:
                selected[(country, plural)] += 1
                tiers[choice.tier] += 1
                portrait += choice.portrait
                hero += choice.hero_quality
            if "only_below_gate" in choice.flags:
                lost.append(
                    [
                        subject.kind,
                        subject.page_id,
                        subject.ordinal,
                        _safe_cell(subject.listing_key or ""),
                        _safe_cell(country),
                        choice.best_long_side,
                        choice.candidates,
                    ]
                )
            for flag in choice.flags:
                flag_counts[flag] += 1
                by_country_flag[(country, plural, flag)] += 1

    coverage: dict[str, dict[str, Any]] = {"all": {}, "by_country": {}}
    country_rows: list[list[Any]] = []
    for plural in ("articles", "listings"):
        total_all = selected_all = 0
        for country in sorted({c for c, p in subject_count if p == plural} | set(denominators)):
            denominator = denominators.get(country, {}).get(plural, {}).get("total")
            total = (
                denominator if isinstance(denominator, int) else subject_count[(country, plural)]
            )
            chosen_count = selected[(country, plural)]
            if not total and not chosen_count:
                continue
            total_all += total
            selected_all += chosen_count
            coverage["by_country"].setdefault(country, {})[plural] = {
                "total": total,
                "selected": chosen_count,
            }
            country_rows.append(
                [_safe_cell(country), plural, total, chosen_count, _percent(chosen_count, total)]
            )
        coverage["all"][plural] = {"total": total_all, "selected": selected_all}
        country_rows.append(
            ["(all)", plural, total_all, selected_all, _percent(selected_all, total_all)]
        )

    totals = {
        "subjects": len(subjects),
        "articles": sum(s.kind == "article" for s in subjects),
        "listings": sum(s.kind == "listing" for s in subjects),
        "selected": sum(selected.values()),
        "portrait": portrait,
        "hero_quality": hero,
        "tiers": dict(sorted(tiers.items())),
        "flags": dict(sorted(flag_counts.items())),
        "gate": _gate_stats(covered, len(lost)),
        "coverage": coverage,
    }

    write_text(
        target / "selection_by_country.csv",
        _csv_text(["country_iso", "subject", "total", "selected", "percent"], country_rows),
    )
    write_text(
        target / "flags_by_country.csv",
        _csv_text(
            ["country_iso", "subject", "flag", "count"],
            [[_safe_cell(c), s, f, n] for (c, s, f), n in sorted(by_country_flag.items())],
        ),
    )
    write_text(
        target / "lost_to_gate.csv",
        _csv_text(
            [
                "subject",
                "page_id",
                "ordinal",
                "listing_key",
                "country_iso",
                "best_long_side",
                "candidates",
            ],
            lost,
        ),
    )
    write_text(
        target / "spot_check_sample.csv",
        _csv_text(
            [
                "bucket",
                "subject",
                "page_id",
                "ordinal",
                "country_iso",
                "tier",
                "score",
                "long_side",
                "commons_file",
                "description_url",
                "thumb_url",
            ],
            _spot_sample(rows, rules),
        ),
    )
    write_text(target / "report.md", _report(totals, dump_date, rules))
    write_manifest(
        target / "manifest.json",
        {
            "schema_version": SCHEMA_VERSION,
            "selection_rules_version": rules.version,
            "dump_date": dump_date,
            "country_filter": country_filter,
            "generated_on": read_at,
            "photo_candidates_manifest_sha256": manifest_sha,
            "inputs_sha256": input_hashes,
            "rejected_files": len(rejected),
            "totals": totals,
            "outputs_sha256": {
                name: hashlib.sha256((target / name).read_bytes()).hexdigest()
                for name in OUTPUT_FILES
            },
        },
    )
    return SelectResult(target, totals)
