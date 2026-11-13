import httpx
import pytest
import respx

from catalogue.errors import PipelineError
from catalogue.photos.commons import (
    BATCH_SIZE,
    fetch_file_info,
    search_marked_files,
    strip_html,
)
from catalogue.photos.rules import load_photo_rules
from tests.photofakes import COMMONS, commons_file, fake_commons

RULES = load_photo_rules()


def test_strip_html_keeps_visible_text_only():
    assert strip_html('<a href="//x">Ann &amp; Co</a> <b>and</b>\n Bo') == "Ann & Co and Bo"
    assert strip_html("<script>x</script>Ann") == "Ann"
    assert strip_html("") is None
    assert strip_html('<a href="//x"></a>  ') is None
    assert strip_html(None) is None


@respx.mock
def test_file_info_reads_licence_author_size_and_hotlinks(client):
    respx.get(COMMONS).mock(
        side_effect=fake_commons({"File:Fort.jpg": commons_file(marks=["Quality images"])})
    )
    info = fetch_file_info(client, ["File:Fort.jpg"], RULES)["File:Fort.jpg"]
    assert info.status == "ok"
    assert info.title == "File:Fort.jpg"
    assert (info.width, info.height, info.mime) == (4000, 3000, "image/jpeg")
    assert info.author == "Ann & Co"
    assert (info.licence_code, info.licence_name) == ("cc-by-sa-4.0", "CC BY-SA 4.0")
    assert info.licence_url == "https://creativecommons.org/licenses/by-sa/4.0/"
    assert info.url.startswith("https://upload.wikimedia.org/")
    assert info.thumb_url.endswith("/1280px-Fort.jpg")
    assert info.description_url == "https://commons.wikimedia.org/wiki/File:Fort.jpg"
    assert info.marks == ("Quality images",)
    assert info.sha1 == "a" * 40


@respx.mock
def test_missing_file_and_missing_fields_stay_empty(client):
    files = {
        "File:Bare.jpg": commons_file(artist=None, licence=None, licence_name=None, thumb=False)
    }
    respx.get(COMMONS).mock(side_effect=fake_commons(files))
    result = fetch_file_info(client, ["File:Bare.jpg", "File:Gone.jpg"], RULES)
    assert result["File:Gone.jpg"].status == "missing"
    bare = result["File:Bare.jpg"]
    assert bare.author is None and bare.licence_code is None and bare.thumb_url is None


@respx.mock
def test_titles_are_batched_by_50_and_each_asked_once(client):
    titles = [f"File:P{n}.jpg" for n in range(120)]
    seen = []
    respx.get(COMMONS).mock(
        side_effect=fake_commons({t: commons_file() for t in titles}, seen=seen)
    )
    result = fetch_file_info(client, titles + titles[:10], RULES)
    assert BATCH_SIZE == 50
    assert sorted(len(batch) for batch in seen) == [20, 50, 50]
    assert len(result) == 120
    assert all(info.status == "ok" for info in result.values())


@respx.mock
def test_normalised_titles_map_back_to_the_requested_title(client):
    def answer(request):
        return httpx.Response(
            200,
            json={
                "query": {
                    "normalized": [{"from": "File:A_b.jpg", "to": "File:A b.jpg"}],
                    "pages": [
                        {
                            "ns": 6,
                            "title": "File:A b.jpg",
                            "imageinfo": [{"width": 1, "height": 1, "mime": "image/png"}],
                        }
                    ],
                }
            },
        )

    respx.get(COMMONS).mock(side_effect=answer)
    result = fetch_file_info(client, ["File:A_b.jpg"], RULES)
    assert result["File:A_b.jpg"].title == "File:A b.jpg"


@respx.mock
def test_continuation_is_merged(client):
    calls = []

    def answer(request):
        calls.append(dict(request.url.params))
        if "clcontinue" not in request.url.params:
            page = {
                "ns": 6,
                "title": "File:A.jpg",
                "imageinfo": [{"width": 9, "height": 9, "mime": "image/jpeg"}],
                "categories": [{"ns": 14, "title": "Category:Quality images"}],
            }
            body = {"continue": {"clcontinue": "x|y", "continue": "||"}, "query": {"pages": [page]}}
            return httpx.Response(200, json=body)
        page = {
            "ns": 6,
            "title": "File:A.jpg",
            "categories": [{"ns": 14, "title": "Category:Valued images"}],
        }
        return httpx.Response(200, json={"query": {"pages": [page]}})

    respx.get(COMMONS).mock(side_effect=answer)
    info = fetch_file_info(client, ["File:A.jpg"], RULES)["File:A.jpg"]
    assert len(calls) == 2
    assert info.width == 9
    assert info.marks == ("Quality images", "Valued images")


@respx.mock
def test_endless_continuation_stops(client):
    respx.get(COMMONS).mock(
        return_value=httpx.Response(
            200, json={"continue": {"clcontinue": "x", "continue": "||"}, "query": {"pages": []}}
        )
    )
    with pytest.raises(PipelineError, match="continue"):
        fetch_file_info(client, ["File:A.jpg"], RULES)


@respx.mock
def test_odd_reply_shape_is_a_pipeline_error(client):
    respx.get(COMMONS).mock(return_value=httpx.Response(200, json={"query": {"pages": {"x": 1}}}))
    with pytest.raises(PipelineError, match="unexpected"):
        fetch_file_info(client, ["File:A.jpg"], RULES)


@respx.mock
def test_search_asks_for_quality_marks_and_caps_at_ten_sorted(client):
    names = [f"File:F{n:02d}.jpg" for n in range(14)]
    queries = []
    respx.get(COMMONS).mock(
        side_effect=fake_commons(categories={"Amer Fort": names}, search_seen=queries)
    )
    found = search_marked_files(client, ["Amer Fort"], RULES)["Amer Fort"]
    assert found.files == tuple(names[:10])
    assert found.capped is True
    query = queries[0]
    assert 'deepcat:"Amer Fort"' in query
    assert "Quality images|Featured pictures on Wikimedia Commons|Valued images" in query


@respx.mock
def test_search_under_the_cap_is_not_capped_and_empty_is_empty(client):
    respx.get(COMMONS).mock(side_effect=fake_commons(categories={"A": ["File:X.jpg"], "B": []}))
    found = search_marked_files(client, ["A", "B"], RULES)
    assert found["A"].files == ("File:X.jpg",) and found["A"].capped is False
    assert found["B"].files == () and found["B"].capped is False


@respx.mock
def test_unsafe_category_names_are_not_searched(client):
    queries = []
    respx.get(COMMONS).mock(side_effect=fake_commons(search_seen=queries))
    found = search_marked_files(client, ['Bad"name', "Pipe|name"], RULES)
    assert queries == []
    assert all(entry.files == () and entry.unusable for entry in found.values())


@respx.mock
def test_titles_that_would_split_the_request_are_never_asked_for(client):
    seen = []
    respx.get(COMMONS).mock(side_effect=fake_commons({"File:Ok.jpg": commons_file()}, seen=seen))
    result = fetch_file_info(client, ["File:Ok.jpg", "File:A|B.jpg", "  "], RULES)
    assert seen == [["File:Ok.jpg"]]
    assert result["File:A|B.jpg"].status == "missing" and result["  "].status == "missing"
