from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from dacpactools.application.dto.lineage_request import LineageSource
from dacpactools.application.ports._handles import LiveConnection
from dacpactools.domain.enums import SqlTargetPlatform
from dacpactools.domain.physical_design import TableRecommendation


@dataclass(frozen=True)
class RecommendIndexStatsRequest:
    schemas: frozenset[str]
    source: LineageSource
    output_dir: Path
    connection: LiveConnection | None = None
    target_override: SqlTargetPlatform | None = None


@dataclass(frozen=True)
class RecommendIndexStatsResult:
    target_platform: SqlTargetPlatform
    tables: tuple[TableRecommendation, ...]
    artifacts: dict[str, Path]
    warnings: tuple[str, ...] = ()
