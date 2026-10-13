"""Keep the backend, API, and frontend release versions aligned."""

from __future__ import annotations

import ast
import json
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EXPECTED_RELEASE_VERSION = "1.4.6"


def _fastapi_version() -> str:
    module = ast.parse((ROOT / "backend/app/main.py").read_text(encoding="utf-8"))
    for statement in module.body:
        if not isinstance(statement, ast.Assign) or not isinstance(statement.value, ast.Call):
            continue
        if not any(
            isinstance(target, ast.Name) and target.id == "app" for target in statement.targets
        ):
            continue
        call = statement.value
        if not isinstance(call.func, ast.Name) or call.func.id != "FastAPI":
            continue
        for keyword in call.keywords:
            if keyword.arg == "version" and isinstance(keyword.value, ast.Constant):
                if isinstance(keyword.value.value, str):
                    return keyword.value.value
    raise AssertionError("VERSION_METADATA_MISMATCH: FastAPI version literal not found")


def test_release_version_metadata_matches() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    package = json.loads((ROOT / "frontend/package.json").read_text(encoding="utf-8"))
    versions = {
        "pyproject.toml": project["project"]["version"],
        "frontend/package.json": package["version"],
        "FastAPI application": _fastapi_version(),
    }
    assert len(set(versions.values())) == 1, f"VERSION_METADATA_MISMATCH: {versions}"
    assert set(versions.values()) == {EXPECTED_RELEASE_VERSION}, (
        f"VERSION_METADATA_MISMATCH: expected {EXPECTED_RELEASE_VERSION}, got {versions}"
    )


def test_loaded_application_and_operator_versions_match_release() -> None:
    from backend.app.main import app
    from backend.app.operator_read_service import BACKEND_VERSION, BRIDGE_VERSION

    assert app.version == EXPECTED_RELEASE_VERSION
    assert BACKEND_VERSION == EXPECTED_RELEASE_VERSION
    assert BRIDGE_VERSION == "0.2.0"
