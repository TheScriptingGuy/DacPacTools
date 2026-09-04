from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

ArtifactType = Literal["adf", "airflow", "auto"]


@dataclass(frozen=True)
class BuildOrchestratorLineageRequest:
    artifact_path: Path
    env_map_path: Path
    output_dir: Path
    artifact_type: ArtifactType = "auto"
    emit_formats: frozenset[str] = frozenset({"json", "html"})
    max_depth: int | None = None
