"""Walk the IsPartOf tree up to the nearest country article (design: section 3, step 3).

A page that cannot be placed gets a short reason code and no country. Nothing is guessed.
"""

from dataclasses import dataclass

from catalogue.discover.parse import Article, ParsedDump

MAX_REDIRECT_HOPS = 5


@dataclass(frozen=True, slots=True)
class Assignment:
    country: str | None  # the title of the country article
    ancestors: tuple[str, ...]  # nearest parent first, ending at the country
    problem: str | None  # parent_missing, cycle, no_parent, no_country_above, unparsed_parent


def _resolve(title: str, parsed: ParsedDump) -> str:
    """Follow redirects, so an IsPartOf that names a redirect still finds its article."""
    for _ in range(MAX_REDIRECT_HOPS):
        target = parsed.redirects.get(title)
        if target is None:
            break
        title = target
    return title


def _assign(start: Article, parsed: ParsedDump) -> Assignment:
    if start.type == "country":
        return Assignment(start.title, (), None)

    seen = {start.title}
    ancestors: list[str] = []
    current = start
    while True:
        if current.parent is None:
            if "unparsed_parent" in current.problems:
                problem = "unparsed_parent"
            else:
                problem = "no_parent" if current is start else "no_country_above"
            return Assignment(None, tuple(ancestors), problem)

        parent_title = _resolve(current.parent, parsed)
        parent = parsed.articles.get(parent_title)
        if parent is None:
            return Assignment(None, tuple(ancestors), "parent_missing")
        if parent.title in seen:
            return Assignment(None, tuple(ancestors), "cycle")
        seen.add(parent.title)
        ancestors.append(parent.title)
        if parent.type == "country":
            return Assignment(parent.title, tuple(ancestors), None)
        current = parent


def assign_countries(parsed: ParsedDump) -> dict[str, Assignment]:
    return {title: _assign(article, parsed) for title, article in parsed.articles.items()}
