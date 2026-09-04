from __future__ import annotations

from datetime import datetime, timezone

from dacpactools.application.ports._handles import AccessToken


class DefaultAzureCredentialTokenProvider:
    """ITokenProvider using azure.identity.DefaultAzureCredential."""

    def __init__(self, exclude_interactive_browser_credential: bool = False) -> None:
        from azure.identity import DefaultAzureCredential  # noqa: PLC0415

        self._cred = DefaultAzureCredential(
            exclude_interactive_browser_credential=exclude_interactive_browser_credential,
        )

    def get_token(self, scope: str = "https://database.windows.net/.default") -> AccessToken:
        raw = self._cred.get_token(scope)
        expires = datetime.fromtimestamp(raw.expires_on, tz=timezone.utc)
        return AccessToken(token=raw.token, expires_on=expires)
