import httpx
import pytest
import respx

from catalogue.enrich.summary import (
    ATTRIBUTION_TEXT,
    SUMMARY_RULES_PATH,
    article_url,
    attribution,
    fetch_summaries,
    first_paragraph,
    has_extract_gap,
    history_url,
    load_summary_rules,
    permalink,
)
from catalogue.errors import PipelineError
from tests.apifakes import fake_wikipedia_summaries

ENWIKI = "https://en.wikipedia.org/w/api.php"


def page(extract="Jaipur is a city.\nIt is pink.", revid=100, disambiguation=False):
    return {
        "extract": extract,
        "revid": revid,
        "timestamp": "2026-11-02T10:00:00Z",
        "disambiguation": disambiguation,
    }


# --- the excerpt and the links ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("One.\nTwo.", "One."),
        ("\n\nLeading blank.\nSecond", "Leading blank."),
        ("  padded  \nx", "padded"),
        ("Single paragraph only.", "Single paragraph only."),
        ("", ""),
        ("   \n  \n", ""),
    ],
)
def test_first_paragraph(text, expected):
    assert first_paragraph(text) == expected


def test_urls_keep_safe_characters_and_encode_the_rest():
    assert article_url("Amer Fort") == "https://en.wikipedia.org/wiki/Amer_Fort"
    assert article_url("Kudli (Karnataka)") == "https://en.wikipedia.org/wiki/Kudli_(Karnataka)"
    assert article_url("Café Royal") == "https://en.wikipedia.org/wiki/Caf%C3%A9_Royal"
    assert article_url("AT&T Tower?") == "https://en.wikipedia.org/wiki/AT%26T_Tower%3F"


def test_permalink_and_history_links_use_the_same_encoding():
    assert permalink("Amer Fort", 123) == (
        "https://en.wikipedia.org/w/index.php?title=Amer_Fort&oldid=123"
    )
    assert history_url("AT&T Tower") == (
        "https://en.wikipedia.org/w/index.php?title=AT%26T_Tower&action=history"
    )


def test_attribution_names_the_article_the_licence_and_that_it_is_an_excerpt():
    text = attribution("Amer Fort")
    assert '"Amer Fort"' in text and "Wikipedia contributors" in text
    assert "CC BY-SA 4.0" in text and "excerpt" in text
    assert ATTRIBUTION_TEXT in text


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Agra (; Hindi: x) is a city.", True),
        ("Gol Gumbaz (; lit. 'Round Dome') is a tomb.", True),
        ("Place (, a village) is small.", True),
        ("Place () is small.", True),
        ("Place ( ) is small.", True),
        ("Thing (see a, ) here.", True),
        ("Kochi ( KOH-chee, Malayalam: x) is a port.", False),
        ("Person (born 1950) is a poet.", False),
        ("Pair (1, 2) is two numbers.", False),
        ("No brackets at all, only commas; and semicolons.", False),
        ("", False),
    ],
)
def test_a_gap_left_by_removed_pronunciation_markup_is_detected(text, expected):
    assert has_extract_gap(text) is expected


# --- fetching -----------------------------------------------------------------------------------


@respx.mock
def test_an_ordinary_page_gives_text_revision_and_timestamp(client):
    respx.get(ENWIKI).mock(side_effect=fake_wikipedia_summaries({"Jaipur": page()}))
    got = fetch_summaries(client, ["Jaipur"])["Jaipur"]
    assert got.status == "ok" and got.title == "Jaipur" and not got.redirected
    assert got.extract == "Jaipur is a city.\nIt is pink."
    assert (got.revision_id, got.timestamp) == (100, "2026-11-02T10:00:00Z")


@respx.mock
def test_requests_ask_for_the_intro_as_plain_text_with_revision_and_redirects(client):
    seen = []
    respx.get(ENWIKI).mock(side_effect=fake_wikipedia_summaries({"Jaipur": page()}, seen=seen))
    fetch_summaries(client, ["Jaipur"])
    params = seen[0]
    assert params["action"] == "query" and params["exintro"] == "1"
    assert params["explaintext"] == "1" and params["exlimit"] == "20"
    assert set(params["prop"].split("|")) == {"extracts", "revisions", "pageprops"}
    assert params["rvprop"] == "ids|timestamp" and params["redirects"] == "1"
    assert params["ppprop"] == "disambiguation" and params["formatversion"] == "2"


@respx.mock
def test_titles_go_in_batches_of_twenty_sorted_without_duplicates(client):
    seen = []
    respx.get(ENWIKI).mock(side_effect=fake_wikipedia_summaries({}, seen=seen))
    titles = [f"Place {n:02d}" for n in range(45)] + ["Place 00", "Place 07"]
    fetch_summaries(client, titles)
    batches = [p["titles"].split("|") for p in seen]
    assert [len(b) for b in batches] == [20, 20, 5]
    flat = [t for b in batches for t in b]
    assert flat == sorted(set(titles)) and len(flat) == 45


@respx.mock
def test_no_titles_means_no_requests(client):
    route = respx.get(ENWIKI).mock(side_effect=fake_wikipedia_summaries({}))
    assert fetch_summaries(client, []) == {}
    assert not route.called


@respx.mock
def test_a_redirect_is_followed_and_reported(client):
    respx.get(ENWIKI).mock(
        side_effect=fake_wikipedia_summaries(
            {"Amer Fort": page()}, redirects={"Amber Fort": "Amer Fort"}
        )
    )
    got = fetch_summaries(client, ["Amber Fort"])["Amber Fort"]
    assert got.status == "ok" and got.title == "Amer Fort" and got.redirected


@respx.mock
def test_a_title_the_server_normalises_is_still_found_under_the_requested_name(client):
    respx.get(ENWIKI).mock(side_effect=fake_wikipedia_summaries({"Amer Fort": page()}))
    got = fetch_summaries(client, ["Amer_Fort"])
    assert got["Amer_Fort"].status == "ok" and got["Amer_Fort"].title == "Amer Fort"
    assert not got["Amer_Fort"].redirected


@respx.mock
def test_missing_and_disambiguation_pages_have_a_status_and_no_text(client):
    respx.get(ENWIKI).mock(
        side_effect=fake_wikipedia_summaries(
            {"Mercury": page("Mercury may refer to:", disambiguation=True)}
        )
    )
    got = fetch_summaries(client, ["Mercury", "Nowhere"])
    assert (got["Mercury"].status, got["Mercury"].extract) == ("disambiguation", None)
    assert (got["Nowhere"].status, got["Nowhere"].extract) == ("missing", None)


@respx.mock
def test_a_page_without_an_extract_is_ok_with_no_text(client):
    respx.get(ENWIKI).mock(side_effect=fake_wikipedia_summaries({"Stub": page(extract=None)}))
    got = fetch_summaries(client, ["Stub"])["Stub"]
    assert got.status == "ok" and got.extract is None


@respx.mock
def test_a_title_the_reply_leaves_out_is_no_answer_not_a_guess(client):
    respx.get(ENWIKI).mock(
        side_effect=fake_wikipedia_summaries({"Jaipur": page(), "Agra": page()}, omit={"Agra"})
    )
    got = fetch_summaries(client, ["Jaipur", "Agra"])
    assert got["Jaipur"].status == "ok" and got["Agra"].status == "no_answer"


@respx.mock
def test_continuation_replies_are_merged(client):
    seen = []
    pages = {name: page(f"{name} text.", revid=n) for n, name in enumerate(["A", "B", "C", "D"], 1)}
    respx.get(ENWIKI).mock(side_effect=fake_wikipedia_summaries(pages, seen=seen, split=2))
    got = fetch_summaries(client, ["A", "B", "C", "D"])
    assert {k: v.status for k, v in got.items()} == dict.fromkeys("ABCD", "ok")
    assert got["D"].extract == "D text." and got["A"].revision_id == 1
    assert len(seen) == 2 and seen[1]["excontinue"] == "2"


@respx.mock
def test_a_continuation_that_never_ends_stops_with_a_safe_error(client):
    def endless(request):
        return httpx.Response(
            200, json={"continue": {"excontinue": 1, "continue": "||"}, "query": {"pages": []}}
        )

    respx.get(ENWIKI).mock(side_effect=endless)
    with pytest.raises(PipelineError, match="continu"):
        fetch_summaries(client, ["A"])


# --- the rules file -----------------------------------------------------------------------------


def test_the_committed_rules_load_with_a_reason():
    rules = load_summary_rules(SUMMARY_RULES_PATH)
    assert isinstance(rules.version, int) and rules.very_short_chars > 0
    assert rules.reasons["very_short_chars"].strip()


@pytest.mark.parametrize(
    "text",
    [
        "not toml [",
        'rules_version = "1"\n[summary.very_short]\nchars = 80\nreason = "x"\n',
        "rules_version = 1\n[summary.very_short]\nchars = 80\n",
        'rules_version = 1\n[summary.very_short]\nchars = -1\nreason = "x"\n',
        "rules_version = 1\n",
    ],
)
def test_a_bad_rules_file_is_refused(tmp_path, text):
    path = tmp_path / "rules.toml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(PipelineError):
        load_summary_rules(path)
