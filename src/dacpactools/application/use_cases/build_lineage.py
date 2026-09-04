from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from dacpactools.application.dto.lineage_request import (
    BuildLineageRequest,
    DacPacSource,
    LiveConnectionSource,
)
from dacpactools.application.dto.lineage_result import BuildLineageResult
from dacpactools.application.ports._handles import (
    ColumnResolver,
    DacPacHandle,
    TSqlObjectHandle,
)
from dacpactools.application.ports.clock import IClock
from dacpactools.application.ports.dacpac_service import IDacPacService
from dacpactools.application.ports.lineage_writer import ILineageWriter
from dacpactools.application.ports.scriptdom_parser import IScriptDomParser
from dacpactools.application.ports.sql_metadata_service import ISqlMetadataService
from dacpactools.application.ports.token_provider import ITokenProvider
from dacpactools.application.ports.viz_renderer import IVizRenderer
from dacpactools.domain.enums import EdgeKind, ObjectType
from dacpactools.domain.errors import UnresolvableObject
from dacpactools.domain.identifiers import ObjectRef
from dacpactools.domain.lineage import (
    ColumnMapping,
    ColumnUsage,
    LineageEdge,
    LineageGraph,
    LineageNode,
)
from dacpactools.presentation.logging_config import get_logger

log = get_logger("build_lineage")


@dataclass
class BuildLineageUseCase:
    dacpac: IDacPacService
    parser: IScriptDomParser
    ol_writer: ILineageWriter
    viz: IVizRenderer
    clock: IClock
    sql_meta: ISqlMetadataService | None = None
    tokens: ITokenProvider | None = None

    def execute(self, request: BuildLineageRequest) -> BuildLineageResult:
        request.output_dir.mkdir(parents=True, exist_ok=True)
        handle = self._resolve_source(request)
        root = self.dacpac.find_object(handle, request.root_object)
        if root is None:
            raise UnresolvableObject(f"root object not found: {request.root_object.qualified()}")

        nodes: dict[str, LineageNode] = {}
        edges: list[LineageEdge] = []
        warnings: list[str] = []
        obj_handles: dict[str, TSqlObjectHandle] = {}

        self._bfs_traverse(root, request, nodes, edges, obj_handles)
        self._column_mapping_pass(nodes, obj_handles, warnings)

        graph = LineageGraph(
            root=root.ref,
            nodes=nodes,
            edges=tuple(edges),
            warnings=tuple(warnings),
        )
        artifacts: dict[str, Path] = {}
        if "json" in request.emit_formats:
            artifacts["openlineage_json"] = self.ol_writer.write(graph, request.output_dir)
        if "html" in request.emit_formats:
            artifacts["html"] = self.viz.render(graph, request.output_dir)
        return BuildLineageResult(graph=graph, artifacts=artifacts, warnings=tuple(warnings))

    def _resolve_source(self, request: BuildLineageRequest) -> DacPacHandle:
        src = request.source
        if isinstance(src, DacPacSource):
            return self.dacpac.load(src.path)
        if isinstance(src, LiveConnectionSource):
            return self.dacpac.load_from_live(src.connection, request.output_dir / ".tmp")
        raise TypeError(f"unsupported source: {type(src).__name__}")

    def _bfs_traverse(
        self,
        root: TSqlObjectHandle,
        request: BuildLineageRequest,
        nodes: dict[str, LineageNode],
        edges: list[LineageEdge],
        obj_handles: dict[str, TSqlObjectHandle],
    ) -> None:
        queue: deque[tuple[TSqlObjectHandle, int]] = deque([(root, 0)])
        while queue:
            obj, depth = queue.popleft()
            key = LineageGraph.node_key(obj.ref)
            if key in nodes:
                continue
            cols = self.dacpac.get_columns(obj)
            nodes[key] = LineageNode(ref=obj.ref, columns=cols)
            obj_handles[key] = obj

            if obj.ref.object_type.is_leaf:
                continue
            if obj.ref.object_type is ObjectType.SYNONYM:
                target = self.dacpac.resolve_synonym_target(obj)
                if target is not None:
                    edges.append(LineageEdge(from_ref=obj.ref, to_ref=target))
                continue
            if request.max_depth is not None and depth >= request.max_depth:
                continue

            for referenced in self.dacpac.get_referenced(obj):
                if referenced.ref.object_type not in request.include_object_types:
                    continue
                edges.append(LineageEdge(from_ref=obj.ref, to_ref=referenced.ref))
                queue.append((referenced, depth + 1))

    def _column_mapping_pass(
        self,
        nodes: dict[str, LineageNode],
        obj_handles: dict[str, TSqlObjectHandle],
        warnings: list[str],
    ) -> None:
        ordered = list(nodes.keys())
        for key in reversed(ordered):
            node = nodes[key]
            if node.ref.object_type.is_leaf or not node.ref.object_type.has_body:
                continue
            handle = obj_handles.get(key)
            if handle is None:
                continue
            script = self.dacpac.get_body_script(handle)
            if not script:
                warnings.append(f"no script for {node.ref.qualified()}")
                continue
            parsed = self.parser.parse(script)
            if parsed.errors:
                warnings.append(f"parse errors in {node.ref.qualified()}: {parsed.errors}")
            resolver = self._build_resolver(node, nodes)
            mappings: tuple[ColumnMapping, ...]
            predicates: tuple[ColumnUsage, ...] = ()
            ctas_specs: tuple = ()
            try:
                extract_lineage = getattr(self.parser, "extract_lineage", None)
                if extract_lineage is not None:
                    parsed_lineage = extract_lineage(parsed, node, resolver)
                    mappings = tuple(parsed_lineage.mappings)
                    predicates = tuple(parsed_lineage.predicates)
                    ctas_specs = tuple(parsed_lineage.ctas_specs)
                else:
                    mappings = self.parser.extract_column_mappings(parsed, node, resolver)
            except Exception as e:  # noqa: BLE001
                warnings.append(f"column mapping failed for {node.ref.qualified()}: {e}")
                continue
            nodes[key] = LineageNode(
                ref=node.ref,
                columns=node.columns,
                column_mappings=mappings,
                predicates=predicates,
                synapse=node.synapse,
            )
            for ct in ctas_specs:
                self._apply_ctas_spec(ct, nodes)

    def _apply_ctas_spec(self, ct: Any, nodes: dict[str, LineageNode]) -> None:
        target_key = LineageGraph.node_key(ct.target)
        target = nodes.get(target_key)
        if target is None:
            return
        nodes[target_key] = LineageNode(
            ref=target.ref,
            columns=target.columns,
            column_mappings=target.column_mappings,
            predicates=target.predicates,
            synapse=ct.spec,
        )

    def _build_resolver(
        self, node: LineageNode, nodes: dict[str, LineageNode]
    ) -> ColumnResolver:
        scope: dict[str, tuple] = {}
        for other in nodes.values():
            if other.ref == node.ref:
                continue
            key1 = other.ref.name.lower()
            key2 = f"{other.ref.schema_name.lower()}.{other.ref.name.lower()}"
            scope[key1] = other.columns
            scope[key2] = other.columns
        return ColumnResolver(scope=scope)


def _now(clock: IClock) -> datetime:
    try:
        return clock.now_utc()
    except Exception:  # noqa: BLE001
        return datetime.now(timezone.utc)
