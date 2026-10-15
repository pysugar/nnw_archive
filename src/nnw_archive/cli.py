#!/usr/bin/env python3
"""Click CLI for the NetNewsWire archive tool."""

from __future__ import annotations

import json
import plistlib
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

import click


def _bootstrap_direct_execution() -> None:
    if __name__ != "__main__" or __package__:
        return
    package_dir = Path(__file__).resolve().parent
    source_root = package_dir.parent
    if str(source_root) not in sys.path:
        sys.path.insert(0, str(source_root))
    globals()["__package__"] = package_dir.name


_bootstrap_direct_execution()

from . import core


def repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def bin_command_path() -> Path:
    return repo_root() / "bin" / "nnw_archive"


def launchd_plist_path(label: str) -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"


def common_options(func):
    func = click.option(
        "--feed-metadata",
        "feed_metadata_path",
        type=click.Path(path_type=Path),
        help="Override FeedMetadata.plist path.",
    )(func)
    func = click.option(
        "--opml",
        "opml_path",
        type=click.Path(path_type=Path),
        help="Override Subscriptions.opml path.",
    )(func)
    func = click.option(
        "--feed-settings-db",
        type=click.Path(path_type=Path),
        help="Override FeedSettings.db path.",
    )(func)
    func = click.option(
        "--archive-db",
        type=click.Path(path_type=Path),
        help="Archive SQLite path. Overrides --data-dir.",
    )(func)
    func = click.option(
        "--data-dir",
        type=click.Path(path_type=Path),
        default=core.default_state_dir(),
        show_default="~/.local/state/netnewswire-archive",
        help="Directory for archive.sqlite3 and logs.",
    )(func)
    func = click.option(
        "--source",
        type=click.Path(path_type=Path),
        help="NetNewsWire DB.sqlite3 path. Defaults to native container path, then ./netnewswire.sqlite3.",
    )(func)
    return func


def logging_options(func):
    func = click.option("--verbose", "-v", is_flag=True, help="Also log debug details to stderr.")(func)
    return func


def prepare_logging(data_dir: Path, verbose: bool) -> Path:
    return core.setup_logging(data_dir=data_dir, verbose=verbose)


def click_error(message: str, log_path: Path | None = None) -> click.ClickException:
    if log_path:
        message = f"{message}\nLog: {log_path}"
    return click.ClickException(message)


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
def cli() -> None:
    """Long-term archive and search for NetNewsWire On My Mac feeds."""


@cli.command()
@common_options
@click.option("--allow-schema-change", is_flag=True, help="Accept a changed NNW schema hash.")
@click.option("--json", "json_output", is_flag=True, help="Print full sync result as JSON.")
@logging_options
def sync(
    source: Path | None,
    data_dir: Path,
    archive_db: Path | None,
    feed_settings_db: Path | None,
    opml_path: Path | None,
    feed_metadata_path: Path | None,
    allow_schema_change: bool,
    json_output: bool,
    verbose: bool,
) -> None:
    """Merge the current NetNewsWire database into the archive."""
    log_path = prepare_logging(data_dir, verbose)
    try:
        result = core.sync_archive(
            source_db=source,
            data_dir=data_dir,
            archive_db=archive_db,
            feed_settings_db=feed_settings_db,
            opml_path=opml_path,
            feed_metadata_path=feed_metadata_path,
            allow_schema_change=allow_schema_change,
        )
    except Exception as exc:
        core.logger.exception("sync command failed")
        raise click_error(str(exc), log_path) from exc

    if json_output:
        click.echo(json.dumps(asdict(result), indent=2, ensure_ascii=False))
        return

    click.secho("Sync completed", fg="green")
    click.echo(f"Archive: {result.archive_db}")
    click.echo(f"Source:  {result.source_db}")
    click.echo(
        "Articles: "
        f"{result.articles_seen} seen, "
        f"{result.articles_inserted} inserted, "
        f"{result.articles_updated} updated"
    )
    click.echo(f"Statuses updated: {result.statuses_updated}")
    click.echo(f"Feeds updated:    {result.feeds_updated}")
    click.echo(f"Source-deleted marked: {result.source_deleted_marked}")
    click.echo(f"Log: {log_path}")


@cli.command()
@common_options
@click.option("--json", "json_output", is_flag=True, help="Print machine-readable JSON.")
@logging_options
def stats(
    source: Path | None,
    data_dir: Path,
    archive_db: Path | None,
    feed_settings_db: Path | None,
    opml_path: Path | None,
    feed_metadata_path: Path | None,
    json_output: bool,
    verbose: bool,
) -> None:
    """Show archive counts and last run metadata."""
    del source, feed_settings_db, opml_path, feed_metadata_path
    log_path = prepare_logging(data_dir, verbose)
    resolved_archive = core.resolve_archive_db(data_dir, archive_db)
    try:
        payload = core.get_stats(resolved_archive)
    except Exception as exc:
        core.logger.exception("stats command failed")
        raise click_error(str(exc), log_path) from exc

    if json_output:
        click.echo(json.dumps(payload, indent=2, ensure_ascii=False))
        return

    click.echo(f"Archive:        {payload['archive_db']}")
    click.echo(f"Articles:       {payload['articles']}")
    click.echo(f"Feeds:          {payload['feeds']}")
    click.echo(f"Starred:        {payload['starred']}")
    click.echo(f"Unread:         {payload['unread']}")
    click.echo(f"Retained:       {payload['source_deleted']}")
    click.echo(f"Last run:       {payload['last_run'] or '-'}")
    click.echo(f"Log:            {log_path}")


@cli.command()
@common_options
@click.option("--host", default="127.0.0.1", show_default=True, help="Bind host.")
@click.option("--port", default=8765, show_default=True, type=int, help="Bind port.")
@click.option("--debug", is_flag=True, help="Enable Flask debug mode.")
@logging_options
def web(
    source: Path | None,
    data_dir: Path,
    archive_db: Path | None,
    feed_settings_db: Path | None,
    opml_path: Path | None,
    feed_metadata_path: Path | None,
    host: str,
    port: int,
    debug: bool,
    verbose: bool,
) -> None:
    """Run the local read-only Flask search UI."""
    del source, feed_settings_db, opml_path, feed_metadata_path
    log_path = prepare_logging(data_dir, verbose)
    resolved_archive = core.resolve_archive_db(data_dir, archive_db)
    try:
        from .webapp import run_server

        click.echo(f"Serving NetNewsWire archive at http://{host}:{port}/")
        click.echo(f"Archive: {resolved_archive}")
        click.echo(f"Log: {log_path}")
        run_server(resolved_archive, host=host, port=port, debug=debug)
    except Exception as exc:
        core.logger.exception("web command failed")
        raise click_error(str(exc), log_path) from exc


@cli.command("install-launchd")
@common_options
@click.option("--label", default=core.DEFAULT_LABEL, show_default=True)
@click.option("--hour", default=2, show_default=True, type=click.IntRange(0, 23))
@click.option("--minute", default=15, show_default=True, type=click.IntRange(0, 59))
@click.option("--allow-schema-change", is_flag=True)
@click.option("--load", is_flag=True, help="Run launchctl load after writing the plist.")
@click.option("--dry-run", is_flag=True, help="Print plist to stdout without writing.")
@logging_options
def install_launchd(
    source: Path | None,
    data_dir: Path,
    archive_db: Path | None,
    feed_settings_db: Path | None,
    opml_path: Path | None,
    feed_metadata_path: Path | None,
    label: str,
    hour: int,
    minute: int,
    allow_schema_change: bool,
    load: bool,
    dry_run: bool,
    verbose: bool,
) -> None:
    """Install a daily LaunchAgent that runs nnw_archive sync."""
    log_path = prepare_logging(data_dir, verbose)
    command = [str(bin_command_path()), "sync", "--data-dir", str(data_dir)]
    if source:
        command.extend(["--source", str(source)])
    if archive_db:
        command.extend(["--archive-db", str(archive_db)])
    for flag, path in (
        ("--feed-settings-db", feed_settings_db),
        ("--opml", opml_path),
        ("--feed-metadata", feed_metadata_path),
    ):
        if path is not None:
            command.extend([flag, str(path.expanduser().resolve())])
    if allow_schema_change:
        command.append("--allow-schema-change")

    plist = {
        "Label": label,
        "ProgramArguments": command,
        "StartCalendarInterval": {"Hour": hour, "Minute": minute},
        "StandardOutPath": str(data_dir / "launchd.out.log"),
        "StandardErrorPath": str(data_dir / "launchd.err.log"),
        "EnvironmentVariables": {
            "UV_CACHE_DIR": "/tmp/uv-cache",
            "PATH": "/opt/local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
        },
    }

    if dry_run:
        sys.stdout.buffer.write(plistlib.dumps(plist, sort_keys=False))
        return

    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        path = launchd_plist_path(label)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as handle:
            plistlib.dump(plist, handle, sort_keys=False)
        if load:
            subprocess.run(["launchctl", "load", str(path)], check=True)
    except Exception as exc:
        core.logger.exception("install-launchd command failed")
        raise click_error(str(exc), log_path) from exc

    click.secho(f"Installed LaunchAgent: {path}", fg="green")
    click.echo(f"Log: {log_path}")


@cli.command("uninstall-launchd")
@click.option("--label", default=core.DEFAULT_LABEL, show_default=True)
@click.option("--unload", is_flag=True, help="Run launchctl unload before deleting.")
def uninstall_launchd(label: str, unload: bool) -> None:
    """Remove the daily sync LaunchAgent."""
    path = launchd_plist_path(label)
    if unload and path.exists():
        subprocess.run(["launchctl", "unload", str(path)], check=False)
    if path.exists():
        path.unlink()
        click.secho(f"Removed LaunchAgent: {path}", fg="green")
    else:
        click.echo(f"LaunchAgent not found: {path}")


if __name__ == "__main__":
    cli()
