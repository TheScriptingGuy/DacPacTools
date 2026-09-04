from __future__ import annotations

from typing import Any

from dacpactools.domain.enums import ObjectType
from dacpactools.domain.identifiers import ObjectRef

_TYPE_MAP: dict[str, ObjectType] = {
    "Procedure": ObjectType.PROCEDURE,
    "View": ObjectType.VIEW,
    "Table": ObjectType.TABLE,
    "TableValuedFunction": ObjectType.TABLE_VALUED_FUNCTION,
    "ScalarFunction": ObjectType.SCALAR_FUNCTION,
    "Function": ObjectType.SCALAR_FUNCTION,
    "Synonym": ObjectType.SYNONYM,
    "ExternalTable": ObjectType.EXTERNAL_TABLE,
}


def type_name(tsql_object: Any) -> str:
    ot = tsql_object.ObjectType
    return ot.Name if hasattr(ot, "Name") else str(ot)


def to_object_type(name: str) -> ObjectType:
    return _TYPE_MAP.get(name, ObjectType.UNKNOWN)


def _parts(tsql_object: Any) -> list[str]:
    return [str(p) for p in tsql_object.Name.Parts]


def to_object_ref(tsql_object: Any) -> ObjectRef:
    """Map a DacFx TSqlObject to a domain ObjectRef (drops trailing part for columns/etc)."""
    parts = _parts(tsql_object)
    tname = type_name(tsql_object)
    ot = to_object_type(tname)

    if tname == "Column":
        # column Parts = [schema, object, column] or [db, schema, object, column]
        parts = parts[:-1]
        ot = ObjectType.UNKNOWN

    if len(parts) == 1:
        return ObjectRef(database=None, schema="dbo", name=parts[0], object_type=ot)
    if len(parts) == 2:
        return ObjectRef(database=None, schema=parts[0], name=parts[1], object_type=ot)
    if len(parts) >= 3:
        return ObjectRef(database=parts[0], schema=parts[1], name=parts[2], object_type=ot)
    return ObjectRef(database=None, schema="dbo", name="?", object_type=ot)


def column_parts(tsql_object: Any) -> tuple[str, str, str] | None:
    """For a Column TSqlObject, return (schema, owning_object, column). None if not a column."""
    if type_name(tsql_object) != "Column":
        return None
    parts = _parts(tsql_object)
    if len(parts) < 3:
        return None
    return parts[-3], parts[-2], parts[-1]
