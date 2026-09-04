from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch


def test_token_provider_returns_domain_access_token() -> None:
    fake_raw = MagicMock()
    fake_raw.token = "fake-jwt"
    fake_raw.expires_on = 1_800_000_000
    fake_cred = MagicMock()
    fake_cred.get_token.return_value = fake_raw

    with patch("azure.identity.DefaultAzureCredential", return_value=fake_cred):
        from dacpactools.infrastructure.azure.token_provider import (
            DefaultAzureCredentialTokenProvider,
        )

        provider = DefaultAzureCredentialTokenProvider()
        token = provider.get_token()

    assert token.token == "fake-jwt"
    assert token.expires_on == datetime.fromtimestamp(1_800_000_000, tz=timezone.utc)
    fake_cred.get_token.assert_called_once_with("https://database.windows.net/.default")


def test_token_provider_custom_scope() -> None:
    fake_raw = MagicMock()
    fake_raw.token = "x"
    fake_raw.expires_on = 1
    fake_cred = MagicMock()
    fake_cred.get_token.return_value = fake_raw
    with patch("azure.identity.DefaultAzureCredential", return_value=fake_cred):
        from dacpactools.infrastructure.azure.token_provider import (
            DefaultAzureCredentialTokenProvider,
        )

        DefaultAzureCredentialTokenProvider().get_token("https://vault.azure.net/.default")
    fake_cred.get_token.assert_called_once_with("https://vault.azure.net/.default")
