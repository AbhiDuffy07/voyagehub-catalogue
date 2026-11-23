import json
from datetime import date

import httpx
import respx

from catalogue.nearby.cache import ApiCache

URL = "https://commons.wikimedia.org/w/api.php"
PARAMS = {"action": "query", "list": "geosearch", "gscoord": "1|2"}


@respx.mock
def test_a_second_identical_request_is_answered_from_disk(client, tmp_path):
    route = respx.get(URL).mock(return_value=httpx.Response(200, json={"query": {"n": 1}}))
    cache = ApiCache(tmp_path, date(2026, 11, 23), 30)
    first = cache.get(client, URL, PARAMS)
    again = ApiCache(tmp_path, date(2026, 11, 24), 30).get(
        client, URL, dict(reversed(PARAMS.items()))
    )
    assert first == again == {"query": {"n": 1}}
    assert route.call_count == 1
    assert cache.misses == 1 and cache.hits == 0


@respx.mock
def test_an_old_entry_is_fetched_again(client, tmp_path):
    route = respx.get(URL).mock(
        side_effect=[httpx.Response(200, json={"n": 1}), httpx.Response(200, json={"n": 2})]
    )
    ApiCache(tmp_path, date(2026, 1, 1), 30).get(client, URL, PARAMS)
    later = ApiCache(tmp_path, date(2026, 2, 1), 30)
    assert later.get(client, URL, PARAMS) == {"n": 2}
    assert route.call_count == 2 and later.misses == 1


@respx.mock
def test_different_params_are_different_entries(client, tmp_path):
    route = respx.get(URL).mock(return_value=httpx.Response(200, json={"n": 1}))
    cache = ApiCache(tmp_path, date(2026, 11, 23), 30)
    cache.get(client, URL, PARAMS)
    cache.get(client, URL, {**PARAMS, "gscoord": "1|3"})
    assert route.call_count == 2


@respx.mock
def test_errors_are_never_cached(client, tmp_path):
    respx.get(URL).mock(return_value=httpx.Response(200, json={"error": {"code": "badvalue"}}))
    cache = ApiCache(tmp_path, date(2026, 11, 23), 30)
    try:
        cache.get(client, URL, PARAMS)
    except Exception:  # noqa: S110 - the error itself is tested elsewhere
        pass
    assert not list(tmp_path.rglob("*.json"))


@respx.mock
def test_a_damaged_entry_is_fetched_again_not_trusted(client, tmp_path):
    route = respx.get(URL).mock(return_value=httpx.Response(200, json={"n": 1}))
    cache = ApiCache(tmp_path, date(2026, 11, 23), 30)
    cache.get(client, URL, PARAMS)
    for path in tmp_path.rglob("*.json"):
        path.write_text("{broken", encoding="utf-8")
    assert cache.get(client, URL, PARAMS) == {"n": 1}
    assert route.call_count == 2


@respx.mock
def test_entries_hold_no_user_agent_or_contact(client, tmp_path):
    respx.get(URL).mock(return_value=httpx.Response(200, json={"n": 1}))
    ApiCache(tmp_path, date(2026, 11, 23), 30).get(client, URL, PARAMS)
    text = "".join(p.read_text(encoding="utf-8") for p in tmp_path.rglob("*.json"))
    assert "example.invalid" not in text
    assert json.loads(text)["body"] == {"n": 1}


@respx.mock
def test_no_folder_means_no_cache(client):
    route = respx.get(URL).mock(return_value=httpx.Response(200, json={"n": 1}))
    cache = ApiCache(None, date(2026, 11, 23), 30)
    cache.get(client, URL, PARAMS)
    cache.get(client, URL, PARAMS)
    assert route.call_count == 2
