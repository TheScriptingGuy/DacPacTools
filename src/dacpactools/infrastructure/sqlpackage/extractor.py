from __future__ import annotations

import subprocess
from pathlib import Path

from dacpactools.application.ports._handles import AccessToken, LiveConnection
from dacpactools.infrastructure.dotnet.path_probes import find_sqlpackage_exe
from dacpactools.presentation.logging_config import get_logger

log = get_logger("sqlpackage")


class SqlPackageError(RuntimeError):
    pass


class SqlPackageExtractor:
    """Wraps `sqlpackage /Action:Extract` for live -> .dacpac."""

    def __init__(self, sqlpackage_path: Path | None = None) -> None:
        self.sqlpackage = sqlpackage_path or find_sqlpackage_exe()

    def extract(
        self,
        connection: LiveConnection,
        token: AccessToken,
        target_dacpac: Path,
    ) -> Path:
        if self.sqlpackage is None:
            raise SqlPackageError("sqlpackage.exe not found; install `dotnet tool install --global microsoft.sqlpackage`")
        target_dacpac.parent.mkdir(parents=True, exist_ok=True)
        args = [
            str(self.sqlpackage),
            "/Action:Extract",
            f"/SourceServerName:{connection.server}",
            f"/SourceDatabaseName:{connection.database}",
            f"/TargetFile:{target_dacpac}",
            f"/AccessToken:{token.token}",
            "/p:ExtractAllTableData=false",
            "/p:IgnorePermissions=true",
            "/p:VerifyExtraction=false",
        ]
        log.info(
            "sqlpackage extract",
            server=connection.server,
            database=connection.database,
            target=str(target_dacpac),
        )
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise SqlPackageError(
                f"sqlpackage failed (exit={result.returncode}): {result.stderr or result.stdout}"
            )
        return target_dacpac
