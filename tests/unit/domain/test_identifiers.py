from __future__ import annotations

import pytest

from dacpactools.domain.enums import ObjectType
from dacpactools.domain.errors import InvalidObjectName
from dacpactools.domain.identifiers import ColumnRef, ObjectRef


@pytest.mark.parametrize(
    "raw, expected_db, expected_schema, expected_name",
    [
        ("[dbo].[usp_LoadFact]", None, "dbo", "usp_LoadFact"),
        ("dbo.usp_LoadFact", None, "dbo", "usp_LoadFact"),
        ("usp_LoadFact", None, "dbo", "usp_LoadFact"),
        ("[warehouse].[dbo].[usp_LoadFact]", "warehouse", "dbo", "usp_LoadFact"),
        ("warehouse.dbo.usp_LoadFact", "warehouse", "dbo", "usp_LoadFact"),
        ("[schema with space].[name.with.dot]", None, "schema with space", "name.with.dot"),
    ],
)
def test_parse(raw: str, expected_db: str | None, expected_schema: str, expected_name: str) -> None:
    ref = ObjectRef.parse(raw)
    assert ref.database == expected_db
    assert ref.schema_name == expected_schema
    assert ref.name == expected_name


def test_parse_invalid() -> None:
    with pytest.raises(InvalidObjectName):
        ObjectRef.parse("")
    with pytest.raises(InvalidObjectName):
        ObjectRef.parse("a.b.c.d")


def test_frozen_hashable() -> None:
    ref = ObjectRef.parse("dbo.T", object_type=ObjectType.TABLE)
    with pytest.raises(Exception):
        ref.name = "other"  # type: ignore[misc]
    d = {ref: 1}
    assert d[ref] == 1


def test_key_case_insensitive() -> None:
    a = ObjectRef.parse("[Dbo].[T1]")
    b = ObjectRef.parse("dbo.t1")
    assert a.key() == b.key()


def test_qualified_and_column() -> None:
    ref = ObjectRef.parse("dbo.T", object_type=ObjectType.TABLE)
    assert ref.qualified() == "[dbo].[T]"
    col = ColumnRef(owner=ref, column_name="c1")
    assert col.qualified() == "[dbo].[T].[c1]"
