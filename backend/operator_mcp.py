"""Local stdio MCP surface for inspection and isolated Draft proposals."""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

from backend.app.controlled_backtest_service import (
    ControlledBacktestError,
    ControlledBacktestService,
)
from backend.app.controlled_version_service import ControlledVersionService, VersionRequestError
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
    backtest_service: ControlledBacktestService | None = None,
    version_service: ControlledVersionService | None = None,
) -> MCPServer:
    """Expose bounded reads and proposal persistence, never Draft mutation."""
    read = service or OperatorReadService()
    proposals = change_set_service or OperatorChangeSetService(read_service=read)

    def controlled_backtests() -> ControlledBacktestService:
        return backtest_service or ControlledBacktestService(read=read)

    def controlled_versions() -> ControlledVersionService:
        return version_service or ControlledVersionService()

    server = MCPServer(
        "etf-quant-draft-proposals",
        version=BRIDGE_VERSION,
        instructions=(
            "Inspect strategy data and prepare isolated Draft ChangeSets or exact-version "
            "backtest configurations. Only the browser can approve or execute a backtest. "
            "Resolve 'latest' to an exact immutable version before preparation. Require explicit "
            "ISO dates and contribution amounts; ask the user to clarify ambiguous dates or DCA. "
            "Monthly DCA covers the full backtest window; weekly and bounded DCA are unsupported."
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

    @server.tool(annotations=_READ_ONLY)
    def quant_get_strategy_version(strategy_id: str, version_id: str = "") -> dict[str, Any]:
        """Resolve an exact immutable version, or pin the latest immutable version now."""
        return controlled_backtests().get_strategy_version(
            _id(strategy_id), _id(version_id) if version_id else ""
        )

    @server.tool(annotations=_PROPOSAL)
    def quant_prepare_backtest_configuration(
        strategy_id: str,
        strategy_version_id: str,
        requested_start: str,
        requested_end: str,
        initial_capital: float,
        contribution_enabled: bool = False,
        contribution_amount: str | None = None,
        contribution_frequency: str | None = None,
        contribution_start_date: str | None = None,
        contribution_end_date: str | None = None,
        contribution_requested_date: str | None = None,
    ) -> dict[str, Any]:
        """Persist a pending configuration only; no backtest or strategy write occurs.

        Dates must be exact ISO dates. Monthly contributions cover the whole backtest
        range. Weekly and independently bounded schedules are unsupported and rejected.
        """
        try:
            return controlled_backtests().prepare(
                strategy_id=_id(strategy_id),
                strategy_version_id=_id(strategy_version_id),
                requested_start=requested_start,
                requested_end=requested_end,
                initial_capital=initial_capital,
                contribution_enabled=contribution_enabled,
                contribution_amount=contribution_amount,
                contribution_frequency=contribution_frequency,
                contribution_start_date=contribution_start_date,
                contribution_end_date=contribution_end_date,
                contribution_requested_date=contribution_requested_date,
            )
        except ControlledBacktestError as exc:
            return {"status": "REJECTED", "reason": str(exc), "config_id": None}

    @server.tool(annotations=_READ_ONLY)
    def quant_get_backtest_configuration(config_id: str) -> dict[str, Any]:
        """Read a persisted pending or terminal configuration without mutation."""
        return controlled_backtests().get(_id(config_id))

    @server.tool(annotations=_READ_ONLY)
    def quant_list_pending_backtest_configurations() -> dict[str, Any]:
        """List recent persisted configurations, including rejected and completed history."""
        return {"configurations": controlled_backtests().list()}

    @server.tool(annotations=_READ_ONLY)
    def quant_get_backtest_result(config_id: str) -> dict[str, Any]:
        """Read production analytics for an approved completed backtest only."""
        return controlled_backtests().get_result(_id(config_id))

    @server.tool(annotations=_PROPOSAL)
    def quant_prepare_strategy_version_creation(
        strategy_id: str,
        expected_draft_revision: int,
        expected_draft_hash: str,
        base_latest_version_id: str = "",
        base_latest_version_hash: str = "",
        client_request_key: str = "",
    ) -> dict[str, Any]:
        """Prepare only. Reuse a key for retries; a fresh key means an explicit new intent."""
        try:
            return controlled_versions().prepare(
                strategy_id=_id(strategy_id),
                expected_draft_revision=expected_draft_revision,
                expected_draft_hash=expected_draft_hash,
                base_latest_version_id=base_latest_version_id or None,
                base_latest_version_hash=base_latest_version_hash or None,
                client_request_key=client_request_key or None,
            )
        except VersionRequestError as exc:
            return {"status": "REJECTED", "reason": str(exc), "request_id": None}

    @server.tool(annotations=_READ_ONLY)
    def quant_get_strategy_version_creation_request(request_id: str) -> dict[str, Any]:
        """Read a persisted request and current approval gate without any write."""
        return controlled_versions().get(_id(request_id))

    return server


def main() -> None:
    create_server().run(transport="stdio")


if __name__ == "__main__":
    main()
