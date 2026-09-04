from __future__ import annotations

import sys
from pathlib import Path

import click

from dacpactools.application.dto.lineage_request import (
    BuildLineageRequest,
    DacPacSource,
    LiveConnectionSource,
)
from dacpactools.application.ports._handles import LiveConnection
from dacpactools.domain.enums import ObjectType
from dacpactools.domain.errors import DomainError, UnresolvableObject
from dacpactools.domain.identifiers import ObjectRef
from dacpactools.presentation.composition import Container, build_lineage_use_case
from dacpactools.presentation.logging_config import get_logger

log = get_logger("cli.lineage")

_ALL_TYPES = [t.value for t in ObjectType if t is not ObjectType.UNKNOWN]


@click.command("lineage")
@click.option("--object", "object_name", required=True, help="Root object, e.g. [dbo].[usp_LoadFact].")
@click.option("--dacpac", "dacpac_path", type=click.Path(dir_okay=False, path_type=Path), default=None)
@click.option("--connection", "connection_name", default=None, help="Named DB connection from env/.env.")
@click.option("--out", "output_dir", type=click.Path(file_okay=False, path_type=Path), default=Path("./out"))
@click.option(
    "--format", "formats", multiple=True, type=click.Choice(["json", "html"]),
    default=("json", "html"), help="Emit formats.",
)
@click.option("--max-depth", type=int, default=None)
@click.option(
    "--include-types", multiple=True, type=click.Choice(_ALL_TYPES), default=(),
    help="Restrict lineage to these object types. Empty = all.",
)
@click.pass_obj
def lineage(
    container: Container,
    object_name: str,
    dacpac_path: Path | None,
    connection_name: str | None,
    output_dir: Path,
    formats: tuple[str, ...],
    max_depth: int | None,
    include_types: tuple[str, ...],
) -> None:
    """Build object-to-base-table column lineage."""
    if bool(dacpac_path) == bool(connection_name):
        click.echo("error: exactly one of --dacpac / --connection is required", err=True)
        sys.exit(2)

    try:
        root_ref = ObjectRef.parse(object_name)
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

    req = BuildLineageRequest(
        root_object=root_ref,
        source=source,
        output_dir=output_dir,
        emit_formats=frozenset(formats),
        max_depth=max_depth,
        include_object_types=(
            frozenset(ObjectType(t) for t in include_types)
            if include_types
            else frozenset(t for t in ObjectType if t is not ObjectType.UNKNOWN)
        ),
    )

    try:
        use_case = build_lineage_use_case(container.settings)
    except Exception as e:  # noqa: BLE001
        click.echo(f"error: CLR / assembly initialisation failed: {e}", err=True)
        sys.exit(4)

    try:
        result = use_case.execute(req)
    except UnresolvableObject as e:
        click.echo(f"error: {e}", err=True)
        sys.exit(3)
    except Exception as e:  # noqa: BLE001
        log.exception("lineage failed", err=str(e))
        click.echo(f"error: {type(e).__name__}: {e}", err=True)
        sys.exit(1)

    click.echo(f"lineage built: {len(result.graph.nodes)} nodes, {len(result.graph.edges)} edges")
    for kind, path in result.artifacts.items():
        click.echo(f"  {kind}: {path}")
    for w in result.warnings:
        click.echo(f"  warning: {w}", err=True)
