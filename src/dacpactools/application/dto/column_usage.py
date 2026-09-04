from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from dacpactools.application.dto.lineage_request import LineageSource
from dacpactools.domain.column_usage import ColumnUsageReport
from dacpactools.domain.identifiers import ObjectRef


@dataclass(frozen=True)
class FindColumnUsageRequest:
    target_owner: ObjectRef
    target_column: str
    source: LineageSource
    output_dir: Path


@dataclass(frozen=True)
class FindColumnUsageResult:
    report: ColumnUsageReport
    artifacts: dict[str, Path]
