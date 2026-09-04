from __future__ import annotations

from pathlib import Path

import pytest

from dacpactools.infrastructure.dotnet import assembly_loader, path_probes
from dacpactools.presentation.settings import Settings


@pytest.fixture
def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    monkeypatch.delenv("DOTNET_RUNTIME", raising=False)
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    return s.model_copy(update={"nuget_cache_dir": tmp_path / "nuget"})


def test_autodetect_env_override(monkeypatch: pytest.MonkeyPatch, settings: Settings) -> None:
    s = settings.model_copy(update={"dotnet_runtime": "netfx"})
    assert assembly_loader.autodetect_runtime(s) == "netfx"
    s = settings.model_copy(update={"dotnet_runtime": "coreclr"})
    assert assembly_loader.autodetect_runtime(s) == "coreclr"


def test_autodetect_uses_cached_net8_when_present(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = settings.nuget_cache_dir / "microsoft.sqlserver.dacfx" / "162.0.0" / "lib" / "net8.0"
    cache.mkdir(parents=True)
    (cache / "Microsoft.SqlServer.Dac.dll").write_bytes(b"stub")

    monkeypatch.setattr(path_probes, "has_dotnet_8", lambda: False)
    monkeypatch.setattr(path_probes, "probe_ssdt_dacfx", lambda: [])
    monkeypatch.setattr(path_probes, "probe_sqlserver_dac_bin", lambda: [])

    assert assembly_loader.autodetect_runtime(settings) == "coreclr"


def test_autodetect_falls_back_to_netfx_when_ssdt_present(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(path_probes, "has_dotnet_8", lambda: False)
    monkeypatch.setattr(path_probes, "probe_ssdt_dacfx", lambda: [Path("C:/fake/dac.dll")])
    monkeypatch.setattr(path_probes, "probe_sqlserver_dac_bin", lambda: [])
    assert assembly_loader.autodetect_runtime(settings) == "netfx"


def test_autodetect_default_netfx_when_nothing(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(path_probes, "has_dotnet_8", lambda: False)
    monkeypatch.setattr(path_probes, "probe_ssdt_dacfx", lambda: [])
    monkeypatch.setattr(path_probes, "probe_sqlserver_dac_bin", lambda: [])
    assert assembly_loader.autodetect_runtime(settings) == "netfx"


def test_resolve_uses_hint(monkeypatch: pytest.MonkeyPatch, settings: Settings, tmp_path: Path) -> None:
    """Explicit DLL hints skip nuget entirely."""
    dacfx = tmp_path / "dacfx.dll"
    scriptdom = tmp_path / "scriptdom.dll"
    sqlclient = tmp_path / "sqlclient.dll"
    for p in (dacfx, scriptdom, sqlclient):
        p.write_bytes(b"stub")
    s = settings.model_copy(
        update={
            "dotnet_runtime": "coreclr",
            "dacfx_dll_hint": dacfx,
            "scriptdom_dll_hint": scriptdom,
            "sqlclient_dll_hint": sqlclient,
        }
    )
    resolved = assembly_loader.resolve_all(s)
    assert resolved.dacfx == dacfx
    assert resolved.scriptdom == scriptdom
    assert resolved.sqlclient == sqlclient
    assert resolved.strategy == {"dacfx": "hint", "scriptdom": "hint", "sqlclient": "hint"}


def test_cached_nuget_dll_finder(tmp_path: Path) -> None:
    lib = tmp_path / "microsoft.sqlserver.dacfx" / "162.5.0" / "lib" / "net472"
    lib.mkdir(parents=True)
    dll = lib / "Microsoft.SqlServer.Dac.dll"
    dll.write_bytes(b"stub")
    found = path_probes.cached_nuget_dll(tmp_path, "Microsoft.SqlServer.DacFx", "net472", "Microsoft.SqlServer.Dac.dll")
    assert found == dll
