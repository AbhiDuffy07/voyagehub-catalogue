"""B13 follow-ups: honest city heroes, every block stop as a place, true thumb widths,
and the getting_around field."""

import json

from catalogue.publish import build as b
from catalogue.publish.getting_around import excerpt_of, load_content_rules, load_ride_apps
from catalogue.publish.hero import hero_problem, load_hero_rules
from catalogue.publish.run import run_publish_static
from tests import publishfixture

V1 = "v1"


def _build(tmp_path):
    kwargs = publishfixture.build(tmp_path)
    return run_publish_static(**kwargs), kwargs


def _city(result, slug):
    return json.loads((result.site_dir / V1 / "cities" / f"{slug}.json").read_text("utf-8"))


# --- hero ---------------------------------------------------------------------------------


def _row(file="Jaipur skyline.jpg", author="Gerd Eichmann", hero=True):
    photo = {**publishfixture.CREDIT_PHOTO, "commons_file": file, "author": author}
    return {"hero_quality": hero, "score": 1.0, "photo": photo}


def test_a_good_photo_passes_the_hero_checks():
    assert hero_problem(_row(), load_hero_rules()) is None


def test_a_photo_below_hero_size_is_not_a_hero():
    assert hero_problem(_row(hero=False), load_hero_rules()) == "size"


def test_a_collage_by_file_name_is_not_a_hero():
    for name in ("Jaipur Montage.png", "Pune_Collage.png", "Vellore Skyline Montage 1.jpg"):
        assert hero_problem(_row(name), load_hero_rules()) == "name", name


def test_a_file_in_the_b6_override_list_is_not_a_hero():
    assert hero_problem(_row("Darbhanga images.png"), load_hero_rules()) == "override"


def test_an_author_with_a_mapillary_handle_is_still_a_valid_name():
    assert hero_problem(_row(author="Ganesh Dhamodkar @ Mapillary.com"), load_hero_rules()) is None


def test_a_b6_drop_word_in_the_file_name_is_not_a_hero():
    assert hero_problem(_row("Jaipur city map.jpg"), load_hero_rules()) == "name"


def test_a_garbled_or_missing_author_is_not_valid():
    rules = load_hero_rules()
    for author in (
        "see above. Compilation: Indianhilbilly",
        "",
        None,
        "x" * 101,
        "Name - Sagnik Samanta Email Address - sagnik1@gmail.com",
        "Abdaal ( talk )M Akhtar",
    ):
        assert hero_problem(_row(author=author), rules) == "author", author


def _up(photos, listings=()):
    return b.Upstream(
        place_listings={7: list(listings)},
        photos=photos,
        summaries={},
        timezones={},
        blocks={},
        stops={},
        block_costs={},
        days={},
        bands={},
    )


CITY = {
    "wikivoyage_page_id": 7,
    "slug": "x",
    "title": "X",
    "country_iso": "IN",
    "tier": "browse_only",
}


def _place(ordinal, name):
    return {
        "page_id": 7,
        "ordinal": ordinal,
        "name": name,
        "listing_key": f"see:{ordinal}",
        "kind": "see",
    }


def test_a_collage_city_photo_is_replaced_by_the_best_place_photo_and_counted():
    up = _up(
        {("article", 7, 0): _row("X Montage.png"), ("listing", 7, 1): _row("Fort.jpg")},
        [_place(1, "Fort")],
    )
    city = b.build_city(CITY, up)
    assert city["hero"]["caption"] == "Fort"
    assert up.counts["own_hero_rejected_name"] == 1


def test_a_city_photo_that_passes_stays_the_hero_with_no_caption():
    up = _up({("article", 7, 0): _row("X view.jpg")})
    city = b.build_city(CITY, up)
    assert city["hero"]["caption"] is None
    assert up.counts["own_hero_rejected_name"] == 0


def test_no_valid_city_or_place_photo_leaves_the_hero_empty():
    up = _up(
        {("article", 7, 0): _row(author="see above"), ("listing", 7, 1): _row(hero=False)},
        [_place(1, "Fort")],
    )
    assert b.build_city(CITY, up)["hero"] is None


def test_a_place_photo_with_a_garbled_author_is_not_used_as_a_fallback_hero():
    up = _up({("listing", 7, 1): _row(author="see above")}, [_place(1, "Fort")])
    assert b.fallback_hero(7, up) is None


def _geo_row(file="Fort near.jpg", sources=("commons_geosearch",)):
    row = _row(file)
    row["photo"] = {**row["photo"], "sources": list(sources)}
    return row


def test_a_photo_found_only_by_geosearch_is_never_a_hero():
    assert hero_problem(_geo_row(), load_hero_rules()) == "geosearch"


def test_a_wikidata_image_also_found_by_geosearch_may_still_be_a_hero():
    row = _geo_row(sources=("commons_geosearch", "wikidata_p18"))
    assert hero_problem(row, load_hero_rules()) is None


def test_a_geosearch_place_photo_is_never_the_fallback_city_hero():
    up = _up({("listing", 7, 1): _geo_row()}, [_place(1, "Fort")])
    assert b.fallback_hero(7, up) is None
    assert b.build_city(CITY, up)["hero"] is None


# --- thumb width -----------------------------------------------------------------------------


def test_thumb_width_is_the_real_width_when_the_original_is_smaller():
    small = {"photo": {**publishfixture.CREDIT_PHOTO, "width": 520, "thumb_width": 1280}}
    assert b.photo_of(small, b.Counter())["thumb_width"] == 520
    big = {"photo": {**publishfixture.CREDIT_PHOTO, "width": 4000, "thumb_width": 1280}}
    assert b.photo_of(big, b.Counter())["thumb_width"] == 1280
    unknown = {"photo": {**publishfixture.CREDIT_PHOTO, "width": None, "thumb_width": 1280}}
    assert b.photo_of(unknown, b.Counter())["thumb_width"] == 1280


# --- every stop is a place --------------------------------------------------------------------


def test_every_block_stop_is_a_place_with_empty_photo_and_summary(tmp_path):
    result, _ = _build(tmp_path)
    city = _city(result, "jaipur")
    places = {p["key"]: p for p in city["places"]}
    for block in city["blocks"]:
        for stop in block["stops"]:
            assert stop["key"] in places, stop
    assert places["see:fort-4"]["photo"] is None and places["see:fort-4"]["summary"] is None
    gone = places["see:gone"]  # not in the listings file: only what the stop itself says
    assert (gone["name"], gone["kind"], gone["lat"], gone["long"]) == (
        "Gone Gate",
        None,
        26.99,
        75.81,
    )
    assert len(places) == len(city["places"])  # no duplicates


def test_unphotographed_places_that_are_no_stop_stay_out(tmp_path):
    result, _ = _build(tmp_path)
    agra = _city(result, "agra")
    assert all(p["photo"] for p in agra["places"])


# --- getting around ---------------------------------------------------------------------------


def test_ride_apps_are_the_ones_whose_site_lists_the_city_with_names_and_links_only(tmp_path):
    result, _ = _build(tmp_path)
    apps = _city(result, "jaipur")["getting_around"]["ride_apps"]
    assert [a["name"] for a in apps["apps"]] == ["Uber"]  # Namma Yatri lists Bengaluru only
    assert all(set(a) == {"name", "url"} and a["url"].startswith("https://") for a in apps["apps"])
    assert apps["note"] == "Availability varies within a city: check the app."


def test_a_country_without_ride_apps_gets_none():
    assert load_ride_apps().get("ZZ") is None


def test_the_excerpt_is_credited_with_section_link_and_history(tmp_path):
    result, _ = _build(tmp_path)
    excerpt = _city(result, "jaipur")["getting_around"]["excerpt"]
    assert excerpt["text"].startswith("Auto rickshaws and app cabs")
    credit = excerpt["credit"]
    assert credit["licence"] == "CC BY-SA 4.0"
    assert credit["author"] == "Wikivoyage contributors"
    assert credit["source_url"].endswith("title=Jaipur&oldid=11#Get_around")
    assert credit["history_url"].endswith("title=Jaipur&action=history")
    assert "Jaipur" in credit["attribution"]


def test_a_city_with_an_empty_section_has_no_excerpt_but_keeps_ride_apps(tmp_path):
    result, _ = _build(tmp_path)
    around = _city(result, "agra")["getting_around"]
    assert around["excerpt"] is None
    assert around["ride_apps"] is not None


def test_the_excerpt_is_in_the_attributions(tmp_path):
    result, _ = _build(tmp_path)
    parts = sorted((result.site_dir / V1 / "attributions").glob("part-*.json"))
    entries = [e for p in parts for e in json.loads(p.read_text("utf-8"))["entries"]]
    assert [e["subject"] for e in entries if e["kind"] == "getting_around"] == ["city"]


def test_totals_report_getting_around_coverage(tmp_path):
    result, _ = _build(tmp_path)
    assert result.totals["with_getting_around_excerpt"] == 1
    assert result.totals["with_ride_apps"] == 2


def test_excerpt_takes_whole_paragraphs_and_skips_heading_stubs():
    rules = load_content_rules()
    body = ("The metro is good. " * 4).strip()
    text = f"Short intro here, fine.\n\nBy metro\n\n{body}"
    assert excerpt_of(text, rules) == f"Short intro here, fine.\n\n{body}"


def test_a_long_first_paragraph_is_cut_at_a_sentence_end():
    rules = load_content_rules()
    cut = excerpt_of("Buses run all day across the whole city. " * 30, rules)
    assert len(cut) <= rules.max_chars and cut.endswith("city.")


def test_an_unbroken_paragraph_is_cut_at_a_word_with_an_ellipsis():
    rules = load_content_rules()
    cut = excerpt_of("word " * 200, rules)
    assert cut.endswith("word…") and len(cut) <= rules.max_chars + 1


def test_paragraphs_with_wiki_markup_are_skipped_and_empty_text_gives_none():
    rules = load_content_rules()
    assert excerpt_of("See [[Agra]] for more details on the whole thing here.", rules) is None
    assert excerpt_of("  \n\n ", rules) is None
    assert excerpt_of("", rules) is None


def test_an_email_or_pointer_in_the_attribution_text_is_not_a_valid_hero_credit():
    rules = load_hero_rules()
    for text in ("Jane Doe <jane@example.com>, CC BY", "see above, CC BY"):
        row = _row()
        row["photo"]["attribution"] = text
        assert hero_problem(row, rules) == "author", text


def test_a_second_get_around_row_for_a_page_keeps_the_first_by_ordinal(tmp_path):
    import hashlib

    kwargs = publishfixture.build(tmp_path)
    folder = kwargs["listings_folder"]
    path = folder / "sections.jsonl"
    rows = [json.loads(line) for line in path.read_text("utf-8").splitlines()]
    extra = {
        **rows[0],
        "ordinal": 9,
        "text": "A later duplicate heading with enough words to count.",
    }
    rows[0]["ordinal"] = 3
    path.write_text("".join(json.dumps(r) + "\n" for r in [extra, *rows]), encoding="utf-8")
    manifest = json.loads((folder / "manifest.json").read_text("utf-8"))
    manifest["outputs_sha256"]["sections.jsonl"] = hashlib.sha256(path.read_bytes()).hexdigest()
    (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    result = run_publish_static(**kwargs)
    assert _city(result, "jaipur")["getting_around"]["excerpt"]["text"].startswith("Auto rickshaws")


def test_a_damaged_section_row_gives_no_excerpt_instead_of_a_crash():
    up = _up({})
    up.sections[7] = {"page_id": 7, "text": "A fine long sentence about getting around."}
    around = b.build_city(CITY, up)["getting_around"]
    assert around["excerpt"] is None
    assert up.counts["getting_around_left_out_no_credit"] == 1
