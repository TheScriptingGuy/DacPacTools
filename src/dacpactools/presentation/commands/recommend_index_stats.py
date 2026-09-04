from __future__ import annotations

import sys
from pathlib import Path

import click

from dacpactools.application.dto.lineage_request import DacPacSource, LiveConnectionSource
from dacpactools.application.dto.recommend_index_stats import RecommendIndexStatsRequest
from dacpactools.application.ports._handles import LiveConnection
from dacpactools.domain.enums import SqlTargetPlatform
from dacpactools.presentation.composition import (
    Container,
    build_recommend_index_stats_use_case,
)
from dacpactools.presentation.logging_config import get_logger

log = get_logger("cli.recommend_index_stats")

_TARGET_CHOICES = [
    p.value
    for p in SqlTargetPlatform
    if p is not SqlTargetPlatform.UNKNOWN
]


@click.command("recommend-index-stats")
@click.option(
    "--schemas",
    "schemas_csv",
    required=True,
    help="Comma-separated schema list (case-insensitive).",
)
@click.option(
    "--dacpac",
    "dacpac_path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Source-of-truth .dacpac file (offline mode).",
)
@click.option(
    "--connection",
    "connection_name",
    default=None,
    help="Named DB connection (env DB__<name>__server/database). Enables auto-detection + existing-object dedup.",
)
@click.option(
    "--target",
    "target_platform_str",
    default=None,
    type=click.Choice(_TARGET_CHOICES, case_sensitive=False),
    help="Target SQL platform. Required when --connection is not supplied.",
)
@click.option(
    "--out",
    "output_dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=Path("./out"),
    help="Output directory for .sql + .md artefacts.",
)
@click.pass_obj
def recommend_index_stats(
    container: Container,
    schemas_csv: str,
    dacpac_path: Path | None,
    connection_name: str | None,
    target_platform_str: str | None,
    output_dir: Path,
) -> None:
    """Recommend indexes and statistics per target SQL platform based on how each
    column is used across the schema's consumers (views/procs/UDFs)."""
    if bool(dacpac_path) == bool(connection_name):
        click.echo(
            "error: exactly one of --dacpac / --connection is required",
            err=True,
        )
        sys.exit(2)

    parts = [s.strip() for s in schemas_csv.split(",") if s.strip()]
    if not parts:
        click.echo("error: --schemas is empty after parsing", err=True)
        sys.exit(2)
    schemas = frozenset(parts)

    connection: LiveConnection | None = None
    if connection_name:
        try:
            db = container.settings.resolve_connection(connection_name)
        except KeyError as e:
            click.echo(f"error: {e}", err=True)
            sys.exit(2)
        connection = LiveConnection(server=db.server, database=db.database)
        source = LiveConnectionSource(connection=connection)
    else:
        assert dacpac_path is not None
        if not dacpac_path.exists():
            click.echo(f"error: dacpac not found: {dacpac_path}", err=True)
            sys.exit(2)
        if target_platform_str is None:
            click.echo(
                "error: --target is required when --connection is not supplied",
                err=True,
            )
            sys.exit(2)
        source = DacPacSource(path=dacpac_path)

    target_override: SqlTargetPlatform | None = None
    if target_platform_str:
        target_override = SqlTargetPlatform(target_platform_str.upper())

    try:
        use_case = build_recommend_index_stats_use_case(container.settings)
    except Exception as e:  # noqa: BLE001
        click.echo(f"error: CLR / assembly initialisation failed: {e}", err=True)
        sys.exit(4)

    try:
        result = use_case.execute(
            RecommendIndexStatsRequest(
                schemas=schemas,
                source=source,
                output_dir=output_dir,
                connection=connection,
                target_override=target_override,
            )
        )
    except Exception as e:
        log.exception("recommend-index-stats failed", err=str(e))
        click.echo(f"error: {type(e).__name__}: {e}", err=True)
        sys.exit(1)

    idx_count = sum(len(t.indexes) for t in result.tables)
    stat_count = sum(len(t.statistics) for t in result.tables)
    dist_count = sum(1 for t in result.tables if t.distribution is not None)
    click.echo(
        f"recommendations: {idx_count} indexes, {stat_count} statistics, "
        f"{dist_count} distributions on {len(result.tables)} table(s)"
    )
    for kind, path in result.artifacts.items():
        click.echo(f"  {kind}: {path}")
    click.echo(f"  platform: {result.target_platform.value}")
    for w in result.warnings:
        click.echo(f"  warning: {w}", err=True)
