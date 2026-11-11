import pytest

from catalogue.listings.quality import distance_km, parse_coordinate


@pytest.mark.parametrize(
    ("lat", "long", "expected"),
    [
        ("9.93", "76.26", (9.93, 76.26)),
        (" -33.9 ", "151.2", (-33.9, 151.2)),
        ("90", "180", (90.0, 180.0)),
        ("", "", None),
        ("9.9", "", None),
        ("abc", "76", None),
        ("91", "10", None),
        ("10", "181", None),
        ("0", "0", None),  # the usual placeholder, not a place
        ("nan", "1", None),
        ("inf", "1", None),
        ("9,9", "76,2", None),
    ],
)
def test_coordinates_are_read_only_when_they_are_real(lat, long, expected):
    assert parse_coordinate(lat, long) == expected


def test_distance_between_known_places():
    # Jaipur to Agra is about 205 km as the crow flies.
    assert distance_km((26.9124, 75.7873), (27.1767, 78.0081)) == pytest.approx(226, abs=8)
    assert distance_km((10.0, 76.0), (10.0, 76.0)) == 0
    # across the antimeridian
    assert distance_km((0.0, 179.9), (0.0, -179.9)) == pytest.approx(22.2, abs=0.5)


def test_percentiles_use_the_nearest_rank_and_are_stable():
    from catalogue.listings.report import percentile

    values = [float(n) for n in range(1, 101)]

    assert percentile(values, 50) == 50
    assert percentile(values, 90) == 90
    assert percentile(values, 99) == 99
    assert percentile(values, 100) == 100
    assert percentile([7.0], 99) == 7.0
