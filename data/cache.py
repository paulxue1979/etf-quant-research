"""Versioned, atomic, disk-backed cache for normalized historical data."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Protocol

from data.exceptions import CacheCorruptionError, CacheWriteError
from data.models import CACHE_FORMAT_VERSION, DataSource, HistoricalDataRequest, HistoricalDataSet

DEFAULT_CACHE_ROOT = Path(__file__).resolve().parent / "cache"


class CacheBackend(Protocol):
    """Storage boundary that lets later phases replace the cache implementation."""

    def get(self, request: HistoricalDataRequest) -> HistoricalDataSet | None:
        """Read a cached data set, or return None on a cache miss."""

    def set(self, data: HistoricalDataSet) -> None:
        """Persist a validated data set."""


class DiskCache:
    """JSON cache with request-specific keys and atomic writes."""

    def __init__(self, root: Path = DEFAULT_CACHE_ROOT) -> None:
        self._root = root

    def path_for(self, request: HistoricalDataRequest) -> Path:
        """Return the deterministic cache location for a request."""
        identity = request.cache_identity()
        filename = "__".join(
            (
                identity["symbol"],
                identity["start_date"],
                identity["end_date"],
                identity["frequency"],
                identity["price_field_used"],
                f"schema-{identity['schema_version']}",
            )
        )
        return self._root / "tiingo" / f"format-{CACHE_FORMAT_VERSION}" / request.symbol / (
            filename + ".json"
        )

    def get(self, request: HistoricalDataRequest) -> HistoricalDataSet | None:
        """Load a cache entry and reject malformed or mismatched payloads."""
        path = self.path_for(request)
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("cache document is not an object")
            if payload.get("cache_format_version") != CACHE_FORMAT_VERSION:
                raise ValueError("unsupported cache format version")
            if payload.get("request") != request.cache_identity():
                raise ValueError("cache request identity does not match")
            data_payload = payload.get("data")
            if not isinstance(data_payload, dict):
                raise ValueError("cache data payload is missing")
            return HistoricalDataSet.from_cache_payload(data_payload).with_source(DataSource.CACHE)
        except (OSError, KeyError, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise CacheCorruptionError(
                f"Market data cache entry is invalid: {path.name}"
            ) from exc

    def set(self, data: HistoricalDataSet) -> None:
        """Persist validated data atomically so partial writes are never returned."""
        path = self.path_for(data.request)
        document = {
            "cache_format_version": CACHE_FORMAT_VERSION,
            "request": data.request.cache_identity(),
            "data": data.to_cache_payload(),
        }
        temporary_path: Path | None = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=path.parent,
                prefix=".tmp-",
                suffix=".json",
                delete=False,
            ) as handle:
                temporary_path = Path(handle.name)
                json.dump(
                    document,
                    handle,
                    ensure_ascii=True,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, path)
        except OSError as exc:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
            raise CacheWriteError(f"Unable to write market data cache: {path.name}") from exc
