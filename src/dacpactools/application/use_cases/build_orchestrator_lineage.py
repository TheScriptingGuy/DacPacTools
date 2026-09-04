from __future__ import annotations

import hashlib
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from uuid import uuid4

from dacpactools.application.dto.lineage_request import BuildLineageRequest
from dacpactools.application.dto.orchestrator_request import BuildOrchestratorLineageRequest
from dacpactools.application.dto.orchestrator_result import BuildOrchestratorLineageResult
from dacpactools.application.ports.clock import IClock
from dacpactools.application.ports.env_map_service import IEnvMapService
from dacpactools.application.ports.orchestrator_artifact_reader import (
    IOrchestratorArtifactReader,
)
from dacpactools.application.ports.orchestrator_writers import (
    IOrchestratorLineageWriter,
    IOrchestratorVizRenderer,
)
from dacpactools.application.ports.scriptdom_parser import IScriptDomParser
from dacpactools.application.use_cases.build_lineage import BuildLineageUseCase
from dacpactools.domain.enums import ObjectType, OrchestratorKind
from dacpactools.domain.errors import (
    InvalidObjectName,
    UnresolvableObject,
    UnresolvableOrchestratorArtifact,
)
from dacpactools.domain.identifiers import ObjectRef
from dacpactools.domain.lineage import LineageGraph, LineageNode
from dacpactools.domain.orchestrator import (
    OrchestratorLineageGraph,
    OrchestratorRun,
    Pipeline,
    SqlActivity,
)
from dacpactools.presentation.logging_config import get_logger

log = get_logger("build_orchestrator_lineage")


_EXEC_PREFIX = re.compile(r"^\s*(?:EXEC|EXECUTE)\s+", re.IGNORECASE)
_QUALIFIED_NAME = re.compile(
    r"^((?:\[[^\]]+\]|[A-Za-z_][A-Za-z0-9_]*)"
    r"(?:\.(?:\[[^\]]+\]|[A-Za-z_][A-Za-z0-9_]*)){0,2})"
)


@dataclass
class BuildOrchestratorLineageUseCase:
    readers: tuple[IOrchestratorArtifactReader, ...]
    env_map: IEnvMapService
    inner: BuildLineageUseCase
    parser: IScriptDomParser
    ol_writer: IOrchestratorLineageWriter
    viz: IOrchestratorVizRenderer
    clock: IClock

    def execute(
        self, request: BuildOrchestratorLineageRequest
    ) -> BuildOrchestratorLineageResult:
        request.output_dir.mkdir(parents=True, exist_ok=True)
        self.env_map.load(request.env_map_path)

        files = _discover_files(request.artifact_path, request.artifact_type)
        run_warnings: list[str] = []
        pipelines: list[Pipeline] = []

        for f, reader in _match_readers(files, self.readers, request.artifact_type):
            try:
                pipelines.extend(reader.read(f))
            except Exception as e:  # noqa: BLE001
                run_warnings.append(f"failed to read {f}: {type(e).__name__}: {e}")

        if not pipelines:
            raise UnresolvableOrchestratorArtifact(
                f"no readable orchestrator artifacts under {request.artifact_path}"
            )

        graph_cache: dict[str, LineageGraph] = {}
        analysed_pipelines: list[Pipeline] = []
        for pipeline in pipelines:
            new_activities: list[SqlActivity] = []
            for activity in pipeline.activities:
                updated, gid, graph = self._analyse_activity(
                    activity, pipeline, request, graph_cache
                )
                if gid is not None and graph is not None and gid not in graph_cache:
                    graph_cache[gid] = graph
                new_activities.append(updated)
            analysed_pipelines.append(
                pipeline.model_copy(update={"activities": tuple(new_activities)})
            )

        run = OrchestratorRun(
            run_id=str(uuid4()),
            name=request.artifact_path.stem or "run",
            pipelines=tuple(analysed_pipelines),
        )
        orch_graph = OrchestratorLineageGraph(
            run=run,
            lineage_graphs=graph_cache,
            warnings=tuple(run_warnings),
        )

        artifacts: dict[str, Path] = {}
        if "json" in request.emit_formats:
            artifacts["openlineage_json"] = self.ol_writer.write(
                orch_graph, request.output_dir
            )
        if "html" in request.emit_formats:
            artifacts["html"] = self.viz.render(orch_graph, request.output_dir)

        return BuildOrchestratorLineageResult(
            graph=orch_graph,
            artifacts=artifacts,
            warnings=tuple(run_warnings),
        )

    def _analyse_activity(
        self,
        activity: SqlActivity,
        pipeline: Pipeline,
        request: BuildOrchestratorLineageRequest,
        graph_cache: dict[str, LineageGraph],
    ) -> tuple[SqlActivity, str | None, LineageGraph | None]:
        warnings = list(activity.warnings)

        if activity.sql_text is None:
            return activity, None, None

        if activity.env_ref is None:
            warnings.append(f"no env_ref for activity '{activity.activity_id}'")
            return activity.model_copy(update={"warnings": tuple(warnings)}), None, None

        source = self.env_map.resolve(activity.env_ref)
        if source is None:
            warnings.append(
                f"no env mapping for '{activity.env_ref}' (activity '{activity.activity_id}')"
            )
            return activity.model_copy(update={"warnings": tuple(warnings)}), None, None

        gid = _stable_gid(activity.env_ref, activity.sql_text)
        if gid in graph_cache:
            return (
                activity.model_copy(
                    update={"lineage_graph_id": gid, "warnings": tuple(warnings)}
                ),
                None,
                None,
            )

        proc_ref = _parse_bare_exec(activity.sql_text)
        if proc_ref is not None:
            try:
                inner_req = BuildLineageRequest(
                    root_object=proc_ref,
                    source=source,
                    output_dir=request.output_dir / ".tmp",
                    emit_formats=frozenset(),
                    max_depth=request.max_depth,
                )
                inner_result = self.inner.execute(inner_req)
                return (
                    activity.model_copy(
                        update={
                            "lineage_graph_id": gid,
                            "warnings": tuple(warnings),
                        }
                    ),
                    gid,
                    inner_result.graph,
                )
            except UnresolvableObject as e:
                warnings.append(
                    f"cannot resolve {proc_ref.qualified()} for '{activity.activity_id}': {e}"
                )
                return (
                    activity.model_copy(update={"warnings": tuple(warnings)}),
                    None,
                    None,
                )
            except Exception as e:  # noqa: BLE001
                warnings.append(
                    f"lineage failed for '{activity.activity_id}': {type(e).__name__}: {e}"
                )
                return (
                    activity.model_copy(update={"warnings": tuple(warnings)}),
                    None,
                    None,
                )

        # Non-EXEC path: parse for validation, emit synthetic-root placeholder graph
        parsed = self.parser.parse(activity.sql_text)
        if parsed.errors:
            warnings.append(
                f"parse error in activity '{activity.activity_id}': {parsed.errors[0]}"
            )
        warnings.append(
            f"statement-level analysis only for non-EXEC SQL in activity '{activity.activity_id}'"
        )
        synth = _synthetic_activity_ref(pipeline.pipeline_id, activity.activity_id)
        graph = LineageGraph(
            root=synth,
            nodes={LineageGraph.node_key(synth): LineageNode(ref=synth)},
        )
        return (
            activity.model_copy(
                update={"lineage_graph_id": gid, "warnings": tuple(warnings)}
            ),
            gid,
            graph,
        )


ArtifactType = Literal["adf", "airflow", "auto"]


def _discover_files(root: Path, artifact_type: ArtifactType) -> list[Path]:
    if root.is_file():
        return [root]
    if not root.is_dir():
        return []
    if artifact_type == "adf":
        globs: tuple[str, ...] = ("**/*.json",)
    elif artifact_type == "airflow":
        globs = ("**/*.py",)
    else:
        globs = ("**/*.json", "**/*.py")
    seen: set[Path] = set()
    out: list[Path] = []
    for g in globs:
        for p in sorted(root.glob(g)):
            if p.is_file() and p not in seen:
                out.append(p)
                seen.add(p)
    return out


def _match_readers(
    files: list[Path],
    readers: tuple[IOrchestratorArtifactReader, ...],
    artifact_type: ArtifactType,
) -> Iterator[tuple[Path, IOrchestratorArtifactReader]]:
    for f in files:
        for r in readers:
            if artifact_type == "adf" and r.kind != OrchestratorKind.ADF:
                continue
            if artifact_type == "airflow" and r.kind != OrchestratorKind.AIRFLOW:
                continue
            if r.can_read(f):
                yield f, r
                break


def _parse_bare_exec(sql: str) -> ObjectRef | None:
    prefix = _EXEC_PREFIX.match(sql)
    if prefix is None:
        return None
    remainder = sql[prefix.end():].lstrip()
    name_match = _QUALIFIED_NAME.match(remainder)
    if name_match is None:
        return None
    trailing = remainder[name_match.end():].strip().rstrip(";").strip()
    if trailing and not trailing.startswith(("@", ",", "--")):
        return None
    try:
        return ObjectRef.parse(name_match.group(1), object_type=ObjectType.PROCEDURE)
    except InvalidObjectName:
        return None


def _synthetic_activity_ref(pipeline_id: str, activity_id: str) -> ObjectRef:
    safe_pipe = re.sub(r"[^A-Za-z0-9_]", "_", pipeline_id)
    safe_act = re.sub(r"[^A-Za-z0-9_]", "_", activity_id)
    return ObjectRef(
        database=None,
        schema="orchestrator",
        name=f"activity_{safe_pipe}_{safe_act}",
        object_type=ObjectType.UNKNOWN,
    )


def _stable_gid(env_ref: str, sql_text: str) -> str:
    h = hashlib.sha1()
    h.update(env_ref.encode("utf-8"))
    h.update(b"::")
    h.update(sql_text.encode("utf-8"))
    return h.hexdigest()[:16]
