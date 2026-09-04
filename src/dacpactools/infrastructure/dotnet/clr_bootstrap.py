from __future__ import annotations

import json
import os
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from dacpactools.infrastructure.dotnet.assembly_loader import ResolvedAssemblies, resolve_all
from dacpactools.presentation.logging_config import get_logger
from dacpactools.presentation.settings import Settings

log = get_logger("clr_bootstrap")

Runtime = Literal["coreclr", "netfx"]

_STATE: dict[str, object] = {"loaded": False, "runtime": None, "resolved": None}


@dataclass(frozen=True)
class RuntimeInfo:
    runtime: Runtime
    resolved: ResolvedAssemblies


def _write_coreclr_runtimeconfig() -> Path:
    """Minimal runtimeconfig.json so pythonnet can host CoreCLR."""
    tmp = Path(tempfile.gettempdir()) / "dacpactools_runtimeconfig.json"
    if tmp.exists():
        return tmp
    tmp.write_text(
        json.dumps(
            {
                "runtimeOptions": {
                    "tfm": "net8.0",
                    "framework": {"name": "Microsoft.NETCore.App", "version": "8.0.0"},
                    "rollForward": "LatestMinor",
                }
            }
        ),
        encoding="utf-8",
    )
    return tmp


def initialize(settings: Settings) -> RuntimeInfo:
    """Idempotent CLR initialisation. Must run before any `import clr` in the process."""
    if _STATE["loaded"]:
        return RuntimeInfo(runtime=_STATE["runtime"], resolved=_STATE["resolved"])  # type: ignore[arg-type]

    resolved = resolve_all(settings)
    rt = resolved.runtime
    from pythonnet import load  # noqa: PLC0415

    if rt == "coreclr":
        cfg = _write_coreclr_runtimeconfig()
        load("coreclr", runtime_config=str(cfg))
    else:
        load("netfx")
    log.info("clr loaded", runtime=rt)

    import clr  # noqa: PLC0415  (pythonnet-side import)
    import System  # noqa: F401, PLC0415
    from System.Reflection import Assembly  # noqa: PLC0415

    added_dirs: set[str] = set()

    def _add_dir(parent: Path) -> None:
        s = str(parent.resolve())
        if s in added_dirs:
            return
        os.add_dll_directory(s)  # type: ignore[attr-defined]
        if s not in sys.path:
            sys.path.insert(0, s)
        added_dirs.add(s)

    def _add_ref(name: str) -> None:
        try:
            clr.AddReference(name)
            log.debug("assembly reference added", name=name)
        except Exception as e:  # noqa: BLE001
            log.warning("assembly reference skipped", name=name, err=str(e))

    def _load_dir_all_dlls(anchor: Path) -> None:
        parent = anchor.resolve().parent
        _add_dir(parent)
        for dll in parent.glob("*.dll"):
            _add_ref(dll.stem)

    _load_dir_all_dlls(resolved.dacfx)
    _load_dir_all_dlls(resolved.scriptdom)
    if resolved.sqlclient is not None:
        _load_dir_all_dlls(resolved.sqlclient)

    if rt == "netfx":
        _register_assembly_resolve(resolved)

    _STATE["loaded"] = True
    _STATE["runtime"] = rt
    _STATE["resolved"] = resolved
    return RuntimeInfo(runtime=rt, resolved=resolved)


def _register_assembly_resolve(resolved: ResolvedAssemblies) -> None:
    """Redirect transitive net472 dependencies to the same nuget cache root."""
    import System  # noqa: PLC0415
    from System import AppDomain  # noqa: PLC0415
    from System.Reflection import Assembly  # noqa: PLC0415

    search_dirs = {
        Path(str(resolved.dacfx)).parent,
        Path(str(resolved.scriptdom)).parent,
    }
    if resolved.sqlclient is not None:
        search_dirs.add(Path(str(resolved.sqlclient)).parent)

    def handler(sender: object, args: object) -> object:
        name = args.Name.split(",")[0]  # type: ignore[attr-defined]
        for d in search_dirs:
            for candidate in (d / f"{name}.dll", d / "runtimes" / "win" / "lib" / "net472" / f"{name}.dll"):
                if candidate.exists():
                    return Assembly.LoadFrom(str(candidate))
        return None

    AppDomain.CurrentDomain.AssemblyResolve += handler  # type: ignore[operator]


def is_loaded() -> bool:
    return bool(_STATE["loaded"])


def current_runtime() -> Runtime | None:
    return _STATE["runtime"]  # type: ignore[return-value]
