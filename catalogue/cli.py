"""Command line: `python -m catalogue <command>`.

B1 has two commands. The jobs (discover, fetch-listings, ...) arrive one by one in later sessions.
A PipelineError ends the command with a one-line message and exit code 1, never a traceback.
"""

import argparse
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

import psycopg

from catalogue import __version__
from catalogue.db import connect, tls_in_use
from catalogue.errors import PipelineError
from catalogue.http import PoliteClient
from catalogue.settings import SettingsError, load_settings

SITEINFO_URL = "https://en.wikivoyage.org/w/api.php"
SITEINFO_PARAMS = {"action": "query", "meta": "siteinfo", "format": "json"}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="catalogue", description="VoyageHub catalogue pipeline")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("version", help="print the version")
    check = commands.add_parser("check", help="check settings, and optionally the database/network")
    check.add_argument("--db", action="store_true", help="connect as catalogue_importer, check TLS")
    check.add_argument("--network", action="store_true", help="make one polite call to Wikivoyage")
    return parser


def _wikivoyage_sitename(client: PoliteClient) -> str:
    response = client.get(SITEINFO_URL, params=SITEINFO_PARAMS)
    try:
        return str(response.json()["query"]["general"]["sitename"])
    except (KeyError, TypeError, ValueError):
        raise PipelineError("unexpected reply shape from Wikivoyage") from None


def _check(
    args: argparse.Namespace,
    environ: Mapping[str, str] | None,
    env_file: Path | str | None,
    db_driver: Callable[..., Any],
) -> None:
    settings = load_settings(environ, env_file)
    print("settings: ok")

    if args.db:
        if settings.database_url is None:
            raise SettingsError("CATALOGUE_DATABASE_URL is required for --db")
        connection = connect(settings.database_url, driver=db_driver)
        try:
            if not tls_in_use(connection):
                raise PipelineError("the database connection is not using TLS")
        finally:
            connection.close()
        print("database: ok (TLS on, logged in as catalogue_importer)")

    if args.network:
        client = PoliteClient(settings)
        try:
            print(f"network: ok ({_wikivoyage_sitename(client)})")
        finally:
            client.close()


def main(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    env_file: Path | str | None = ".env",
    db_driver: Callable[..., Any] = psycopg.connect,
) -> int:
    args = _parser().parse_args(argv)
    if args.command == "version":
        print(__version__)
        return 0
    try:
        _check(args, environ, env_file, db_driver)
    except PipelineError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0
