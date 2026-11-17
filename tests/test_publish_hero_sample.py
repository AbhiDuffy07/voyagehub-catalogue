import json

import pytest

from catalogue.cli import main
from catalogue.errors import PipelineError
from catalogue.publish.hero_sample import sample_heroes, write_sample


def make_site(tmp_path, n=120, no_hero=(), caption=()):
    site = tmp_path / "site"
    cities = site / "v1" / "cities"
    cities.mkdir(parents=True)
    for i in range(n):
        hero = None
        if i not in no_hero:
            hero = {
                "thumb_url": f"https://upload.example/thumb/{i}.jpg",
                "caption": f"Place {i}" if i in caption else None,
                "credit": {"source_url": f"https://commons.example/File:{i}.jpg"},
            }
        doc = {
            "slug": f"city-{i:03d}",
            "title": f"City {i}",
            "country_iso": "US" if i % 2 else "IN",
            "hero": hero,
        }
        (cities / f"city-{i:03d}.json").write_text(json.dumps(doc), encoding="utf-8")
    return site


def test_the_sample_has_fifty_distinct_cities_with_names_and_urls(tmp_path):
    site = make_site(tmp_path)

    sample = sample_heroes(site, count=50, seed=7)

    assert len(sample.picked) == 50 and len({p["slug"] for p in sample.picked}) == 50
    first = sample.picked[0]
    assert set(first) == {"slug", "title", "country_iso", "thumb_url", "source_url", "caption"}
    assert first["thumb_url"].startswith("https://upload.example/thumb/")


def test_the_same_seed_gives_the_same_sample_and_another_seed_another(tmp_path):
    site = make_site(tmp_path)

    assert sample_heroes(site, 50, 7).picked == sample_heroes(site, 50, 7).picked
    assert sample_heroes(site, 50, 7).picked != sample_heroes(site, 50, 8).picked


def test_cities_without_a_hero_are_not_sampled_but_counted(tmp_path):
    site = make_site(tmp_path, n=60, no_hero=range(0, 20))

    sample = sample_heroes(site, 50, 1)

    assert sample.cities == 60 and sample.with_hero == 40 and sample.without_hero == 20
    assert len(sample.picked) == 40  # fewer heroes than the count: all of them
    assert all(p["slug"] >= "city-020" for p in sample.picked)


def test_a_caption_means_the_hero_is_a_place_photo_and_is_shown(tmp_path):
    site = make_site(tmp_path, n=3, caption=(1,))

    sample = sample_heroes(site, 50, 1)

    assert [p["caption"] for p in sample.picked] == [None, "Place 1", None]


def test_the_markdown_lists_each_city_with_its_urls_sorted_by_country(tmp_path):
    site = make_site(tmp_path, n=6)
    sample = sample_heroes(site, 4, 3)
    out = tmp_path / "heroes_sample.md"

    write_sample(sample, out, seed=3)

    text = out.read_text(encoding="utf-8")
    assert "seed 3" in text and "4 of 6" in text
    assert text.count("https://upload.example/thumb/") == 4
    assert text.count("https://commons.example/File:") == 4
    assert text.index("| IN |") < text.index("| US |")


def test_a_missing_site_is_a_clear_error(tmp_path):
    with pytest.raises(PipelineError, match="site"):
        sample_heroes(tmp_path / "nope", 50, 1)


def test_a_damaged_city_file_is_a_clear_error(tmp_path):
    site = make_site(tmp_path, n=2)
    (site / "v1" / "cities" / "city-000.json").write_text("{", encoding="utf-8")

    with pytest.raises(PipelineError, match="damaged"):
        sample_heroes(site, 50, 1)


def test_the_cli_writes_the_file_next_to_the_site(tmp_path, capsys):
    site = make_site(tmp_path, n=10)

    code = main(["sample-heroes", "--site-dir", str(site), "--count", "5", "--seed", "2"])

    assert code == 0
    assert (site.parent / "heroes_sample.md").is_file()
    assert "5 of 10" in capsys.readouterr().out


def test_a_pipe_or_line_break_in_a_caption_does_not_break_the_table(tmp_path):
    site = make_site(tmp_path, n=1, caption=(0,))
    path = site / "v1" / "cities" / "city-000.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["hero"]["caption"] = "Fort | the \nold gate"
    path.write_text(json.dumps(doc), encoding="utf-8")
    out = tmp_path / "heroes_sample.md"

    write_sample(sample_heroes(site, 5, 1), out, seed=1)

    row = [ln for ln in out.read_text(encoding="utf-8").splitlines() if ln.startswith("| IN |")]
    assert len(row) == 1 and "Fort \\| the old gate" in row[0]
