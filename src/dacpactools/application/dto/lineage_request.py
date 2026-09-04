from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from dacpactools.application.ports._handles import LiveConnection
from dacpactools.domain.enums import ObjectType
from dacpactools.domain.identifiers import ObjectRef


@dataclass(frozen=True)
class DacPacSource:
    path: Path


@dataclass(frozen=True)
class LiveConnectionSource:
    connection: LiveConnection


LineageSource = DacPacSource | LiveConnectionSource


_ALL_OBJECT_TYPES: frozenset[ObjectType] = frozenset(
    ot for ot in ObjectType if ot is not ObjectType.UNKNOWN
)


@dataclass(frozen=True)
class BuildLineageRequest:
    root_object: ObjectRef
    source: LineageSource
    output_dir: Path
    emit_formats: frozenset[str] = frozenset({"json", "html"})
    max_depth: int | None = None
    include_object_types: frozenset[ObjectType] = _ALL_OBJECT_TYPES
