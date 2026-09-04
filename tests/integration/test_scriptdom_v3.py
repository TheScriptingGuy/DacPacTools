from __future__ import annotations

import pytest

from dacpactools.application.ports._handles import ColumnResolver
from dacpactools.domain.enums import ObjectType
from dacpactools.domain.identifiers import ColumnRef, ObjectRef
from dacpactools.domain.lineage import LineageNode
from dacpactools.infrastructure.scriptdom.parser_service import ScriptDomParserService

pytestmark = pytest.mark.dotnet


def _table(name: str, cols: tuple[str, ...]) -> tuple[ObjectRef, tuple[ColumnRef, ...]]:
    ref = ObjectRef(database=None, schema="dbo", name=name, object_type=ObjectType.TABLE)
    return ref, tuple(ColumnRef(owner=ref, column_name=c, ordinal=i) for i, c in enumerate(cols))


def _proc_node(name: str) -> LineageNode:
    ref = ObjectRef(database=None, schema="dbo", name=name, object_type=ObjectType.PROCEDURE)
    return LineageNode(ref=ref)


def _resolver(*tables: tuple[ObjectRef, tuple[ColumnRef, ...]]) -> ColumnResolver:
    scope: dict[str, tuple] = {}
    for ref, cols in tables:
        scope[ref.name.lower()] = cols
        scope[f"{ref.schema_name.lower()}.{ref.name.lower()}"] = cols
    return ColumnResolver(scope=scope)


def test_insert_select_maps_by_ordinal(clr_initialized: None) -> None:
    tgt, tgt_cols = _table("Fact", ("id", "amt", "ts"))
    src, src_cols = _table("Stage", ("s_id", "s_amt", "s_ts"))
    node = _proc_node("usp_Load")
    parser = ScriptDomParserService()
    parsed = parser.parse(
        "CREATE PROCEDURE dbo.usp_Load AS "
        "INSERT INTO dbo.Fact (id, amt, ts) SELECT s_id, s_amt, s_ts FROM dbo.Stage"
    )
    assert parsed.errors == ()
    mappings = parser.extract_column_mappings(parsed, node, _resolver((tgt, tgt_cols), (src, src_cols)))
    assert len(mappings) == 3
    assert mappings[0].target.owner.name == "Fact"
    assert mappings[0].target.column_name == "id"
    assert mappings[0].sources[0].column_name == "s_id"
    assert mappings[2].sources[0].column_name == "s_ts"


def test_merge_update_and_insert(clr_initialized: None) -> None:
    tgt, tgt_cols = _table("Fact", ("id", "amt"))
    src, src_cols = _table("Stage", ("s_id", "s_amt"))
    node = _proc_node("usp_Merge")
    parser = ScriptDomParserService()
    parsed = parser.parse(
        "CREATE PROCEDURE dbo.usp_Merge AS "
        "MERGE INTO dbo.Fact AS T USING dbo.Stage AS S ON T.id = S.s_id "
        "WHEN MATCHED THEN UPDATE SET amt = S.s_amt "
        "WHEN NOT MATCHED THEN INSERT (id, amt) VALUES (S.s_id, S.s_amt);"
    )
    assert parsed.errors == ()
    mappings = parser.extract_column_mappings(parsed, node, _resolver((tgt, tgt_cols), (src, src_cols)))
    assert len(mappings) == 3
    # UPDATE SET amt = S.s_amt
    upd = next(m for m in mappings if m.target.column_name == "amt" and m.sources[0].column_name == "s_amt" and m.expression and "s_amt" in m.expression)
    assert upd.target.owner.name == "Fact"
    # INSERT (id, amt) VALUES (S.s_id, S.s_amt)
    ins_id = next(m for m in mappings if m.target.column_name == "id" and m.sources and m.sources[0].column_name == "s_id")
    ins_amt = next(m for m in mappings if m.target.column_name == "amt" and m.sources and m.sources[0].column_name == "s_amt" and m is not upd)
    assert ins_id.target.owner.name == "Fact"
    assert ins_amt.target.owner.name == "Fact"


def test_function_call_marks_not_direct(clr_initialized: None) -> None:
    src, src_cols = _table("T", ("a",))
    node = LineageNode(
        ref=ObjectRef(database=None, schema="dbo", name="v_Sum", object_type=ObjectType.VIEW),
        columns=(),
    )
    parser = ScriptDomParserService()
    parsed = parser.parse("CREATE VIEW dbo.v_Sum AS SELECT SUM(a) AS total FROM dbo.T")
    assert parsed.errors == ()
    mappings = parser.extract_column_mappings(parsed, node, _resolver((src, src_cols)))
    assert len(mappings) == 1
    assert mappings[0].target.column_name == "total"
    assert not mappings[0].is_direct
    assert mappings[0].sources[0].column_name == "a"
