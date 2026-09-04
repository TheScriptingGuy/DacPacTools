from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from dacpactools.application.dto.column_usage import (
    FindColumnUsageRequest,
    FindColumnUsageResult,
)
from dacpactools.application.dto.lineage_request import (
    DacPacSource,
    LiveConnectionSource,
)
from dacpactools.application.ports._handles import DacPacHandle
from dacpactools.application.ports.dacpac_service import IDacPacService
from dacpactools.application.ports.scriptdom_parser import IScriptDomParser
from dacpactools.domain.column_usage import (
    ColumnCitation,
    ColumnUsageReport,
)
from dacpactools.domain.enums import ObjectType
from dacpactools.domain.identifiers import ColumnRef, ObjectRef
from dacpactools.infrastructure.scriptdom.column_usage_finder import find_column_usage
from dacpactools.presentation.logging_config import get_logger

log = get_logger("find_column_usage")

_ENUM_TYPES = (
    ObjectType.VIEW,
    ObjectType.PROCEDURE,
    ObjectType.TABLE_VALUED_FUNCTION,
    ObjectType.SCALAR_FUNCTION,
)


@dataclass
class FindColumnUsageUseCase:
    dacpac: IDacPacService
    parser: IScriptDomParser

    def execute(self, request: FindColumnUsageRequest) -> FindColumnUsageResult:
        request.output_dir.mkdir(parents=True, exist_ok=True)
        handle = self._load(request)
        citations: list[ColumnCitation] = []
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
                    hits, ws = find_column_usage(
                        consumer.ref,
                        parsed.fragment,
                        request.target_owner,
                        request.target_column,
                    )
                    citations.extend(hits)
                    for w in ws:
                        warnings.append(f"{consumer.ref.qualified()}: {w}")
                except Exception as e:  # noqa: BLE001
                    warnings.append(
                        f"column-usage scan failed for {consumer.ref.qualified()}: {e}"
                    )
        target_col_ref = ColumnRef(
            owner=request.target_owner, column_name=request.target_column
        )
        report = ColumnUsageReport(
            target_column=target_col_ref,
            citations=tuple(citations),
            warnings=tuple(warnings),
        )
        log.info(
            "column-usage scan complete",
            target=f"{request.target_owner.qualified()}.[{request.target_column}]",
            scanned=scanned,
            hits=len(citations),
        )
        artifacts = self._write_artifacts(report, request.output_dir)
        return FindColumnUsageResult(report=report, artifacts=artifacts)

    def _load(self, request: FindColumnUsageRequest) -> DacPacHandle:
        src = request.source
        if isinstance(src, DacPacSource):
            return self.dacpac.load(src.path)
        if isinstance(src, LiveConnectionSource):
            return self.dacpac.load_from_live(src.connection, request.output_dir / ".tmp")
        raise TypeError(f"unsupported source: {type(src).__name__}")

    def _enumerate(self, handle: DacPacHandle, object_type: ObjectType):  # noqa: ANN202
        from dacpactools.application.ports._handles import (  # noqa: PLC0415
            TSqlObjectHandle,
        )
        from dacpactools.infrastructure.dacpac.dacpac_service import (  # noqa: PLC0415
            _clr_types,
        )
        from dacpactools.infrastructure.dacpac.object_mapper import (  # noqa: PLC0415
            to_object_ref,
        )

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

    def _write_artifacts(
        self, report: ColumnUsageReport, out_dir: Path
    ) -> dict[str, Path]:
        slug = _slug(report.target_column)
        json_path = out_dir / f"column_usage_{slug}.json"
        json_path.write_text(
            json.dumps(_report_dict(report), indent=2), encoding="utf-8"
        )
        html_path = out_dir / f"column_usage_{slug}.html"
        html_path.write_text(_render_html(report), encoding="utf-8")
        return {"json": json_path, "html": html_path}


def _slug(c: ColumnRef) -> str:
    o = c.owner
    return f"{o.schema_name}_{o.name}_{c.column_name}".replace(".", "_")


def _report_dict(r: ColumnUsageReport) -> dict:
    c = r.target_column
    return {
        "target": {
            "owner": c.owner.qualified(),
            "column": c.column_name,
        },
        "citations": [
            {
                "consumer": u.consumer.qualified(),
                "context": u.context.value,
                "aliasUsed": u.alias_used,
                "expression": u.expression,
                "statementKind": u.statement_kind,
                "line": u.line,
            }
            for u in r.citations
        ],
        "warnings": list(r.warnings),
    }


_CTX_COLORS: dict[str, str] = {
    "PROJECTION": "#2aa198",
    "JOIN_ON": "#4b6cff",
    "WHERE": "#dc322f",
    "HAVING": "#cb4b16",
    "GROUP_BY": "#859900",
    "ORDER_BY": "#586e75",
    "PARTITION_BY": "#b58900",
    "AGGREGATE_ARG": "#6c71c4",
    "CASE_WHEN": "#d33682",
    "CASE_THEN": "#d33682",
    "CASE_ELSE": "#d33682",
    "ASSIGN_TARGET": "#8fbc8f",
    "ASSIGN_VALUE": "#5f9ea0",
}


def _render_html(r: ColumnUsageReport) -> str:
    tc = r.target_column
    header = f"{tc.owner.qualified()}.[{tc.column_name}]"
    rows_html = []
    for u in r.citations:
        color = _CTX_COLORS.get(u.context.value, "#586e75")
        badge = (
            f'<span class="ctx" style="background:{color}">{_esc(u.context.value)}</span>'
        )
        line = f"L{u.line}" if u.line else ""
        rows_html.append(
            f"<tr><td>{_esc(u.consumer.qualified())}</td>"
            f"<td>{badge}</td>"
            f"<td>{_esc(u.statement_kind)}</td>"
            f"<td>{_esc(u.alias_used or '')}</td>"
            f"<td><code>{_esc(u.expression or '')}</code></td>"
            f"<td>{line}</td></tr>"
        )
    warns_html = "".join(f"<li>{_esc(w)}</li>" for w in r.warnings)
    body = "".join(rows_html) or (
        '<tr><td colspan=6><em>no citations found</em></td></tr>'
    )
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Column usage — {_esc(header)}</title>
<style>
  body {{ font-family: system-ui, sans-serif; background:#111; color:#eee; padding:20px; }}
  h1 {{ font-size:18px; }}
  h2 {{ font-size:14px; margin-top:24px; color:#f0a020; }}
  table {{ border-collapse: collapse; width:100%; font-size:12px; }}
  th,td {{ text-align:left; padding:4px 8px; border-bottom:1px solid #333; vertical-align:top; }}
  th {{ background:#222; }}
  code {{ background:#000; color:#7fc; padding:1px 4px; border-radius:3px; }}
  .ctx {{ display:inline-block; padding:2px 6px; border-radius:6px; color:#000; font-weight:600; font-size:11px; }}
</style></head>
<body>
  <h1>Column usage: {_esc(header)} — {len(r.citations)} citation(s)</h1>
  <table><thead><tr><th>Consumer</th><th>Context</th><th>Stmt</th><th>Alias</th><th>Expression</th><th>Line</th></tr></thead>
  <tbody>{body}</tbody></table>
  {'<h2>Warnings (' + str(len(r.warnings)) + ')</h2><ul>' + warns_html + '</ul>' if r.warnings else ''}
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
