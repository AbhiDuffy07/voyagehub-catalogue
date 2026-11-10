import threading
import time

import httpx
import pytest
import respx

from catalogue.http import PoliteClient, RateLimitedError, UpstreamError
from catalogue.settings import load_settings, user_agent

URL = "https://en.wikivoyage.org/w/api.php"
EMAIL = "pipeline@example.invalid"


class FakeTime:
    """A clock the test controls: sleeping advances it and is recorded, nothing really waits."""

    def __init__(self):
        self.now = 1000.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.fixture
def settings():
    return load_settings(environ={"CONTACT_EMAIL": EMAIL}, env_file=None)


@pytest.fixture
def fake_time():
    return FakeTime()


def make_client(settings, fake_time, **kwargs):
    return PoliteClient(settings, sleep=fake_time.sleep, clock=fake_time.clock, **kwargs)


@respx.mock
def test_sends_the_descriptive_user_agent(settings, fake_time):
    route = respx.get(URL).respond(200, json={"ok": True})

    make_client(settings, fake_time).get(URL, params={"action": "query"})

    assert route.calls.last.request.headers["user-agent"] == user_agent(settings)


@respx.mock
def test_every_request_has_every_timeout_set(settings, fake_time):
    route = respx.get(URL).respond(200, json={})

    make_client(settings, fake_time).get(URL)

    timeout = route.calls.last.request.extensions["timeout"]
    assert all(value is not None for value in timeout.values())


@respx.mock
def test_requests_are_spaced_to_stay_under_200_a_minute(settings, fake_time):
    respx.get(URL).respond(200, json={})
    client = make_client(settings, fake_time)

    for _ in range(3):
        client.get(URL)

    # 200 a minute is one request every 0.3 s; the first needs no wait.
    assert sum(fake_time.sleeps) == pytest.approx(0.6)


@respx.mock
def test_a_429_waits_for_retry_after_then_retries(settings, fake_time):
    route = respx.get(URL).mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "7"}),
            httpx.Response(200, json={"ok": True}),
        ]
    )

    response = make_client(settings, fake_time).get(URL)

    assert response.status_code == 200
    assert route.call_count == 2
    assert 7 in fake_time.sleeps


@respx.mock
def test_a_429_without_retry_after_waits_at_least_five_seconds(settings, fake_time):
    respx.get(URL).mock(side_effect=[httpx.Response(429), httpx.Response(200, json={})])

    make_client(settings, fake_time).get(URL)

    assert any(seconds >= 5 for seconds in fake_time.sleeps)


@respx.mock
def test_a_retry_after_date_is_treated_as_missing(settings, fake_time):
    respx.get(URL).mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}),
            httpx.Response(200, json={}),
        ]
    )

    response = make_client(settings, fake_time).get(URL)

    assert response.status_code == 200
    assert any(seconds >= 5 for seconds in fake_time.sleeps)


@respx.mock
def test_a_huge_retry_after_stops_the_job_instead_of_sleeping(settings, fake_time):
    route = respx.get(URL).respond(429, headers={"Retry-After": "3600"})

    with pytest.raises(RateLimitedError):
        make_client(settings, fake_time).get(URL)

    assert route.call_count == 1
    assert max(fake_time.sleeps, default=0) < 300


@respx.mock
def test_a_429_that_never_clears_gives_up_after_the_retries(settings, fake_time):
    route = respx.get(URL).respond(429, headers={"Retry-After": "1"})

    with pytest.raises(RateLimitedError):
        make_client(settings, fake_time).get(URL)

    assert route.call_count == 4  # the first try and three retries


@respx.mock
def test_a_503_is_retried(settings, fake_time):
    route = respx.get(URL).mock(
        side_effect=[httpx.Response(503), httpx.Response(200, json={"ok": True})]
    )

    response = make_client(settings, fake_time).get(URL)

    assert response.status_code == 200
    assert route.call_count == 2


@respx.mock
def test_a_404_is_not_retried_and_says_what_happened(settings, fake_time):
    route = respx.get(URL).respond(404)

    with pytest.raises(UpstreamError) as error:
        make_client(settings, fake_time).get(URL)

    assert route.call_count == 1
    assert error.value.status == 404


@respx.mock
def test_timeouts_are_retried_then_reported_without_leaking_the_contact(settings, fake_time):
    route = respx.get(URL).mock(side_effect=httpx.ReadTimeout("slow"))

    with pytest.raises(UpstreamError) as error:
        make_client(settings, fake_time).get(URL)

    assert route.call_count == 4
    assert error.value.status is None
    assert EMAIL not in str(error.value)


@respx.mock
def test_never_more_than_three_requests_at_once(settings):
    lock = threading.Lock()
    state = {"active": 0, "max_active": 0}

    def handler(request):
        with lock:
            state["active"] += 1
            state["max_active"] = max(state["max_active"], state["active"])
        time.sleep(0.05)
        with lock:
            state["active"] -= 1
        return httpx.Response(200, json={})

    respx.get(URL).mock(side_effect=handler)
    # A huge per-minute allowance, so only the concurrency cap is being tested.
    client = PoliteClient(settings, requests_per_minute=600_000)

    threads = [threading.Thread(target=client.get, args=(URL,)) for _ in range(9)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert state["max_active"] == 3
