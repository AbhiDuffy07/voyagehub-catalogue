from catalogue.discover.parse import Article, ParsedDump
from catalogue.discover.tree import assign_countries, continent_of


def art(title, kind, parent=None, *, problems=()):
    return Article(
        page_id=abs(hash(title)) % 10_000,
        title=title,
        revision_id=1,
        timestamp="2026-10-01T00:00:00Z",
        type=kind,
        status="usable",
        parent=parent,
        problems=tuple(problems),
    )


def world(*articles, redirects=None):
    return ParsedDump({a.title: a for a in articles}, redirects or {})


def test_a_city_walks_up_through_regions_to_its_country():
    parsed = world(
        art("India", "country", "Asia"),
        art("Asia", "continent"),
        art("Rajasthan", "region", "India"),
        art("Jaipur", "city", "Rajasthan"),
    )

    result = assign_countries(parsed)["Jaipur"]

    assert result.country == "India"
    assert result.ancestors == ("Rajasthan", "India")
    assert result.problem is None


def test_the_nearest_country_wins_when_countries_nest():
    parsed = world(
        art("China", "country"),
        art("Hong Kong region", "country", "China"),
        art("Kowloon", "district", "Hong Kong region"),
    )

    assert assign_countries(parsed)["Kowloon"].country == "Hong Kong region"


def test_a_country_is_assigned_to_itself():
    result = assign_countries(world(art("Nepal", "country", "Asia"), art("Asia", "continent")))

    assert result["Nepal"].country == "Nepal"
    assert result["Nepal"].ancestors == ()


def test_a_parent_that_is_a_redirect_is_followed():
    parsed = world(
        art("India", "country"),
        art("Delhi NCR", "region", "India"),
        art("Noida", "city", "Delhi region"),
        redirects={"Delhi region": "Delhi NCR"},
    )

    result = assign_countries(parsed)["Noida"]

    assert result.country == "India"
    assert result.ancestors == ("Delhi NCR", "India")


def test_a_missing_parent_is_reported_not_guessed():
    result = assign_countries(world(art("Lost", "city", "Nowhere")))["Lost"]

    assert (result.country, result.problem) == (None, "parent_missing")


def test_a_cycle_is_detected():
    parsed = world(art("A", "region", "B"), art("B", "region", "A"), art("C", "city", "A"))

    results = assign_countries(parsed)

    assert results["C"].problem == "cycle"
    assert results["A"].problem == "cycle"


def test_a_page_that_is_its_own_parent_is_a_cycle():
    assert assign_countries(world(art("Loop", "region", "Loop")))["Loop"].problem == "cycle"


def test_no_parent_means_no_country():
    result = assign_countries(world(art("Orphan", "city")))["Orphan"]

    assert (result.country, result.problem) == (None, "no_parent")


def test_an_unparsed_parent_keeps_its_reason():
    parsed = world(art("Kustavi", "city", None, problems=["unparsed_parent"]))

    assert assign_countries(parsed)["Kustavi"].problem == "unparsed_parent"


def test_a_chain_that_ends_without_a_country_is_reported():
    parsed = world(art("Asia", "continent"), art("Caucasus", "region", "Asia"))

    assert assign_countries(parsed)["Caucasus"].problem == "no_country_above"


def test_the_continent_is_found_by_walking_all_the_way_up():
    parsed = world(
        art("Asia", "continent"),
        art("South Asia", "region", "Asia"),
        art("India", "country", "South Asia"),
        art("Jaipur", "city", "India"),
    )

    assert continent_of("Jaipur", parsed) == "Asia"
    assert continent_of("India", parsed) == "Asia"


def test_a_continent_has_no_continent_of_its_own():
    assert continent_of("Asia", world(art("Asia", "continent"))) is None


def test_the_continent_is_empty_when_the_chain_never_reaches_one():
    parsed = world(art("Island", "region", "Nowhere"), art("Loner", "city"))

    assert continent_of("Island", parsed) is None
    assert continent_of("Loner", parsed) is None


def test_a_cycle_gives_no_continent():
    parsed = world(art("A", "region", "B"), art("B", "region", "A"))

    assert continent_of("A", parsed) is None


def test_a_redirect_in_the_chain_is_followed_to_the_continent():
    parsed = world(
        art("Asia", "continent"),
        art("India", "country", "Asia region"),
        redirects={"Asia region": "Asia"},
    )

    assert continent_of("India", parsed) == "Asia"
