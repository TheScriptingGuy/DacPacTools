from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from dacpactools.domain.orchestrator import OrchestratorLineageGraph


@runtime_checkable
class IOrchestratorLineageWriter(Protocol):
    def write(self, graph: OrchestratorLineageGraph, out_dir: Path) -> Path: ...


@runtime_checkable
class IOrchestratorVizRenderer(Protocol):
    def render(self, graph: OrchestratorLineageGraph, out_dir: Path) -> Path: ...
