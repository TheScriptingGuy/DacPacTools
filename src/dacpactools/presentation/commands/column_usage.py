from __future__ import annotations

import re
import sys
from pathlib import Path

import click

from dacpactools.application.dto.column_usage import FindColumnUsageRequest
from dacpactools.application.dto.lineage_request import (
    DacPacSource,
    LiveConnectionSource,
)
from dacpactools.application.ports._handles import LiveConnection
from dacpactools.domain.errors import DomainError
from dacpactools.domain.identifiers import ObjectRef
from dacpactools.presentation.composition import (
    Container,
    build_find_column_usage_use_case,
)
from dacpactools.presentation.logging_config import get_logger

log = get_logger("cli.column_usage")

_BRACKETED = re.compile(r"^\[?([^\]\.]+)\]?$")


@click.command("column-usage")
@click.option(
    "--column",
    "column_arg",
    default=None,
    help="Full column ref, e.g. [Schema].[Table].[Column]. "
    "Alternative: use --table + --column-name.",
)
@click.option(
    "--table",
    "table_arg",
    default=None,
    help="Owner table, e.g. [Schema].[Table]. Combine with --column-name.",
)
@click.option(
    "--column-name",
    "column_name_arg",
    default=None,
    help="Column name when --table is set (bracket-wrapping optional).",
)
@click.option(
    "--dacpac",
    "dacpac_path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
)
@click.option("--connection", "connection_name", default=None)
@click.option(
    "--out",
    "output_dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=Path("./out"),
)
@click.pass_obj
def column_usage(
    container: Container,
    column_arg: str | None,
    table_arg: str | None,
    column_name_arg: str | None,
    dacpac_path: Path | None,
    connection_name: str | None,
    output_dir: Path,
) -> None:
    """Find every view/proc/UDF that references a specific column, tagged by
    clause context (SELECT, JOIN ON, WHERE, GROUP BY, aggregate, CASE, ...)."""
    if bool(dacpac_path) == bool(connection_name):
        click.echo("error: exactly one of --dacpac / --connection is required", err=True)
        sys.exit(2)

    owner, col_name = _parse_target(column_arg, table_arg, column_name_arg)
    if owner is None or not col_name:
        click.echo(
            "error: specify either --column '[S].[T].[C]' OR --table + --column-name",
            err=True,
        )
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
        use_case = build_find_column_usage_use_case(container.settings)
    except Exception as e:  # noqa: BLE001
        click.echo(f"error: CLR / assembly initialisation failed: {e}", err=True)
        sys.exit(4)

    try:
        result = use_case.execute(
            FindColumnUsageRequest(
                target_owner=owner,
                target_column=col_name,
                source=source,
                output_dir=output_dir,
            )
        )
    except Exception as e:  # noqa: BLE001
        log.exception("column-usage scan failed", err=str(e))
        click.echo(f"error: {type(e).__name__}: {e}", err=True)
        sys.exit(1)

    tc = result.report.target_column
    click.echo(
        f"column usage {tc.owner.qualified()}.[{tc.column_name}]: "
        f"{len(result.report.citations)} citation(s)"
    )
    for kind, path in result.artifacts.items():
        click.echo(f"  {kind}: {path}")
    for w in result.report.warnings:
        click.echo(f"  warning: {w}", err=True)


def _parse_target(
    column_arg: str | None, table_arg: str | None, column_name_arg: str | None
) -> tuple[ObjectRef | None, str]:
    if column_arg:
        # Split on unbracketed dots: [S].[T].[C]  or  S.T.C
        parts = _split_dots(column_arg)
        if len(parts) not in (2, 3):
            raise click.BadParameter(
                "--column must be [Schema].[Table].[Column] or Schema.Table.Column"
            )
        if len(parts) == 2:
            table_ref_str = f"[dbo].[{parts[0]}]"
            col = parts[1]
        else:
            table_ref_str = f"[{parts[0]}].[{parts[1]}]"
            col = parts[2]
        try:
            owner = ObjectRef.parse(table_ref_str)
        except DomainError as e:
            raise click.BadParameter(str(e))
        return owner, col
    if table_arg and column_name_arg:
        try:
            owner = ObjectRef.parse(table_arg)
        except DomainError as e:
            raise click.BadParameter(str(e))
        m = _BRACKETED.match(column_name_arg.strip())
        col = m.group(1) if m else column_name_arg.strip()
        return owner, col
    return None, ""


def _split_dots(text: str) -> list[str]:
    """Split a dotted name honoring [bracket.quoting]."""
    out: list[str] = []
    buf: list[str] = []
    in_brackets = False
    for ch in text.strip():
        if ch == "[":
            in_brackets = True
            continue
        if ch == "]":
            in_brackets = False
            continue
        if ch == "." and not in_brackets:
            out.append("".join(buf).strip())
            buf = []
            continue
        buf.append(ch)
    if buf:
        out.append("".join(buf).strip())
    return [p for p in out if p]
