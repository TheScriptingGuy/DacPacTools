from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from dacpactools.infrastructure.dotnet import path_probes
from dacpactools.infrastructure.dotnet.nuget_fetcher import NugetFetcher
from dacpactools.presentation.logging_config import get_logger
from dacpactools.presentation.settings import Settings

log = get_logger("assembly_loader")

Runtime = Literal["coreclr", "netfx"]

_DACFX_ID = "Microsoft.SqlServer.DacFx"
_SCRIPTDOM_ID = "Microsoft.SqlServer.TransactSql.ScriptDom"
_SQLCLIENT_ID = "Microsoft.Data.SqlClient"

_DACFX_DLL = "Microsoft.SqlServer.Dac.dll"
_SCRIPTDOM_DLL = "Microsoft.SqlServer.TransactSql.ScriptDom.dll"
_SQLCLIENT_DLL = "Microsoft.Data.SqlClient.dll"


@dataclass(frozen=True)
class ResolvedAssemblies:
    runtime: Runtime
    dacfx: Path
    scriptdom: Path
    sqlclient: Path | None
    strategy: dict[str, str]


def autodetect_runtime(settings: Settings) -> Runtime:
    """Rules: env override > net8 nuget cache > .NET 8 Desktop Runtime > SSDT > netfx."""
    if settings.dotnet_runtime in ("coreclr", "netfx"):
        return settings.dotnet_runtime  # type: ignore[return-value]
    if path_probes.cached_nuget_dll(settings.nuget_cache_dir, _DACFX_ID, "net8.0", _DACFX_DLL):
        return "coreclr"
    if path_probes.has_dotnet_8():
        return "coreclr"
    if path_probes.probe_ssdt_dacfx() or path_probes.probe_sqlserver_dac_bin():
        return "netfx"
    return "netfx"


def _tfm(runtime: Runtime) -> Literal["net8.0", "net472"]:
    return "net8.0" if runtime == "coreclr" else "net472"


def _resolve_one(
    *,
    hint: Path | None,
    package_id: str,
    dll_name: str,
    runtime: Runtime,
    settings: Settings,
    fetcher: NugetFetcher,
    version: str | None,
    ssdt_probes: list[Path] | None = None,
    dotnet_tool_probes: list[Path] | None = None,
) -> tuple[Path, str]:
    if hint and hint.exists():
        return hint, "hint"
    # sqlpackage global tool is preferred on coreclr because it bundles all transitive deps.
    if runtime == "coreclr" and dotnet_tool_probes:
        for p in dotnet_tool_probes:
            if p.exists():
                return p, "dotnet-tool"
    tfm = _tfm(runtime)
    cached = path_probes.cached_nuget_dll(settings.nuget_cache_dir, package_id, tfm, dll_name)
    if cached:
        return cached, "nuget-cache"
    if runtime == "netfx" and ssdt_probes:
        for p in ssdt_probes:
            if p.exists():
                return p, "ssdt"
    pkg_root = fetcher.ensure(package_id, version=version)
    cand = pkg_root / "lib" / tfm / dll_name
    if not cand.exists():
        raise FileNotFoundError(
            f"NuGet package {package_id} does not contain lib/{tfm}/{dll_name} (looked in {cand})"
        )
    return cand, "nuget-fetch"


def resolve_all(settings: Settings, runtime: Runtime | None = None) -> ResolvedAssemblies:
    rt = runtime or autodetect_runtime(settings)
    with NugetFetcher(settings.nuget_source, settings.nuget_cache_dir) as fetcher:
        dacfx, s1 = _resolve_one(
            hint=settings.dacfx_dll_hint,
            package_id=_DACFX_ID,
            dll_name=_DACFX_DLL,
            runtime=rt,
            settings=settings,
            fetcher=fetcher,
            version=settings.dacfx_version,
            ssdt_probes=path_probes.probe_ssdt_dacfx() + path_probes.probe_sqlserver_dac_bin(),
            dotnet_tool_probes=path_probes.probe_dotnet_tool_sqlpackage(_DACFX_DLL),
        )
        scriptdom, s2 = _resolve_one(
            hint=settings.scriptdom_dll_hint,
            package_id=_SCRIPTDOM_ID,
            dll_name=_SCRIPTDOM_DLL,
            runtime=rt,
            settings=settings,
            fetcher=fetcher,
            version=settings.scriptdom_version,
            ssdt_probes=path_probes.probe_ssdt_scriptdom(),
            dotnet_tool_probes=path_probes.probe_dotnet_tool_sqlpackage(_SCRIPTDOM_DLL),
        )
        try:
            sqlclient, s3 = _resolve_one(
                hint=settings.sqlclient_dll_hint,
                package_id=_SQLCLIENT_ID,
                dll_name=_SQLCLIENT_DLL,
                runtime=rt,
                settings=settings,
                fetcher=fetcher,
                version=settings.sqlclient_version,
                dotnet_tool_probes=path_probes.probe_dotnet_tool_sqlpackage(_SQLCLIENT_DLL),
            )
        except FileNotFoundError as e:
            log.warning("sqlclient not resolved (Azure live path disabled)", err=str(e))
            sqlclient = None
            s3 = "unresolved"
    return ResolvedAssemblies(
        runtime=rt,
        dacfx=dacfx,
        scriptdom=scriptdom,
        sqlclient=sqlclient,
        strategy={"dacfx": s1, "scriptdom": s2, "sqlclient": s3},
    )
