from __future__ import annotations

import os
from collections.abc import Iterator

import pytest


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Strip any DacPacTools env vars so Settings picks up only test values."""
    for key in list(os.environ):
        if key.startswith(("DACFX_", "SCRIPTDOM_", "SQLCLIENT_", "DB__", "DOTNET_", "NUGET_", "LOG_", "OUTPUT_")):
            monkeypatch.delenv(key, raising=False)
    yield
