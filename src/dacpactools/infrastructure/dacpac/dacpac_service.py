from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

from dacpactools.application.ports._handles import (
    DacPacHandle,
    LiveConnection,
    TSqlObjectHandle,
)
from dacpactools.domain.enums import ObjectType
from dacpactools.domain.identifiers import ColumnRef, ObjectRef
from dacpactools.infrastructure.dacpac.object_mapper import (
    to_object_ref,
    type_name,
)
from dacpactools.presentation.logging_config import get_logger

log = get_logger("dacfx_service")


def _clr_types() -> tuple[Any, Any, Any, Any]:
    from Microsoft.SqlServer.Dac.Model import (  # type: ignore  # noqa: PLC0415
        DacQueryScopes,
        ModelSchema,
        ObjectIdentifier,
        TSqlModel,
    )

    return TSqlModel, ModelSchema, DacQueryScopes, ObjectIdentifier


def _model_schema_for(ot: ObjectType) -> Any | None:
    _, ModelSchema, _, _ = _clr_types()
    m = {
        ObjectType.PROCEDURE: ModelSchema.Procedure,
        ObjectType.VIEW: ModelSchema.View,
        ObjectType.TABLE: ModelSchema.Table,
        ObjectType.TABLE_VALUED_FUNCTION: ModelSchema.TableValuedFunction,
        ObjectType.SCALAR_FUNCTION: ModelSchema.ScalarFunction,
        ObjectType.SYNONYM: ModelSchema.Synonym,
        ObjectType.EXTERNAL_TABLE: getattr(ModelSchema, "ExternalTable", None),
    }
    return m.get(ot)


class DacFxDacPacService:
    """Concrete IDacPacService using DacFx TSqlModel via pythonnet."""

    def load(self, dacpac_path: Path) -> DacPacHandle:
        TSqlModel, *_ = _clr_types()
        model = TSqlModel(str(dacpac_path.resolve()))
        log.info("dacpac loaded", path=str(dacpac_path))
        return DacPacHandle(model=model, source_path=str(dacpac_path))

    def load_from_model(self, model: Any) -> DacPacHandle:
        """Test/fixture helper: wrap an already-constructed TSqlModel."""
        return DacPacHandle(model=model, source_path=None)

    def load_from_live(self, connection: LiveConnection, tmp_dir: Path) -> DacPacHandle:
        from dacpactools.application.ports._handles import AccessToken  # noqa: PLC0415
        from dacpactools.infrastructure.azure.token_provider import (  # noqa: PLC0415
            DefaultAzureCredentialTokenProvider,
        )
        from dacpactools.infrastructure.sqlpackage.extractor import SqlPackageExtractor  # noqa: PLC0415

        tmp_dir.mkdir(parents=True, exist_ok=True)
        target = tmp_dir / f"{connection.server.replace('.', '_')}__{connection.database}.dacpac"
        token: AccessToken = DefaultAzureCredentialTokenProvider().get_token()
        SqlPackageExtractor().extract(connection, token, target)
        return self.load(target)

    def list_all_objects(self, handle: DacPacHandle) -> Iterable[TSqlObjectHandle]:
        _, _, DacQueryScopes, _ = _clr_types()
        for ot in (
            ObjectType.TABLE,
            ObjectType.VIEW,
            ObjectType.PROCEDURE,
            ObjectType.SCALAR_FUNCTION,
            ObjectType.TABLE_VALUED_FUNCTION,
            ObjectType.SYNONYM,
            ObjectType.EXTERNAL_TABLE,
        ):
            ms = _model_schema_for(ot)
            if ms is None:
                continue
            for obj in handle.model.GetObjects(DacQueryScopes.UserDefined, ms):
                raw = to_object_ref(obj)
                resolved = ObjectRef(
                    database=raw.database,
                    schema=raw.schema_name,
                    name=raw.name,
                    object_type=ot,
                )
                yield TSqlObjectHandle(ref=resolved, tsql_object=obj)

    def find_object(self, handle: DacPacHandle, ref: ObjectRef) -> TSqlObjectHandle | None:
        _, ModelSchema, DacQueryScopes, ObjectIdentifier = _clr_types()
        candidates = [ref.object_type] if ref.object_type is not ObjectType.UNKNOWN else [
            ObjectType.PROCEDURE,
            ObjectType.VIEW,
            ObjectType.TABLE_VALUED_FUNCTION,
            ObjectType.SCALAR_FUNCTION,
            ObjectType.TABLE,
            ObjectType.SYNONYM,
            ObjectType.EXTERNAL_TABLE,
        ]
        oid = ObjectIdentifier(ref.schema_name, ref.name)
        for ot in candidates:
            ms = _model_schema_for(ot)
            if ms is None:
                continue
            match = handle.model.GetObject(ms, oid, DacQueryScopes.UserDefined)
            if match is not None:
                resolved = ObjectRef(
                    database=ref.database,
                    schema=ref.schema_name,
                    name=ref.name,
                    object_type=ot,
                )
                return TSqlObjectHandle(ref=resolved, tsql_object=match)
        return None

    def get_referenced(self, obj: TSqlObjectHandle) -> Iterable[TSqlObjectHandle]:
        seen: set[str] = set()
        for referenced in obj.tsql_object.GetReferenced():
            tname = type_name(referenced)
            if tname in {"Column", "Schema", "DataType", "SqlFile", "Parameter"}:
                continue
            ref = to_object_ref(referenced)
            key = f"{ref.database or ''}|{ref.schema_name.lower()}|{ref.name.lower()}|{tname}"
            if key in seen:
                continue
            seen.add(key)
            yield TSqlObjectHandle(ref=ref, tsql_object=referenced)

    def get_referencing(self, obj: TSqlObjectHandle) -> Iterable[TSqlObjectHandle]:
        seen: set[str] = set()
        for referencing in obj.tsql_object.GetReferencing():
            tname = type_name(referencing)
            if tname in {"Column", "Schema", "DataType"}:
                continue
            ref = to_object_ref(referencing)
            key = f"{ref.database or ''}|{ref.schema_name.lower()}|{ref.name.lower()}|{tname}"
            if key in seen:
                continue
            seen.add(key)
            yield TSqlObjectHandle(ref=ref, tsql_object=referencing)

    def get_body_script(self, obj: TSqlObjectHandle) -> str | None:
        if not obj.ref.object_type.has_body:
            return None
        try:
            ok, script = obj.tsql_object.TryGetScript(None)
            return str(script) if ok and script else None
        except Exception as e:  # noqa: BLE001
            log.warning("TryGetScript failed", obj=obj.ref.qualified(), err=str(e))
            return None

    def get_columns(self, obj: TSqlObjectHandle) -> tuple[ColumnRef, ...]:
        _, _, DacQueryScopes, _ = _clr_types()
        cols: list[ColumnRef] = []
        try:
            children = obj.tsql_object.GetChildren(DacQueryScopes.UserDefined)
        except Exception as e:  # noqa: BLE001
            log.debug("GetChildren failed", obj=obj.ref.qualified(), err=str(e))
            return ()
        idx = 0
        for child in children:
            if type_name(child) != "Column":
                continue
            parts = [str(p) for p in child.Name.Parts]
            col_name = parts[-1]
            cols.append(
                ColumnRef(owner=obj.ref, column_name=col_name, ordinal=idx, data_type=None)
            )
            idx += 1
        return tuple(cols)

    def resolve_synonym_target(self, obj: TSqlObjectHandle) -> ObjectRef | None:
        if obj.ref.object_type is not ObjectType.SYNONYM:
            return None
        for referenced in obj.tsql_object.GetReferenced():
            tname = type_name(referenced)
            if tname in {"Column", "Schema", "DataType"}:
                continue
            return to_object_ref(referenced)
        return None
