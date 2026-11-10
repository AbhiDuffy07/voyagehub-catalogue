"""Command line: `python -m catalogue <command>`.

Commands: version, check, and the jobs as they are built (discover in B2; fetch-listings and the
rest arrive one by one in later sessions).
A PipelineError ends the command with a one-line message and exit code 1, never a traceback.
"""

import argparse
import sys
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg

from catalogue import __version__
from catalogue.db import connect, tls_in_use
from catalogue.discover.dump import fetch_dump
from catalogue.discover.overrides import load_country_iso_overrides
from catalogue.discover.run import run_discover
from catalogue.errors import PipelineError
from catalogue.http import PoliteClient
from catalogue.settings import Settings, SettingsError, load_settings

SITEINFO_URL = "https://en.wikivoyage.org/w/api.php"
SITEINFO_PARAMS = {"action": "query", "meta": "siteinfo", "format": "json"}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="catalogue", description="VoyageHub catalogue pipeline")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("version", help="print the version")
    check = commands.add_parser("check", help="check settings, and optionally the database/network")
    check.add_argument("--db", action="store_true", help="connect as catalogue_importer, check TLS")
    check.add_argument("--network", action="store_true", help="make one polite call to Wikivoyage")

    discover = commands.add_parser(
        "discover", help="find destinations in a Wikivoyage dump; writes local files, no database"
    )
    discover.add_argument("--dump-date", required=True, help="dump directory date, like 20261101")
    discover.add_argument("--country", help="only this ISO country code (for testing)")
    discover.add_argument(
        "--detail", action="append", default=[], metavar="CC", help="write a detail report for CC"
    )
    discover.add_argument(
        "--highlight-file", type=Path, help="destination titles to show in the detail reports"
    )
    discover.add_argument("--dumps-dir", type=Path, default=Path("dumps"))
    discover.add_argument(
        "--overrides-dir", type=Path, default=Path("overrides"), help="folder with country_iso.csv"
    )
    discover.add_argument("--out-dir", type=Path, default=Path("out/discover"))
    return parser


def read_highlights(path: Path) -> list[str]:
    """Titles one per line; blank lines and # comments are skipped."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        raise PipelineError("cannot read the highlight file") from None
    return [line.strip() for line in lines if line.strip() and not line.lstrip().startswith("#")]


def _discover(
    args: argparse.Namespace,
    environ: Mapping[str, str] | None,
    env_file: Path | str | None,
    client_factory: Callable[[Settings], PoliteClient],
) -> None:
    settings = load_settings(environ, env_file)
    highlights = read_highlights(args.highlight_file) if args.highlight_file else []
    overrides = load_country_iso_overrides(args.overrides_dir / "country_iso.csv")
    client = client_factory(settings)
    try:
        dump = fetch_dump(client, args.dump_date, args.dumps_dir)
        result = run_discover(
            client,
            dump_path=dump,
            dump_date=args.dump_date,
            out_dir=args.out_dir,
            today=datetime.now(UTC).date(),
            country_iso=args.country,
            detail_countries=args.detail,
            highlight_titles=highlights,
            country_iso_overrides=overrides,
        )
    finally:
        client.close()
    print(f"dump {args.dump_date}: checksum verified")
    print(result.summary(top=10))
    print(f"files: {result.output_dir}")


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
    client_factory: Callable[[Settings], PoliteClient] = PoliteClient,
) -> int:
    args = _parser().parse_args(argv)
    if args.command == "version":
        print(__version__)
        return 0
    try:
        if args.command == "discover":
            _discover(args, environ, env_file, client_factory)
        else:
            _check(args, environ, env_file, db_driver)
    except PipelineError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0
