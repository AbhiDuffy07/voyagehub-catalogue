"""The run log: one `import_runs` row per job run (migration 6 in the app repo).

The `running` row is committed first, so a crashed or cancelled run is still visible. A job commits
its own per-city transactions; if it fails, anything it left uncommitted is rolled back before the
failure is recorded. The stored error is the class name, plus the message only for our own
PipelineError (safe by construction). Other messages can hold data or secrets.
"""

import logging
from collections.abc import Generator
from contextlib import contextmanager
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from catalogue.errors import PipelineError

logger = logging.getLogger(__name__)

_START = "insert into import_runs (job, city_slug, stats) values (%s, %s, %s) returning id"
_FINISH = (
    "update import_runs set status = %s, finished_at = now(), stats = %s, error = %s where id = %s"
)


def _safe_error(exc: BaseException) -> str:
    name = type(exc).__name__
    if isinstance(exc, PipelineError) and str(exc):
        return f"{name}: {exc}"
    return name


def _finish(
    connection: Any, run_id: Any, status: str, stats: dict[str, Any], error: str | None
) -> None:
    connection.execute(_FINISH, (status, Jsonb(stats), error, run_id))
    connection.commit()


@contextmanager
def record_run(
    connection: Any, job: str, *, city_slug: str | None = None, country: str | None = None
) -> Generator[dict[str, Any]]:
    """Log a run. The body may add counts to the yielded `stats` dict."""
    stats: dict[str, Any] = {"country": country} if country else {}
    run_id = connection.execute(_START, (job, city_slug, Jsonb(stats))).fetchone()[0]
    connection.commit()
    try:
        yield stats
    except BaseException as exc:  # includes Ctrl-C: a cancelled run must not stay "running"
        connection.rollback()
        try:
            _finish(connection, run_id, "failed", stats, _safe_error(exc))
        except psycopg.Error as record_error:
            logger.error(
                "could not record the failed run of %s: %s", job, type(record_error).__name__
            )
        raise
    _finish(connection, run_id, "succeeded", stats, None)
