"""MCP preparation and browser-only approval of bounded canonical IS grids."""

from __future__ import annotations

import math
import threading
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Any
from uuid import uuid4

from backend.app.backtest_repository import BacktestRepository
from backend.app.candidate_execution_repository import CandidateExecutionRepository
from backend.app.controlled_experiment_repository import (
    ControlledExperimentError,
    ControlledExperimentRepository,
)
from backend.app.experiment_repository import ExperimentRepository
from backend.app.experiment_result_repository import ExperimentResultRepository
from backend.app.experiment_results_read_service import ExperimentResultsReadService
from backend.app.experiment_workflow_api import _build_experiment
from backend.app.grid_search_api import _service as canonical_grid_service
from backend.app.operator_read_service import OperatorReadService
from backend.app.research_protocol import (
    CandidateSetStatus,
    ProtocolStatus,
    ResearchProtocolRepository,
)
from backend.app.strategy_repository import StrategyRepository
from research.canonical import sha256_hash
from research.enums import ExperimentStatus
from research.experiments import Experiment
from research.grid_search import GridSearchDefinition, preflight_grid_search
from research.semantic_bindings import discover_semantic_bindings


class ControlledExperimentService:
    MAX_AXES = 2
    MAX_CANDIDATES = 16

    def __init__(
        self,
        *,
        path: Path | None = None,
        read: OperatorReadService | None = None,
        grid: Any | None = None,
    ) -> None:
        self._plans = ControlledExperimentRepository(path)
        self._path = self._plans.path
        self._strategies = StrategyRepository(self._path)
        self._protocols = ResearchProtocolRepository(self._path)
        self._experiments = ExperimentRepository(self._path)
        self._read = read or OperatorReadService(self._path)
        self._grid = grid

    @staticmethod
    def _date(value: str) -> date:
        try:
            parsed = date.fromisoformat(value)
            if parsed.isoformat() != value:
                raise ValueError
            return parsed
        except (TypeError, ValueError) as exc:
            raise ControlledExperimentError("INVALID_IS_RANGE") from exc

    @staticmethod
    def _values(raw: Any, value_type: str, unit: str) -> list[int | float]:
        if not isinstance(raw, list) or not 1 <= len(raw) <= 16:
            raise ControlledExperimentError("INVALID_AXIS_VALUES")
        percentage = value_type in {"relative_threshold", "allocation_weight"}
        if unit != ("percent" if percentage else "native"):
            raise ControlledExperimentError("INVALID_AXIS_UNIT")
        values: list[int | float] = []
        for value in raw:
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
            ):
                raise ControlledExperimentError("INVALID_AXIS_VALUES")
            if percentage:
                if value_type == "allocation_weight" and not 0 <= value <= 100:
                    raise ControlledExperimentError("INVALID_AXIS_VALUES")
                values.append(value / 100)
            else:
                if not isinstance(value, int) or (value_type == "positive_integer" and value <= 0):
                    raise ControlledExperimentError("INVALID_AXIS_VALUES")
                values.append(value)
        if len(set(values)) != len(values):
            raise ControlledExperimentError("DUPLICATE_AXIS_VALUES")
        return values

    def _frozen_inputs(
        self,
        strategy_id: str,
        version_id: str,
        version_hash: str,
        protocol_id: str,
        start: date,
        end: date,
        initial_capital: float,
    ) -> tuple[Any, Any]:
        if not protocol_id:
            raise ControlledExperimentError("FROZEN_PROTOCOL_REQUIRED")
        version = self._strategies.get(strategy_id, version_id)
        if version is None:
            raise ControlledExperimentError("STRATEGY_VERSION_NOT_FOUND")
        if version.content_hash != version_hash:
            raise ControlledExperimentError("STRATEGY_VERSION_HASH_MISMATCH")
        protocol = self._protocols.get_protocol(protocol_id)
        if protocol is None or protocol.status != ProtocolStatus.FROZEN:
            raise ControlledExperimentError("FROZEN_PROTOCOL_REQUIRED")
        if (protocol.is_start_date, protocol.is_end_date) != (start, end):
            raise ControlledExperimentError("PROTOCOL_IS_RANGE_MISMATCH")
        sets = self._protocols.list_candidate_sets(protocol_id)
        if not any(
            item.status == CandidateSetStatus.LOCKED
            and item.strategy_version_content_hashes.get(version_id) == version_hash
            for item in sets
        ):
            raise ControlledExperimentError("PROTOCOL_VERSION_BINDING_MISMATCH")
        evaluation = protocol.evaluation_config
        if evaluation is None:
            raise ControlledExperimentError("PROTOCOL_EVALUATION_MISSING")
        if (
            evaluation.initial_capital != initial_capital
            or evaluation.price_field_used != version.configuration.price_field.value
            or evaluation.rebalance_policy != version.configuration.rebalance_policy.to_dict()
            or evaluation.commission != {"rate": 0.0, "per_order": 0.0}
            or evaluation.slippage != 0
            or evaluation.contribution_schedule is not None
            or evaluation.position_rebalance_policy is not None
            or evaluation.fractional_shares
            or evaluation.execution_rule != "next_trading_day_open"
        ):
            raise ControlledExperimentError("UNSUPPORTED_EVALUATION_CONFIGURATION")
        return version, protocol

    def _plan(self, intent: dict[str, Any], config_id: str) -> dict[str, Any]:
        strategy_id = intent["strategy_id"]
        version_id = intent["strategy_version_id"]
        version_hash = intent["strategy_version_hash"]
        start, end = (
            self._date(intent["requested_is_start"]),
            self._date(intent["requested_is_end"]),
        )
        if start > end:
            raise ControlledExperimentError("INVALID_IS_RANGE")
        capital = intent["initial_capital"]
        if (
            isinstance(capital, bool)
            or not isinstance(capital, (int, float))
            or not math.isfinite(capital)
            or capital <= 0
        ):
            raise ControlledExperimentError("INVALID_INITIAL_CAPITAL")
        version, protocol = self._frozen_inputs(
            strategy_id, version_id, version_hash, intent["protocol_id"], start, end, capital
        )
        axes = intent["axes"]
        if not isinstance(axes, list) or not 1 <= len(axes) <= self.MAX_AXES:
            raise ControlledExperimentError("INVALID_AXIS_COUNT")
        catalog = {
            item.parameter_name: item for item in discover_semantic_bindings(version).supported
        }
        used: set[str] = set()
        parameters = []
        bindings = []
        display_axes = []
        for axis in axes:
            if not isinstance(axis, dict) or set(axis) != {"semantic_target_id", "unit", "values"}:
                raise ControlledExperimentError("INVALID_AXIS")
            target_id = axis["semantic_target_id"]
            if target_id not in catalog:
                raise ControlledExperimentError("UNSUPPORTED_TARGET")
            if target_id in used:
                raise ControlledExperimentError("DUPLICATE_TARGET")
            used.add(target_id)
            binding = catalog[target_id]
            values = self._values(axis["values"], binding.value_type.value, axis["unit"])
            parameters.append({"name": target_id, "type": "discrete", "allowed_values": values})
            bindings.append(binding.to_dict())
            display_axes.append(
                {**axis, "canonical_values": values, "value_type": binding.value_type.value}
            )
        count = math.prod(len(item["allowed_values"]) for item in parameters)
        if count > self.MAX_CANDIDATES:
            raise ControlledExperimentError("CANDIDATE_BUDGET_EXCEEDED")
        body = {
            "experiment_id": f"experiment-operator-{config_id.removeprefix('experiment-config-')}",
            "protocol_id": protocol.protocol_id,
            "base_strategy_version_id": version_id,
            "name": f"Controlled IS grid {config_id}",
            "is_start": start.isoformat(),
            "is_end": end.isoformat(),
            "initial_capital": capital,
            "price_field": version.configuration.price_field.value,
            "parameter_space": {
                "parameters": parameters,
                "constraints": [],
                "max_candidates": count,
            },
            "grid_definition": {
                "semantic_bindings": bindings,
                "fixed_parameters": {},
                "constraints": [],
                "max_candidates": count,
                "theoretical_guard": count,
            },
            "objective_specification": {
                "primary_metric": "cagr",
                "metric_directions": {"cagr": "maximize"},
            },
        }
        experiment = _build_experiment(body, strategies=self._strategies, protocols=self._protocols)
        definition = GridSearchDefinition.from_dict(
            {
                **body["grid_definition"],
                "experiment_id": experiment.experiment_id,
                "experiment_hash": experiment.content_hash,
            }
        )
        preflight = preflight_grid_search(experiment, definition, version)
        invalid = [dict(item) for item in preflight.pruned_samples]
        invalid.extend(
            {
                "source_index": item["source_index"],
                "values": item["values"],
                "reason": "DUPLICATE_CANDIDATE",
            }
            for item in preflight.duplicate_samples
        )
        invalid.sort(key=lambda item: item["source_index"])
        availability = self._read.get_data_availability(strategy_id, version_id, start, end)
        issues = list(preflight.issues)
        if invalid:
            issues.append("INVALID_CANDIDATE_COMBINATION")
        if availability["status"] != "READY":
            issues.append("DATA_PREFLIGHT_BLOCKED")
        return {
            "source": "CODEX_MCP",
            "config_id": config_id,
            "client_request_key": intent["client_request_key"],
            "strategy_id": strategy_id,
            "strategy_name": version.configuration.name,
            "strategy_version_id": version_id,
            "strategy_version_hash": version_hash,
            "protocol_id": protocol.protocol_id,
            "requested_is_start": start.isoformat(),
            "requested_is_end": end.isoformat(),
            "oos_start": protocol.oos_start_date.isoformat(),
            "oos_end": protocol.oos_end_date.isoformat(),
            "initial_capital": capital,
            "axes": display_axes,
            "candidate_count": count,
            "experiment_id": experiment.experiment_id,
            "experiment_snapshot": experiment.to_dict(),
            "grid_definition": definition.to_dict(),
            "grid_preflight": preflight.to_dict(),
            "invalid_candidates": invalid,
            "data_availability": availability,
            "validation_status": "READY" if not issues else "BLOCKED",
            "validation_issues": issues,
        }

    def prepare(
        self,
        *,
        strategy_id: str,
        strategy_version_id: str,
        strategy_version_hash: str,
        protocol_id: str,
        requested_is_start: str,
        requested_is_end: str,
        initial_capital: float,
        axes: list[dict[str, Any]],
        client_request_key: str,
    ) -> dict[str, Any]:
        if not isinstance(client_request_key, str) or not 1 <= len(client_request_key) <= 128:
            raise ControlledExperimentError("INVALID_REQUEST_KEY")
        intent = {
            "strategy_id": strategy_id,
            "strategy_version_id": strategy_version_id,
            "strategy_version_hash": strategy_version_hash,
            "protocol_id": protocol_id,
            "requested_is_start": requested_is_start,
            "requested_is_end": requested_is_end,
            "initial_capital": initial_capital,
            "axes": axes,
            "client_request_key": client_request_key,
        }
        intent_hash = sha256_hash(intent)
        proposal_hash = sha256_hash(
            {key: value for key, value in intent.items() if key != "client_request_key"}
        )
        existing = self._plans.get_by_key(client_request_key)
        if existing is not None:
            if existing["intent_hash"] != intent_hash:
                raise ControlledExperimentError("IDEMPOTENCY_KEY_CONFLICT")
            return existing
        config_id = f"experiment-config-{uuid4().hex}"
        body = self._plan(intent, config_id)
        body["intent_hash"] = intent_hash
        body["proposal_hash"] = proposal_hash
        return self._plans.prepare(body, intent_hash, proposal_hash)

    def get(self, config_id: str) -> dict[str, Any]:
        return self._plans.get(config_id)

    def list(self) -> list[dict[str, Any]]:
        return self._plans.list()

    def reject(self, config_id: str) -> dict[str, Any]:
        return self._plans.reject(config_id)

    def _revalidate(self, plan: dict[str, Any]) -> None:
        if plan["validation_status"] != "READY" or plan["invalid_candidates"]:
            raise ControlledExperimentError("EXPERIMENT_PREFLIGHT_BLOCKED")
        start, end = self._date(plan["requested_is_start"]), self._date(plan["requested_is_end"])
        version, _ = self._frozen_inputs(
            plan["strategy_id"],
            plan["strategy_version_id"],
            plan["strategy_version_hash"],
            plan["protocol_id"],
            start,
            end,
            plan["initial_capital"],
        )
        experiment = Experiment.from_dict(plan["experiment_snapshot"])
        definition = GridSearchDefinition.from_dict(plan["grid_definition"])
        if experiment.content_hash != definition.experiment_hash:
            raise ControlledExperimentError("EXPERIMENT_CONFIG_INTEGRITY_ERROR")
        preflight = preflight_grid_search(experiment, definition, version)
        if (
            not preflight.can_execute
            or preflight.pruned_count
            or preflight.duplicate_count
            or preflight.executable_count != plan["candidate_count"]
            or preflight.definition.definition_hash != plan["grid_preflight"]["definition_hash"]
        ):
            raise ControlledExperimentError("EXPERIMENT_PREFLIGHT_CHANGED")
        availability = self._read.get_data_availability(
            plan["strategy_id"], plan["strategy_version_id"], start, end
        )
        if availability["status"] != "READY":
            raise ControlledExperimentError("DATA_PREFLIGHT_BLOCKED")
        if availability != plan["data_availability"]:
            raise ControlledExperimentError("DATA_PREFLIGHT_CHANGED")

    def approve_and_run(self, config_id: str) -> dict[str, Any]:
        plan = self.get(config_id)
        if plan["execution_status"] == "COMPLETED":
            return plan
        if plan["approval_status"] not in {"PROPOSED", "APPROVED"}:
            raise ControlledExperimentError("EXPERIMENT_CONFIG_NOT_PROPOSED")
        if (
            self._grid is None
            and self._path.resolve()
            != (Path(__file__).resolve().parents[2] / "data" / "strategy.db").resolve()
        ):
            raise ControlledExperimentError("ISOLATED_GRID_SERVICE_REQUIRED")
        if plan["approval_status"] == "PROPOSED":
            try:
                self._revalidate(plan)
            except ControlledExperimentError as exc:
                if str(exc) not in {"DATA_PREFLIGHT_BLOCKED", "EXPERIMENT_PREFLIGHT_BLOCKED"}:
                    self._plans.stale(config_id, str(exc))
                raise
        else:
            version, _ = self._frozen_inputs(
                plan["strategy_id"],
                plan["strategy_version_id"],
                plan["strategy_version_hash"],
                plan["protocol_id"],
                self._date(plan["requested_is_start"]),
                self._date(plan["requested_is_end"]),
                plan["initial_capital"],
            )
            del version
            availability = self._read.get_data_availability(
                plan["strategy_id"],
                plan["strategy_version_id"],
                self._date(plan["requested_is_start"]),
                self._date(plan["requested_is_end"]),
            )
            if availability["status"] != "READY" or availability != plan["data_availability"]:
                raise ControlledExperimentError("DATA_PREFLIGHT_CHANGED")
        token = self._plans.claim(config_id)
        if token is None:
            return self.get(config_id)
        stop = threading.Event()

        def maintain_lease() -> None:
            while not stop.wait(30):
                self._plans.heartbeat(config_id, token)

        heartbeat = threading.Thread(target=maintain_lease, daemon=True)
        heartbeat.start()
        try:
            experiment = self._experiments.get(plan["experiment_id"])
            if experiment is None:
                experiment = Experiment.from_dict(plan["experiment_snapshot"])
                self._experiments.create(experiment)
            if (
                experiment.experiment_id != plan["experiment_id"]
                or replace(experiment, status=ExperimentStatus.SPACE_FROZEN).content_hash
                != plan["grid_definition"]["experiment_hash"]
            ):
                raise ControlledExperimentError("EXPERIMENT_IDENTITY_CONFLICT")
            grid = self._grid or canonical_grid_service()
            definition = GridSearchDefinition.from_dict(plan["grid_definition"])
            if experiment.status is ExperimentStatus.SPACE_FROZEN:
                prepared = grid.prepare(experiment.experiment_id, definition)
                if not prepared["can_execute"]:
                    raise ControlledExperimentError("GRID_PREFLIGHT_INVALID")
            progress = grid.execute(experiment.experiment_id)
            current = self._experiments.get(experiment.experiment_id)
            terminal = (
                "COMPLETED"
                if current.status is ExperimentStatus.COMPLETED
                else "FAILED"
                if current.status is ExperimentStatus.INVALID
                else "RUNNING"
            )
            self._plans.finish(config_id, token, terminal)
            return {**self.get(config_id), "grid_progress": progress}
        except Exception as exc:
            current = self._experiments.get(plan["experiment_id"])
            if current is not None and current.status is ExperimentStatus.COMPLETED:
                self._plans.finish(config_id, token, "COMPLETED")
                return self.get(config_id)
            self._plans.finish(config_id, token, "FAILED", type(exc).__name__)
            raise ControlledExperimentError("GRID_EXECUTION_FAILED") from exc
        finally:
            stop.set()
            heartbeat.join(timeout=1)

    def get_result(self, config_id: str) -> dict[str, Any]:
        plan = self.get(config_id)
        if plan["approval_status"] != "APPROVED":
            raise ControlledExperimentError("EXPERIMENT_NOT_APPROVED")
        read = ExperimentResultsReadService(
            self._experiments,
            CandidateExecutionRepository(self._path),
            ExperimentResultRepository(self._path),
            BacktestRepository(self._path),
            self._strategies,
        )
        return read.get(plan["experiment_id"], protocol_id=plan["protocol_id"]).to_dict()
