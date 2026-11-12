import re

import pytest

from catalogue.progress import Progress

LINE = re.compile(r"progress: [A-Za-z0-9 _-]+ \d+( of \d+ \(\d+%\))?( done)?")


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def make(label="items", total=100, **kw):
    lines: list[str] = []
    clock = Clock()
    progress = Progress(label, total, emit=lines.append, clock=clock, **kw)
    return progress, lines, clock


def test_nothing_is_printed_before_the_interval():
    progress, lines, clock = make()
    for second in (0, 10, 29):
        clock.now = second
        progress.advance(5)
    assert lines == []


def test_a_line_is_printed_once_the_interval_has_passed():
    progress, lines, clock = make()
    progress.advance(10)
    clock.now = 31
    progress.advance(10)
    assert lines == ["progress: items 20 of 100 (20%)"]


def test_lines_are_throttled_to_one_per_interval():
    progress, lines, clock = make()
    clock.now = 31
    progress.advance(10)
    clock.now = 40
    progress.advance(10)
    clock.now = 50
    progress.advance(10)
    assert len(lines) == 1
    clock.now = 62
    progress.advance(10)
    assert lines == ["progress: items 10 of 100 (10%)", "progress: items 40 of 100 (40%)"]


def test_done_always_prints_a_final_line_even_for_a_fast_job():
    progress, lines, _ = make(total=3)
    progress.advance(3)
    progress.done()
    assert lines == ["progress: items 3 of 3 (100%) done"]


def test_without_a_total_only_the_count_is_shown():
    progress, lines, clock = make("pages read", None)
    clock.now = 31
    progress.advance(12000)
    progress.done()
    assert lines == ["progress: pages read 12000", "progress: pages read 12000 done"]


def test_a_job_that_did_nothing_prints_nothing():
    for total in (0, None):
        progress, lines, _ = make(total=total)
        progress.done()
        assert lines == []


def test_done_twice_prints_once():
    progress, lines, _ = make(total=2)
    progress.advance(2)
    progress.done()
    progress.done()
    assert len(lines) == 1


def test_a_count_past_the_total_is_shown_as_the_total():
    progress, lines, _ = make(total=10)
    progress.advance(12)
    progress.done()
    assert lines == ["progress: items 10 of 10 (100%) done"]


@pytest.mark.parametrize("label", ["", "two\nlines", "x@y.example", "path/to", "Wikidata (items)"])
def test_a_label_that_could_carry_data_is_refused(label):
    with pytest.raises(ValueError):
        Progress(label, 1)


def test_the_context_manager_finishes_on_success_but_not_on_an_error():
    lines: list[str] = []
    with Progress("items", 2, emit=lines.append) as progress:
        progress.advance(2)
    assert lines == ["progress: items 2 of 2 (100%) done"]

    lines.clear()
    with pytest.raises(RuntimeError), Progress("items", 2, emit=lines.append) as progress:
        progress.advance(1)
        raise RuntimeError("boom")
    assert lines == []


def test_every_line_has_only_the_label_and_counts():
    progress, lines, clock = make("Wikidata items", 5000)
    for step in range(1, 6):
        clock.now = step * 31
        progress.advance(500)
    progress.done()
    assert lines and all(LINE.fullmatch(line) for line in lines)


def test_the_default_sink_is_standard_output_not_standard_error(capsys):
    progress = Progress("items", 1)
    progress.advance(1)
    progress.done()
    captured = capsys.readouterr()
    assert captured.out == "progress: items 1 of 1 (100%) done\n"
    assert captured.err == ""
