from __future__ import annotations

import sys
from pathlib import Path

import click

from dacpactools.application.dto.find_joins import FindJoinsRequest
from dacpactools.application.dto.lineage_request import DacPacSource, LiveConnectionSource
from dacpactools.application.ports._handles import LiveConnection
from dacpactools.domain.errors import DomainError
from dacpactools.domain.identifiers import ObjectRef
from dacpactools.presentation.composition import Container, build_find_joins_use_case
from dacpactools.presentation.logging_config import get_logger

log = get_logger("cli.joins")


@click.command("joins")
@click.option("--table", "table_name", required=True, help="Target table, e.g. [dbo].[Fact].")
@click.option("--dacpac", "dacpac_path", type=click.Path(dir_okay=False, path_type=Path), default=None)
@click.option("--connection", "connection_name", default=None, help="Named DB connection from env/.env.")
@click.option(
    "--out",
    "output_dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=Path("./out"),
)
@click.pass_obj
def joins(
    container: Container,
    table_name: str,
    dacpac_path: Path | None,
    connection_name: str | None,
    output_dir: Path,
) -> None:
    """Find every view/proc/UDF that joins against a given table."""
    if bool(dacpac_path) == bool(connection_name):
        click.echo("error: exactly one of --dacpac / --connection is required", err=True)
        sys.exit(2)

    try:
        target = ObjectRef.parse(table_name)
    except DomainError as e:
        click.echo(f"error: {e}", err=True)
        sys.exit(2)

    if dacpac_path is not None:
        if not dacpac_path.exists():
            click.echo(f"error: dacpac not found: {dacpac_path}", err=True)
            sys.exit(2)
        source = DacPacSource(path=dacpac_path)
    else:
        try:
            conn = container.settings.resolve_connection(connection_name or "")
        except KeyError as e:
            click.echo(f"error: {e}", err=True)
            sys.exit(2)
        source = LiveConnectionSource(
            connection=LiveConnection(server=conn.server, database=conn.database)
        )

    try:
        use_case = build_find_joins_use_case(container.settings)
    except Exception as e:  # noqa: BLE001
        click.echo(f"error: CLR / assembly initialisation failed: {e}", err=True)
        sys.exit(4)

    try:
        result = use_case.execute(
            FindJoinsRequest(target=target, source=source, output_dir=output_dir)
        )
    except Exception as e:  # noqa: BLE001
        log.exception("joins scan failed", err=str(e))
        click.echo(f"error: {type(e).__name__}: {e}", err=True)
        sys.exit(1)

    click.echo(f"joins on {target.qualified()}: {len(result.report.usages)} usage(s)")
    for kind, path in result.artifacts.items():
        click.echo(f"  {kind}: {path}")
    for w in result.report.warnings:
        click.echo(f"  warning: {w}", err=True)
