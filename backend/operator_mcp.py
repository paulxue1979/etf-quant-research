"""Local stdio MCP surface for read-only ETF Quant strategy inspection."""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

from backend.app.operator_read_service import (
    BRIDGE_VERSION,
    OperatorReadError,
    OperatorReadService,
)

_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
_READ_ONLY = ToolAnnotations(
    readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
)


def _id(value: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise OperatorReadError("INVALID_IDENTIFIER")
    return value


def _date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise OperatorReadError("INVALID_DATE") from exc


def create_server(service: OperatorReadService | None = None) -> MCPServer:
    """Expose exactly eight bounded read tools; no resources or write handlers."""
    read = service or OperatorReadService()
    server = MCPServer(
        "etf-quant-read-only",
        version=BRIDGE_VERSION,
        instructions="Local ETF Quant inspection only. Never infer missing data or attempt writes.",
    )

    @server.tool(annotations=_READ_ONLY)
    def quant_health() -> dict[str, Any]:
        """Read-only liveness and version check; has no side effects."""
        return read.health()

    @server.tool(annotations=_READ_ONLY)
    def quant_list_strategies() -> dict[str, Any]:
        """Read-only list of active strategy identities; no payloads or side effects."""
        return read.list_strategies()

    @server.tool(annotations=_READ_ONLY)
    def quant_get_strategy(strategy_id: str) -> dict[str, Any]:
        """Read-only compact strategy identity and latest version; no side effects."""
        return read.get_strategy(_id(strategy_id))

    @server.tool(annotations=_READ_ONLY)
    def quant_get_draft(strategy_id: str) -> dict[str, Any]:
        """Read-only active draft revision and summary; never saves or repairs it."""
        return read.get_draft(_id(strategy_id))

    @server.tool(annotations=_READ_ONLY)
    def quant_get_strategy_structure(strategy_id: str, version_id: str = "") -> dict[str, Any]:
        """Read-only human-readable draft structure, or a specified immutable version."""
        return read.get_structure(_id(strategy_id), _id(version_id) if version_id else None)

    @server.tool(annotations=_READ_ONLY)
    def quant_get_semantic_targets(strategy_id: str, version_id: str) -> dict[str, Any]:
        """Read-only adjustable target catalog for an immutable version; no binding."""
        return read.get_semantic_targets(_id(strategy_id), _id(version_id))

    @server.tool(annotations=_READ_ONLY)
    def quant_validate_draft(strategy_id: str) -> dict[str, Any]:
        """Read-only canonical validation of the stored active draft; no auto-fix."""
        return read.validate_draft(_id(strategy_id))

    @server.tool(annotations=_READ_ONLY)
    def quant_get_data_availability(
        strategy_id: str, version_id: str, requested_start: str, requested_end: str
    ) -> dict[str, Any]:
        """Read-only cache-only availability projection; never fetches remote data."""
        return read.get_data_availability(
            _id(strategy_id), _id(version_id), _date(requested_start), _date(requested_end)
        )

    return server


def main() -> None:
    create_server().run(transport="stdio")


if __name__ == "__main__":
    main()
