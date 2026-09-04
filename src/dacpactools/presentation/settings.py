from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

DotnetRuntime = Literal["auto", "coreclr", "netfx"]


class DbConnection(BaseModel):
    server: str
    database: str


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        extra="ignore",
        case_sensitive=False,
    )

    dotnet_runtime: DotnetRuntime = "auto"
    dacfx_dll_hint: Path | None = None
    scriptdom_dll_hint: Path | None = None
    sqlclient_dll_hint: Path | None = None
    nuget_source: str = "https://api.nuget.org/v3-flatcontainer"
    nuget_cache_dir: Path = Path(".tools/nuget")
    dacfx_version: str | None = None       # None = latest stable
    scriptdom_version: str | None = None
    sqlclient_version: str | None = None
    log_level: str = "INFO"
    log_json: bool = False
    output_dir: Path = Path("./out")
    db: dict[str, DbConnection] = Field(default_factory=dict)

    def resolve_connection(self, name: str) -> DbConnection:
        key = name.lower()
        lookup = {k.lower(): v for k, v in self.db.items()}
        if key not in lookup:
            available = ", ".join(sorted(lookup)) or "(none)"
            raise KeyError(f"connection {name!r} not configured; available: {available}")
        return lookup[key]
