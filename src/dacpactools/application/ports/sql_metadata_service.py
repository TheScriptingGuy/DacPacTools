from __future__ import annotations

from typing import Protocol, runtime_checkable

from dacpactools.application.ports._handles import (
    AccessToken,
    ConnectionHandle,
    LiveConnection,
)
from dacpactools.domain.identifiers import ColumnRef, ObjectRef


@runtime_checkable
class ISqlMetadataService(Protocol):
    def open(self, connection: LiveConnection, access_token: AccessToken) -> ConnectionHandle: ...

    def fetch_object_definition(self, conn: ConnectionHandle, ref: ObjectRef) -> str: ...

    def fetch_columns(self, conn: ConnectionHandle, ref: ObjectRef) -> tuple[ColumnRef, ...]: ...

    def close(self, conn: ConnectionHandle) -> None: ...
