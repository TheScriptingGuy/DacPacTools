from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from dacpactools.domain.orchestrator import OrchestratorLineageGraph


@dataclass(frozen=True)
class BuildOrchestratorLineageResult:
    graph: OrchestratorLineageGraph
    artifacts: dict[str, Path] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()
