"""Near misses: cities one step from being published, by the one thing that stops them.

A city is a near miss when it fails exactly one readiness rule, or when it is ready but waits for
a slug (`blocked_no_slug`). This only counts; it changes no threshold. Counts are safe for public
logs (no names).
"""

import csv
import io
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from typing import Any

NO_SLUG = "ready_but_no_slug"
ALL = "ALL"


def near_misses(rows: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, int]]:
    """country -> reason -> cities, for cities failing exactly one rule or blocked for a slug."""
    table: dict[str, Counter[str]] = defaultdict(Counter)
    for r in rows:
        country = r["country_iso"] or "unknown"
        failed = r["failed_rules"]
        if len(failed) == 1:
            table[country][failed[0]] += 1
        elif r["ready"] and r["publish_action"] == "blocked_no_slug":
            table[country][NO_SLUG] += 1
    return {country: dict(counts) for country, counts in table.items()}


def _totals(table: Mapping[str, Mapping[str, int]]) -> Counter[str]:
    total: Counter[str] = Counter()
    for counts in table.values():
        total.update(counts)
    return total


def near_miss_csv(table: Mapping[str, Mapping[str, int]]) -> str:
    """One line per reason for the whole run (country ALL), then per country and reason."""
    out = io.StringIO(newline="")
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(["country_iso", "reason", "cities"])
    for reason, n in sorted(_totals(table).items()):
        writer.writerow([ALL, reason, n])
    for country in sorted(table):
        for reason, n in sorted(table[country].items()):
            writer.writerow([country, reason, n])
    return out.getvalue()


def near_miss_markdown(table: Mapping[str, Mapping[str, int]], top: int = 25) -> str:
    totals = _totals(table)
    lines = [
        "## Near misses (cities one step from publishing)",
        "",
        "A city fails exactly one readiness rule, or is ready and waits for a slug. "
        "No threshold is changed by this report.",
        "",
        "| Reason | Cities |",
        "|---|---|",
    ]
    lines += [
        f"| {reason} | {n} |" for reason, n in sorted(totals.items(), key=lambda t: (-t[1], t[0]))
    ]
    if not totals:
        lines.append("| (none) | 0 |")
    by_country = sorted(table.items(), key=lambda t: (-sum(t[1].values()), t[0]))[:top]
    lines += ["", f"Top {len(by_country)} countries (all countries are in near_miss.csv):", ""]
    lines += ["| Country | Cities | Reasons |", "|---|---|---|"]
    for country, counts in by_country:
        reasons = ", ".join(
            f"{r} {n}" for r, n in sorted(counts.items(), key=lambda t: (-t[1], t[0]))
        )
        lines.append(f"| {country} | {sum(counts.values())} | {reasons} |")
    return "\n".join(lines) + "\n"
