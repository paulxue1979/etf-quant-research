"""Read-only projections for the local Codex operator bridge."""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path
from typing import Any

from backend.app.strategy_repository import StrategyPersistenceError, StrategyRepository
from data.cache import DiskCache
from data.exceptions import DataEngineError
from data.models import HistoricalDataRequest
from research.data_availability import project_data_availability, warmup_start_for_strategy
from research.semantic_bindings import discover_semantic_bindings
from strategies import StrategyDefinition, StrategyValidator
from strategies.models import Condition, Operand, RuleGroup, StrategyVersion
from strategies.strategy_evaluation import required_indicators

BACKEND_VERSION = "1.4.1"
BRIDGE_VERSION = "0.1.0"


class OperatorReadError(ValueError):
    """Stable, non-sensitive read failure for MCP clients."""


class _ReadOnlyStrategyRepository(StrategyRepository):
    """Reuse canonical repository reads without its write-capable initialization."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    def _connect(self) -> sqlite3.Connection:
        try:
            connection = sqlite3.connect(
                self.db_path.resolve().as_uri() + "?mode=ro",
                uri=True,
                timeout=5.0,
                isolation_level=None,
            )
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA query_only = ON")
            return connection
        except sqlite3.Error as exc:
            raise StrategyPersistenceError("read-only strategy store unavailable") from exc


def _operand_label(operand: Operand) -> str:
    if operand.operand_type.value == "constant":
        return str(operand.value)
    label = (
        f"{operand.symbol} {operand.timeframe.value.upper()} {operand.operand_type.value.upper()}"
    )
    return f"{label}{operand.period}" if operand.period is not None else label


def _condition_label(node: Condition | RuleGroup) -> str:
    if isinstance(node, RuleGroup):
        return f" {node.operator.value.upper()} ".join(
            f"({_condition_label(child)})" for child in node.children
        )
    text = f"{_operand_label(node.left)} {node.operator.value} {_operand_label(node.right)}"
    if node.threshold is not None:
        text += f"; {node.threshold.threshold_type.value} {node.threshold.value}"
    return text


def _allocation_rows(allocation: Any) -> list[dict[str, Any]]:
    return [
        {"symbol": item.symbol.symbol, "target_weight": item.target_weight}
        for item in allocation.allocations
    ]


def _target_label(descriptor: Any) -> str:
    location = descriptor.transition_id or descriptor.state_id or descriptor.zone_id
    return f"{location} | {descriptor.target_kind.value.replace('_', ' ')}"


class OperatorReadService:
    """Bounded, typed views over existing strategy and research domain services."""

    def __init__(self, db_path: Path | None = None, cache: DiskCache | None = None) -> None:
        path = db_path or Path(__file__).resolve().parents[2] / "data" / "strategy.db"
        self._strategies = _ReadOnlyStrategyRepository(path)
        self._cache = cache or DiskCache()
        self._validator = StrategyValidator()

    def health(self) -> dict[str, str]:
        try:
            self._strategies.list_strategies()
        except (StrategyPersistenceError, sqlite3.Error):
            status = "UNAVAILABLE"
        else:
            status = "READY"
        return {
            "status": status,
            "backend_version": BACKEND_VERSION,
            "bridge_version": BRIDGE_VERSION,
        }

    def list_strategies(self) -> dict[str, Any]:
        try:
            strategies = self._strategies.list_strategies()
            rows = []
            for strategy in strategies:
                versions = self._strategies.list_version_records(strategy.strategy_id)
                draft = self._strategies.get_draft(strategy.strategy_id)
                rows.append(
                    {
                        "strategy_id": strategy.strategy_id,
                        "display_name": strategy.name,
                        "mode": draft.active_mode
                        if draft
                        else (
                            versions[-1].version.configuration.strategy_mode.value
                            if versions
                            else None
                        ),
                        "draft_exists": draft is not None,
                        "latest_version": versions[-1].version.version_id if versions else None,
                        "lifecycle": strategy.lifecycle_status,
                    }
                )
            return {"strategies": rows[:100], "total": len(rows), "truncated": len(rows) > 100}
        except (StrategyPersistenceError, sqlite3.Error) as exc:
            raise OperatorReadError("STRATEGY_READ_UNAVAILABLE") from exc

    def get_strategy(self, strategy_id: str) -> dict[str, Any]:
        try:
            strategy = self._strategies.get_strategy(strategy_id)
            if strategy is None:
                raise OperatorReadError("STRATEGY_NOT_FOUND")
            versions = self._strategies.list_version_records(strategy_id)
            draft = self._strategies.get_draft(strategy_id)
            latest = versions[-1].version if versions else None
            return {
                "strategy_id": strategy.strategy_id,
                "display_name": strategy.name,
                "description": strategy.description,
                "lifecycle": strategy.lifecycle_status,
                "mode": draft.active_mode
                if draft
                else (latest.configuration.strategy_mode.value if latest else None),
                "draft_exists": draft is not None,
                "draft_revision": draft.draft_revision if draft else None,
                "version_count": len(versions),
                "latest_version_id": latest.version_id if latest else None,
                "latest_version_hash": latest.content_hash if latest else None,
            }
        except (StrategyPersistenceError, sqlite3.Error) as exc:
            raise OperatorReadError("STRATEGY_READ_UNAVAILABLE") from exc

    def get_draft(self, strategy_id: str) -> dict[str, Any]:
        try:
            draft = self._strategies.get_draft(strategy_id)
            if draft is None:
                raise OperatorReadError("DRAFT_NOT_FOUND")
            payload = draft.active_strategy_payload
            try:
                definition = self._active_definition(draft) if payload else None
            except OperatorReadError:
                definition = None
            return {
                "strategy_id": strategy_id,
                "draft_id": None,
                "revision": draft.draft_revision,
                "draft_hash": draft.draft_hash,
                "mode": draft.active_mode,
                "active_semantics": {
                    "price_field": definition.price_field.value if definition else None,
                    "no_match_behavior": (
                        definition.no_match_behavior.value
                        if definition and definition.strategy_mode.value == "rule_based"
                        else None
                    ),
                    "initial_regime": definition.initial_regime if definition else None,
                },
                "summary": {
                    "name": definition.name if definition else None,
                    "assets": [item.symbol for item in definition.assets] if definition else [],
                    "state_count": len(definition.regimes) if definition else 0,
                    "transition_count": len(definition.transitions) if definition else 0,
                    "active_payload_available": payload is not None,
                    "active_payload_parseable": definition is not None,
                },
            }
        except (StrategyPersistenceError, sqlite3.Error) as exc:
            raise OperatorReadError("DRAFT_READ_UNAVAILABLE") from exc

    def _active_definition(self, draft: Any) -> StrategyDefinition:
        try:
            return StrategyDefinition.from_dict(draft.active_strategy_payload)
        except (TypeError, ValueError) as exc:
            raise OperatorReadError("ACTIVE_DRAFT_INVALID") from exc

    def _source(
        self, strategy_id: str, version_id: str | None = None
    ) -> tuple[StrategyDefinition, str]:
        try:
            if version_id:
                version = self._strategies.get(strategy_id, version_id)
                if version is None:
                    raise OperatorReadError("VERSION_NOT_FOUND")
                return version.configuration, version.version_id
            draft = self._strategies.get_draft(strategy_id)
            if draft is None:
                raise OperatorReadError("DRAFT_NOT_FOUND")
            return self._active_definition(draft), f"draft-revision-{draft.draft_revision}"
        except (StrategyPersistenceError, sqlite3.Error) as exc:
            raise OperatorReadError("STRATEGY_READ_UNAVAILABLE") from exc

    def get_structure(self, strategy_id: str, version_id: str | None = None) -> dict[str, Any]:
        definition, source = self._source(strategy_id, version_id)
        return {
            "strategy_id": strategy_id,
            "source": source,
            "mode": definition.strategy_mode.value,
            "initial_regime": definition.initial_regime,
            "states": [
                {
                    "state_id": state.state_id,
                    "display_name": state.display_name,
                    "target_allocations": _allocation_rows(state.target_allocation),
                }
                for state in definition.regimes
            ],
            "transitions": [
                {
                    "transition_id": edge.transition_id,
                    "from_state": edge.from_state,
                    "to_state": edge.to_state,
                    "priority": edge.priority,
                    "condition": _condition_label(edge.condition),
                }
                for edge in definition.transitions
            ],
            "value_zones": [
                {
                    "zone_id": zone.zone_id,
                    "display_name": zone.display_name,
                    "asset": zone.asset.symbol,
                    "indicator": (
                        f"{zone.timeframe.value.upper()} "
                        f"{zone.indicator_kind.value.upper()}{zone.period}"
                    ),
                    "entry_threshold": zone.entry_threshold,
                    "exit_threshold": zone.exit_threshold,
                    "priority": zone.priority,
                }
                for zone in definition.value_zones
            ],
            "rules": [
                {
                    "rule_id": rule.rule_id,
                    "priority": rule.priority,
                    "condition": _condition_label(rule.condition) if rule.condition else "always",
                    "target_allocations": [
                        {"symbol": item.symbol.symbol, "target_weight": item.target_weight}
                        for item in rule.allocations
                    ],
                }
                for rule in definition.rules
            ]
            if definition.strategy_mode.value == "rule_based"
            else [],
        }

    def get_semantic_targets(self, strategy_id: str, version_id: str) -> dict[str, Any]:
        version = self._version(strategy_id, version_id)
        catalog = discover_semantic_bindings(version)
        return {
            "strategy_version_id": version.version_id,
            "strategy_version_hash": version.content_hash,
            "targets": [
                {
                    "parameter_name": item.parameter_name,
                    "value_type": item.value_type.value,
                    "target_kind": item.descriptor.target_kind.value,
                    "label": _target_label(item.descriptor),
                    "descriptor": item.descriptor.to_dict(),
                }
                for item in catalog.supported
            ],
            "unsupported": [dict(item) for item in catalog.unsupported],
        }

    def validate_draft(self, strategy_id: str) -> dict[str, Any]:
        try:
            draft = self._strategies.get_draft(strategy_id)
            if draft is None:
                raise OperatorReadError("DRAFT_NOT_FOUND")
            if draft.active_strategy_payload is None:
                return {
                    "status": "FAIL",
                    "blocking_issues": [
                        {
                            "code": "ACTIVE_PAYLOAD_MISSING",
                            "path": "$",
                            "message": "Active draft is incomplete.",
                        }
                    ],
                    "warnings": [],
                }
            result = self._validator.validate(draft.active_strategy_payload).to_dict()
            return {
                "status": "PASS" if result["is_valid"] else "FAIL",
                "blocking_issues": result["errors"],
                "warnings": result["warnings"],
            }
        except (StrategyPersistenceError, sqlite3.Error) as exc:
            raise OperatorReadError("DRAFT_READ_UNAVAILABLE") from exc

    def _version(self, strategy_id: str, version_id: str) -> StrategyVersion:
        try:
            version = self._strategies.get(strategy_id, version_id)
        except (StrategyPersistenceError, sqlite3.Error) as exc:
            raise OperatorReadError("VERSION_READ_UNAVAILABLE") from exc
        if version is None:
            raise OperatorReadError("VERSION_NOT_FOUND")
        return version

    def get_data_availability(
        self, strategy_id: str, version_id: str, requested_start: date, requested_end: date
    ) -> dict[str, Any]:
        if requested_start > requested_end:
            raise OperatorReadError("INVALID_DATE_RANGE")
        version = self._version(strategy_id, version_id)
        warmup = warmup_start_for_strategy(requested_start, version)
        symbols = {asset.symbol for asset in version.configuration.assets}
        symbols.update(item.symbol for item in required_indicators(version))
        data = {}
        cache_misses = []
        for symbol in sorted(symbols):
            request = HistoricalDataRequest(
                symbol=symbol,
                start_date=warmup,
                end_date=requested_end,
                price_field_used=version.configuration.price_field,
            )
            try:
                data[symbol] = self._cache.get(request)
            except DataEngineError:
                data[symbol] = None
            if data[symbol] is None:
                cache_misses.append(symbol)
        projection = project_data_availability(
            version,
            requested_start=requested_start,
            requested_end=requested_end,
            data_by_symbol=data,
            warmup_start=warmup,
        )
        return {
            "status": projection["status"],
            "cache_only": True,
            "cache_miss_symbols": cache_misses,
            "cache_miss_is_not_market_unavailability": True,
            "requested_start": projection["requested_start"],
            "requested_end": projection["requested_end"],
            "effective_start_expected": projection["effective_start_expected"],
            "effective_end_expected": projection["effective_end_expected"],
            "warmup_start": projection["warmup_start"],
            "price_field": projection["price_field"],
            "history_requirement": "all_time_to_date"
            if any(
                item.get("peak_mode") == "all_time_to_date"
                for item in projection["indicator_requirements"]
            )
            else "fixed_warmup",
            "assets": projection["assets"],
            "indicator_requirements": projection["indicator_requirements"],
            "warnings": projection["warnings"],
            "blocking_issues": projection["blocking_issues"],
        }
