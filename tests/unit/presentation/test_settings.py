from __future__ import annotations

from pathlib import Path

import pytest

from dacpactools.presentation.settings import Settings


@pytest.mark.usefixtures("clean_env")
def test_defaults() -> None:
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.dotnet_runtime == "auto"
    assert s.nuget_source.startswith("https://")
    assert s.log_level == "INFO"
    assert s.log_json is False
    assert s.db == {}


@pytest.mark.usefixtures("clean_env")
def test_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOTNET_RUNTIME", "netfx")
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("DACFX_DLL_HINT", r"C:\dac\Microsoft.SqlServer.Dac.dll")
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.dotnet_runtime == "netfx"
    assert s.log_level == "DEBUG"
    assert s.dacfx_dll_hint == Path(r"C:\dac\Microsoft.SqlServer.Dac.dll")


@pytest.mark.usefixtures("clean_env")
def test_nested_db_connections(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DB__PROD__SERVER", "prod.database.windows.net")
    monkeypatch.setenv("DB__PROD__DATABASE", "warehouse")
    monkeypatch.setenv("DB__SYNAPSE__SERVER", "pool.sql.azuresynapse.net")
    monkeypatch.setenv("DB__SYNAPSE__DATABASE", "analytics")
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert set(s.db) == {"prod", "synapse"}
    conn = s.resolve_connection("PROD")
    assert conn.server == "prod.database.windows.net"
    assert conn.database == "warehouse"


@pytest.mark.usefixtures("clean_env")
def test_resolve_connection_missing() -> None:
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    with pytest.raises(KeyError, match="not configured"):
        s.resolve_connection("nope")


@pytest.mark.usefixtures("clean_env")
def test_cli_override_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOTNET_RUNTIME", "netfx")
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    s = s.model_copy(update={"dotnet_runtime": "coreclr"})
    assert s.dotnet_runtime == "coreclr"
