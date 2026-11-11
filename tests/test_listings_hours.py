import pytest

from catalogue.listings.hours import parse_hours

WEEK = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def parsed(raw):
    result = parse_hours(raw)
    assert result.status == "parsed", (raw, result)
    return result.parsed


def entry(days, *ranges):
    return {"days": days, "ranges": [list(r) for r in ranges]}


def test_an_empty_value_is_empty():
    assert parse_hours("  ").status == "empty"
    assert parse_hours("").parsed is None


def test_days_with_one_time_range():
    assert parsed("Mon-Fri 9AM-5PM") == {
        "schedule": [entry(WEEK[:5], ("09:00", "17:00"))],
        "closed_days": [],
        "always_open": False,
        "overnight": False,
    }


def test_a_range_of_days_may_wrap_the_week():
    days = parsed("Sat-Thu 9AM-5PM")["schedule"][0]["days"]

    assert days == ["sat", "sun", "mon", "tue", "wed", "thu"]


def test_several_clauses_and_a_closed_day():
    result = parsed("Mon-Fri 9AM-5PM; Sat 10AM-2PM; closed Sun")

    assert result["schedule"] == [
        entry(WEEK[:5], ("09:00", "17:00")),
        entry(["sat"], ("10:00", "14:00")),
    ]
    assert result["closed_days"] == ["sun"]


@pytest.mark.parametrize("raw", ["Closed Mondays", "Mon: closed", "Mon closed"])
def test_closed_day_forms(raw):
    assert parsed(raw)["closed_days"] == ["mon"]
    assert parsed(raw)["schedule"] == []


def test_times_without_days_apply_every_day():
    assert parsed("9AM-5PM")["schedule"] == [entry(WEEK, ("09:00", "17:00"))]
    assert parsed("Daily 9AM-5PM")["schedule"] == [entry(WEEK, ("09:00", "17:00"))]


def test_a_split_day_has_two_ranges():
    result = parsed("Mon-Fri 9AM-12PM, 2PM-5PM; Sat 9AM-1PM")

    assert result["schedule"] == [
        entry(WEEK[:5], ("09:00", "12:00"), ("14:00", "17:00")),
        entry(["sat"], ("09:00", "13:00")),
    ]


@pytest.mark.parametrize(
    "raw", ["24/7", "Open 24 hours", "24 hours daily", "Always open", "24 hrs"]
)
def test_always_open(raw):
    result = parsed(raw)

    assert result["always_open"] is True and result["schedule"] == []


def test_a_start_without_am_pm_is_accepted_only_when_the_end_decides_it():
    assert parsed("3-8PM")["schedule"][0]["ranges"] == [["15:00", "20:00"]]
    assert parsed("6-11PM")["schedule"][0]["ranges"] == [["18:00", "23:00"]]


@pytest.mark.parametrize("raw", ["9-5PM", "10-12PM", "11-1PM", "9-5", "9AM-5", "noon-5"])
def test_ambiguous_ranges_stay_raw(raw):
    result = parse_hours(raw)

    assert (result.status, result.parsed) == ("raw_only", None)


def test_twenty_four_hour_clock_needs_a_colon_and_an_unmistakable_form():
    assert parsed("10:00-14:00")["schedule"][0]["ranges"] == [["10:00", "14:00"]]
    assert parsed("09:00-12:00")["schedule"][0]["ranges"] == [["09:00", "12:00"]]
    assert parsed("9:00-17:00")["schedule"][0]["ranges"] == [["09:00", "17:00"]]
    for raw in ("9:00-5:00", "9:00-12:00", "9-17"):
        assert parse_hours(raw).status == "raw_only", raw


def test_noon_midnight_and_twelve_oclock():
    assert parsed("11AM-noon")["schedule"][0]["ranges"] == [["11:00", "12:00"]]
    assert parsed("6PM-midnight")["schedule"][0]["ranges"] == [["18:00", "24:00"]]
    assert parsed("6PM-midnight")["overnight"] is False
    assert parsed("midnight-6AM")["schedule"][0]["ranges"] == [["00:00", "06:00"]]
    assert parsed("12AM-12PM")["schedule"][0]["ranges"] == [["00:00", "12:00"]]
    assert parsed("12:30PM-1:30PM")["schedule"][0]["ranges"] == [["12:30", "13:30"]]


def test_overnight_ranges_are_marked():
    assert parsed("8PM-2AM")["overnight"] is True
    assert parsed("22:00-02:00")["overnight"] is True
    assert parsed("Fri-Sat 10PM-4AM")["schedule"][0]["days"] == ["fri", "sat"]


def test_dots_dashes_and_to_are_understood():
    expected = [entry(WEEK, ("09:00", "17:00"))]

    assert parsed("9 a.m. – 5 p.m.")["schedule"] == expected
    assert parsed("9am to 5pm")["schedule"] == expected
    assert parsed("Mon–Fri 9am to 5pm")["schedule"][0]["days"] == WEEK[:5]


@pytest.mark.parametrize(
    "raw",
    [
        "Summer 9AM-5PM",
        "Only guided tour",
        "Closed",
        "Mon-Fri",
        "Sunrise to sunset",
        "Mon-Fri 9AM-5PM; Wed 10AM-4PM",  # two readings of Wednesday
        "Mon-Fri 9AM-5PM; closed Wed",
        "13PM-5PM",
        "9:75AM-5PM",
        "9AM-9AM",
        "Mon, Wed, Fri 9AM-5PM",
        "Open daily except holidays 9AM-5PM",
    ],
)
def test_everything_else_stays_raw_only(raw):
    result = parse_hours(raw)

    assert (result.status, result.parsed) == ("raw_only", None), raw
