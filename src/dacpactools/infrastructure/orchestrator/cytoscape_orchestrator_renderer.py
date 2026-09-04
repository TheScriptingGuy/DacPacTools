from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from dacpactools.domain.orchestrator import (
    OrchestratorLineageGraph,
    Pipeline,
    SqlActivity,
)
from dacpactools.infrastructure.writers._tree_facet import tree_facet
from dacpactools.infrastructure.writers.filesystem import atomic_write_text

_SLUG_RX = re.compile(r"[^A-Za-z0-9_.-]+")
_TEMPLATE_DIR = Path(__file__).parent.parent / "viz" / "templates"


class CytoscapeOrchestratorRenderer:
    def __init__(self) -> None:
        self._env = Environment(
            loader=FileSystemLoader(str(_TEMPLATE_DIR)),
            autoescape=select_autoescape(["html", "xml"]),
        )

    def render(self, graph: OrchestratorLineageGraph, out_dir: Path) -> Path:
        data = self._graph_to_data(graph)
        template = self._env.get_template("orchestrator.html.j2")
        html = template.render(
            run_name=graph.run.name,
            pipeline_count=len(graph.run.pipelines),
            activity_count=sum(len(p.activities) for p in graph.run.pipelines),
            graph_count=len(graph.lineage_graphs),
            warnings=list(graph.warnings),
            data_json=json.dumps(data, indent=None, separators=(",", ":")),
        )
        slug = _SLUG_RX.sub("_", graph.run.name).strip("_") or "run"
        path = out_dir / f"orchestrator_{slug}.html"
        atomic_write_text(path, html)
        return path

    def _graph_to_data(self, graph: OrchestratorLineageGraph) -> dict[str, Any]:
        return {
            "run": {"runId": graph.run.run_id, "name": graph.run.name},
            "pipelines": [self._pipeline(p) for p in graph.run.pipelines],
            "lineageGraphs": {gid: tree_facet(g) for gid, g in graph.lineage_graphs.items()},
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
