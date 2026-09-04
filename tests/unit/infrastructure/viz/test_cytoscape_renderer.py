from __future__ import annotations

import json
import re
from pathlib import Path

from dacpactools.domain.enums import ColumnUsageKind, EdgeKind, ObjectType
from dacpactools.domain.identifiers import ColumnRef, ObjectRef
from dacpactools.domain.lineage import (
    ColumnMapping,
    ColumnUsage,
    LineageEdge,
    LineageGraph,
    LineageNode,
)
from dacpactools.infrastructure.viz.cytoscape_renderer import CytoscapeHtmlRenderer


def _ref(name: str, ot: ObjectType) -> ObjectRef:
    return ObjectRef(database=None, schema="dbo", name=name, object_type=ot)


def test_renders_html_with_embedded_json(tmp_path: Path) -> None:
    tbl = _ref("Fact", ObjectType.TABLE)
    view = _ref("v_Fact", ObjectType.VIEW)
    tbl_cols = (ColumnRef(owner=tbl, column_name="id", ordinal=0),)
    view_cols = (ColumnRef(owner=view, column_name="id", ordinal=0),)
    m = ColumnMapping(target=view_cols[0], sources=tbl_cols, expression="id", is_direct=True)
    graph = LineageGraph(
        root=view,
        nodes={
            LineageGraph.node_key(view): LineageNode(ref=view, columns=view_cols, column_mappings=(m,)),
            LineageGraph.node_key(tbl): LineageNode(ref=tbl, columns=tbl_cols),
        },
        edges=(LineageEdge(from_ref=view, to_ref=tbl, kind=EdgeKind.REFERENCES),),
    )
    renderer = CytoscapeHtmlRenderer()
    path = renderer.render(graph, tmp_path)
    assert path.exists()
    assert path.name == "lineage_dbo_v_Fact.html"
    html = path.read_text(encoding="utf-8")
    assert 'id="graph-data"' in html
    assert 'cytoscape' in html
    match = re.search(r'<script id="graph-data"[^>]*>(.*?)</script>', html, re.DOTALL)
    assert match is not None
    data = json.loads(match.group(1))
    assert data["root"] == "dbo.v_Fact"
    assert {n["ref"] for n in data["nodes"]} == {"dbo.v_Fact", "dbo.Fact"}
    assert data["edges"][0]["from"] == "dbo.v_Fact"


def test_renders_predicates_in_node_json(tmp_path: Path) -> None:
    tbl = _ref("Fact", ObjectType.TABLE)
    view = _ref("v_Fact", ObjectType.VIEW)
    tbl_cols = (
        ColumnRef(owner=tbl, column_name="id", ordinal=0),
        ColumnRef(owner=tbl, column_name="active", ordinal=1),
    )
    view_cols = (ColumnRef(owner=view, column_name="id", ordinal=0),)
    m = ColumnMapping(target=view_cols[0], sources=(tbl_cols[0],), expression="id", is_direct=True)
    pred = ColumnUsage(
        kind=ColumnUsageKind.WHERE,
        columns=(tbl_cols[1],),
        expression="active = 1",
    )
    join = ColumnUsage(
        kind=ColumnUsageKind.JOIN_ON,
        columns=(tbl_cols[0],),
        expression="a.id = b.id",
        partner=tbl_cols[0],
    )
    graph = LineageGraph(
        root=view,
        nodes={
            LineageGraph.node_key(view): LineageNode(
                ref=view,
                columns=view_cols,
                column_mappings=(m,),
                predicates=(pred, join),
            ),
            LineageGraph.node_key(tbl): LineageNode(ref=tbl, columns=tbl_cols),
        },
        edges=(LineageEdge(from_ref=view, to_ref=tbl, kind=EdgeKind.REFERENCES),),
    )
    path = CytoscapeHtmlRenderer().render(graph, tmp_path)
    html = path.read_text(encoding="utf-8")
    match = re.search(r'<script id="graph-data"[^>]*>(.*?)</script>', html, re.DOTALL)
    assert match is not None
    data = json.loads(match.group(1))
    node = next(n for n in data["nodes"] if n["ref"] == "dbo.v_Fact")
    kinds = [p["kind"] for p in node["predicates"]]
    assert kinds == ["WHERE", "JOIN_ON"]
    assert node["predicates"][0]["expression"] == "active = 1"
    assert node["predicates"][1]["partner"] == {"object": "dbo.Fact", "column": "id"}
