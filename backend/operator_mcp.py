"""Local stdio MCP surface for inspection and isolated Draft proposals."""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

from backend.app.operator_change_set_service import (
    ChangeSetPreparationError,
    DraftChangeOperation,
    OperatorChangeSetService,
)
from backend.app.operator_read_service import (
    BRIDGE_VERSION,
    OperatorReadError,
    OperatorReadService,
)

_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
_READ_ONLY = ToolAnnotations(
    readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
)
_PROPOSAL = ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False
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


def create_server(
    service: OperatorReadService | None = None,
    change_set_service: OperatorChangeSetService | None = None,
) -> MCPServer:
    """Expose bounded reads and proposal persistence, never Draft mutation."""
    read = service or OperatorReadService()
    proposals = change_set_service or OperatorChangeSetService(read_service=read)
    server = MCPServer(
        "etf-quant-draft-proposals",
        version=BRIDGE_VERSION,
        instructions=(
            "Inspect strategy data and prepare isolated Draft ChangeSet proposals only. "
            "A proposal does not modify the Draft. No approval or apply capability is available."
        ),
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

    @server.tool(annotations=_PROPOSAL)
    def quant_prepare_draft_changes(
        strategy_id: str,
        expected_draft_revision: int,
        operations: list[DraftChangeOperation],
    ) -> dict[str, Any]:
        """Persist a validated hypothetical ChangeSet; never save or alter the Draft."""
        try:
            return proposals.prepare(_id(strategy_id), expected_draft_revision, operations)
        except ChangeSetPreparationError as exc:
            return {"status": "REJECTED", "reason": str(exc), "change_set_id": None}

    @server.tool(annotations=_READ_ONLY)
    def quant_get_change_set(change_set_id: str) -> dict[str, Any]:
        """Read a persisted proposal and its effective stale status; no writes."""
        return proposals.get_change_set(_id(change_set_id))

    return server


def main() -> None:
    create_server().run(transport="stdio")


if __name__ == "__main__":
    main()
