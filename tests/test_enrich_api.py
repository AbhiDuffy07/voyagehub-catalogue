import httpx
import pytest
import respx

from catalogue.enrich.api import MAX_LAG_BUDGET, MAX_LAG_WAIT, api_get
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
def test_endless_maxlag_stops_after_the_time_budget_with_a_safe_error(client):
    waits = []
    respx.get(URL).mock(side_effect=lambda request: maxlag_reply("1"))
    with pytest.raises(PipelineError) as raised:
        api_get(client, URL, {"action": "query"}, sleep=waits.append)
    assert "lag" in str(raised.value)
    assert sum(waits) <= MAX_LAG_BUDGET
    assert sum(waits) > MAX_LAG_BUDGET - MAX_LAG_WAIT  # it really used the patience it was given
    assert respx.calls.call_count == len(waits) + 1


@respx.mock
def test_maxlag_waits_grow_but_never_exceed_the_cap(client):
    waits = []
    respx.get(URL).mock(side_effect=lambda request: maxlag_reply("1"))
    with pytest.raises(PipelineError):
        api_get(client, URL, {"action": "query"}, sleep=waits.append)
    assert waits[0] < waits[3]
    assert waits == sorted(waits)
    assert max(waits) <= MAX_LAG_WAIT


@respx.mock
def test_a_longer_retry_after_than_the_backoff_is_honoured(client):
    waits = []
    respx.get(URL).mock(side_effect=[maxlag_reply("45"), httpx.Response(200, json={"ok": 1})])
    api_get(client, URL, {"action": "query"}, sleep=waits.append)
    assert waits == [45.0]


@respx.mock
def test_it_recovers_when_the_lag_clears_after_many_waits(client):
    replies = [maxlag_reply("3") for _ in range(12)] + [httpx.Response(200, json={"ok": 1})]
    respx.get(URL).mock(side_effect=replies)
    assert api_get(client, URL, {"action": "query"}, sleep=lambda s: None) == {"ok": 1}


@respx.mock
def test_waiting_prints_one_short_progress_line_per_wait_with_counts_only(client, capsys):
    respx.get(URL).mock(side_effect=[maxlag_reply("3"), httpx.Response(200, json={"ok": 1})])
    api_get(client, URL, {"action": "query", "ids": "Q1"}, sleep=lambda s: None)
    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 1
    assert "www.wikidata.org" in lines[0] and "lag" in lines[0]
    assert "Q1" not in lines[0] and "example.invalid" not in lines[0]


@respx.mock
def test_no_progress_line_when_there_is_no_lag(client, capsys):
    respx.get(URL).mock(return_value=httpx.Response(200, json={"ok": 1}))
    api_get(client, URL, {"action": "query"})
    assert capsys.readouterr().out == ""


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


def typed_lag_reply(lag_type, lag=412.5, wait="5"):
    error = {"code": "maxlag", "info": "Waiting for 10.0.0.1: secret lagged", "lag": lag}
    if lag_type is not None:
        error["type"] = lag_type
    return httpx.Response(200, headers={"Retry-After": wait}, json={"error": error})


@respx.mock
def test_a_db_lag_reply_keeps_the_backoff_and_keeps_maxlag(client):
    waits = []
    route = respx.get(URL).mock(
        side_effect=[typed_lag_reply("db", 8.0), httpx.Response(200, json={"ok": 1})]
    )
    assert api_get(client, URL, {"action": "query"}, sleep=waits.append) == {"ok": 1}
    assert waits == [5.0]
    assert all(c.request.url.params["maxlag"] == "5" for c in route.calls)


@pytest.mark.parametrize("lag_type", [None, "", 7, ["db"], "!!!", "DB", " Db "])
@respx.mock
def test_a_missing_or_odd_lag_type_is_treated_as_db(client, lag_type):
    waits = []
    route = respx.get(URL).mock(
        side_effect=[typed_lag_reply(lag_type), httpx.Response(200, json={"ok": 1})]
    )
    api_get(client, URL, {"action": "query"}, sleep=waits.append)
    assert waits == [5.0]
    assert "maxlag" in route.calls.last.request.url.params


@respx.mock
def test_a_non_db_lag_reply_retries_once_without_maxlag_and_without_waiting(client):
    waits = []
    route = respx.get(URL).mock(
        side_effect=[typed_lag_reply("queryservice"), httpx.Response(200, json={"ok": 1})]
    )
    body = api_get(client, URL, {"action": "query", "ids": "Q1"}, sleep=waits.append)
    assert body == {"ok": 1}
    assert waits == []
    first, second = (c.request.url.params for c in route.calls)
    assert first["maxlag"] == "5" and "maxlag" not in second
    assert second["ids"] == "Q1" and second["format"] == "json"


@respx.mock
def test_the_unthrottled_retry_is_not_repeated_if_it_fails(client):
    route = respx.get(URL).mock(
        side_effect=[
            typed_lag_reply("queryservice"),
            httpx.Response(200, json={"error": {"code": "badvalue"}}),
        ]
    )
    with pytest.raises(PipelineError) as raised:
        api_get(client, URL, {"action": "query"}, sleep=lambda s: None)
    assert "badvalue" in str(raised.value)
    assert route.call_count == 2


@respx.mock
def test_a_maxlag_error_on_the_unthrottled_retry_is_a_safe_stop_not_a_loop(client):
    route = respx.get(URL).mock(side_effect=lambda request: typed_lag_reply("queryservice"))
    with pytest.raises(PipelineError) as raised:
        api_get(client, URL, {"action": "query"}, sleep=lambda s: None)
    assert "lag" in str(raised.value)
    assert route.call_count == 2


@respx.mock
def test_the_unthrottled_retry_is_per_request_so_the_next_request_asks_politely_again(client):
    route = respx.get(URL).mock(
        side_effect=[
            typed_lag_reply("queryservice"),
            httpx.Response(200, json={"n": 1}),
            httpx.Response(200, json={"n": 2}),
        ]
    )
    api_get(client, URL, {"action": "query"}, sleep=lambda s: None)
    api_get(client, URL, {"action": "query"}, sleep=lambda s: None)
    assert "maxlag" in route.calls.last.request.url.params


@respx.mock
def test_the_lag_line_shows_seconds_and_type_but_never_info_or_host_addresses(client, capsys):
    respx.get(URL).mock(
        side_effect=[typed_lag_reply("db", 8.25), httpx.Response(200, json={"ok": 1})]
    )
    api_get(client, URL, {"action": "query"}, sleep=lambda s: None)
    line = capsys.readouterr().out.strip()
    assert "8.2 s lag" in line or "8.3 s lag" in line
    assert "type db" in line
    assert "secret" not in line and "10.0.0.1" not in line


@respx.mock
def test_the_non_db_line_names_the_type_and_the_unthrottled_retry(client, capsys):
    respx.get(URL).mock(
        side_effect=[typed_lag_reply("queryservice\nINJECT"), httpx.Response(200, json={"ok": 1})]
    )
    api_get(client, URL, {"action": "query"}, sleep=lambda s: None)
    out = capsys.readouterr().out.strip().splitlines()
    assert len(out) == 1
    assert "queryserviceinject" in out[0] and "without maxlag" in out[0]


def api_error_reply(code, wait=None):
    headers = {"Retry-After": wait} if wait else {}
    return httpx.Response(
        200, headers=headers, json={"error": {"code": code, "info": "secret@example.invalid"}}
    )


TRANSIENT_CODES = [
    "cirrussearch-backend-error",
    "internal_api_error_MWException",
    "internal_api_error_DBQueryError",
    "ratelimited",
    "readonly",
]


@pytest.mark.parametrize("code", TRANSIENT_CODES)
@respx.mock
def test_a_temporary_api_error_is_retried_with_backoff_then_succeeds(client, code):
    waits = []
    respx.get(URL).mock(side_effect=[api_error_reply(code), httpx.Response(200, json={"ok": 1})])
    assert api_get(client, URL, {"action": "query"}, sleep=waits.append) == {"ok": 1}
    assert waits == [5.0]


@respx.mock
def test_temporary_error_waits_grow_and_respect_the_cap_and_the_budget(client):
    waits = []
    respx.get(URL).mock(side_effect=lambda request: api_error_reply("cirrussearch-backend-error"))
    with pytest.raises(PipelineError) as raised:
        api_get(client, URL, {"action": "query"}, sleep=waits.append)
    assert "cirrussearch-backend-error" in str(raised.value)
    assert "secret" not in str(raised.value)
    assert waits == sorted(waits) and waits[0] < waits[3]
    assert max(waits) <= MAX_LAG_WAIT
    assert sum(waits) <= MAX_LAG_BUDGET
    assert sum(waits) > MAX_LAG_BUDGET - MAX_LAG_WAIT
    assert respx.calls.call_count == len(waits) + 1


@respx.mock
def test_a_retry_after_on_a_temporary_error_is_honoured(client):
    waits = []
    respx.get(URL).mock(
        side_effect=[api_error_reply("ratelimited", "30"), httpx.Response(200, json={"ok": 1})]
    )
    api_get(client, URL, {"action": "query"}, sleep=waits.append)
    assert waits == [30.0]


@respx.mock
def test_a_huge_retry_after_on_a_temporary_error_stops_instead_of_hanging(client):
    respx.get(URL).mock(return_value=api_error_reply("readonly", "100000"))
    with pytest.raises(PipelineError):
        api_get(client, URL, {"action": "query"}, sleep=lambda s: None)
    assert respx.calls.call_count == 1


@respx.mock
def test_the_temporary_error_line_shows_host_and_code_only(client, capsys):
    respx.get(URL).mock(
        side_effect=[
            api_error_reply("internal_api_error_X"),
            httpx.Response(200, json={"ok": 1}),
        ]
    )
    api_get(client, URL, {"action": "query", "ids": "Q1"}, sleep=lambda s: None)
    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 1
    assert "www.wikidata.org" in lines[0] and "internal_api_error_X" in lines[0]
    assert "secret" not in lines[0] and "Q1" not in lines[0]


@respx.mock
def test_lag_and_temporary_errors_share_one_waiting_budget(client):
    waits = []
    replies = iter([maxlag_reply("1"), api_error_reply("readonly")])

    def reply(request):
        return next(replies, None) or maxlag_reply("1")

    respx.get(URL).mock(side_effect=reply)
    with pytest.raises(PipelineError):
        api_get(client, URL, {"action": "query"}, sleep=waits.append)
    assert sum(waits) <= MAX_LAG_BUDGET


@respx.mock
def test_other_error_codes_are_still_fatal_at_once(client):
    route = respx.get(URL).mock(return_value=api_error_reply("badvalue"))
    with pytest.raises(PipelineError):
        api_get(client, URL, {"action": "query"}, sleep=lambda s: None)
    assert route.call_count == 1


@respx.mock
def test_the_too_busy_search_code_is_left_to_its_own_handler(client):
    route = respx.get(URL).mock(return_value=api_error_reply("cirrussearch-too-busy-error"))
    with pytest.raises(PipelineError) as raised:
        api_get(client, URL, {"action": "query"}, sleep=lambda s: None)
    assert "cirrussearch-too-busy-error" in str(raised.value)
    assert route.call_count == 1


@respx.mock
def test_http_5xx_that_outlasts_the_clients_own_retries_is_retried_by_api_get(client):
    waits = []
    replies = [httpx.Response(503)] * 4 + [httpx.Response(200, json={"ok": 1})]
    route = respx.get(URL).mock(side_effect=replies)
    assert api_get(client, URL, {"action": "query"}, sleep=waits.append) == {"ok": 1}
    assert route.call_count == 5
    assert waits == [5.0]


@respx.mock
def test_endless_http_5xx_fails_after_the_budget_with_a_safe_message(client):
    waits = []
    respx.get(URL).mock(return_value=httpx.Response(502, text="<html>secret</html>"))
    with pytest.raises(PipelineError) as raised:
        api_get(client, URL, {"action": "query"}, sleep=waits.append)
    assert "502" in str(raised.value) and "secret" not in str(raised.value)
    assert sum(waits) <= MAX_LAG_BUDGET and max(waits) <= MAX_LAG_WAIT
    assert sum(waits) > MAX_LAG_BUDGET - MAX_LAG_WAIT


@respx.mock
def test_http_4xx_is_still_fatal_at_once(client):
    route = respx.get(URL).mock(return_value=httpx.Response(404))
    with pytest.raises(PipelineError):
        api_get(client, URL, {"action": "query"}, sleep=lambda s: None)
    assert route.call_count == 1
