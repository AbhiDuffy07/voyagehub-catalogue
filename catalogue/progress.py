"""Count-only progress lines for the long jobs.

A job that runs for an hour and prints nothing cannot be told from a hung one. `Progress` prints a
line such as `progress: Wikidata items 12500 of 111984 (11%)` at most once every 30 seconds, and a
final `... done` line.

Safe for public logs by construction: a line holds only a label and whole numbers. The label is
fixed text in the code, and anything that could carry data (a title, a path, an address, a
parenthesis) is refused, so no row text, contact address or User-Agent can reach a log through
here. Lines go to standard output, next to each job's own summary, so that standard error still
means "an error" and nothing else.
"""

import re
import time
from collections.abc import Callable
from types import TracebackType
from typing import Self

INTERVAL_SECONDS = 30.0
_LABEL = re.compile(r"[A-Za-z0-9 _-]+")


def _print(line: str) -> None:
    print(line, flush=True)


class Progress:
    def __init__(
        self,
        label: str,
        total: int | None = None,
        *,
        interval: float = INTERVAL_SECONDS,
        emit: Callable[[str], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not _LABEL.fullmatch(label):
            raise ValueError("a progress label may hold only letters, digits, spaces, _ and -")
        self._label = label
        self._total = total
        self._interval = interval
        self._emit = emit if emit is not None else _print
        self._clock = clock
        self._count = 0
        self._last = clock()
        self._finished = False

    def advance(self, count: int = 1) -> None:
        self._count += count
        now = self._clock()
        if now - self._last >= self._interval:
            self._last = now
            self._emit(self._line())

    def done(self) -> None:
        """The final line. Nothing is printed for a job that processed nothing."""
        if self._finished:
            return
        self._finished = True
        if self._count > 0:
            self._emit(f"{self._line()} done")

    def _line(self) -> str:
        if not self._total:
            return f"progress: {self._label} {self._count}"
        shown = min(self._count, self._total)
        return f"progress: {self._label} {shown} of {self._total} ({100 * shown // self._total}%)"

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        kind: type[BaseException] | None,
        error: BaseException | None,
        trace: TracebackType | None,
    ) -> None:
        if kind is None:
            self.done()
