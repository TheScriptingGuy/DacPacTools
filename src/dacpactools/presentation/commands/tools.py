from __future__ import annotations

import click

from dacpactools.infrastructure.dotnet.assembly_loader import resolve_all
from dacpactools.presentation.composition import Container


@click.group("tools")
def tools_group() -> None:
    """Runtime toolchain management (assembly fetch, cache inspection)."""


@tools_group.command("install-dacfx")
@click.option(
    "--runtime",
    type=click.Choice(["auto", "coreclr", "netfx", "both"]),
    default="auto",
    help="Which TFM(s) to prefetch into the local NuGet cache.",
)
@click.pass_obj
def install_dacfx(container: Container, runtime: str) -> None:
    """Download DacFx + ScriptDom + Microsoft.Data.SqlClient into .tools/nuget/."""
    settings = container.settings
    runtimes: list[str] = []
    if runtime == "both":
        runtimes = ["coreclr", "netfx"]
    elif runtime == "auto":
        runtimes = [None]  # type: ignore[list-item]
    else:
        runtimes = [runtime]

    for rt in runtimes:
        click.echo(f"resolving assemblies (runtime={rt or 'auto'}) ...")
        resolved = resolve_all(settings, runtime=rt)  # type: ignore[arg-type]
        click.echo(f"  runtime  : {resolved.runtime}")
        click.echo(f"  dacfx    : {resolved.dacfx} [{resolved.strategy['dacfx']}]")
        click.echo(f"  scriptdom: {resolved.scriptdom} [{resolved.strategy['scriptdom']}]")
        if resolved.sqlclient:
            click.echo(f"  sqlclient: {resolved.sqlclient} [{resolved.strategy['sqlclient']}]")
        else:
            click.echo(f"  sqlclient: (unresolved) [{resolved.strategy['sqlclient']}]")
