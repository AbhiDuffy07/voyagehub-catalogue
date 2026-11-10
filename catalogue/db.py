"""The pipeline's Postgres connection: always TLS, and only ever as `catalogue_importer`.

The importer role can touch only catalogue, routing and bookkeeping tables (migration 7 in the app
repo). Refusing any other login means a pasted admin URL cannot be used by a job by mistake.
Error messages never contain the URL or the driver's message: both can carry the password.
"""

from collections.abc import Callable
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import psycopg

from catalogue.errors import PipelineError

EXPECTED_ROLE = "catalogue_importer"
CONNECT_TIMEOUT_SECONDS = 15

# libpq's default is `prefer`: it quietly falls back to plain text if TLS is blocked.
_WEAK_SSL_MODES = {"disable", "allow", "prefer"}


class DatabaseUnavailable(PipelineError):
    """The database could not be reached or queried. The message holds only the error's class."""


class WrongRoleError(PipelineError):
    """The login is not the importer role."""


def require_tls(url: str) -> str:
    """The same postgresql:// URL, but never allowed to fall back to an unencrypted connection.

    A missing or weak sslmode becomes `require`; a stricter one (verify-ca, verify-full) is kept.
    """
    parts = urlsplit(url)
    if parts.scheme not in {"postgresql", "postgres"}:
        raise ValueError("expected a postgresql:// URL")  # never echo the URL: it holds a password
    query = parse_qsl(parts.query, keep_blank_values=True)
    modes = [value for key, value in query if key == "sslmode"]
    if modes and modes[-1] not in _WEAK_SSL_MODES:
        return url
    query = [(key, value) for key, value in query if key != "sslmode"]
    query.append(("sslmode", "require"))
    return urlunsplit(parts._replace(query=urlencode(query)))


def tls_in_use(connection: Any) -> bool:
    """Ask Postgres whether this very connection is encrypted (not just whether we asked)."""
    row = connection.execute("select ssl from pg_stat_ssl where pid = pg_backend_pid()").fetchone()
    return bool(row and row[0])


def connect(
    url: str, *, driver: Callable[..., Any] = psycopg.connect
) -> psycopg.Connection[tuple[Any, ...]]:
    """Open a TLS connection and check it logged in as the importer role."""
    conninfo = require_tls(url)
    try:
        connection = driver(
            conninfo,
            connect_timeout=CONNECT_TIMEOUT_SECONDS,
            prepare_threshold=None,  # the Supabase pooler does not support prepared statements
        )
        role = connection.execute("select current_user").fetchone()[0]
    except psycopg.Error as exc:
        raise DatabaseUnavailable(f"database error: {type(exc).__name__}") from None
    if role != EXPECTED_ROLE:
        connection.close()
        raise WrongRoleError(f"logged in as {role!r}, but jobs must use {EXPECTED_ROLE!r}")
    return connection
