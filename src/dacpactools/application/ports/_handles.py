from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from dacpactools.domain.identifiers import ObjectRef


@dataclass(frozen=True)
class DacPacHandle:
    """Opaque wrapper around a DacFx TSqlModel instance."""
    model: Any
    source_path: str | None = None


@dataclass(frozen=True)
class TSqlObjectHandle:
    """Opaque wrapper around a DacFx TSqlObject + resolved ObjectRef."""
    ref: ObjectRef
    tsql_object: Any


@dataclass(frozen=True)
class ParsedBatch:
    """Opaque wrapper around a ScriptDom TSqlScript root fragment."""
    fragment: Any
    errors: tuple[str, ...] = ()


@dataclass(frozen=True)
class LiveConnection:
    server: str
    database: str
    encrypt: bool = True

    def connection_string(self) -> str:
        return (
            f"Server=tcp:{self.server},1433;"
            f"Database={self.database};"
            f"Encrypt={'yes' if self.encrypt else 'no'};TrustServerCertificate=no;"
        )


@dataclass(frozen=True)
class ConnectionHandle:
    connection: Any  # SqlConnection
    live: LiveConnection


@dataclass(frozen=True)
class AccessToken:
    token: str
    expires_on: datetime


@dataclass
class ColumnResolver:
    """Symbol table passed to the parser. Maps alias/table-name -> ordered column list."""
    scope: dict[str, tuple[Any, ...]] = field(default_factory=dict)


@dataclass(frozen=True)
class ParsedLineage:
    mappings: tuple[Any, ...] = ()
    predicates: tuple[Any, ...] = ()
    ctas_specs: tuple[Any, ...] = ()
