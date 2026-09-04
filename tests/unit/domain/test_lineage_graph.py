from __future__ import annotations

from dacpactools.domain.enums import EdgeKind, ObjectType
from dacpactools.domain.identifiers import ColumnRef, ObjectRef
from dacpactools.domain.lineage import ColumnMapping, LineageEdge, LineageGraph, LineageNode


def _ref(name: str, ot: ObjectType) -> ObjectRef:
    return ObjectRef.parse(f"dbo.{name}", object_type=ot)


def test_leaves_and_get() -> None:
    proc = _ref("usp_Load", ObjectType.PROCEDURE)
    tbl = _ref("Fact", ObjectType.TABLE)
    ext = _ref("ExtStg", ObjectType.EXTERNAL_TABLE)
    view = _ref("v_Src", ObjectType.VIEW)

    nodes = {
        LineageGraph.node_key(r): LineageNode(ref=r)
        for r in (proc, tbl, ext, view)
    }
    g = LineageGraph(
        root=proc,
        nodes=nodes,
        edges=(
            LineageEdge(from_ref=proc, to_ref=view),
            LineageEdge(from_ref=view, to_ref=tbl),
        ),
    )

    leaves = g.leaves()
    assert {n.ref.name for n in leaves} == {"Fact", "ExtStg"}
    assert g.get(proc) is not None
    assert g.get(_ref("Missing", ObjectType.TABLE)) is None


def test_column_mapping_shape() -> None:
    tbl = _ref("Src", ObjectType.TABLE)
    view = _ref("v", ObjectType.VIEW)
    src_col = ColumnRef(owner=tbl, column_name="a")
    tgt_col = ColumnRef(owner=view, column_name="a")
    m = ColumnMapping(target=tgt_col, sources=(src_col,), expression="a", is_direct=True)
    assert m.sources[0] == src_col
    assert m.is_direct is True


def test_edge_kind_default() -> None:
    a = _ref("A", ObjectType.PROCEDURE)
    b = _ref("B", ObjectType.TABLE)
    e = LineageEdge(from_ref=a, to_ref=b)
    assert e.kind == EdgeKind.REFERENCES
