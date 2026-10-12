"""Prepare bounded Draft ChangeSets without modifying strategy state."""

from __future__ import annotations

import copy
import math
import re
import sqlite3
from dataclasses import replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, field_validator

from backend.app.operator_change_set_repository import OperatorChangeSetRepository
from backend.app.operator_read_service import OperatorReadError, OperatorReadService
from backend.app.strategy_repository import (
    StrategyDraft,
    StrategyPersistenceError,
    StrategyRepository,
)
from research.exceptions import InvalidParameterBindingError, ParameterBindingError
from research.materialization import ParameterBinding, _apply_binding, _resolve_target
from research.semantic_bindings import (
    SemanticParameterBinding,
    SemanticTargetDescriptor,
    SemanticTargetKind,
    discover_semantic_bindings,
    resolve_semantic_bindings,
)
from strategies import StrategyDefinition, StrategyValidator, StrategyVersion

_SYMBOL = re.compile(r"[A-Z][A-Z0-9.-]{0,15}\Z")
_MAX_OPERATIONS = 16


class OperationKind(StrEnum):
    SET_INDICATOR_PERIOD = "SET_INDICATOR_PERIOD"
    SET_TRANSITION_THRESHOLD = "SET_TRANSITION_THRESHOLD"
    SET_STATE_ALLOCATION = "SET_STATE_ALLOCATION"
    SET_PEAK_DRAWDOWN_THRESHOLD = "SET_PEAK_DRAWDOWN_THRESHOLD"
    SET_INITIAL_ALLOCATION = "SET_INITIAL_ALLOCATION"
    SET_TIMEFRAME = "SET_TIMEFRAME"


class DraftChangeOperation(BaseModel):
    """A semantic identity, never a caller-provided JSON or array path."""

    model_config = ConfigDict(extra="forbid")

    operation_type: OperationKind
    value: int | float | str | dict[str, float]
    transition_id: str | None = None
    state_id: str | None = None
    symbol: str | None = None
    semantic_target: str | None = None

    @field_validator("value", mode="before")
    @classmethod
    def reject_implicit_value_coercion(cls, value: object) -> object:
        if isinstance(value, bool):
            raise ValueError("boolean is not a typed operation value")
        if isinstance(value, dict):
            if any(
                not isinstance(key, str)
                or isinstance(weight, bool)
                or not isinstance(weight, (int, float))
                for key, weight in value.items()
            ):
                raise ValueError("allocation weights must be numeric")
        return value


class ChangeSetPreparationError(ValueError):
    """Stable error code for a rejected proposal request."""


def _draft_envelope(draft: StrategyDraft, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "strategy_id": draft.strategy_id,
        "active_mode": draft.active_mode,
        "rule_based_draft": dict(draft.rule_based_draft),
        "regime_draft": dict(draft.regime_draft),
        "active_strategy_payload": payload,
        "source_metadata": dict(draft.source_metadata),
    }


def _numeric(value: object, *, integer: bool = False) -> int | float:
    if integer:
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ChangeSetPreparationError("INVALID_POSITIVE_PERIOD")
        return value
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ChangeSetPreparationError("INVALID_NUMERIC_VALUE")
    if not math.isfinite(float(value)):
        raise ChangeSetPreparationError("INVALID_NUMERIC_VALUE")
    return float(value)


def _display(value: object, kind: OperationKind) -> str:
    if kind in {
        OperationKind.SET_PEAK_DRAWDOWN_THRESHOLD,
        OperationKind.SET_TRANSITION_THRESHOLD,
        OperationKind.SET_STATE_ALLOCATION,
    }:
        return f"{float(value) * 100:.15g}%"
    if isinstance(value, dict):
        return ", ".join(
            f"{symbol} {weight * 100:.15g}%" for symbol, weight in sorted(value.items())
        )
    return str(value).upper() if kind is OperationKind.SET_TIMEFRAME else str(value)


def _binding_in_draft_order(
    binding: ParameterBinding,
    descriptor: SemanticTargetDescriptor,
    payload: dict[str, Any],
) -> ParameterBinding:
    """Map internal canonical indices to the stored Draft's ID-based ordering."""
    path = binding.target_path
    if descriptor.transition_id is not None:
        matches = [
            index
            for index, item in enumerate(payload.get("transitions", ()))
            if item.get("transition_id") == descriptor.transition_id
        ]
        if len(matches) != 1 or re.match(r"^transitions\[\d+\]", path) is None:
            raise ChangeSetPreparationError("SEMANTIC_TARGET_INVALID")
        path = re.sub(r"^transitions\[\d+\]", f"transitions[{matches[0]}]", path)
    elif descriptor.state_id is not None:
        states = [
            (index, item)
            for index, item in enumerate(payload.get("regimes", ()))
            if item.get("state_id") == descriptor.state_id
        ]
        if len(states) != 1 or re.match(r"^regimes\[\d+\]", path) is None:
            raise ChangeSetPreparationError("SEMANTIC_TARGET_INVALID")
        state_index, state = states[0]
        allocations = [
            index
            for index, item in enumerate(state.get("target_allocation", {}).get("allocations", ()))
            if item.get("symbol") == descriptor.symbol
        ]
        if len(allocations) != 1 or re.search(r"\.allocations\[\d+\]", path) is None:
            raise ChangeSetPreparationError("SEMANTIC_TARGET_INVALID")
        path = re.sub(r"^regimes\[\d+\]", f"regimes[{state_index}]", path)
        path = re.sub(r"\.allocations\[\d+\]", f".allocations[{allocations[0]}]", path)
    return replace(binding, target_path=path)


class OperatorChangeSetService:
    """Validate and persist proposals; never call a draft/version write method."""

    def __init__(
        self,
        read_service: OperatorReadService | None = None,
        repository: OperatorChangeSetRepository | None = None,
    ) -> None:
        self._read = read_service or OperatorReadService()
        self._repository = repository or OperatorChangeSetRepository()
        self._validator = StrategyValidator()

    def prepare(
        self,
        strategy_id: str,
        expected_draft_revision: int,
        operations: list[DraftChangeOperation],
    ) -> dict[str, Any]:
        if isinstance(expected_draft_revision, bool) or expected_draft_revision < 1:
            raise ChangeSetPreparationError("INVALID_DRAFT_REVISION")
        if not 1 <= len(operations) <= _MAX_OPERATIONS:
            raise ChangeSetPreparationError("INVALID_OPERATION_COUNT")
        try:
            draft = self._read._strategies.get_draft(strategy_id)
        except (StrategyPersistenceError, sqlite3.Error) as exc:
            raise ChangeSetPreparationError("DRAFT_READ_UNAVAILABLE") from exc
        if draft is None or draft.active_strategy_payload is None:
            raise ChangeSetPreparationError("ACTIVE_DRAFT_NOT_FOUND")
        if draft.draft_revision != expected_draft_revision:
            raise ChangeSetPreparationError("DRAFT_REVISION_CONFLICT")
        after_payload, details = self.preview(draft, operations)
        proposal = {
            "change_set_id": f"change-set-{uuid4().hex}",
            "source": "CODEX_MCP",
            "strategy_id": strategy_id,
            "expected_draft_revision": draft.draft_revision,
            "operations": [item.model_dump(mode="json", exclude_none=True) for item in operations],
            "before_hash": draft.draft_hash,
            **details,
            "status": "PROPOSED",
            "created_at": datetime.now(UTC).isoformat(),
        }
        self._repository.insert(proposal)
        return self.get_change_set(proposal["change_set_id"])

    def preview(
        self, draft: StrategyDraft, operations: list[DraftChangeOperation]
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Replay typed operations on a clone for prepare and locked approval."""
        if not 1 <= len(operations) <= _MAX_OPERATIONS:
            raise ChangeSetPreparationError("INVALID_OPERATION_COUNT")
        if draft.active_strategy_payload is None:
            raise ChangeSetPreparationError("ACTIVE_DRAFT_NOT_FOUND")
        before_payload = dict(draft.active_strategy_payload)
        if (
            StrategyRepository._hash_json(_draft_envelope(draft, before_payload))
            != draft.draft_hash
        ):
            raise ChangeSetPreparationError("DRAFT_HASH_MISMATCH")
        try:
            definition = StrategyDefinition.from_dict(before_payload)
            base = StrategyVersion(
                strategy_id=draft.strategy_id,
                version_id=f"draft-revision-{draft.draft_revision}",
                version_number=1,
                created_at=draft.created_at,
                configuration=definition,
            )
        except (TypeError, ValueError) as exc:
            raise ChangeSetPreparationError("ACTIVE_DRAFT_INVALID") from exc
        catalog = discover_semantic_bindings(base)
        after_payload = copy.deepcopy(before_payload)
        changes: list[dict[str, Any]] = []
        identities: set[str] = set()
        for operation in operations:
            if not isinstance(operation, DraftChangeOperation):
                raise ChangeSetPreparationError("INVALID_OPERATION")
            change = self._apply_one(base, catalog.supported, after_payload, operation)
            if change["identity"] in identities:
                raise ChangeSetPreparationError("DUPLICATE_SEMANTIC_TARGET")
            identities.add(change["identity"])
            if not change["changed"]:
                raise ChangeSetPreparationError("NO_CHANGE")
            changes.append(change)
        validation = self._validator.validate(after_payload).to_dict()
        proposed_hash = StrategyRepository._hash_json(_draft_envelope(draft, after_payload))
        unchanged = ["Price field", "Execution timing"]
        if all(op.operation_type is OperationKind.SET_PEAK_DRAWDOWN_THRESHOLD for op in operations):
            unchanged = [
                "Exit logic",
                "Recovery logic",
                "MA period",
                "State allocations",
                *unchanged,
            ]
        details = {
            "proposed_after_hash": proposed_hash,
            "validation_status": "PASS" if validation["is_valid"] else "FAIL",
            "validation_issues": validation["errors"],
            "validation_warnings": validation["warnings"],
            "human_readable_diff": {
                "summary": f"Codex proposes {len(changes)} change(s)",
                "changes": [
                    {
                        key: value
                        for key, value in change.items()
                        if key not in {"identity", "changed"}
                    }
                    for change in changes
                ],
                "unchanged": unchanged,
            },
        }
        return after_payload, details

    def _apply_one(
        self,
        base: StrategyVersion,
        supported: tuple[SemanticParameterBinding, ...],
        payload: dict[str, Any],
        operation: DraftChangeOperation,
    ) -> dict[str, Any]:
        kind = operation.operation_type
        if kind is OperationKind.SET_INITIAL_ALLOCATION:
            if any(
                (
                    operation.transition_id,
                    operation.state_id,
                    operation.symbol,
                    operation.semantic_target,
                )
            ):
                raise ChangeSetPreparationError("UNEXPECTED_OPERATION_SELECTOR")
            allocation = operation.value
            if not isinstance(allocation, dict) or len(allocation) > 16:
                raise ChangeSetPreparationError("INVALID_INITIAL_ALLOCATION")
            clean: dict[str, float] = {}
            for symbol, weight in allocation.items():
                if not _SYMBOL.fullmatch(symbol):
                    raise ChangeSetPreparationError("INVALID_ALLOCATION_SYMBOL")
                clean[symbol] = float(_numeric(weight))
                if not 0 <= clean[symbol] <= 1:
                    raise ChangeSetPreparationError("INVALID_ALLOCATION_WEIGHT")
            before = {
                item["symbol"]: item["target_weight"]
                for item in (payload.get("initial_allocation") or {}).get("allocations", ())
            }
            payload["initial_allocation"] = {
                "allocations": [
                    {"symbol": symbol, "target_weight": weight}
                    for symbol, weight in sorted(clean.items())
                ]
            }
            return {
                "identity": "initial_allocation",
                "label": "Starting allocation",
                "before": _display(before, kind),
                "after": _display(clean, kind),
                "changed": before != clean,
            }
        allowed = {
            OperationKind.SET_INDICATOR_PERIOD: {
                SemanticTargetKind.TRANSITION_INDICATOR_PERIOD,
                SemanticTargetKind.NESTED_CONDITION_PERIOD,
            },
            OperationKind.SET_TRANSITION_THRESHOLD: {
                SemanticTargetKind.TRANSITION_THRESHOLD,
                SemanticTargetKind.NESTED_CONDITION_THRESHOLD,
            },
            OperationKind.SET_STATE_ALLOCATION: {SemanticTargetKind.STATE_ALLOCATION_WEIGHT},
            OperationKind.SET_PEAK_DRAWDOWN_THRESHOLD: {SemanticTargetKind.PEAK_DRAWDOWN_THRESHOLD},
            OperationKind.SET_TIMEFRAME: {
                SemanticTargetKind.TRANSITION_INDICATOR_PERIOD,
                SemanticTargetKind.NESTED_CONDITION_PERIOD,
            },
        }[kind]
        if kind is OperationKind.SET_PEAK_DRAWDOWN_THRESHOLD and (
            base.configuration.strategy_mode.value != "regime_state_machine"
        ):
            raise ChangeSetPreparationError("PEAK_DRAWDOWN_ENTRY_ONLY")
        matches = [
            item
            for item in supported
            if item.descriptor.target_kind in allowed
            and (
                operation.transition_id is None
                or item.descriptor.transition_id == operation.transition_id
            )
            and (operation.state_id is None or item.descriptor.state_id == operation.state_id)
            and (operation.symbol is None or item.descriptor.symbol == operation.symbol)
            and (
                operation.semantic_target is None
                or item.parameter_name == operation.semantic_target
            )
        ]
        if len(matches) != 1:
            code = (
                "PEAK_DRAWDOWN_ENTRY_ONLY"
                if kind is OperationKind.SET_PEAK_DRAWDOWN_THRESHOLD and not matches
                else "SEMANTIC_TARGET_NOT_UNIQUE"
            )
            raise ChangeSetPreparationError(code)
        selected = matches[0]
        descriptor = selected.descriptor
        if kind is OperationKind.SET_STATE_ALLOCATION:
            if not operation.state_id or not operation.symbol:
                raise ChangeSetPreparationError("STATE_AND_SYMBOL_REQUIRED")
        elif not operation.transition_id:
            raise ChangeSetPreparationError("TRANSITION_ID_REQUIRED")
        try:
            resolved = resolve_semantic_bindings(base, (selected,))[0]
        except InvalidParameterBindingError as exc:
            raise ChangeSetPreparationError("SEMANTIC_TARGET_INVALID") from exc
        binding = _binding_in_draft_order(resolved.parameter_binding, descriptor, payload)
        try:
            target = _resolve_target(payload, binding.target_path)
        except ParameterBindingError as exc:
            raise ChangeSetPreparationError("SEMANTIC_TARGET_INVALID") from exc
        field = (
            "timeframe"
            if kind is OperationKind.SET_TIMEFRAME
            else "period"
            if kind is OperationKind.SET_INDICATOR_PERIOD
            else "target_weight"
            if kind is OperationKind.SET_STATE_ALLOCATION
            else "value"
        )
        before = target[field]
        if kind is OperationKind.SET_TIMEFRAME:
            if operation.value not in {"daily", "weekly"}:
                raise ChangeSetPreparationError("INVALID_TIMEFRAME")
            target[field] = operation.value
        else:
            value = _numeric(operation.value, integer=kind is OperationKind.SET_INDICATOR_PERIOD)
            if kind is OperationKind.SET_STATE_ALLOCATION and not 0 <= value <= 1:
                raise ChangeSetPreparationError("INVALID_ALLOCATION_WEIGHT")
            if kind is OperationKind.SET_PEAK_DRAWDOWN_THRESHOLD and not -1 <= value < 0:
                raise ChangeSetPreparationError("INVALID_PEAK_THRESHOLD")
            try:
                _apply_binding(payload, binding, value)
            except ParameterBindingError as exc:
                raise ChangeSetPreparationError("SEMANTIC_TARGET_INVALID") from exc
        transition = next(
            (
                item
                for item in payload.get("transitions", ())
                if item.get("transition_id") == descriptor.transition_id
            ),
            None,
        )
        label = (
            f"{transition['to_state']} Entry | Peak Drawdown threshold"
            if kind is OperationKind.SET_PEAK_DRAWDOWN_THRESHOLD and transition
            else f"{descriptor.state_id} | {descriptor.symbol} target weight"
            if kind is OperationKind.SET_STATE_ALLOCATION
            else f"{descriptor.transition_id} | {kind.value.replace('_', ' ').title()}"
        )
        return {
            "identity": selected.parameter_name
            if kind is not OperationKind.SET_TIMEFRAME
            else selected.parameter_name + ":timeframe",
            "label": label,
            "before": _display(before, kind),
            "after": _display(target[field], kind),
            "changed": before != target[field],
        }

    def get_change_set(self, change_set_id: str) -> dict[str, Any]:
        proposal = self._repository.get(change_set_id)
        if proposal is None:
            raise ChangeSetPreparationError("CHANGE_SET_NOT_FOUND")
        if proposal["status"] == "PROPOSED":
            try:
                draft = self._read._strategies.get_draft(proposal["strategy_id"])
            except (OperatorReadError, StrategyPersistenceError, sqlite3.Error):
                draft = None
            if (
                draft is None
                or draft.draft_revision != proposal["expected_draft_revision"]
                or draft.draft_hash != proposal["before_hash"]
            ):
                proposal["status"] = "STALE"
        return proposal
