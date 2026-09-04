from __future__ import annotations

import sys
from pathlib import Path

import click

from dacpactools.application.dto.drop_script import DropScriptRequest
from dacpactools.application.ports._handles import LiveConnection
from dacpactools.presentation.composition import Container, build_drop_script_use_case
from dacpactools.presentation.logging_config import get_logger

log = get_logger("cli.drop_script")


@click.command("drop-script")
@click.option(
    "--dacpac",
    "dacpac_path",
    required=True,
    type=click.Path(dir_okay=False, path_type=Path),
    help="Source-of-truth .dacpac file.",
)
@click.option(
    "--connection",
    "connection_name",
    required=True,
    help="Named DB connection from env/.env (DB__<name>__server/database).",
)
@click.option(
    "--schemas",
    "schemas_csv",
    default=None,
    help="Optional comma-separated schema whitelist (case-insensitive).",
)
@click.option(
    "--out",
    "output_dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=Path("./out"),
    help="Output directory.",
)
@click.pass_obj
def drop_script(
    container: Container,
    dacpac_path: Path,
    connection_name: str,
    schemas_csv: str | None,
    output_dir: Path,
) -> None:
    """Generate a .sql script that drops every object present in the target DB
    but missing from the dacpac. Optionally restrict to a set of schemas."""
    if not dacpac_path.exists():
        click.echo(f"error: dacpac not found: {dacpac_path}", err=True)
        sys.exit(2)

    try:
        db = container.settings.resolve_connection(connection_name)
    except KeyError as e:
        click.echo(f"error: {e}", err=True)
        sys.exit(2)

    schemas_filter: frozenset[str] | None = None
    if schemas_csv:
        parts = [s.strip() for s in schemas_csv.split(",") if s.strip()]
        if not parts:
            click.echo("error: --schemas is empty after parsing", err=True)
            sys.exit(2)
        schemas_filter = frozenset(parts)

    try:
        use_case = build_drop_script_use_case(container.settings)
    except Exception as e:  # noqa: BLE001
        click.echo(f"error: CLR / assembly initialisation failed: {e}", err=True)
        sys.exit(4)

    try:
        result = use_case.execute(
            DropScriptRequest(
                dacpac_path=dacpac_path,
                connection=LiveConnection(server=db.server, database=db.database),
                output_dir=output_dir,
                schemas_filter=schemas_filter,
            )
        )
    except Exception as e:
        log.exception("drop-script failed", err=str(e))
        click.echo(f"error: {type(e).__name__}: {e}", err=True)
        sys.exit(1)

    click.echo(f"drops: {len(result.dropped)} object(s)  → {result.script_path}")
    for w in result.warnings:
        click.echo(f"  warning: {w}", err=True)
