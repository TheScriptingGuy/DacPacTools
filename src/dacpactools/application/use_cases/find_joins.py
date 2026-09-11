from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from dacpactools.application.dto.find_joins import FindJoinsRequest, FindJoinsResult
from dacpactools.application.dto.lineage_request import DacPacSource, LiveConnectionSource
from dacpactools.application.ports._handles import DacPacHandle
from dacpactools.application.ports.dacpac_service import IDacPacService
from dacpactools.application.ports.scriptdom_parser import IScriptDomParser
from dacpactools.domain.enums import ObjectType
from dacpactools.domain.identifiers import ObjectRef
from dacpactools.domain.joins import JoinReport, JoinUsage
from dacpactools.infrastructure.scriptdom.join_finder import find_joins_for
from dacpactools.presentation.logging_config import get_logger

log = get_logger("find_joins")

_ENUM_TYPES = (
    ObjectType.VIEW,
    ObjectType.PROCEDURE,
    ObjectType.TABLE_VALUED_FUNCTION,
    ObjectType.SCALAR_FUNCTION,
)


@dataclass
class FindJoinsUseCase:
    dacpac: IDacPacService
    parser: IScriptDomParser

    def execute(self, request: FindJoinsRequest) -> FindJoinsResult:
        request.output_dir.mkdir(parents=True, exist_ok=True)
        handle = self._load(request)
        usages: list[JoinUsage] = []
        warnings: list[str] = []
        scanned = 0
        for consumer_type in _ENUM_TYPES:
            for consumer in self._enumerate(handle, consumer_type):
                scanned += 1
                script = self.dacpac.get_body_script(consumer)
                if not script:
                    continue
                parsed = self.parser.parse(script)
                if parsed.errors:
                    warnings.append(
                        f"parse errors in {consumer.ref.qualified()}: {parsed.errors}"
                    )
                try:
                    usages.extend(find_joins_for(consumer.ref, parsed.fragment, request.target))
                except Exception as e:  # noqa: BLE001
                    warnings.append(f"join scan failed for {consumer.ref.qualified()}: {e}")
        log.info(
            "join scan complete",
            target=request.target.qualified(),
            scanned=scanned,
            hits=len(usages),
        )
        report = JoinReport(
            target=request.target,
            usages=tuple(usages),
            warnings=tuple(warnings),
        )
        artifacts = self._write_artifacts(report, request.output_dir)
        return FindJoinsResult(report=report, artifacts=artifacts)

    def _load(self, request: FindJoinsRequest) -> DacPacHandle:
        src = request.source
        if isinstance(src, DacPacSource):
            return self.dacpac.load(src.path)
        if isinstance(src, LiveConnectionSource):
            return self.dacpac.load_from_live(src.connection, request.output_dir / ".tmp")
        raise TypeError(f"unsupported source: {type(src).__name__}")

    def _enumerate(self, handle: DacPacHandle, object_type: ObjectType):
        from dacpactools.application.ports._handles import TSqlObjectHandle
        from dacpactools.infrastructure.dacpac.dacpac_service import _clr_types
        from dacpactools.infrastructure.dacpac.object_mapper import to_object_ref

        _, ModelSchema, DacQueryScopes, _ = _clr_types()
        mapping = {
            ObjectType.PROCEDURE: ModelSchema.Procedure,
            ObjectType.VIEW: ModelSchema.View,
            ObjectType.TABLE_VALUED_FUNCTION: ModelSchema.TableValuedFunction,
            ObjectType.SCALAR_FUNCTION: ModelSchema.ScalarFunction,
        }
        ms = mapping.get(object_type)
        if ms is None:
            return
        for obj in handle.model.GetObjects(DacQueryScopes.UserDefined, ms):
            ref = to_object_ref(obj)
            resolved = ObjectRef(
                database=ref.database,
                schema=ref.schema_name,
                name=ref.name,
                object_type=object_type,
            )
            yield TSqlObjectHandle(ref=resolved, tsql_object=obj)

    def _write_artifacts(self, report: JoinReport, out_dir: Path) -> dict[str, Path]:
        slug = _slug(report.target)
        json_path = out_dir / f"joins_{slug}.json"
        json_path.write_text(
            json.dumps(_report_dict(report), indent=2), encoding="utf-8"
        )
        html_path = out_dir / f"joins_{slug}.html"
        html_path.write_text(_render_html(report), encoding="utf-8")
        return {"json": json_path, "html": html_path}


def _slug(ref: ObjectRef) -> str:
    return f"{ref.schema_name}_{ref.name}".replace(".", "_")


def _report_dict(r: JoinReport) -> dict:
    return {
        "target": r.target.qualified(),
        "usages": [
            {
                "consumer": u.consumer.qualified(),
                "target": u.target.qualified(),
                "other": u.other.qualified(),
                "joinType": u.join_type,
                "targetAlias": u.target_alias,
                "otherAlias": u.other_alias,
                "pairs": [
                    {"target": p.target_column, "other": p.other_column} for p in u.pairs
                ],
                "onExpression": u.on_expression,
            }
            for u in r.usages
        ],
        "warnings": list(r.warnings),
    }


def _render_html(r: JoinReport) -> str:
    rows = "".join(
        f"<tr><td>{_esc(u.consumer.qualified())}</td>"
        f"<td>{_esc(u.join_type)}</td>"
        f"<td>{_esc(u.other.qualified())}</td>"
        f"<td>{_esc(', '.join(f'{p.target_column} = {p.other_column}' for p in u.pairs) or '')}</td>"
        f"<td><code>{_esc(u.on_expression or '')}</code></td></tr>"
        for u in r.usages
    )
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Joins on {_esc(r.target.qualified())}</title>
<style>
  body {{ font-family: system-ui, sans-serif; background:#111; color:#eee; padding:20px; }}
  h1 {{ font-size:18px; }}
  table {{ border-collapse: collapse; width:100%; font-size:12px; }}
  th,td {{ text-align:left; padding:4px 8px; border-bottom:1px solid #333; vertical-align:top; }}
  th {{ background:#222; }}
  code {{ background:#000; color:#7fc; padding:1px 4px; border-radius:3px; }}
</style></head>
<body>
  <h1>Joins on {_esc(r.target.qualified())} — {len(r.usages)} usage(s)</h1>
  <table><thead><tr><th>Consumer</th><th>Join</th><th>Other table</th><th>Keys</th><th>ON</th></tr></thead>
  <tbody>{rows or '<tr><td colspan=5><em>no usages found</em></td></tr>'}</tbody></table>
</body></html>
"""


def _esc(s: str) -> str:
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
