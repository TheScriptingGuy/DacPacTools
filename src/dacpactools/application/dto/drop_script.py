from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from dacpactools.application.ports._handles import LiveConnection
from dacpactools.domain.identifiers import ObjectRef


@dataclass(frozen=True)
class DropScriptRequest:
    dacpac_path: Path
    connection: LiveConnection
    output_dir: Path
    schemas_filter: frozenset[str] | None = None


@dataclass(frozen=True)
class DropScriptResult:
    script_path: Path
    dropped: tuple[ObjectRef, ...]
    warnings: tuple[str, ...] = ()
