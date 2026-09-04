from __future__ import annotations

from dataclasses import dataclass

from dacpactools.application.dto.drop_script import DropScriptRequest, DropScriptResult
from dacpactools.application.ports.clock import IClock
from dacpactools.application.ports.dacpac_service import IDacPacService
from dacpactools.application.ports.sql_metadata_service import ISqlMetadataService
from dacpactools.application.ports.token_provider import ITokenProvider
from dacpactools.domain.enums import ObjectType
from dacpactools.domain.identifiers import ObjectRef
from dacpactools.infrastructure.writers.filesystem import atomic_write_text
from dacpactools.presentation.logging_config import get_logger

log = get_logger("generate_drop_script")


_DROP_ORDER: tuple[ObjectType, ...] = (
    ObjectType.SYNONYM,
    ObjectType.EXTERNAL_TABLE,
    ObjectType.VIEW,
    ObjectType.PROCEDURE,
    ObjectType.SCALAR_FUNCTION,
    ObjectType.TABLE_VALUED_FUNCTION,
    ObjectType.TABLE,
)

_DROP_HEADING: dict[ObjectType, str] = {
    ObjectType.SYNONYM: "SYNONYMs",
    ObjectType.EXTERNAL_TABLE: "EXTERNAL TABLEs",
    ObjectType.VIEW: "VIEWs",
    ObjectType.PROCEDURE: "PROCEDUREs",
    ObjectType.SCALAR_FUNCTION: "SCALAR FUNCTIONs",
    ObjectType.TABLE_VALUED_FUNCTION: "TABLE-VALUED FUNCTIONs",
    ObjectType.TABLE: "TABLEs",
}


def _drop_statement(ref: ObjectRef) -> str | None:
    q = ref.qualified()
    match ref.object_type:
        case ObjectType.TABLE:
            return f"DROP TABLE IF EXISTS {q};"
        case ObjectType.VIEW:
            return f"DROP VIEW IF EXISTS {q};"
        case ObjectType.PROCEDURE:
            return f"DROP PROCEDURE IF EXISTS {q};"
        case ObjectType.SCALAR_FUNCTION | ObjectType.TABLE_VALUED_FUNCTION:
            return f"DROP FUNCTION IF EXISTS {q};"
        case ObjectType.SYNONYM:
            return f"DROP SYNONYM IF EXISTS {q};"
        case ObjectType.EXTERNAL_TABLE:
            return f"DROP EXTERNAL TABLE IF EXISTS {q};"
        case _:
            return None


def _key(ref: ObjectRef) -> tuple[str, str, ObjectType]:
    return (ref.schema_name.lower(), ref.name.lower(), ref.object_type)


@dataclass
class GenerateDropScriptUseCase:
    dacpac: IDacPacService
    sql_meta: ISqlMetadataService
    token_provider: ITokenProvider
    clock: IClock

    def execute(self, request: DropScriptRequest) -> DropScriptResult:
        request.output_dir.mkdir(parents=True, exist_ok=True)

        handle = self.dacpac.load(request.dacpac_path)
        dacpac_keys: set[tuple[str, str, ObjectType]] = {
            _key(obj.ref) for obj in self.dacpac.list_all_objects(handle)
        }
        log.info("dacpac objects enumerated", count=len(dacpac_keys))

        token = self.token_provider.get_token()
        conn = self.sql_meta.open(request.connection, token)
        try:
            db_objects = tuple(self.sql_meta.list_all_objects(conn))
        finally:
            self.sql_meta.close(conn)
        log.info("db objects enumerated", count=len(db_objects))

        schemas_lc = (
            frozenset(s.lower() for s in request.schemas_filter)
            if request.schemas_filter is not None
            else None
        )

        warnings: list[str] = []
        orphans: list[ObjectRef] = []
        for obj in db_objects:
            if schemas_lc is not None and obj.schema_name.lower() not in schemas_lc:
                continue
            if obj.object_type is ObjectType.UNKNOWN:
                warnings.append(
                    f"skipping {obj.qualified()} (unrecognised SQL Server type)"
                )
                continue
            if _key(obj) in dacpac_keys:
                continue
            orphans.append(obj)

        orphans.sort(
            key=lambda r: (
                _DROP_ORDER.index(r.object_type)
                if r.object_type in _DROP_ORDER
                else len(_DROP_ORDER),
                r.schema_name.lower(),
                r.name.lower(),
            )
        )

        script = self._render(request, orphans, warnings)
        script_path = request.output_dir / self._filename(request)
        atomic_write_text(script_path, script)

        log.info(
            "drop-script written",
            path=str(script_path),
            orphans=len(orphans),
            warnings=len(warnings),
        )
        return DropScriptResult(
            script_path=script_path,
            dropped=tuple(orphans),
            warnings=tuple(warnings),
        )

    def _filename(self, request: DropScriptRequest) -> str:
        ts = self.clock.now_utc().strftime("%Y%m%dT%H%M%SZ")
        server_slug = request.connection.server.replace(".", "_")
        return f"drop_script_{server_slug}__{request.connection.database}_{ts}.sql"

    def _render(
        self,
        request: DropScriptRequest,
        orphans: list[ObjectRef],
        warnings: list[str],
    ) -> str:
        ts = self.clock.now_utc().strftime("%Y-%m-%dT%H:%M:%SZ")
        schema_line = (
            ", ".join(sorted(request.schemas_filter))
            if request.schemas_filter
            else "(none - all schemas)"
        )
        lines: list[str] = [
            f"-- drop-script generated {ts}",
            f"-- source dacpac : {request.dacpac_path}",
            f"-- target db     : {request.connection.server} / {request.connection.database}",
            f"-- schemas filter: {schema_line}",
            f"-- {len(orphans)} orphan object(s)",
        ]
        for w in warnings:
            lines.append(f"-- warning: {w}")
        lines.append("--")

        by_type: dict[ObjectType, list[ObjectRef]] = {}
        for o in orphans:
            by_type.setdefault(o.object_type, []).append(o)

        for ot in _DROP_ORDER:
            bucket = by_type.get(ot)
            if not bucket:
                continue
            lines.append(f"-- {_DROP_HEADING[ot]}")
            for ref in bucket:
                stmt = _drop_statement(ref)
                if stmt is not None:
                    lines.append(stmt)
            lines.append("")

        return "\n".join(lines).rstrip() + "\n"
