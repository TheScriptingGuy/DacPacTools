from __future__ import annotations

from pathlib import Path
from typing import ClassVar, Protocol, runtime_checkable

from dacpactools.domain.enums import OrchestratorKind
from dacpactools.domain.orchestrator import Pipeline


@runtime_checkable
class IOrchestratorArtifactReader(Protocol):
    kind: ClassVar[OrchestratorKind]

    def can_read(self, path: Path) -> bool: ...

    def read(self, path: Path) -> tuple[Pipeline, ...]: ...
