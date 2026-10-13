"""Prepare exact Draft-bound version requests; only browser approval creates versions."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from backend.app.controlled_version_repository import (
    ControlledVersionRepository,
    VersionRequestError,
)

_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
_HASH = re.compile(r"[0-9a-f]{64}\Z")


class ControlledVersionService:
    def __init__(self, path: Path | None = None) -> None:
        self._requests = ControlledVersionRepository(path)

    @staticmethod
    def _strategy_id(value: str) -> str:
        if not isinstance(value, str) or not _ID.fullmatch(value):
            raise VersionRequestError("INVALID_STRATEGY_ID")
        return value

    @staticmethod
    def _request_id(value: str) -> str:
        if not isinstance(value, str) or not _ID.fullmatch(value):
            raise VersionRequestError("INVALID_REQUEST_ID")
        return value

    def prepare(
        self,
        *,
        strategy_id: str,
        expected_draft_revision: int,
        expected_draft_hash: str,
        base_latest_version_id: str | None,
        base_latest_version_hash: str | None,
        client_request_key: str | None = None,
    ) -> dict[str, Any]:
        self._strategy_id(strategy_id)
        if (
            not isinstance(expected_draft_revision, int)
            or isinstance(expected_draft_revision, bool)
            or expected_draft_revision < 1
        ):
            raise VersionRequestError("INVALID_DRAFT_REVISION")
        if not isinstance(expected_draft_hash, str) or not _HASH.fullmatch(expected_draft_hash):
            raise VersionRequestError("INVALID_DRAFT_HASH")
        if (base_latest_version_id is None) != (base_latest_version_hash is None):
            raise VersionRequestError("INVALID_VERSION_BASELINE")
        if base_latest_version_id is not None:
            if (
                not isinstance(base_latest_version_id, str)
                or not isinstance(base_latest_version_hash, str)
                or not _ID.fullmatch(base_latest_version_id)
                or not _HASH.fullmatch(base_latest_version_hash)
            ):
                raise VersionRequestError("INVALID_VERSION_BASELINE")
        if client_request_key is not None and (
            not isinstance(client_request_key, str) or not _ID.fullmatch(client_request_key)
        ):
            raise VersionRequestError("INVALID_CLIENT_REQUEST_KEY")
        return self._requests.prepare(
            strategy_id,
            expected_draft_revision,
            expected_draft_hash,
            base_latest_version_id,
            base_latest_version_hash,
            client_request_key,
        )

    def get(self, request_id: str) -> dict[str, Any]:
        return self._requests.get(self._request_id(request_id))

    def get_for_strategy(self, strategy_id: str, request_id: str) -> dict[str, Any]:
        record = self.get(request_id)
        if record["strategy_id"] != self._strategy_id(strategy_id):
            raise VersionRequestError("VERSION_REQUEST_NOT_FOUND")
        return record

    def list(self, strategy_id: str) -> list[dict[str, Any]]:
        return self._requests.list(self._strategy_id(strategy_id))

    def reject(self, strategy_id: str, request_id: str) -> dict[str, Any]:
        self.get_for_strategy(strategy_id, request_id)
        return self._requests.reject(request_id)

    def approve(self, strategy_id: str, request_id: str) -> dict[str, Any]:
        self.get_for_strategy(strategy_id, request_id)
        return self._requests.approve(request_id)


__all__ = ["ControlledVersionService", "VersionRequestError"]
