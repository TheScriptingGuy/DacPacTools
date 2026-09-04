from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any, ClassVar

from dacpactools.domain.enums import ActivityType, OrchestratorKind
from dacpactools.domain.orchestrator import ActivityEdge, Pipeline, SqlActivity

_ACTIVITY_TYPE_MAP: dict[str, ActivityType] = {
    "SqlServerStoredProcedure": ActivityType.SQL_STORED_PROCEDURE,
    "SqlPoolStoredProcedure": ActivityType.SQL_STORED_PROCEDURE,
    "AzureSqlServerStoredProcedureActivity": ActivityType.SQL_STORED_PROCEDURE,
    "Script": ActivityType.SQL_SCRIPT,
    "Lookup": ActivityType.SQL_LOOKUP,
    "Copy": ActivityType.SQL_COPY_SOURCE,
    "CopyActivity": ActivityType.SQL_COPY_SOURCE,
}

_SQL_SOURCE_TYPES = {
    "SqlSource",
    "AzureSqlSource",
    "SqlDWSource",
    "SqlMISource",
    "SqlServerSource",
}


class AdfArtifactReader:
    kind: ClassVar[OrchestratorKind] = OrchestratorKind.ADF

    def can_read(self, path: Path) -> bool:
        if path.suffix.lower() != ".json":
            return False
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        return _looks_like_adf(raw)

    def read(self, path: Path) -> tuple[Pipeline, ...]:
        raw: Any = json.loads(path.read_text(encoding="utf-8"))
        pipelines: list[Pipeline] = []
        for name, props in _iter_pipelines(raw):
            pipelines.append(self._build_pipeline(path, name, props))
        return tuple(pipelines)

    def _build_pipeline(
        self, source_path: Path, name: str, props: dict[str, Any]
    ) -> Pipeline:
        activities: list[SqlActivity] = []
        edges: list[ActivityEdge] = []
        counter = [0]
        acts_raw = props.get("activities")
        if not isinstance(acts_raw, list):
            acts_raw = []
        for act in _walk_activities(acts_raw):
            built = _to_activity(act, counter)
            if built is None:
                continue
            activities.append(built)
            for prev in built.depends_on:
                edges.append(
                    ActivityEdge(from_activity_id=prev, to_activity_id=built.activity_id)
                )
        return Pipeline(
            pipeline_id=name,
            name=name,
            kind=OrchestratorKind.ADF,
            source_path=source_path,
            activities=tuple(activities),
            activity_edges=tuple(edges),
        )


def _looks_like_adf(raw: Any) -> bool:
    if not isinstance(raw, dict):
        return False
    props = raw.get("properties")
    if isinstance(props, dict) and isinstance(props.get("activities"), list):
        return True
    resources = raw.get("resources")
    if isinstance(resources, list):
        for r in resources:
            if isinstance(r, dict) and str(r.get("type", "")).endswith("/pipelines"):
                return True
    return False


def _iter_pipelines(raw: Any) -> Iterator[tuple[str, dict[str, Any]]]:
    if not isinstance(raw, dict):
        return
    props = raw.get("properties")
    if isinstance(props, dict) and isinstance(props.get("activities"), list):
        name = str(raw.get("name") or "pipeline")
        yield name, props
        return
    resources = raw.get("resources")
    if isinstance(resources, list):
        for r in resources:
            if not isinstance(r, dict):
                continue
            if not str(r.get("type", "")).endswith("/pipelines"):
                continue
            full_name = str(r.get("name") or "pipeline")
            leaf = full_name.split("/")[-1].strip("'").strip('"')
            props_r = r.get("properties")
            if isinstance(props_r, dict):
                yield leaf, props_r


def _walk_activities(acts: list[Any]) -> Iterator[dict[str, Any]]:
    for a in acts:
        if not isinstance(a, dict):
            continue
        yield a
        tp = _dict_or_empty(a.get("typeProperties"))
        for sub_key in ("activities", "ifTrueActivities", "ifFalseActivities", "defaultActivities"):
            sub = tp.get(sub_key)
            if isinstance(sub, list):
                yield from _walk_activities(sub)
        cases = tp.get("cases")
        if isinstance(cases, list):
            for c in cases:
                if isinstance(c, dict) and isinstance(c.get("activities"), list):
                    yield from _walk_activities(c["activities"])


def _dict_or_empty(v: Any) -> dict[str, Any]:
    return v if isinstance(v, dict) else {}


def _to_activity(act: dict[str, Any], counter: list[int]) -> SqlActivity | None:
    name = str(act.get("name") or "").strip()
    if not name:
        return None
    raw_type = str(act.get("type") or "").strip()
    tp = _dict_or_empty(act.get("typeProperties"))
    env_ref = _linked_service_ref(act)
    counter[0] += 1
    order = counter[0]
    warnings: list[str] = []

    activity_type = _ACTIVITY_TYPE_MAP.get(raw_type, ActivityType.UNSUPPORTED)
    sql_text: str | None = None

    if activity_type == ActivityType.SQL_STORED_PROCEDURE:
        sql_text = _synth_exec(tp)
        if sql_text is None:
            warnings.append(f"activity '{name}' missing storedProcedureName")
    elif activity_type == ActivityType.SQL_SCRIPT:
        sql_text = _extract_script_text(tp)
        if sql_text is None:
            warnings.append(f"activity '{name}' Script has no scripts")
    elif activity_type in (ActivityType.SQL_LOOKUP, ActivityType.SQL_COPY_SOURCE):
        source = _dict_or_empty(tp.get("source"))
        if _is_sql_source(source):
            sql_text = _sql_from_source(source)
            if sql_text is None:
                if activity_type == ActivityType.SQL_LOOKUP:
                    warnings.append(
                        f"activity '{name}' Lookup has no sqlReaderQuery/sqlReaderStoredProcedureName"
                    )
                else:
                    activity_type = ActivityType.UNSUPPORTED
                    warnings.append(
                        f"activity '{name}' Copy source is table-only (no sqlReaderQuery)"
                    )
        else:
            activity_type = ActivityType.UNSUPPORTED
            src_type = source.get("type")
            warnings.append(
                f"activity '{name}' source type {src_type!r} is not a SQL source"
            )
    else:
        if _has_sqlish_field(tp):
            warnings.append(
                f"activity '{name}' type {raw_type!r} not supported but references SQL fields"
            )
        else:
            warnings.append(
                f"unsupported activity type '{raw_type}' in activity '{name}'"
            )

    if env_ref is None and activity_type != ActivityType.UNSUPPORTED:
        warnings.append(
            f"activity '{name}' has no linkedServiceName.referenceName; env cannot resolve"
        )

    return SqlActivity(
        activity_id=name,
        name=name,
        activity_type=activity_type,
        sql_text=sql_text,
        source_uri=None,
        env_ref=env_ref,
        order=order,
        depends_on=_deps(act),
        warnings=tuple(warnings),
    )


def _linked_service_ref(act: dict[str, Any]) -> str | None:
    ls = act.get("linkedServiceName")
    if isinstance(ls, dict):
        ref = ls.get("referenceName")
        if isinstance(ref, str) and ref.strip():
            return ref
    return None


def _deps(act: dict[str, Any]) -> tuple[str, ...]:
    deps = act.get("dependsOn")
    if not isinstance(deps, list):
        return ()
    out: list[str] = []
    for d in deps:
        if isinstance(d, dict):
            prev = d.get("activity")
            if isinstance(prev, str) and prev.strip():
                out.append(prev)
    return tuple(out)


def _is_sql_source(source: Any) -> bool:
    if not isinstance(source, dict):
        return False
    st = source.get("type")
    return isinstance(st, str) and st in _SQL_SOURCE_TYPES


def _sql_from_source(source: dict[str, Any]) -> str | None:
    q = source.get("sqlReaderQuery")
    if isinstance(q, str) and q.strip():
        return q
    sp = source.get("sqlReaderStoredProcedureName")
    if isinstance(sp, str) and sp.strip():
        params = source.get("storedProcedureParameters")
        return _format_exec(sp, params if isinstance(params, dict) else None)
    return None


def _synth_exec(tp: dict[str, Any]) -> str | None:
    sp = tp.get("storedProcedureName")
    if not isinstance(sp, str) or not sp.strip():
        return None
    params = tp.get("storedProcedureParameters")
    return _format_exec(sp, params if isinstance(params, dict) else None)


def _format_exec(proc_name: str, params: dict[str, Any] | None) -> str:
    if not params:
        return f"EXEC {proc_name}"
    parts: list[str] = []
    for pname, pspec in params.items():
        value: Any = None
        if isinstance(pspec, dict):
            value = pspec.get("value")
        parts.append(f"@{pname} = {_format_param(value)}")
    return f"EXEC {proc_name} " + ", ".join(parts)


def _format_param(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        escaped = value.replace("'", "''")
        return f"'{escaped}'"
    return "NULL"


def _extract_script_text(tp: dict[str, Any]) -> str | None:
    scripts = tp.get("scripts")
    if isinstance(scripts, list):
        parts: list[str] = []
        for s in scripts:
            if isinstance(s, dict):
                t = s.get("text")
                if isinstance(t, str) and t.strip():
                    parts.append(t.rstrip(";").strip())
        if parts:
            return ";\n".join(parts) + ";"
    for legacy_key in ("text", "query"):
        legacy = tp.get(legacy_key)
        if isinstance(legacy, str) and legacy.strip():
            return legacy
    return None


def _has_sqlish_field(tp: dict[str, Any]) -> bool:
    if not isinstance(tp, dict):
        return False
    if "storedProcedureName" in tp or "sqlReaderQuery" in tp:
        return True
    source = tp.get("source")
    return isinstance(source, dict) and (
        "sqlReaderQuery" in source or "sqlReaderStoredProcedureName" in source
    )
