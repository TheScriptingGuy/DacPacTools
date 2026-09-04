from __future__ import annotations

import sys

import click

from dacpactools import __version__
from dacpactools.presentation.commands.column_usage import column_usage as column_usage_cmd
from dacpactools.presentation.commands.joins import joins as joins_cmd
from dacpactools.presentation.commands.lineage import lineage as lineage_cmd
from dacpactools.presentation.commands.orchestrator import orchestrator as orchestrator_cmd
from dacpactools.presentation.commands.tools import tools_group
from dacpactools.presentation.composition import Container, build_container
from dacpactools.presentation.settings import DotnetRuntime, Settings


@click.group()
@click.version_option(__version__, prog_name="dacpactools")
@click.option(
    "--runtime",
    type=click.Choice(["auto", "coreclr", "netfx"]),
    default=None,
    help="Force CLR host runtime. Overrides DOTNET_RUNTIME env.",
)
@click.option("--log-level", default=None, help="DEBUG | INFO | WARNING | ERROR.")
@click.option("--log-json", is_flag=True, default=None, help="Emit JSON log lines.")
@click.pass_context
def main(
    ctx: click.Context,
    runtime: DotnetRuntime | None,
    log_level: str | None,
    log_json: bool | None,
) -> None:
    """DacPac + Azure SQL object-to-base-table column lineage."""
    settings = Settings()
    overrides: dict[str, object] = {}
    if runtime is not None:
        overrides["dotnet_runtime"] = runtime
    if log_level is not None:
        overrides["log_level"] = log_level
    if log_json is not None:
        overrides["log_json"] = log_json
    if overrides:
        settings = settings.model_copy(update=overrides)
    ctx.obj = build_container(settings)


@main.command()
@click.option("--probe-clr", is_flag=True, help="Attempt CLR load + list assembly versions.")
@click.pass_obj
def doctor(container: Container, probe_clr: bool) -> None:
    """Verify runtime dependencies (CLR host, DacFx, credentials)."""
    from dacpactools.infrastructure.dotnet import path_probes
    from dacpactools.infrastructure.dotnet.assembly_loader import autodetect_runtime

    s = container.settings
    click.echo("dacpactools doctor")
    click.echo(f"  version           : {__version__}")
    click.echo(f"  python            : {sys.version.split()[0]}")
    click.echo(f"  dotnet_runtime    : {s.dotnet_runtime} (autodetect -> {autodetect_runtime(s)})")
    click.echo(f"  .NET 8 present    : {path_probes.has_dotnet_8()}")
    click.echo(f"  sqlpackage.exe    : {path_probes.find_sqlpackage_exe() or '(not found)'}")
    click.echo(f"  ssdt DacFx        : {len(path_probes.probe_ssdt_dacfx())} match(es)")
    click.echo(f"  SqlServer DAC bin : {len(path_probes.probe_sqlserver_dac_bin())} match(es)")
    click.echo(f"  nuget_source      : {s.nuget_source}")
    click.echo(f"  nuget_cache_dir   : {s.nuget_cache_dir}")
    click.echo(f"  output_dir        : {s.output_dir}")
    click.echo(f"  db connections    : {', '.join(sorted(s.db)) or '(none)'}")
    click.echo(f"  dacfx_dll_hint    : {s.dacfx_dll_hint or '(auto-probe)'}")
    click.echo(f"  scriptdom_dll_hint: {s.scriptdom_dll_hint or '(auto-probe)'}")
    click.echo(f"  sqlclient_dll_hint: {s.sqlclient_dll_hint or '(auto-probe)'}")

    if probe_clr:
        click.echo("")
        click.echo("attempting CLR load ...")
        try:
            from dacpactools.infrastructure.dotnet.clr_bootstrap import initialize

            info = initialize(s)
            click.echo(f"  loaded runtime    : {info.runtime}")
            click.echo(f"  dacfx             : {info.resolved.dacfx} [{info.resolved.strategy['dacfx']}]")
            click.echo(f"  scriptdom         : {info.resolved.scriptdom} [{info.resolved.strategy['scriptdom']}]")
            click.echo(f"  sqlclient         : {info.resolved.sqlclient or '(none)'} [{info.resolved.strategy['sqlclient']}]")
        except Exception as e:  # noqa: BLE001
            click.echo(f"  CLR load FAILED   : {type(e).__name__}: {e}", err=True)


main.add_command(tools_group)
main.add_command(lineage_cmd)
main.add_command(orchestrator_cmd)
main.add_command(joins_cmd)
main.add_command(column_usage_cmd)


if __name__ == "__main__":
    main()
