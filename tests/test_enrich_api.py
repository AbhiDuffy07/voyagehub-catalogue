import httpx
import pytest
import respx

from catalogue.enrich.api import MAX_LAG_RETRIES, api_get
from catalogue.errors import PipelineError

URL = "https://www.wikidata.org/w/api.php"


def maxlag_reply(wait="3"):
    return httpx.Response(
        200,
        headers={"Retry-After": wait},
        json={
            "error": {"code": "maxlag", "info": "Waiting for a database server: 5 seconds lagged"}
        },
    )


@respx.mock
def test_adds_maxlag_and_json_format_and_returns_the_body(client):
    route = respx.get(URL).mock(return_value=httpx.Response(200, json={"entities": {}}))
    body = api_get(client, URL, {"action": "wbgetentities", "ids": "Q1"})
    assert body == {"entities": {}}
    params = route.calls.last.request.url.params
    assert params["maxlag"] == "5" and params["format"] == "json"
    assert params["action"] == "wbgetentities" and params["ids"] == "Q1"


@respx.mock
def test_a_maxlag_reply_waits_for_retry_after_and_tries_again(client):
    waits = []
    respx.get(URL).mock(
        side_effect=[maxlag_reply("7"), httpx.Response(200, json={"ok": 1})],
    )
    body = api_get(client, URL, {"action": "query"}, sleep=waits.append)
    assert body == {"ok": 1}
    assert waits == [7.0]


@respx.mock
def test_maxlag_without_retry_after_uses_a_default_wait(client):
    waits = []
    reply = httpx.Response(200, json={"error": {"code": "maxlag", "info": "x"}})
    respx.get(URL).mock(side_effect=[reply, httpx.Response(200, json={"ok": 1})])
    api_get(client, URL, {"action": "query"}, sleep=waits.append)
    assert waits == [5.0]


@respx.mock
def test_endless_maxlag_stops_with_a_safe_error(client):
    respx.get(URL).mock(side_effect=lambda request: maxlag_reply("1"))
    with pytest.raises(PipelineError) as raised:
        api_get(client, URL, {"action": "query"}, sleep=lambda s: None)
    assert "lag" in str(raised.value)
    assert respx.calls.call_count == MAX_LAG_RETRIES + 1


@respx.mock
def test_a_very_long_maxlag_wait_stops_instead_of_hanging(client):
    respx.get(URL).mock(return_value=maxlag_reply("100000"))
    with pytest.raises(PipelineError):
        api_get(client, URL, {"action": "query"}, sleep=lambda s: None)
    assert respx.calls.call_count == 1


@respx.mock
def test_other_api_errors_show_the_code_only_never_the_info_text(client):
    respx.get(URL).mock(
        return_value=httpx.Response(
            200, json={"error": {"code": "badvalue", "info": "secret@example.invalid inside"}}
        )
    )
    with pytest.raises(PipelineError) as raised:
        api_get(client, URL, {"action": "query"})
    assert "badvalue" in str(raised.value)
    assert "secret" not in str(raised.value)


@respx.mock
def test_a_hostile_error_code_is_cleaned_before_it_is_printed(client):
    respx.get(URL).mock(
        return_value=httpx.Response(200, json={"error": {"code": "x\nINJECTED line " * 20}})
    )
    with pytest.raises(PipelineError) as raised:
        api_get(client, URL, {"action": "query"})
    assert "\n" not in str(raised.value) and len(str(raised.value)) < 120


@respx.mock
def test_a_body_that_is_not_json_is_a_safe_error(client):
    respx.get(URL).mock(return_value=httpx.Response(200, text="<html>oops</html>"))
    with pytest.raises(PipelineError) as raised:
        api_get(client, URL, {"action": "query"})
    assert "oops" not in str(raised.value)


@respx.mock
def test_a_json_body_that_is_not_an_object_is_a_safe_error(client):
    respx.get(URL).mock(return_value=httpx.Response(200, json=[1, 2]))
    with pytest.raises(PipelineError):
        api_get(client, URL, {"action": "query"})
