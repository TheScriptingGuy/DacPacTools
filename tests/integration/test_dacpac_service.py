from __future__ import annotations

from typing import Any

import pytest

from dacpactools.domain.enums import ObjectType
from dacpactools.domain.identifiers import ObjectRef
from dacpactools.infrastructure.dacpac.dacpac_service import DacFxDacPacService


@pytest.mark.dotnet
def test_traverse_simple_view_and_proc(tsql_model: Any) -> None:
    tsql_model.AddObjects("CREATE TABLE dbo.Fact (id int, amt decimal(10,2))")
    tsql_model.AddObjects("CREATE VIEW dbo.v_Fact AS SELECT id, amt FROM dbo.Fact")
    tsql_model.AddObjects("CREATE PROCEDURE dbo.usp_Load AS SELECT id FROM dbo.v_Fact")

    svc = DacFxDacPacService()
    handle = svc.load_from_model(tsql_model)

    root = ObjectRef.parse("dbo.usp_Load", object_type=ObjectType.PROCEDURE)
    found = svc.find_object(handle, root)
    assert found is not None
    assert found.ref.object_type is ObjectType.PROCEDURE

    referenced = list(svc.get_referenced(found))
    ref_names = {r.ref.name.lower() for r in referenced}
    assert "v_fact" in ref_names

    body = svc.get_body_script(found)
    assert body is not None
    assert "SELECT id FROM dbo.v_Fact" in body


@pytest.mark.dotnet
def test_get_columns_on_table(tsql_model: Any) -> None:
    tsql_model.AddObjects("CREATE TABLE dbo.T (a int, b nvarchar(50), c date)")
    svc = DacFxDacPacService()
    handle = svc.load_from_model(tsql_model)
    tbl = svc.find_object(handle, ObjectRef.parse("dbo.T", object_type=ObjectType.TABLE))
    assert tbl is not None
    cols = svc.get_columns(tbl)
    assert [c.column_name for c in cols] == ["a", "b", "c"]
    assert [c.ordinal for c in cols] == [0, 1, 2]


@pytest.mark.dotnet
def test_synonym_resolution(tsql_model: Any) -> None:
    tsql_model.AddObjects("CREATE TABLE dbo.Src (id int)")
    tsql_model.AddObjects("CREATE SYNONYM dbo.SynSrc FOR dbo.Src")
    svc = DacFxDacPacService()
    handle = svc.load_from_model(tsql_model)
    syn = svc.find_object(handle, ObjectRef.parse("dbo.SynSrc", object_type=ObjectType.SYNONYM))
    assert syn is not None
    target = svc.resolve_synonym_target(syn)
    assert target is not None
    assert target.name.lower() == "src"


@pytest.mark.dotnet
def test_missing_object_returns_none(tsql_model: Any) -> None:
    svc = DacFxDacPacService()
    handle = svc.load_from_model(tsql_model)
    assert svc.find_object(handle, ObjectRef.parse("dbo.Missing")) is None
