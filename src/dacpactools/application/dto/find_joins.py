from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from dacpactools.application.dto.lineage_request import LineageSource
from dacpactools.domain.identifiers import ObjectRef
from dacpactools.domain.joins import JoinReport


@dataclass(frozen=True)
class FindJoinsRequest:
    target: ObjectRef
    source: LineageSource
    output_dir: Path


@dataclass(frozen=True)
class FindJoinsResult:
    report: JoinReport
    artifacts: dict[str, Path]
