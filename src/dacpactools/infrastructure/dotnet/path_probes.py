from __future__ import annotations

import glob
import os
from pathlib import Path
from typing import Literal

TargetFramework = Literal["net8.0", "net472"]


def _pf() -> list[Path]:
    out = []
    for env in ("ProgramFiles", "ProgramFiles(x86)"):
        val = os.environ.get(env)
        if val:
            out.append(Path(val))
    return out


def _user_profile() -> Path:
    return Path(os.environ.get("USERPROFILE") or Path.home())


def _local_appdata() -> Path:
    v = os.environ.get("LOCALAPPDATA")
    return Path(v) if v else _user_profile() / "AppData" / "Local"


def probe_ssdt_dacfx() -> list[Path]:
    """SSDT / VS-bundled DacFx (net472)."""
    matches: list[Path] = []
    for pf in _pf():
        pattern = str(pf / "Microsoft Visual Studio" / "*" / "*" / "Common7" / "IDE" / "Extensions" / "Microsoft" / "SQLDB" / "DAC" / "*" / "Microsoft.SqlServer.Dac.dll")
        matches.extend(Path(p) for p in glob.glob(pattern))
    return sorted(matches, reverse=True)


def probe_ssdt_scriptdom() -> list[Path]:
    matches: list[Path] = []
    for pf in _pf():
        pattern = str(pf / "Microsoft Visual Studio" / "*" / "*" / "Common7" / "IDE" / "Extensions" / "Microsoft" / "SQLDB" / "DAC" / "*" / "Microsoft.SqlServer.TransactSql.ScriptDom.dll")
        matches.extend(Path(p) for p in glob.glob(pattern))
    return sorted(matches, reverse=True)


def probe_sqlserver_dac_bin() -> list[Path]:
    """Legacy SQL Server DAC install (net472)."""
    matches: list[Path] = []
    for pf in _pf():
        pattern = str(pf / "Microsoft SQL Server" / "*" / "DAC" / "bin" / "Microsoft.SqlServer.Dac.dll")
        matches.extend(Path(p) for p in glob.glob(pattern))
    return sorted(matches, reverse=True)


def probe_dotnet_tool_sqlpackage(dll: str = "Microsoft.SqlServer.Dac.dll") -> list[Path]:
    """sqlpackage installed as a dotnet global tool (net8.0). Bundles all transitive deps."""
    root = _user_profile() / ".dotnet" / "tools" / ".store" / "microsoft.sqlpackage"
    if not root.exists():
        return []
    patterns = [
        f"*/microsoft.sqlpackage/*/tools/net8.0/any/{dll}",
        f"*/microsoft.sqlpackage/*/tools/net8.0/{dll}",
        f"*/microsoft.sqlpackage/*/tools/{dll}",
    ]
    matches: list[Path] = []
    for pat in patterns:
        matches.extend(root.glob(pat))
    return sorted(matches, reverse=True)


def probe_standalone_sqlpackage() -> list[Path]:
    """Standalone sqlpackage extraction under LOCALAPPDATA."""
    root = _local_appdata() / "Microsoft" / "SqlPackage"
    if not root.exists():
        return []
    return sorted(root.rglob("Microsoft.SqlServer.Dac.dll"), reverse=True)


def probe_dotnet_runtime_root() -> Path | None:
    for pf in _pf():
        cand = pf / "dotnet" / "shared" / "Microsoft.NETCore.App"
        if cand.exists():
            return cand
    return None


def has_dotnet_8() -> bool:
    root = probe_dotnet_runtime_root()
    if root is None:
        return False
    return any(p.name.startswith("8.") for p in root.iterdir() if p.is_dir())


def find_sqlpackage_exe() -> Path | None:
    for name in ("sqlpackage.exe", "SqlPackage.exe"):
        for path_entry in os.environ.get("PATH", "").split(os.pathsep):
            cand = Path(path_entry) / name
            if cand.exists():
                return cand
    tool = _user_profile() / ".dotnet" / "tools" / "sqlpackage.exe"
    if tool.exists():
        return tool
    return None


def cached_nuget_dll(cache_dir: Path, package_id: str, tfm: TargetFramework, dll_name: str) -> Path | None:
    root = cache_dir / package_id.lower()
    if not root.exists():
        return None
    for version_dir in sorted(root.iterdir(), reverse=True):
        cand = version_dir / "lib" / tfm / dll_name
        if cand.exists():
            return cand
    return None
