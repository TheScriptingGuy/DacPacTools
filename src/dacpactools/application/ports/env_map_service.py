from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from dacpactools.application.dto.lineage_request import LineageSource


@runtime_checkable
class IEnvMapService(Protocol):
    def load(self, path: Path) -> None: ...

    def resolve(self, env_ref: str) -> LineageSource | None: ...

    def known_refs(self) -> tuple[str, ...]: ...
