from __future__ import annotations

from typing import Protocol, runtime_checkable

from dacpactools.application.ports._handles import AccessToken


@runtime_checkable
class ITokenProvider(Protocol):
    def get_token(self, scope: str = "https://database.windows.net/.default") -> AccessToken: ...
