from __future__ import annotations

from typing import Any

import pytest

from dacpactools.presentation.settings import Settings


@pytest.fixture(scope="session")
def clr_initialized() -> None:
    try:
        from dacpactools.infrastructure.dotnet.clr_bootstrap import initialize

        initialize(Settings(_env_file=None))  # type: ignore[call-arg]
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"CLR / DacFx not available: {e}")


@pytest.fixture
def tsql_model(clr_initialized: None) -> Any:
    from Microsoft.SqlServer.Dac.Model import (  # type: ignore
        SqlServerVersion,
        TSqlModel,
        TSqlModelOptions,
    )

    model = TSqlModel(SqlServerVersion.SqlAzure, TSqlModelOptions())
    yield model
    try:
        model.Dispose()
    except Exception:  # noqa: BLE001, S110
        pass
