from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from dacpactools.application.ports._handles import AccessToken, LiveConnection
from dacpactools.infrastructure.sqlpackage.extractor import (
    SqlPackageError,
    SqlPackageExtractor,
)


def _tok() -> AccessToken:
    return AccessToken(token="abc.def.ghi", expires_on=datetime.now(UTC))


def _conn() -> LiveConnection:
    return LiveConnection(server="server.database.windows.net", database="warehouse")


def test_extract_missing_sqlpackage_raises(tmp_path: Path) -> None:
    with patch(
        "dacpactools.infrastructure.sqlpackage.extractor.find_sqlpackage_exe",
        return_value=None,
    ):
        ex = SqlPackageExtractor(sqlpackage_path=None)
    with pytest.raises(SqlPackageError, match="sqlpackage.exe not found"):
        ex.extract(_conn(), _tok(), tmp_path / "out.dacpac")


def test_extract_shells_out_with_access_token(tmp_path: Path) -> None:
    ex = SqlPackageExtractor(sqlpackage_path=Path("C:/fake/sqlpackage.exe"))
    fake = MagicMock(returncode=0, stdout="ok", stderr="")
    with patch("subprocess.run", return_value=fake) as run_mock:
        target = tmp_path / "out.dacpac"
        result = ex.extract(_conn(), _tok(), target)
    assert result == target
    args = run_mock.call_args.args[0]
    assert args[0] == "C:/fake/sqlpackage.exe" or args[0].endswith("sqlpackage.exe")
    assert "/Action:Extract" in args
    assert any(a.startswith("/AccessToken:") for a in args)
    assert any(a.startswith("/SourceServerName:server.database.windows.net") for a in args)
    assert any(a.startswith("/SourceDatabaseName:warehouse") for a in args)


def test_extract_nonzero_returns_raises(tmp_path: Path) -> None:
    ex = SqlPackageExtractor(sqlpackage_path=Path("C:/fake/sqlpackage.exe"))
    fake = MagicMock(returncode=1, stdout="", stderr="auth failed")
    with patch("subprocess.run", return_value=fake), pytest.raises(SqlPackageError, match="exit=1"):
        ex.extract(_conn(), _tok(), tmp_path / "x.dacpac")
