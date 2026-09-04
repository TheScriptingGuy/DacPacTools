from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from dacpactools.domain.lineage import LineageGraph


@dataclass(frozen=True)
class BuildLineageResult:
    graph: LineageGraph
    artifacts: dict[str, Path] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()
