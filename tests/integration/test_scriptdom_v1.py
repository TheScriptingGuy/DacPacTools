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


def test_simple_select_from_table(clr_initialized: None) -> None:
    tbl, cols = _table("Fact", ("id", "amt"))
    node = _view_node("v_Fact", ("id", "amt"))
    parser = ScriptDomParserService()
    parsed = parser.parse("CREATE VIEW dbo.v_Fact AS SELECT id, amt FROM dbo.Fact")
    assert parsed.errors == ()
    mappings = parser.extract_column_mappings(parsed, node, _resolver((tbl, cols)))
    assert len(mappings) == 2
    assert mappings[0].target.column_name == "id"
    assert [s.column_name for s in mappings[0].sources] == ["id"]
    assert mappings[1].target.column_name == "amt"
    assert [s.column_name for s in mappings[1].sources] == ["amt"]
    assert all(m.is_direct for m in mappings)


def test_alias_and_qualified_join(clr_initialized: None) -> None:
    orders_ref, orders_cols = _table("Orders", ("order_id", "customer_id", "amount"))
    cust_ref, cust_cols = _table("Customers", ("customer_id", "name"))
    node = _view_node("v_OrderReport", ("order_id", "customer_name"))
    parser = ScriptDomParserService()
    parsed = parser.parse(
        "CREATE VIEW dbo.v_OrderReport AS "
        "SELECT o.order_id, c.name AS customer_name "
        "FROM dbo.Orders o JOIN dbo.Customers c ON o.customer_id = c.customer_id"
    )
    assert parsed.errors == ()
    mappings = parser.extract_column_mappings(
        parsed, node, _resolver((orders_ref, orders_cols), (cust_ref, cust_cols))
    )
    assert len(mappings) == 2
    assert mappings[0].target.column_name == "order_id"
    assert mappings[0].sources[0].owner.name == "Orders"
    assert mappings[1].target.column_name == "customer_name"
    assert mappings[1].sources[0].owner.name == "Customers"
    assert mappings[1].sources[0].column_name == "name"


def test_derived_expression_is_not_direct(clr_initialized: None) -> None:
    tbl, cols = _table("Fact", ("qty", "price"))
    node = _view_node("v_Total", ("total",))
    parser = ScriptDomParserService()
    parsed = parser.parse("CREATE VIEW dbo.v_Total AS SELECT qty * price AS total FROM dbo.Fact")
    assert parsed.errors == ()
    mappings = parser.extract_column_mappings(parsed, node, _resolver((tbl, cols)))
    assert len(mappings) == 1
    assert not mappings[0].is_direct
    src_names = sorted(s.column_name for s in mappings[0].sources)
    assert src_names == ["price", "qty"]


def test_procedure_select_body(clr_initialized: None) -> None:
    tbl, cols = _table("Fact", ("id",))
    ref = ObjectRef(database=None, schema="dbo", name="usp_Get", object_type=ObjectType.PROCEDURE)
    node = LineageNode(ref=ref, columns=(ColumnRef(owner=ref, column_name="id", ordinal=0),))
    parser = ScriptDomParserService()
    parsed = parser.parse("CREATE PROCEDURE dbo.usp_Get AS SELECT id FROM dbo.Fact")
    assert parsed.errors == ()
    mappings = parser.extract_column_mappings(parsed, node, _resolver((tbl, cols)))
    assert len(mappings) == 1
    assert mappings[0].sources[0].column_name == "id"
