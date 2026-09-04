from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from dacpactools.domain.lineage import LineageGraph


@runtime_checkable
class IVizRenderer(Protocol):
    def render(self, graph: LineageGraph, out_dir: Path) -> Path: ...
