from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dacpactools.application.ports.clock import IClock, SystemClock
from dacpactools.domain.identifiers import ObjectRef
from dacpactools.domain.lineage import ColumnMapping, LineageGraph, LineageNode
from dacpactools.infrastructure.writers._tree_facet import (
    PRODUCER,
    TREE_FACET_SCHEMA,
    object_name,
    tree_facet,
)
from dacpactools.infrastructure.writers.filesystem import atomic_write_text

_SCHEMA_URL = "https://openlineage.io/spec/2-0-2/OpenLineage.json"

_SLUG_RX = re.compile(r"[^A-Za-z0-9_.-]+")


class OpenLineageJsonWriter:
    def __init__(self, namespace: str = "mssql://local", clock: IClock | None = None) -> None:
        self.namespace = namespace.rstrip("/")
        self._clock = clock or SystemClock()

    def write(self, graph: LineageGraph, out_dir: Path) -> Path:
        now = self._clock.now_utc().replace(tzinfo=UTC)
        payload = self._build_payload(graph, now)
        text = json.dumps(payload, indent=2, sort_keys=False, default=_json_default)
        slug = self._slugify(graph.root)
        stamp = now.strftime("%Y%m%dT%H%M%SZ")
        path = out_dir / f"lineage_{slug}_{stamp}.json"
        atomic_write_text(path, text)
        return path

    # ---------------------------------------------------------- payload build

    def _build_payload(self, graph: LineageGraph, now: datetime) -> dict[str, Any]:
        root_node = graph.get(graph.root)
        assert root_node is not None, "graph missing root node"

        root_ds = self._dataset(root_node)
        cl_facet = self._column_lineage_facet(root_node, graph)
        if cl_facet is not None:
            root_ds["facets"]["columnLineage"] = cl_facet

        input_datasets: list[dict[str, Any]] = []
        for key, node in graph.nodes.items():
            if key == LineageGraph.node_key(graph.root):
                continue
            input_datasets.append(self._dataset(node))

        return {
            "eventType": "COMPLETE",
            "eventTime": now.isoformat(),
            "run": {
                "runId": str(uuid.uuid4()),
                "facets": {"dacpactools_tree": tree_facet(graph)},
            },
            "job": {
                "namespace": self.namespace,
                "name": f"lineage:{self._slugify(graph.root)}",
            },
            "inputs": input_datasets,
            "outputs": [root_ds],
            "producer": PRODUCER,
            "schemaURL": _SCHEMA_URL,
        }

    def _dataset(self, node: LineageNode) -> dict[str, Any]:
        facets: dict[str, Any] = {
            "schema": {
                "_producer": PRODUCER,
                "_schemaURL": _SCHEMA_URL,
                "fields": [
                    {"name": c.column_name, "type": c.data_type or "unknown"}
                    for c in node.columns
                ],
            },
            "dacpactools_object": {
                "_producer": PRODUCER,
                "_schemaURL": TREE_FACET_SCHEMA,
                "objectType": node.ref.object_type.value,
                "database": node.ref.database,
                "schema": node.ref.schema_name,
                "name": node.ref.name,
            },
        }
        if node.synapse is not None:
            s = node.synapse
            facets["dacpactools_synapse"] = {
                "_producer": PRODUCER,
                "_schemaURL": TREE_FACET_SCHEMA,
                "distributionKind": s.distribution_kind.value,
                "distributionColumns": list(s.distribution_columns),
                "indexKind": s.index_kind.value,
                "indexColumns": list(s.index_columns),
                "source": s.source,
            }
        if node.predicates:
            facets["dacpactools_predicates"] = {
                "_producer": PRODUCER,
                "_schemaURL": TREE_FACET_SCHEMA,
                "usages": [
                    {
                        "kind": p.kind.value,
                        "expression": p.expression,
                        "columns": [
                            {"object": object_name(c.owner), "field": c.column_name}
                            for c in p.columns
                        ],
                        "partner": (
                            {
                                "object": object_name(p.partner.owner),
                                "field": p.partner.column_name,
                            }
                            if p.partner is not None
                            else None
                        ),
                    }
                    for p in node.predicates
                ],
            }
        return {
            "namespace": self.namespace,
            "name": object_name(node.ref),
            "facets": facets,
        }

    def _column_lineage_facet(
        self, root: LineageNode, graph: LineageGraph
    ) -> dict[str, Any] | None:
        if not root.column_mappings:
            return None
        fields: dict[str, Any] = {}
        for mapping in root.column_mappings:
            fields[mapping.target.column_name] = {
                "inputFields": [
                    {
                        "namespace": self.namespace,
                        "name": object_name(s.owner),
                        "field": s.column_name,
                        "transformations": self._transformation_hint(mapping),
                    }
                    for s in mapping.sources
                ],
                "transformationDescription": mapping.expression or "",
                "transformationType": "IDENTITY" if mapping.is_direct else "TRANSFORMATION",
            }
        return {
            "_producer": PRODUCER,
            "_schemaURL": "https://openlineage.io/spec/facets/1-1-0/ColumnLineageDatasetFacet.json",
            "fields": fields,
        }

    def _transformation_hint(self, mapping: ColumnMapping) -> list[dict[str, Any]]:
        if mapping.is_direct:
            return [{"type": "DIRECT", "subtype": "IDENTITY"}]
        return [{"type": "INDIRECT", "subtype": "TRANSFORMATION"}]

    # ---------------------------------------------------------- helpers

    def _slugify(self, ref: ObjectRef) -> str:
        base = object_name(ref).replace(".", "_")
        return _SLUG_RX.sub("_", base).strip("_") or "lineage"


def _json_default(o: Any) -> Any:
    if isinstance(o, datetime):
        return o.isoformat()
    if isinstance(o, Path):
        return str(o)
    raise TypeError(f"cannot serialize {type(o).__name__}")
