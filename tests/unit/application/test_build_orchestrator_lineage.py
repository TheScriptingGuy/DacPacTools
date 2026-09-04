from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar

import pytest

from dacpactools.application.dto.lineage_request import (
    BuildLineageRequest,
    DacPacSource,
    LineageSource,
)
from dacpactools.application.dto.lineage_result import BuildLineageResult
from dacpactools.application.dto.orchestrator_request import (
    BuildOrchestratorLineageRequest,
)
from dacpactools.application.ports._handles import ColumnResolver, ParsedBatch, ParsedLineage
from dacpactools.application.ports.clock import SystemClock
from dacpactools.application.use_cases.build_orchestrator_lineage import (
    BuildOrchestratorLineageUseCase,
)
from dacpactools.domain.enums import ActivityType, ObjectType, OrchestratorKind
from dacpactools.domain.errors import UnresolvableOrchestratorArtifact
from dacpactools.domain.identifiers import ObjectRef
from dacpactools.domain.lineage import (
    ColumnMapping,
    LineageGraph,
    LineageNode,
)
from dacpactools.domain.orchestrator import (
    OrchestratorLineageGraph,
    Pipeline,
    SqlActivity,
)


class FakeReader:
    kind: ClassVar[OrchestratorKind] = OrchestratorKind.ADF

    def __init__(self, pipelines: tuple[Pipeline, ...]) -> None:
        self._pipelines = pipelines

    def can_read(self, path: Path) -> bool:
        return True

    def read(self, path: Path) -> tuple[Pipeline, ...]:
        return self._pipelines


class FakeEnvMap:
    def __init__(self, mapping: dict[str, LineageSource]) -> None:
        self._map = mapping

    def load(self, path: Path) -> None:
        return None

    def resolve(self, env_ref: str) -> LineageSource | None:
        return self._map.get(env_ref)

    def known_refs(self) -> tuple[str, ...]:
        return tuple(self._map)


@dataclass
class FakeInner:
    graph: LineageGraph
    calls: list[BuildLineageRequest] = field(default_factory=list)

    def execute(self, req: BuildLineageRequest) -> BuildLineageResult:
        self.calls.append(req)
        return BuildLineageResult(graph=self.graph)


class FakeParser:
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


class FakeWriter:
    def __init__(self, name: str) -> None:
        self.name = name

    def write(self, graph: OrchestratorLineageGraph, out_dir: Path) -> Path:
        p = out_dir / f"{self.name}.json"
        p.write_text("ok")
        return p


class FakeViz:
    def render(self, graph: OrchestratorLineageGraph, out_dir: Path) -> Path:
        p = out_dir / "viz.html"
        p.write_text("ok")
        return p


def _activity(name: str, sql: str | None, env: str | None = "AzureSqlDatabase1") -> SqlActivity:
    at = (
        ActivityType.SQL_STORED_PROCEDURE
        if sql and sql.upper().startswith("EXEC")
        else ActivityType.SQL_SCRIPT
    )
    return SqlActivity(
        activity_id=name, name=name, activity_type=at, sql_text=sql, env_ref=env
    )


def _pipeline(*activities: SqlActivity, name: str = "pl", src: Path | None = None) -> Pipeline:
    return Pipeline(
        pipeline_id=name,
        name=name,
        kind=OrchestratorKind.ADF,
        source_path=src or Path("fake.json"),
        activities=activities,
    )


def _dummy_graph() -> LineageGraph:
    ref = ObjectRef(database=None, schema="dbo", name="Fact", object_type=ObjectType.TABLE)
    return LineageGraph(root=ref, nodes={LineageGraph.node_key(ref): LineageNode(ref=ref)})


def _make(
    pipelines: tuple[Pipeline, ...],
    env_map: dict[str, LineageSource],
    inner_graph: LineageGraph | None = None,
) -> tuple[BuildOrchestratorLineageUseCase, FakeInner]:
    inner = FakeInner(graph=inner_graph or _dummy_graph())
    uc: Any = BuildOrchestratorLineageUseCase(
        readers=(FakeReader(pipelines),),
        env_map=FakeEnvMap(env_map),
        inner=inner,
        parser=FakeParser(),
        ol_writer=FakeWriter("orch"),
        viz=FakeViz(),
        clock=SystemClock(),
    )
    return uc, inner


def _req(tmp_path: Path) -> BuildOrchestratorLineageRequest:
    return BuildOrchestratorLineageRequest(
        artifact_path=tmp_path / "artifact.json",
        env_map_path=tmp_path / "map.json",
        output_dir=tmp_path / "out",
    )


def _touch_inputs(tmp_path: Path) -> None:
    (tmp_path / "artifact.json").write_text("{}")
    (tmp_path / "map.json").write_text("{}")


def test_bare_exec_dispatches_to_inner_use_case(tmp_path: Path) -> None:
    _touch_inputs(tmp_path)
    uc, inner = _make(
        (_pipeline(_activity("t1", "EXEC [dbo].[usp_Load]")),),
        {"AzureSqlDatabase1": DacPacSource(path=tmp_path / "sample.dacpac")},
    )
    result = uc.execute(_req(tmp_path))
    assert len(inner.calls) == 1
    assert inner.calls[0].root_object.name == "usp_Load"
    assert inner.calls[0].root_object.schema_name == "dbo"
    activity = result.graph.run.pipelines[0].activities[0]
    assert activity.lineage_graph_id is not None
    assert activity.lineage_graph_id in result.graph.lineage_graphs


def test_non_exec_produces_placeholder_and_statement_warning(tmp_path: Path) -> None:
    _touch_inputs(tmp_path)
    uc, inner = _make(
        (_pipeline(_activity("t1", "SELECT * FROM dbo.Fact")),),
        {"AzureSqlDatabase1": DacPacSource(path=tmp_path / "sample.dacpac")},
    )
    result = uc.execute(_req(tmp_path))
    assert not inner.calls
    activity = result.graph.run.pipelines[0].activities[0]
    assert activity.lineage_graph_id is not None
    assert any("statement-level analysis only" in w for w in activity.warnings)


def test_unresolved_env_ref_warns_and_skips(tmp_path: Path) -> None:
    _touch_inputs(tmp_path)
    uc, inner = _make(
        (_pipeline(_activity("t1", "EXEC dbo.p", env="unknown_env")),),
        {},
    )
    result = uc.execute(_req(tmp_path))
    assert not inner.calls
    activity = result.graph.run.pipelines[0].activities[0]
    assert activity.lineage_graph_id is None
    assert any("no env mapping" in w for w in activity.warnings)


def test_dedupe_by_env_and_sql_hash(tmp_path: Path) -> None:
    _touch_inputs(tmp_path)
    uc, inner = _make(
        (
            _pipeline(
                _activity("t1", "EXEC [dbo].[usp_Load]"),
                _activity("t2", "EXEC [dbo].[usp_Load]"),
            ),
        ),
        {"AzureSqlDatabase1": DacPacSource(path=tmp_path / "sample.dacpac")},
    )
    result = uc.execute(_req(tmp_path))
    assert len(inner.calls) == 1
    activities = result.graph.run.pipelines[0].activities
    assert activities[0].lineage_graph_id == activities[1].lineage_graph_id
    assert len(result.graph.lineage_graphs) == 1


def test_no_pipelines_raises(tmp_path: Path) -> None:
    _touch_inputs(tmp_path)
    uc, _inner = _make((), {})
    with pytest.raises(UnresolvableOrchestratorArtifact):
        uc.execute(_req(tmp_path))
