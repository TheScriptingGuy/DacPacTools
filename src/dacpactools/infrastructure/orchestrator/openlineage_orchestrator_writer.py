from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dacpactools.application.ports.clock import IClock, SystemClock
from dacpactools.domain.orchestrator import (
    OrchestratorLineageGraph,
    Pipeline,
    SqlActivity,
)
from dacpactools.infrastructure.writers._tree_facet import (
    ORCHESTRATOR_FACET_SCHEMA,
    PRODUCER,
    tree_facet,
)
from dacpactools.infrastructure.writers.filesystem import atomic_write_text

_SCHEMA_URL = "https://openlineage.io/spec/2-0-2/OpenLineage.json"
_SLUG_RX = re.compile(r"[^A-Za-z0-9_.-]+")


class OpenLineageOrchestratorWriter:
    def __init__(
        self, namespace: str = "orchestrator://local", clock: IClock | None = None
    ) -> None:
        self.namespace = namespace.rstrip("/")
        self._clock = clock or SystemClock()

    def write(self, graph: OrchestratorLineageGraph, out_dir: Path) -> Path:
        now = self._clock.now_utc().replace(tzinfo=UTC)
        payload = self._build_payload(graph, now)
        text = json.dumps(payload, indent=2, sort_keys=False, default=_json_default)
        slug = _slug(graph.run.name)
        stamp = now.strftime("%Y%m%dT%H%M%SZ")
        path = out_dir / f"orchestrator_{slug}_{stamp}.json"
        atomic_write_text(path, text)
        return path

    def _build_payload(self, graph: OrchestratorLineageGraph, now: datetime) -> dict[str, Any]:
        orch_facet: dict[str, Any] = {
            "_producer": PRODUCER,
            "_schemaURL": ORCHESTRATOR_FACET_SCHEMA,
            "runId": graph.run.run_id,
            "runName": graph.run.name,
            "pipelines": [self._pipeline(p) for p in graph.run.pipelines],
            "warnings": list(graph.warnings),
        }
        activity_lineage: dict[str, Any] = {
            "_producer": PRODUCER,
            "_schemaURL": ORCHESTRATOR_FACET_SCHEMA,
            "graphs": {gid: tree_facet(g) for gid, g in graph.lineage_graphs.items()},
        }
        return {
            "eventType": "COMPLETE",
            "eventTime": now.isoformat(),
            "run": {
                "runId": graph.run.run_id,
                "facets": {
                    "dacpactools_orchestrator_tree": orch_facet,
                    "dacpactools_activity_lineage": activity_lineage,
                },
            },
            "job": {
                "namespace": self.namespace,
                "name": f"orchestrator:{graph.run.name}",
            },
            "inputs": [],
            "outputs": [],
            "producer": PRODUCER,
            "schemaURL": _SCHEMA_URL,
        }

    def _pipeline(self, p: Pipeline) -> dict[str, Any]:
        return {
            "pipelineId": p.pipeline_id,
            "name": p.name,
            "kind": p.kind.value,
            "sourcePath": str(p.source_path),
            "activities": [self._activity(a) for a in p.activities],
            "dependencies": [
                {"from": e.from_activity_id, "to": e.to_activity_id}
                for e in p.activity_edges
            ],
        }

    def _activity(self, a: SqlActivity) -> dict[str, Any]:
        return {
            "activityId": a.activity_id,
            "name": a.name,
            "activityType": a.activity_type.value,
            "envRef": a.env_ref,
            "order": a.order,
            "sqlText": a.sql_text,
            "sourceUri": a.source_uri,
            "dependsOn": list(a.depends_on),
            "lineageGraphId": a.lineage_graph_id,
            "warnings": list(a.warnings),
        }


def _slug(text: str) -> str:
    return _SLUG_RX.sub("_", text).strip("_") or "run"


def _json_default(o: Any) -> Any:
    if isinstance(o, datetime):
        return o.isoformat()
    if isinstance(o, Path):
        return str(o)
    raise TypeError(f"cannot serialize {type(o).__name__}")
