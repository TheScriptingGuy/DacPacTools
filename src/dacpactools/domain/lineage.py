from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from dacpactools.domain.enums import (
    ColumnUsageKind,
    EdgeKind,
    SynapseDistributionKind,
    SynapseIndexKind,
)
from dacpactools.domain.identifiers import ColumnRef, ObjectRef


class ColumnMapping(BaseModel):
    model_config = ConfigDict(frozen=True)

    target: ColumnRef
    sources: tuple[ColumnRef, ...] = ()
    expression: str | None = None
    is_direct: bool = True


class ColumnUsage(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: ColumnUsageKind
    columns: tuple[ColumnRef, ...] = ()
    expression: str | None = None
    partner: ColumnRef | None = None


class LineageEdge(BaseModel):
    model_config = ConfigDict(frozen=True)

    from_ref: ObjectRef
    to_ref: ObjectRef
    kind: EdgeKind = EdgeKind.REFERENCES


class SynapseTableSpec(BaseModel):
    model_config = ConfigDict(frozen=True)

    distribution_kind: SynapseDistributionKind
    distribution_columns: tuple[str, ...] = ()
    index_kind: SynapseIndexKind = SynapseIndexKind.NONE
    index_columns: tuple[str, ...] = ()
    source: str = "CTAS"


class LineageNode(BaseModel):
    model_config = ConfigDict(frozen=True)

    ref: ObjectRef
    columns: tuple[ColumnRef, ...] = ()
    column_mappings: tuple[ColumnMapping, ...] = ()
    predicates: tuple[ColumnUsage, ...] = ()
    synapse: SynapseTableSpec | None = None


class LineageGraph(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    root: ObjectRef
    nodes: dict[str, LineageNode]
    edges: tuple[LineageEdge, ...] = ()
    warnings: tuple[str, ...] = ()

    @staticmethod
    def node_key(ref: ObjectRef) -> str:
        db = ref.database or ""
        return f"{db}|{ref.schema_name.lower()}|{ref.name.lower()}"

    def get(self, ref: ObjectRef) -> LineageNode | None:
        return self.nodes.get(self.node_key(ref))

    def leaves(self) -> tuple[LineageNode, ...]:
        return tuple(n for n in self.nodes.values() if n.ref.object_type.is_leaf)
