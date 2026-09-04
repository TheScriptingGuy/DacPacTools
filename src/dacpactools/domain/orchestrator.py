from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from dacpactools.domain.enums import ActivityType, OrchestratorKind
from dacpactools.domain.lineage import LineageGraph


class SqlActivity(BaseModel):
    model_config = ConfigDict(frozen=True)

    activity_id: str
    name: str
    activity_type: ActivityType
    sql_text: str | None = None
    source_uri: str | None = None
    env_ref: str | None = None
    order: int = 0
    depends_on: tuple[str, ...] = ()
    lineage_graph_id: str | None = None
    warnings: tuple[str, ...] = ()


class ActivityEdge(BaseModel):
    model_config = ConfigDict(frozen=True)

    from_activity_id: str
    to_activity_id: str


class Pipeline(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    pipeline_id: str
    name: str
    kind: OrchestratorKind
    source_path: Path
    activities: tuple[SqlActivity, ...] = ()
    activity_edges: tuple[ActivityEdge, ...] = ()


class OrchestratorRun(BaseModel):
    model_config = ConfigDict(frozen=True)

    run_id: str
    name: str
    pipelines: tuple[Pipeline, ...] = ()


class OrchestratorLineageGraph(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    run: OrchestratorRun
    lineage_graphs: dict[str, LineageGraph] = Field(default_factory=dict)
    warnings: tuple[str, ...] = ()
