from catalogue.listings.rollup import resolve_owner


def art(page_id, title, kind, status="usable", ancestors=()):
    return {
        "wikivoyage_page_id": page_id,
        "title": title,
        "type": kind,
        "status": status,
        "ancestors": list(ancestors),
    }


def index(*records):
    return {r["title"]: r for r in records}


WORLD = index(
    art(1, "India", "country", ancestors=()),
    art(2, "Kerala", "region", ancestors=["India"]),
    art(3, "Kochi", "city", ancestors=["Kerala", "India"]),
    art(4, "Fort Kochi", "district", "outline", ["Kochi", "Kerala", "India"]),
    art(5, "Old Fort Kochi", "district", "usable", ["Fort Kochi", "Kochi", "Kerala", "India"]),
    art(6, "Broken", "district", "usable", ["Nowhere", "India"]),
    art(7, "Lonely", "district", "usable", []),
    art(8, "Delta", "district", "usable", ["Kerala", "India"]),
)


def test_a_city_owns_its_own_listings():
    owner = resolve_owner(WORLD["Kochi"], WORLD)

    assert (owner.page_id, owner.title, owner.type, owner.via_district) == (
        3,
        "Kochi",
        "city",
        False,
    )


def test_a_district_rolls_up_to_the_city_even_when_the_district_is_outline():
    owner = resolve_owner(WORLD["Fort Kochi"], WORLD)

    assert (owner.page_id, owner.status, owner.via_district) == (3, "usable", True)


def test_a_district_of_a_district_rolls_up_to_the_nearest_non_district():
    owner = resolve_owner(WORLD["Old Fort Kochi"], WORLD)

    assert (owner.page_id, owner.title, owner.via_district) == (3, "Kochi", True)


def test_a_district_directly_under_a_region_rolls_up_to_the_region():
    owner = resolve_owner(WORLD["Delta"], WORLD)

    assert (owner.page_id, owner.type, owner.via_district) == (2, "region", True)


def test_a_broken_chain_gives_no_owner_instead_of_a_guess():
    assert resolve_owner(WORLD["Broken"], WORLD) is None
    assert resolve_owner(WORLD["Lonely"], WORLD) is None


def test_a_cycle_does_not_loop_forever():
    looped = index(
        art(1, "A", "district", ancestors=["B"]),
        art(2, "B", "district", ancestors=["A"]),
    )

    assert resolve_owner(looped["A"], looped) is None


def test_pages_that_are_not_districts_own_themselves_whatever_their_type():
    for title, page_id in (("India", 1), ("Kerala", 2)):
        owner = resolve_owner(WORLD[title], WORLD)

        assert (owner.page_id, owner.via_district) == (page_id, False)
