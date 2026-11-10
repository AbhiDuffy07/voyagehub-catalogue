import uuid

import psycopg
import pytest

from catalogue.errors import PipelineError
from catalogue.runlog import record_run

RUN_ID = uuid.uuid4()


class FakeCursor:
    def fetchone(self):
        return (RUN_ID,)


class FakeConnection:
    """Records every statement and transaction event, in order."""

    def __init__(self, fail_on: str | None = None) -> None:
        self.events: list[tuple] = []
        self.fail_on = fail_on

    def execute(self, sql: str, params: tuple = ()):
        kind = "insert" if sql.lstrip().lower().startswith("insert") else "update"
        if self.fail_on == kind:
            raise psycopg.OperationalError("database went away")
        self.events.append((kind, params))
        return FakeCursor()

    def commit(self) -> None:
        self.events.append(("commit",))

    def rollback(self) -> None:
        self.events.append(("rollback",))

    def kinds(self) -> list[str]:
        return [event[0] for event in self.events]

    def last(self, kind: str) -> tuple:
        return [event for event in self.events if event[0] == kind][-1]


def test_a_running_row_is_committed_before_the_job_body_starts():
    connection = FakeConnection()

    with record_run(connection, "fetch-listings", city_slug="jaipur", country="IN"):
        assert connection.kinds() == ["insert", "commit"]
        _, params = connection.last("insert")
        assert params[0] == "fetch-listings"
        assert params[1] == "jaipur"
        assert params[2].obj == {"country": "IN"}


def test_a_successful_run_is_marked_succeeded_with_the_counts_the_body_added():
    connection = FakeConnection()

    with record_run(connection, "enrich-wikidata", country="IN") as stats:
        stats["places_updated"] = 12

    _, params = connection.last("update")
    status, final_stats, error, run_id = params
    assert status == "succeeded"
    assert final_stats.obj == {"country": "IN", "places_updated": 12}
    assert error is None
    assert run_id == RUN_ID
    assert connection.kinds()[-1] == "commit"


def test_a_failed_run_rolls_back_first_then_records_the_failure_and_reraises():
    connection = FakeConnection()

    with pytest.raises(PipelineError):
        with record_run(connection, "discover"):
            raise PipelineError("the dump was not a bz2 file")

    assert connection.kinds() == ["insert", "commit", "rollback", "update", "commit"]
    status, _, error, _ = connection.last("update")[1]
    assert status == "failed"
    assert error == "PipelineError: the dump was not a bz2 file"


def test_an_unknown_exceptions_message_is_not_recorded():
    # Actions logs of a public repo are public, so only our own safe messages are kept.
    connection = FakeConnection()

    with pytest.raises(ValueError):
        with record_run(connection, "discover"):
            raise ValueError("postgresql://user:s3cret-pw@host/db")

    _, _, error, _ = connection.last("update")[1]
    assert error == "ValueError"


def test_if_recording_the_failure_fails_the_original_error_still_comes_out():
    connection = FakeConnection()

    with pytest.raises(PipelineError, match="original"):
        with record_run(connection, "discover"):
            connection.fail_on = "update"
            raise PipelineError("original problem")
