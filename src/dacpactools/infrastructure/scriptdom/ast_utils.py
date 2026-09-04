from __future__ import annotations

from typing import Any


def type_name(node: Any) -> str:
    return type(node).__name__


def script_text(node: Any) -> str | None:
    """Reconstruct source text for any TSqlFragment via ScriptTokenStream if available."""
    try:
        tokens = node.ScriptTokenStream
        if tokens is None:
            return None
        first = node.FirstTokenIndex
        last = node.LastTokenIndex
        parts: list[str] = []
        for i in range(first, last + 1):
            parts.append(str(tokens[i].Text))
        return "".join(parts).strip()
    except Exception:  # noqa: BLE001
        return None


def multipart_parts(mpi: Any) -> list[str]:
    """Extract identifier value list from a MultiPartIdentifier / SchemaObjectName."""
    parts: list[str] = []
    ids = getattr(mpi, "Identifiers", None)
    if ids is None:
        return parts
    for ident in ids:
        parts.append(str(ident.Value))
    return parts


def schema_object_parts(schema_object: Any) -> tuple[str | None, str, str | None]:
    """Return (database, schema_or_none, name) from SchemaObjectName."""
    parts = multipart_parts(schema_object)
    if len(parts) >= 3:
        return parts[-3], parts[-2], parts[-1]
    if len(parts) == 2:
        return None, parts[0], parts[1]
    if len(parts) == 1:
        return None, None, parts[0]
    return None, None, None


def identifier_value(identifier: Any) -> str | None:
    if identifier is None:
        return None
    return str(getattr(identifier, "Value", identifier))
