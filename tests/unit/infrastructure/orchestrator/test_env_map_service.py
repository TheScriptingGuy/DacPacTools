from __future__ import annotations

import json
from pathlib import Path

import pytest

from dacpactools.application.dto.lineage_request import (
    DacPacSource,
    LiveConnectionSource,
)
from dacpactools.application.ports._handles import LiveConnection
from dacpactools.infrastructure.orchestrator.env_map_service import JsonEnvMapService


def _write_map(tmp_path: Path, mapping: dict[str, object]) -> Path:
    p = tmp_path / "map.json"
    p.write_text(json.dumps({"version": 1, "mappings": mapping}))
    return p


def test_resolves_dacpac_relative_to_map_file(tmp_path: Path) -> None:
    p = _write_map(tmp_path, {"foo": {"kind": "dacpac", "path": "./data/warehouse.dacpac"}})
    svc = JsonEnvMapService(connection_resolver=lambda n: None)
    svc.load(p)
    src = svc.resolve("foo")
    assert isinstance(src, DacPacSource)
    assert src.path == (tmp_path / "data" / "warehouse.dacpac").resolve()


def test_resolves_absolute_dacpac_path_unchanged(tmp_path: Path) -> None:
    abs_path = (tmp_path / "abs.dacpac").resolve()
    p = _write_map(tmp_path, {"foo": {"kind": "dacpac", "path": str(abs_path)}})
    svc = JsonEnvMapService(connection_resolver=lambda n: None)
    svc.load(p)
    src = svc.resolve("foo")
    assert isinstance(src, DacPacSource)
    assert src.path == abs_path


def test_resolves_connection_via_callback(tmp_path: Path) -> None:
    live = LiveConnection(server="s.example.com", database="db")

    def resolver(n: str) -> LiveConnection | None:
        return live if n == "warehouse" else None

    p = _write_map(tmp_path, {"mssql_default": {"kind": "connection", "name": "warehouse"}})
    svc = JsonEnvMapService(connection_resolver=resolver)
    svc.load(p)
    src = svc.resolve("mssql_default")
    assert isinstance(src, LiveConnectionSource)
    assert src.connection is live


def test_connection_unresolvable_returns_none(tmp_path: Path) -> None:
    p = _write_map(tmp_path, {"nope": {"kind": "connection", "name": "missing"}})
    svc = JsonEnvMapService(connection_resolver=lambda n: None)
    svc.load(p)
    assert svc.resolve("nope") is None


def test_unknown_env_ref_returns_none(tmp_path: Path) -> None:
    p = _write_map(tmp_path, {"a": {"kind": "dacpac", "path": "./x.dacpac"}})
    svc = JsonEnvMapService(connection_resolver=lambda n: None)
    svc.load(p)
    assert svc.resolve("nothere") is None


def test_env_ref_lookup_case_insensitive(tmp_path: Path) -> None:
    p = _write_map(tmp_path, {"AzureSqlDatabase1": {"kind": "dacpac", "path": "./x.dacpac"}})
    svc = JsonEnvMapService(connection_resolver=lambda n: None)
    svc.load(p)
    assert isinstance(svc.resolve("azuresqldatabase1"), DacPacSource)


def test_rejects_bad_version(tmp_path: Path) -> None:
    p = tmp_path / "map.json"
    p.write_text(json.dumps({"version": 2, "mappings": {}}))
    svc = JsonEnvMapService(connection_resolver=lambda n: None)
    with pytest.raises(ValueError, match="unsupported version"):
        svc.load(p)


def test_rejects_unknown_kind(tmp_path: Path) -> None:
    p = _write_map(tmp_path, {"a": {"kind": "s3", "bucket": "x"}})
    svc = JsonEnvMapService(connection_resolver=lambda n: None)
    with pytest.raises(ValueError, match="unsupported kind"):
        svc.load(p)


def test_known_refs_returns_sorted_lowercase(tmp_path: Path) -> None:
    p = _write_map(
        tmp_path,
        {
            "AzureSqlDatabase1": {"kind": "dacpac", "path": "./a.dacpac"},
            "Beta": {"kind": "dacpac", "path": "./b.dacpac"},
        },
    )
    svc = JsonEnvMapService(connection_resolver=lambda n: None)
    svc.load(p)
    assert svc.known_refs() == ("azuresqldatabase1", "beta")
