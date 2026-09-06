from __future__ import annotations

import pytest

from data.config import TiingoSettings
from data.exceptions import TiingoConfigurationError


def test_missing_api_key_has_safe_configuration_error(tmp_path) -> None:
    with pytest.raises(TiingoConfigurationError, match="TIINGO_API_KEY is required"):
        TiingoSettings.from_environment(environ={}, dotenv_path=tmp_path / "absent.env")


def test_api_key_is_loaded_from_provided_environment(tmp_path) -> None:
    settings = TiingoSettings.from_environment(
        environ={"TIINGO_API_KEY": "test-key"},
        dotenv_path=tmp_path / "absent.env",
    )

    assert settings.api_key == "test-key"
