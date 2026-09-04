from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol, runtime_checkable

from dacpactools.application.ports._handles import (
    AccessToken,
    ConnectionHandle,
    LiveConnection,
)
from dacpactools.domain.enums import SqlTargetPlatform, SynapseDistributionKind
from dacpactools.domain.identifiers import ColumnRef, ObjectRef
from dacpactools.domain.physical_design import ExistingIndex, ExistingStatistic


@runtime_checkable
class ISqlMetadataService(Protocol):
    def open(self, connection: LiveConnection, access_token: AccessToken) -> ConnectionHandle: ...

    def fetch_object_definition(self, conn: ConnectionHandle, ref: ObjectRef) -> str: ...

    def fetch_columns(self, conn: ConnectionHandle, ref: ObjectRef) -> tuple[ColumnRef, ...]: ...

    def list_all_objects(self, conn: ConnectionHandle) -> Iterable[ObjectRef]: ...

    def close(self, conn: ConnectionHandle) -> None: ...

    def detect_target_platform(self, conn: ConnectionHandle) -> SqlTargetPlatform: ...

    def fetch_indexes(
        self, conn: ConnectionHandle, ref: ObjectRef
    ) -> tuple[ExistingIndex, ...]: ...

    def fetch_statistics(
        self, conn: ConnectionHandle, ref: ObjectRef
    ) -> tuple[ExistingStatistic, ...]: ...

    def fetch_row_count(self, conn: ConnectionHandle, ref: ObjectRef) -> int | None: ...

    def fetch_synapse_distribution(
        self, conn: ConnectionHandle, ref: ObjectRef
    ) -> tuple[SynapseDistributionKind, tuple[str, ...]] | None: ...

    def fetch_table_size_mb(
        self, conn: ConnectionHandle, ref: ObjectRef
    ) -> float | None: ...

    def fetch_column_distinct_count(
        self, conn: ConnectionHandle, ref: ObjectRef, column: str
    ) -> int | None: ...

    def fetch_distribution_skew_ratio(
        self, conn: ConnectionHandle, ref: ObjectRef
    ) -> float | None: ...
