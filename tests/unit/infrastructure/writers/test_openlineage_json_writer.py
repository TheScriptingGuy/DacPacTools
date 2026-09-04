from __future__ import annotations

import json
from datetime import datetime, timezone
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
from dacpactools.infrastructure.writers.openlineage_json_writer import (
    OpenLineageJsonWriter,
)


class FrozenClock:
    def __init__(self, moment: datetime) -> None:
        self._moment = moment

    def now_utc(self) -> datetime:
        return self._moment


def _ref(name: str, ot: ObjectType, schema: str = "dbo") -> ObjectRef:
    return ObjectRef(database=None, schema=schema, name=name, object_type=ot)


def _cols(ref: ObjectRef, names: tuple[str, ...]) -> tuple[ColumnRef, ...]:
    return tuple(ColumnRef(owner=ref, column_name=n, ordinal=i) for i, n in enumerate(names))


def test_writes_openlineage_json_with_tree_facet(tmp_path: Path) -> None:
    tbl = _ref("Fact", ObjectType.TABLE)
    view = _ref("v_Fact", ObjectType.VIEW)
    tbl_cols = _cols(tbl, ("id", "amt"))
    view_cols = _cols(view, ("id", "amt"))

    view_mappings = tuple(
        ColumnMapping(target=view_cols[i], sources=(tbl_cols[i],), expression=tbl_cols[i].column_name, is_direct=True)
        for i in range(2)
    )
    nodes = {
        LineageGraph.node_key(view): LineageNode(ref=view, columns=view_cols, column_mappings=view_mappings),
        LineageGraph.node_key(tbl): LineageNode(ref=tbl, columns=tbl_cols),
    }
    graph = LineageGraph(
        root=view,
        nodes=nodes,
        edges=(LineageEdge(from_ref=view, to_ref=tbl, kind=EdgeKind.REFERENCES),),
    )

    clock = FrozenClock(datetime(2026, 9, 2, 12, 0, 0, tzinfo=timezone.utc))
    writer = OpenLineageJsonWriter(namespace="mssql://server/db", clock=clock)
    path = writer.write(graph, tmp_path)

    assert path.exists()
    assert path.name.startswith("lineage_dbo_v_Fact_")
    payload = json.loads(path.read_text())

    assert payload["eventType"] == "COMPLETE"
    assert payload["outputs"][0]["name"] == "dbo.v_Fact"
    assert payload["outputs"][0]["facets"]["columnLineage"]["fields"]["id"]["inputFields"][0]["name"] == "dbo.Fact"
    assert payload["outputs"][0]["facets"]["columnLineage"]["fields"]["id"]["transformationType"] == "IDENTITY"

    assert len(payload["inputs"]) == 1
    assert payload["inputs"][0]["name"] == "dbo.Fact"

    tree = payload["run"]["facets"]["dacpactools_tree"]
    assert tree["root"] == "dbo.v_Fact"
    node_refs = {n["ref"] for n in tree["nodes"]}
    assert node_refs == {"dbo.v_Fact", "dbo.Fact"}
    assert tree["edges"][0]["from"] == "dbo.v_Fact"
    assert tree["edges"][0]["to"] == "dbo.Fact"


def test_writes_predicates_facet(tmp_path: Path) -> None:
    tbl = _ref("Fact", ObjectType.TABLE)
    view = _ref("v_Fact", ObjectType.VIEW)
    tbl_cols = _cols(tbl, ("id", "active"))
    view_cols = _cols(view, ("id",))
    m = ColumnMapping(target=view_cols[0], sources=(tbl_cols[0],), expression="id", is_direct=True)
    pred = ColumnUsage(
        kind=ColumnUsageKind.WHERE,
        columns=(tbl_cols[1],),
        expression="active = 1",
    )
    graph = LineageGraph(
        root=view,
        nodes={
            LineageGraph.node_key(view): LineageNode(
                ref=view, columns=view_cols, column_mappings=(m,), predicates=(pred,)
            ),
            LineageGraph.node_key(tbl): LineageNode(ref=tbl, columns=tbl_cols),
        },
        edges=(LineageEdge(from_ref=view, to_ref=tbl, kind=EdgeKind.REFERENCES),),
    )
    path = OpenLineageJsonWriter(namespace="mssql://x/y").write(graph, tmp_path)
    payload = json.loads(path.read_text())
    facet = payload["outputs"][0]["facets"]["dacpactools_predicates"]
    assert facet["usages"][0]["kind"] == "WHERE"
    assert facet["usages"][0]["expression"] == "active = 1"
    assert facet["usages"][0]["columns"][0] == {"object": "dbo.Fact", "field": "active"}


def test_writes_deterministic_slug_and_namespace(tmp_path: Path) -> None:
    tbl = _ref("Fact", ObjectType.TABLE)
    graph = LineageGraph(
        root=tbl,
        nodes={LineageGraph.node_key(tbl): LineageNode(ref=tbl, columns=_cols(tbl, ("id",)))},
    )
    writer = OpenLineageJsonWriter(namespace="mssql://prod.database.windows.net/warehouse")
    path = writer.write(graph, tmp_path)
    payload = json.loads(path.read_text())
    assert payload["outputs"][0]["namespace"] == "mssql://prod.database.windows.net/warehouse"
