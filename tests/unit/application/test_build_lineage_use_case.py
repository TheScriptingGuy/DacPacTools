from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

import pytest

from dacpactools.application.dto.lineage_request import BuildLineageRequest, DacPacSource
from dacpactools.application.ports._handles import (
    ColumnResolver,
    DacPacHandle,
    LiveConnection,
    ParsedBatch,
    ParsedLineage,
    TSqlObjectHandle,
)
from dacpactools.application.ports.clock import SystemClock
from dacpactools.application.use_cases.build_lineage import BuildLineageUseCase
from dacpactools.domain.enums import ObjectType
from dacpactools.domain.errors import UnresolvableObject
from dacpactools.domain.identifiers import ColumnRef, ObjectRef
from dacpactools.domain.lineage import ColumnMapping, LineageGraph, LineageNode


def _ref(name: str, ot: ObjectType) -> ObjectRef:
    return ObjectRef.parse(f"dbo.{name}", object_type=ot)


class FakeDacPacService:
    def __init__(self, graph: dict[ObjectRef, list[ObjectRef]], columns: dict[ObjectRef, tuple[str, ...]], scripts: dict[ObjectRef, str]) -> None:
        self._graph = graph
        self._columns = columns
        self._scripts = scripts

    def load(self, dacpac_path: Path) -> DacPacHandle:
        return DacPacHandle(model=None, source_path=str(dacpac_path))

    def load_from_live(self, connection: LiveConnection, tmp_dir: Path) -> DacPacHandle:
        return DacPacHandle(model=None, source_path=None)

    def find_object(self, handle: DacPacHandle, ref: ObjectRef) -> TSqlObjectHandle | None:
        for r in self._graph:
            if r.name.lower() == ref.name.lower():
                return TSqlObjectHandle(ref=r, tsql_object=None)
        return None

    def get_referenced(self, obj: TSqlObjectHandle) -> Iterable[TSqlObjectHandle]:
        for r in self._graph.get(obj.ref, []):
            yield TSqlObjectHandle(ref=r, tsql_object=None)

    def get_referencing(self, obj: TSqlObjectHandle) -> Iterable[TSqlObjectHandle]:
        return []

    def get_body_script(self, obj: TSqlObjectHandle) -> str | None:
        return self._scripts.get(obj.ref)

    def get_columns(self, obj: TSqlObjectHandle) -> tuple[ColumnRef, ...]:
        cols = self._columns.get(obj.ref, ())
        return tuple(ColumnRef(owner=obj.ref, column_name=c, ordinal=i) for i, c in enumerate(cols))

    def resolve_synonym_target(self, obj: TSqlObjectHandle) -> ObjectRef | None:
        refs = self._graph.get(obj.ref, [])
        return refs[0] if refs else None


class NoopParser:
    def parse(self, tsql: str) -> ParsedBatch:
        return ParsedBatch(fragment=None, errors=())

    def extract_column_mappings(
        self, parsed: ParsedBatch, node: LineageNode, resolver: ColumnResolver
    ) -> tuple[ColumnMapping, ...]:
        return ()

    def extract_lineage(
        self, parsed: ParsedBatch, node: LineageNode, resolver: ColumnResolver
    ) -> ParsedLineage:
        return ParsedLineage(mappings=(), predicates=())


class NoopWriter:
    def __init__(self, name: str) -> None:
        self.name = name

    def write(self, graph: LineageGraph, out_dir: Path) -> Path:
        p = out_dir / f"{self.name}.out"
        p.write_text("ok")
        return p


class NoopViz:
    def render(self, graph: LineageGraph, out_dir: Path) -> Path:
        p = out_dir / "viz.html"
        p.write_text("ok")
        return p


def test_bfs_builds_expected_graph(tmp_path: Path) -> None:
    proc = _ref("usp_Load", ObjectType.PROCEDURE)
    view = _ref("v_Src", ObjectType.VIEW)
    tbl = _ref("Fact", ObjectType.TABLE)
    graph = {proc: [view], view: [tbl], tbl: []}
    cols = {tbl: ("id", "amt"), view: ("id", "amt"), proc: ()}
    scripts = {proc: "SELECT id FROM dbo.v_Src", view: "SELECT id, amt FROM dbo.Fact"}

    uc = BuildLineageUseCase(
        dacpac=FakeDacPacService(graph, cols, scripts),  # type: ignore[arg-type]
        parser=NoopParser(),  # type: ignore[arg-type]
        ol_writer=NoopWriter("ol"),  # type: ignore[arg-type]
        viz=NoopViz(),  # type: ignore[arg-type]
        clock=SystemClock(),
    )

    req = BuildLineageRequest(
        root_object=proc,
        source=DacPacSource(path=tmp_path / "fake.dacpac"),
        output_dir=tmp_path / "out",
    )
    result = uc.execute(req)

    assert LineageGraph.node_key(proc) in result.graph.nodes
    assert LineageGraph.node_key(view) in result.graph.nodes
    assert LineageGraph.node_key(tbl) in result.graph.nodes
    assert result.graph.leaves()[0].ref.name.lower() == "fact"
    assert (result.artifacts["openlineage_json"]).exists()
    assert (result.artifacts["html"]).exists()


def test_missing_root_raises(tmp_path: Path) -> None:
    uc = BuildLineageUseCase(
        dacpac=FakeDacPacService({}, {}, {}),  # type: ignore[arg-type]
        parser=NoopParser(),  # type: ignore[arg-type]
        ol_writer=NoopWriter("ol"),  # type: ignore[arg-type]
        viz=NoopViz(),  # type: ignore[arg-type]
        clock=SystemClock(),
    )
    req = BuildLineageRequest(
        root_object=_ref("nope", ObjectType.PROCEDURE),
        source=DacPacSource(path=tmp_path / "fake.dacpac"),
        output_dir=tmp_path / "out",
    )
    with pytest.raises(UnresolvableObject):
        uc.execute(req)


def test_max_depth_stops_traversal(tmp_path: Path) -> None:
    proc = _ref("usp_A", ObjectType.PROCEDURE)
    view = _ref("v_B", ObjectType.VIEW)
    tbl = _ref("Fact", ObjectType.TABLE)
    graph = {proc: [view], view: [tbl], tbl: []}
    uc = BuildLineageUseCase(
        dacpac=FakeDacPacService(graph, {}, {}),  # type: ignore[arg-type]
        parser=NoopParser(),  # type: ignore[arg-type]
        ol_writer=NoopWriter("ol"),  # type: ignore[arg-type]
        viz=NoopViz(),  # type: ignore[arg-type]
        clock=SystemClock(),
    )
    req = BuildLineageRequest(
        root_object=proc,
        source=DacPacSource(path=tmp_path / "fake.dacpac"),
        output_dir=tmp_path / "out",
        max_depth=1,
    )
    result = uc.execute(req)
    assert LineageGraph.node_key(proc) in result.graph.nodes
    assert LineageGraph.node_key(view) in result.graph.nodes
    assert LineageGraph.node_key(tbl) not in result.graph.nodes
