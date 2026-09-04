from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from dacpactools.domain.identifiers import ObjectRef
from dacpactools.domain.lineage import (
    ColumnMapping,
    ColumnUsage,
    LineageEdge,
    LineageGraph,
    LineageNode,
    SynapseTableSpec,
)
from dacpactools.infrastructure.writers.filesystem import atomic_write_text

_SLUG_RX = re.compile(r"[^A-Za-z0-9_.-]+")
_TEMPLATE_DIR = Path(__file__).parent / "templates"


class CytoscapeHtmlRenderer:
    def __init__(self) -> None:
        self._env = Environment(
            loader=FileSystemLoader(str(_TEMPLATE_DIR)),
            autoescape=select_autoescape(["html", "xml"]),
        )

    def render(self, graph: LineageGraph, out_dir: Path) -> Path:
        data = self._graph_to_data(graph)
        template = self._env.get_template("lineage.html.j2")
        html = template.render(
            root=self._object_name(graph.root),
            data_json=json.dumps(data, indent=None, separators=(",", ":")),
            node_count=len(graph.nodes),
            edge_count=len(graph.edges),
            warnings=list(graph.warnings),
        )
        slug = _SLUG_RX.sub("_", self._object_name(graph.root).replace(".", "_"))
        path = out_dir / f"lineage_{slug}.html"
        atomic_write_text(path, html)
        return path

    def _graph_to_data(self, graph: LineageGraph) -> dict[str, Any]:
        return {
            "root": self._object_name(graph.root),
            "nodes": [self._node(n) for n in graph.nodes.values()],
            "edges": [self._edge(e) for e in graph.edges],
        }

    def _node(self, node: LineageNode) -> dict[str, Any]:
        return {
            "ref": self._object_name(node.ref),
            "objectType": node.ref.object_type.value,
            "columns": [
                {"ordinal": c.ordinal, "name": c.column_name, "dataType": c.data_type}
                for c in node.columns
            ],
            "columnMappings": [self._mapping(m) for m in node.column_mappings],
            "predicates": [self._predicate(p) for p in node.predicates],
            "synapse": self._synapse(node.synapse),
        }

    def _synapse(self, s: SynapseTableSpec | None) -> dict[str, Any] | None:
        if s is None:
            return None
        return {
            "distributionKind": s.distribution_kind.value,
            "distributionColumns": list(s.distribution_columns),
            "indexKind": s.index_kind.value,
            "indexColumns": list(s.index_columns),
            "source": s.source,
        }

    def _predicate(self, p: ColumnUsage) -> dict[str, Any]:
        return {
            "kind": p.kind.value,
            "expression": p.expression,
            "columns": [
                {"object": self._object_name(c.owner), "column": c.column_name}
                for c in p.columns
            ],
            "partner": (
                {"object": self._object_name(p.partner.owner), "column": p.partner.column_name}
                if p.partner is not None
                else None
            ),
        }

    def _mapping(self, m: ColumnMapping) -> dict[str, Any]:
        return {
            "target": {
                "object": self._object_name(m.target.owner),
                "column": m.target.column_name,
                "ordinal": m.target.ordinal,
            },
            "sources": [
                {"object": self._object_name(s.owner), "column": s.column_name}
                for s in m.sources
            ],
            "isDirect": m.is_direct,
            "expression": m.expression,
        }

    def _edge(self, e: LineageEdge) -> dict[str, Any]:
        return {
            "from": self._object_name(e.from_ref),
            "to": self._object_name(e.to_ref),
            "kind": e.kind.value,
        }

    def _object_name(self, ref: ObjectRef) -> str:
        if ref.database:
            return f"{ref.database}.{ref.schema_name}.{ref.name}"
        return f"{ref.schema_name}.{ref.name}"
