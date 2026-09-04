from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Protocol, runtime_checkable

from dacpactools.application.ports._handles import (
    DacPacHandle,
    LiveConnection,
    TSqlObjectHandle,
)
from dacpactools.domain.identifiers import ColumnRef, ObjectRef


@runtime_checkable
class IDacPacService(Protocol):
    def load(self, dacpac_path: Path) -> DacPacHandle: ...

    def load_from_live(self, connection: LiveConnection, tmp_dir: Path) -> DacPacHandle: ...

    def find_object(self, handle: DacPacHandle, ref: ObjectRef) -> TSqlObjectHandle | None: ...

    def get_referenced(self, obj: TSqlObjectHandle) -> Iterable[TSqlObjectHandle]: ...

    def get_referencing(self, obj: TSqlObjectHandle) -> Iterable[TSqlObjectHandle]: ...

    def get_body_script(self, obj: TSqlObjectHandle) -> str | None: ...

    def get_columns(self, obj: TSqlObjectHandle) -> tuple[ColumnRef, ...]: ...

    def resolve_synonym_target(self, obj: TSqlObjectHandle) -> ObjectRef | None: ...
