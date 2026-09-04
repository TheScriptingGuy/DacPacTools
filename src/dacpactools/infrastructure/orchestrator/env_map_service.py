from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dacpactools.application.dto.lineage_request import (
    DacPacSource,
    LineageSource,
    LiveConnectionSource,
)
from dacpactools.application.ports._handles import LiveConnection

ConnectionResolver = Callable[[str], LiveConnection | None]


@dataclass(frozen=True)
class _Entry:
    kind: str  # "dacpac" | "connection"
    path: Path | None = None
    name: str | None = None


class JsonEnvMapService:
    def __init__(self, connection_resolver: ConnectionResolver) -> None:
        self._resolve_conn = connection_resolver
        self._entries: dict[str, _Entry] = {}
        self._base_dir: Path = Path.cwd()

    def load(self, path: Path) -> None:
        raw: Any = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError(  # noqa: TRY004 (JSON schema violation, ValueError is idiomatic)
                f"env-map: expected object at root, got {type(raw).__name__}"
            )
        version = raw.get("version")
        if version != 1:
            raise ValueError(f"env-map: unsupported version {version!r}, expected 1")
        mappings = raw.get("mappings")
        if not isinstance(mappings, dict):
            raise ValueError("env-map: 'mappings' must be an object")  # noqa: TRY004
        self._base_dir = path.parent.resolve()
        self._entries = {
            str(key).lower(): _parse_entry(str(key), spec) for key, spec in mappings.items()
        }

    def resolve(self, env_ref: str) -> LineageSource | None:
        entry = self._entries.get(env_ref.lower())
        if entry is None:
            return None
        if entry.kind == "dacpac":
            assert entry.path is not None
            p = entry.path
            if not p.is_absolute():
                p = (self._base_dir / p).resolve()
            return DacPacSource(path=p)
        if entry.kind == "connection":
            assert entry.name is not None
            live = self._resolve_conn(entry.name)
            if live is None:
                return None
            return LiveConnectionSource(connection=live)
        return None

    def known_refs(self) -> tuple[str, ...]:
        return tuple(sorted(self._entries.keys()))


def _parse_entry(key: str, spec: Any) -> _Entry:
    if not isinstance(spec, dict):
        raise ValueError(f"env-map: entry {key!r} must be an object")  # noqa: TRY004
    kind = spec.get("kind")
    if kind == "dacpac":
        path_s = spec.get("path")
        if not isinstance(path_s, str):
            raise ValueError(f"env-map: entry {key!r} dacpac 'path' must be a string")
        return _Entry(kind="dacpac", path=Path(path_s))
    if kind == "connection":
        name = spec.get("name")
        if not isinstance(name, str):
            raise ValueError(f"env-map: entry {key!r} connection 'name' must be a string")
        return _Entry(kind="connection", name=name)
    raise ValueError(f"env-map: entry {key!r} has unsupported kind {kind!r}")
