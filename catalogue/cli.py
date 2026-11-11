"""Command line: `python -m catalogue <command>`.

Commands: version, check, and the jobs as they are built (discover in B2, fetch-listings in B3;
the rest arrive one by one in later sessions).
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
from catalogue.discover.dump import dump_filename, fetch_dump
from catalogue.discover.overrides import load_country_iso_overrides
from catalogue.discover.report import write_research
from catalogue.discover.run import run_discover
from catalogue.errors import PipelineError
from catalogue.http import PoliteClient
from catalogue.listings.run import run_fetch_listings
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
    discover.add_argument(
        "--data-dir",
        type=Path,
        default=Path("data"),
        help="folder with the committed slug registry, slugs.json",
    )
    discover.add_argument(
        "--init-registry",
        action="store_true",
        help="allow a missing slug registry (a genuine first run only)",
    )

    fetch = commands.add_parser(
        "fetch-listings",
        help="collect every listing and section text from the dump (offline, no settings needed)",
    )
    fetch.add_argument("--dump-date", required=True, help="dump directory date, like 20261101")
    fetch.add_argument("--country", help="only this ISO country code (for testing)")
    fetch.add_argument(
        "--detail", action="append", default=[], metavar="CC", help="write a detail report for CC"
    )
    fetch.add_argument(
        "--proving-file",
        type=Path,
        default=Path("config/proving_batch.txt"),
        help="city titles to show in proving-batch.md",
    )
    fetch.add_argument("--dumps-dir", type=Path, default=Path("dumps"))
    fetch.add_argument(
        "--discover-dir", type=Path, default=Path("out/discover"), help="where discover wrote"
    )
    fetch.add_argument("--out-dir", type=Path, default=Path("out/listings"))
    fetch.add_argument(
        "--data-dir",
        type=Path,
        default=Path("data"),
        help="folder with the committed slug registry, slugs.json",
    )

    report = commands.add_parser(
        "report", help="write research files from the discover output (offline, no settings needed)"
    )
    report.add_argument("--dump-date", required=True, help="dump directory date, like 20261101")
    report.add_argument("--detail", action="append", default=[], metavar="CC")
    report.add_argument(
        "--category-total",
        action="append",
        default=[],
        metavar="STATUS=N",
        help="Wikivoyage category total to compare with, like usable=11233",
    )
    report.add_argument("--out-dir", type=Path, default=Path("out/discover"))
    return parser


def parse_category_totals(items: Sequence[str]) -> dict[str, int]:
    """Read STATUS=N pairs. Only usable, guide and star have a Wikivoyage category total."""
    totals: dict[str, int] = {}
    for item in items:
        status, _, number = item.partition("=")
        if status not in ("usable", "guide", "star") or not number.isdigit():
            raise PipelineError("category total must look like usable=11233 (usable, guide, star)")
        totals[status] = int(number)
    return totals


def _report(args: argparse.Namespace) -> None:
    folder = write_research(
        args.out_dir / args.dump_date,
        detail_countries=args.detail,
        category_totals=parse_category_totals(args.category_total) or None,
    )
    print(f"research files: {folder}")
    for path in sorted(folder.iterdir()):
        print(f"  {path.name}")


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
    registry_path = args.data_dir / "slugs.json"
    if not registry_path.is_file() and not args.init_registry:
        # A missing registry would silently restart slug assignment and could hand out slugs that
        # differ from the published ones. In a checkout it is almost always a wrong folder.
        raise PipelineError(
            f"the slug registry {registry_path} does not exist; run from the repository folder "
            "or fix --data-dir (add --init-registry only for a genuine first run)"
        )
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
            registry_path=registry_path,
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


def _fetch_listings(args: argparse.Namespace) -> None:
    registry_path = args.data_dir / "slugs.json"
    if not registry_path.is_file():
        raise PipelineError(
            f"the slug registry {registry_path} does not exist; run from the repository folder "
            "or fix --data-dir"
        )
    proving = read_highlights(args.proving_file)
    result = run_fetch_listings(
        dump_path=args.dumps_dir / args.dump_date / dump_filename(args.dump_date),
        dump_date=args.dump_date,
        discover_folder=args.discover_dir / args.dump_date,
        out_dir=args.out_dir,
        registry_path=registry_path,
        today=datetime.now(UTC).date(),
        country_iso=args.country,
        detail_countries=args.detail,
        proving_titles=proving,
    )
    print(f"dump {args.dump_date}: matches the discover run")
    print(result.summary())
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
        elif args.command == "report":
            _report(args)
        elif args.command == "fetch-listings":
            _fetch_listings(args)
        else:
            _check(args, environ, env_file, db_driver)
    except PipelineError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0
