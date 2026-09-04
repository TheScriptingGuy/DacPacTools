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


def _view_node(name: str, out_cols: tuple[str, ...]) -> LineageNode:
    ref = ObjectRef(database=None, schema="dbo", name=name, object_type=ObjectType.VIEW)
    cols = tuple(ColumnRef(owner=ref, column_name=c, ordinal=i) for i, c in enumerate(out_cols))
    return LineageNode(ref=ref, columns=cols)


def _resolver(*tables: tuple[ObjectRef, tuple[ColumnRef, ...]]) -> ColumnResolver:
    scope: dict[str, tuple] = {}
    for ref, cols in tables:
        scope[ref.name.lower()] = cols
        scope[f"{ref.schema_name.lower()}.{ref.name.lower()}"] = cols
    return ColumnResolver(scope=scope)


def test_select_star_expansion(clr_initialized: None) -> None:
    tbl, cols = _table("Fact", ("id", "amt", "ts"))
    node = _view_node("v_Fact", ("id", "amt", "ts"))
    parser = ScriptDomParserService()
    parsed = parser.parse("CREATE VIEW dbo.v_Fact AS SELECT * FROM dbo.Fact")
    assert parsed.errors == ()
    mappings = parser.extract_column_mappings(parsed, node, _resolver((tbl, cols)))
    assert [m.target.column_name for m in mappings] == ["id", "amt", "ts"]
    assert [m.sources[0].column_name for m in mappings] == ["id", "amt", "ts"]


def test_union_by_ordinal(clr_initialized: None) -> None:
    a_ref, a_cols = _table("A", ("id", "amt"))
    b_ref, b_cols = _table("B", ("bid", "bamt"))
    node = _view_node("v_U", ("id", "amt"))
    parser = ScriptDomParserService()
    parsed = parser.parse(
        "CREATE VIEW dbo.v_U AS "
        "SELECT id, amt FROM dbo.A UNION ALL SELECT bid, bamt FROM dbo.B"
    )
    assert parsed.errors == ()
    mappings = parser.extract_column_mappings(parsed, node, _resolver((a_ref, a_cols), (b_ref, b_cols)))
    assert len(mappings) == 2
    src_cols_first = sorted(s.column_name for s in mappings[0].sources)
    assert src_cols_first == ["bid", "id"]
    src_cols_second = sorted(s.column_name for s in mappings[1].sources)
    assert src_cols_second == ["amt", "bamt"]


def test_cte(clr_initialized: None) -> None:
    tbl, cols = _table("Fact", ("id", "amt"))
    node = _view_node("v_Cte", ("id", "amt"))
    parser = ScriptDomParserService()
    parsed = parser.parse(
        "CREATE VIEW dbo.v_Cte AS "
        "WITH src AS (SELECT id, amt FROM dbo.Fact) SELECT id, amt FROM src"
    )
    assert parsed.errors == ()
    mappings = parser.extract_column_mappings(parsed, node, _resolver((tbl, cols)))
    assert len(mappings) == 2
    # Base sources traced through CTE
    assert mappings[0].sources[0].owner.name == "Fact"
    assert mappings[0].sources[0].column_name == "id"
    assert mappings[1].sources[0].column_name == "amt"


def test_derived_table_in_from(clr_initialized: None) -> None:
    tbl, cols = _table("Orders", ("order_id", "customer_id", "amount"))
    node = _view_node("v_Sub", ("order_id", "amount"))
    parser = ScriptDomParserService()
    parsed = parser.parse(
        "CREATE VIEW dbo.v_Sub AS "
        "SELECT s.order_id, s.amount FROM (SELECT order_id, amount FROM dbo.Orders) s"
    )
    assert parsed.errors == ()
    mappings = parser.extract_column_mappings(parsed, node, _resolver((tbl, cols)))
    assert len(mappings) == 2
    assert mappings[0].sources[0].owner.name == "Orders"
    assert mappings[0].sources[0].column_name == "order_id"
    assert mappings[1].sources[0].column_name == "amount"
