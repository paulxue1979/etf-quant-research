"""Credential loading for the Tiingo data engine."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

from data.exceptions import TiingoConfigurationError

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DOTENV_PATH = PROJECT_ROOT / ".env"


@dataclass(frozen=True)
class TiingoSettings:
    """Runtime settings that keep the Tiingo credential server-side."""

    api_key: str = field(repr=False)
    base_url: str = "https://api.tiingo.com"

    @classmethod
    def from_environment(
        cls,
        environ: Mapping[str, str] | None = None,
        dotenv_path: Path | None = DEFAULT_DOTENV_PATH,
    ) -> TiingoSettings:
        """Load the API key without exposing it in errors or logs."""
        if dotenv_path is not None:
            load_dotenv(dotenv_path=dotenv_path, override=False)
        values = os.environ if environ is None else environ
        api_key = values.get("TIINGO_API_KEY", "").strip()
        if not api_key:
            raise TiingoConfigurationError(
                "TIINGO_API_KEY is required. Configure it in .env or the environment."
            )
        return cls(api_key=api_key)
