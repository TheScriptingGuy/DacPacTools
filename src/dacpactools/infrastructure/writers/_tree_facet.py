from __future__ import annotations

from typing import Any

from dacpactools.domain.identifiers import ObjectRef
from dacpactools.domain.lineage import (
    ColumnMapping,
    LineageEdge,
    LineageGraph,
    LineageNode,
)

PRODUCER = "https://github.com/wesselvdlinden/dacpactools"
TREE_FACET_SCHEMA = (
    "https://github.com/wesselvdlinden/dacpactools/schemas/dacpactools_tree.json"
)
ORCHESTRATOR_FACET_SCHEMA = (
    "https://github.com/wesselvdlinden/dacpactools/schemas/dacpactools_orchestrator_tree.json"
)


def object_name(ref: ObjectRef) -> str:
    if ref.database:
        return f"{ref.database}.{ref.schema_name}.{ref.name}"
    return f"{ref.schema_name}.{ref.name}"


def tree_facet(graph: LineageGraph) -> dict[str, Any]:
    return {
        "_producer": PRODUCER,
        "_schemaURL": TREE_FACET_SCHEMA,
        "root": object_name(graph.root),
        "nodes": [tree_node(n) for n in graph.nodes.values()],
        "edges": [tree_edge(e) for e in graph.edges],
        "warnings": list(graph.warnings),
    }


def tree_node(node: LineageNode) -> dict[str, Any]:
    return {
        "ref": object_name(node.ref),
        "objectType": node.ref.object_type.value,
        "columns": [
            {"name": c.column_name, "ordinal": c.ordinal, "dataType": c.data_type}
            for c in node.columns
        ],
        "columnMappings": [tree_mapping(m) for m in node.column_mappings],
    }


def tree_mapping(m: ColumnMapping) -> dict[str, Any]:
    return {
        "target": {
            "object": object_name(m.target.owner),
            "column": m.target.column_name,
            "ordinal": m.target.ordinal,
        },
        "sources": [
            {"object": object_name(s.owner), "column": s.column_name}
            for s in m.sources
        ],
        "expression": m.expression,
        "isDirect": m.is_direct,
    }


def tree_edge(e: LineageEdge) -> dict[str, Any]:
    return {
        "from": object_name(e.from_ref),
        "to": object_name(e.to_ref),
        "kind": e.kind.value,
    }
