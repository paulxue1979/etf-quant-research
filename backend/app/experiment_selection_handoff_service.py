"""Controlled handoff from one experiment selection to PHASE 7 governance."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from backend.app.backtest_repository import BacktestRepository
from backend.app.candidate_execution_repository import CandidateExecutionRepository
from backend.app.experiment_repository import ExperimentRepository
from backend.app.experiment_result_repository import ExperimentResultRepository
from backend.app.experiment_selection_repository import ExperimentSelectionRepository
from backend.app.research_protocol import (
    CandidateSetStatus,
    ProtocolStatus,
    ResearchEvaluationConfig,
    ResearchProtocolRepository,
    SelectionDecision,
    StrategyFreezeRecord,
)
from backend.app.strategy_repository import StrategyRepository
from research.canonical import canonical_json, sha256_hash
from research.execution import CandidateExecutionStatus, candidate_id_for
from research.materialization import derived_strategy_version_hash, derived_strategy_version_id


class ExperimentSelectionHandoffError(RuntimeError):
    """Raised when an experiment selection cannot safely enter PHASE 7."""

    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


class ExperimentSelectionHandoffService:
    """Create one immutable PHASE 7 handoff from persisted IS-only evidence.

    This service deliberately resolves every identity from ``experiment_id``.
    It neither ranks candidates nor executes a backtest.  The protocol write is
    delegated to ``ResearchProtocolRepository`` so the SelectionDecision,
    lifecycle event, and StrategyFreezeRecord share one SQLite transaction.
    """

    def __init__(
        self,
        db_path: str | Path | None = None,
        *,
        experiment_repository: ExperimentRepository | None = None,
        selection_repository: ExperimentSelectionRepository | None = None,
        result_repository: ExperimentResultRepository | None = None,
        execution_repository: CandidateExecutionRepository | None = None,
        backtest_repository: BacktestRepository | None = None,
        strategy_repository: StrategyRepository | None = None,
        protocol_repository: ResearchProtocolRepository | None = None,
    ) -> None:
        path = Path(db_path) if db_path is not None else None
        self._experiments = experiment_repository or ExperimentRepository(path)
        shared_path = self._experiments.db_path
        self._selections = selection_repository or ExperimentSelectionRepository(shared_path)
        self._results = result_repository or ExperimentResultRepository(shared_path)
        self._executions = execution_repository or CandidateExecutionRepository(shared_path)
        self._backtests = backtest_repository or BacktestRepository(shared_path)
        self._strategies = strategy_repository or StrategyRepository(shared_path)
        self._protocols = protocol_repository or ResearchProtocolRepository(shared_path)

    def handoff_experiment_selection(
        self, experiment_id: str
    ) -> tuple[SelectionDecision, StrategyFreezeRecord]:
        """Persist the selected derived strategy as the protocol's IS selection."""
        if not isinstance(experiment_id, str) or not experiment_id.strip():
            self._fail("experiment_id is invalid", "EXPERIMENT_NOT_FOUND")
        experiment_id = experiment_id.strip()

        experiment = self._require(
            self._experiments.get(experiment_id), "experiment", "EXPERIMENT_NOT_FOUND"
        )
        if experiment.status.value != "selection_recorded":
            self._fail("experiment selection has not been recorded", "EXPERIMENT_INVALID_STATE")
        selection = self._require(
            self._selections.get_by_experiment_id(experiment_id),
            "experiment selection",
            "SELECTION_NOT_FOUND",
        )
        self._validate_selection(experiment, selection)

        candidate = next(
            (
                item
                for item in self._experiments.list_candidates(experiment_id)
                if item.candidate_index == selection.selected_candidate_index
            ),
            None,
        )
        candidate = self._require(candidate, "selected candidate", "CANDIDATE_NOT_FOUND")
        expected_candidate_id = candidate_id_for(
            experiment_id, candidate.candidate_index, candidate.parameter_set_hash
        )
        if any(
            (
                selection.selected_candidate_id != expected_candidate_id,
                selection.selected_parameter_set_hash != candidate.parameter_set_hash,
                selection.candidate_set_hash != candidate.candidate_set_hash,
            )
        ):
            self._fail("selected candidate identity is inconsistent", "CANDIDATE_IDENTITY_MISMATCH")

        protocol = self._require(
            self._protocols.get_protocol(experiment.protocol_id),
            "research protocol",
            "PROTOCOL_NOT_FOUND",
        )
        if protocol.status not in {
            ProtocolStatus.IS_EVALUATED,
            ProtocolStatus.SELECTION_RECORDED,
        }:
            self._fail("research protocol is not ready for selection", "PROTOCOL_INVALID_STATE")

        result = self._require(
            self._results.get(selection.selected_experiment_result_id),
            "selected experiment result",
            "RESULT_NOT_FOUND",
        )
        stored_result = self._require(
            self._results.get_by_candidate(experiment_id, selection.selected_candidate_id),
            "selected candidate result",
            "RESULT_NOT_FOUND",
        )
        if result != stored_result or result.result_hash != sha256_hash(result.hash_payload()):
            self._fail("selected experiment result is not canonical", "RESULT_HASH_MISMATCH")
        self._validate_result(experiment, selection, candidate, result)

        execution = self._require(
            self._executions.get_execution_by_candidate(
                experiment_id, selection.selected_candidate_index
            ),
            "candidate execution",
            "EXECUTION_NOT_FOUND",
        )
        if execution.status is not CandidateExecutionStatus.COMPLETED:
            self._fail("selected candidate execution is not completed", "EXECUTION_NOT_COMPLETED")
        if any(
            (
                execution.candidate_id != selection.selected_candidate_id,
                execution.parameter_set_hash != selection.selected_parameter_set_hash,
                execution.base_strategy_version_id != experiment.base_strategy_version_id,
                execution.base_strategy_version_hash != experiment.base_strategy_version_hash,
                execution.derived_strategy_version_id != result.derived_strategy_version_id,
                execution.derived_strategy_version_hash != result.derived_strategy_version_hash,
                execution.parameter_binding_hash != result.binding_hash,
            )
        ):
            self._fail(
                "candidate execution does not bind the selected result", "EXECUTION_MISMATCH"
            )

        records = self._backtests.get_records((result.backtest_run_id,))
        if len(records) != 1:
            self._fail("selected result backtest run was not found", "BACKTEST_RUN_NOT_FOUND")
        run = records[0].run
        if any(
            (
                run.backtest_run_id != result.backtest_run_id,
                run.strategy_version_id != result.derived_strategy_version_id,
                run.strategy_version_content_hash != result.derived_strategy_version_hash,
                run.backtest_result.start_date != experiment.is_start_date,
                run.backtest_result.end_date != experiment.is_end_date,
                run.performance_analysis.start_date != experiment.is_start_date,
                run.performance_analysis.end_date != experiment.is_end_date,
                run.performance_analysis.price_field_used is not result.price_field_used,
                not self._analysis_summary_matches(run, result),
            )
        ):
            self._fail(
                "selected result backtest evidence is inconsistent", "BACKTEST_EVIDENCE_MISMATCH"
            )

        derived = self._require(
            self._strategies.get_any_version(result.derived_strategy_version_id),
            "derived strategy version",
            "DERIVED_STRATEGY_NOT_FOUND",
        )
        self._validate_derived_strategy(experiment, selection, result, derived)

        candidate_set = self._require(
            self._protocols.get_candidate_set(
                self._protocol_candidate_set_id(protocol.protocol_id)
            ),
            "protocol candidate set",
            "CANDIDATE_SET_NOT_FOUND",
        )
        if candidate_set.status != CandidateSetStatus.LOCKED:
            self._fail("protocol candidate set is not locked", "CANDIDATE_SET_NOT_LOCKED")
        if (
            experiment.base_strategy_version_id not in candidate_set.strategy_version_ids
            or candidate_set.strategy_version_content_hashes.get(
                experiment.base_strategy_version_id
            )
            != experiment.base_strategy_version_hash
        ):
            self._fail(
                "protocol candidate set does not bind the experiment base strategy",
                "PROTOCOL_MISMATCH",
            )

        frozen_config = self._require(
            self._protocols.get_frozen_evaluation_config(protocol.protocol_id),
            "frozen evaluation configuration",
            "FROZEN_CONFIGURATION_NOT_FOUND",
        )
        try:
            run_config = ResearchEvaluationConfig.from_backtest_run(run)
        except ValueError as exc:
            raise ExperimentSelectionHandoffError(
                "selected backtest configuration is invalid", code="CONFIGURATION_MISMATCH"
            ) from exc
        if frozen_config.mismatch_fields(run_config):
            self._fail(
                "selected backtest differs from frozen protocol configuration",
                "CONFIGURATION_MISMATCH",
            )
        self._validate_provenance(experiment, result, run.provenance)

        metrics = {
            metric: self._json_safe(result.performance_summary[metric])
            for metric in protocol.allowed_metrics
            if metric in result.performance_summary
        }
        provenance = {
            "handoff": True,
            "selection_hash": selection.selection_hash,
            "experiment_id": experiment.experiment_id,
            "experiment_selection_id": selection.selection_id,
            "candidate_id": selection.selected_candidate_id,
            "candidate_index": selection.selected_candidate_index,
            "experiment_result_id": result.experiment_result_id,
            "result_hash": result.result_hash,
            "base_strategy_version_id": experiment.base_strategy_version_id,
            "base_strategy_version_hash": experiment.base_strategy_version_hash,
            "derived_strategy_version_id": derived.version_id,
            "derived_strategy_content_hash": derived.content_hash,
            "parameter_set_hash": selection.selected_parameter_set_hash,
            "parameter_space_hash": experiment.parameter_space_hash,
            "candidate_set_hash": selection.candidate_set_hash,
            "objective_hash": selection.objective_hash,
            "binding_hash": result.binding_hash,
            "backtest_run_id": run.backtest_run_id,
            "selection_method": selection.selection_method.value,
            "is_start_date": experiment.is_start_date.isoformat(),
            "is_end_date": experiment.is_end_date.isoformat(),
            "price_field_used": result.price_field_used.value,
            "engine_version": result.engine_version,
            "analysis_version": result.analysis_version,
            "data_snapshot_reference": dict(result.data_snapshot_reference),
            "configuration": frozen_config.to_dict(),
        }
        self._assert_safe_is_only(provenance)
        decision_id = f"protocol-selection-{selection.selection_hash[:32]}"
        decision = SelectionDecision(
            decision_id=decision_id,
            protocol_id=protocol.protocol_id,
            candidate_set_id=candidate_set.candidate_set_id,
            selected_strategy_version_id=derived.version_id,
            is_backtest_run_ids=(run.backtest_run_id,),
            selected_metrics=metrics,
            rationale=selection.researcher_rationale,
            created_at=selection.created_at,
            data_provenance=provenance,
            source="experiment_selection_handoff",
        )
        freeze = StrategyFreezeRecord(
            freeze_id=f"protocol-freeze-{selection.selection_hash[:32]}",
            protocol_id=protocol.protocol_id,
            strategy_version_id=derived.version_id,
            strategy_version_content_hash=derived.content_hash or "",
            selection_decision_id=decision.decision_id,
            frozen_at=selection.created_at,
            reason="IS experiment selection handoff; freeze before OOS observation",
        )
        return self._protocols.persist_selection_and_freeze_atomic(
            decision,
            freeze,
            candidate_set=candidate_set,
            version=derived,
            run=run,
            provenance=provenance,
        )

    def _validate_selection(self, experiment: Any, selection: Any) -> None:
        if selection.selection_hash != sha256_hash(selection.semantic_payload()):
            self._fail("experiment selection hash is invalid", "SELECTION_HASH_MISMATCH")
        if any(
            (
                selection.protocol_id != experiment.protocol_id,
                selection.objective_hash != experiment.objective_spec_hash,
                selection.parameter_space_hash != experiment.parameter_space_hash,
            )
        ):
            self._fail("experiment selection does not match experiment", "SELECTION_MISMATCH")

    def _validate_result(
        self, experiment: Any, selection: Any, candidate: Any, result: Any
    ) -> None:
        if any(
            (
                result.experiment_id != experiment.experiment_id,
                result.candidate_id != selection.selected_candidate_id,
                result.candidate_index != selection.selected_candidate_index,
                result.parameter_set_hash != selection.selected_parameter_set_hash,
                result.parameter_space_hash != experiment.parameter_space_hash,
                result.candidate_set_hash != selection.candidate_set_hash,
                result.base_strategy_version_id != experiment.base_strategy_version_id,
                result.base_strategy_version_hash != experiment.base_strategy_version_hash,
                result.experiment_result_id != selection.selected_experiment_result_id,
                result.result_hash != selection.selected_result_hash,
                result.is_start != experiment.is_start_date,
                result.is_end != experiment.is_end_date,
                result.price_field_used != experiment.provenance.price_field_used,
                result.backtest_configuration_hash
                != sha256_hash(experiment.backtest_configuration.snapshot({})),
                result.engine_version != experiment.engine_version,
                result.analysis_version != experiment.analysis_version,
                candidate.parameter_set_hash != result.parameter_set_hash,
            )
        ):
            self._fail("selected result does not match frozen experiment", "RESULT_MISMATCH")

    def _validate_derived_strategy(
        self, experiment: Any, selection: Any, result: Any, derived: Any
    ) -> None:
        provenance = derived.materialization_provenance
        if provenance is None or any(
            (
                derived.version_id != selection.selected_derived_strategy_version_id,
                derived.content_hash != selection.selected_derived_strategy_content_hash,
                derived.content_hash != result.derived_strategy_version_hash,
                provenance.base_strategy_version_id != experiment.base_strategy_version_id,
                provenance.base_strategy_version_hash != experiment.base_strategy_version_hash,
                provenance.parameter_set_hash != selection.selected_parameter_set_hash,
                provenance.binding_hash != result.binding_hash,
            )
        ):
            self._fail("derived strategy provenance is inconsistent", "DERIVED_STRATEGY_MISMATCH")
        expected_semantic_hash = derived_strategy_version_hash(
            derived.configuration,
            base_strategy_version_id=provenance.base_strategy_version_id,
            base_strategy_version_hash=provenance.base_strategy_version_hash,
            parameter_set_hash=provenance.parameter_set_hash,
            binding_hash=provenance.binding_hash,
            materialization_spec_hash=provenance.materialization_spec_hash,
        )
        if any(
            (
                provenance.derived_strategy_version_hash != expected_semantic_hash,
                derived.version_id
                != derived_strategy_version_id(
                    provenance.base_strategy_version_id, expected_semantic_hash
                ),
            )
        ):
            self._fail("derived strategy semantic identity is invalid", "DERIVED_STRATEGY_MISMATCH")

    def _validate_provenance(
        self, experiment: Any, result: Any, run_provenance: Mapping[str, Any]
    ) -> None:
        if experiment.provenance is None:
            self._fail("experiment provenance is unavailable", "PROVENANCE_MISMATCH")
        if canonical_json(result.data_snapshot_reference) != canonical_json(
            experiment.provenance.data_snapshot_reference
        ):
            self._fail(
                "result data provenance differs from frozen experiment", "PROVENANCE_MISMATCH"
            )
        self._assert_safe_is_only(result.data_snapshot_reference)
        self._assert_safe_is_only(run_provenance)

    @classmethod
    def _json_safe(cls, value: object) -> object:
        if isinstance(value, Mapping):
            return {str(key): cls._json_safe(child) for key, child in value.items()}
        if isinstance(value, (tuple, list)):
            return [cls._json_safe(child) for child in value]
        return value

    @staticmethod
    def _analysis_summary_matches(run: Any, result: Any) -> bool:
        """Compare analytics while ignoring duplicated identity metadata.

        Experiment results retain the analytical summary as provenance, while
        the persisted BacktestRun remains the source of truth for run and
        strategy identities.  Older result payloads may therefore contain
        null or omitted identity fields even when the analytics themselves are
        identical.
        """
        identity_fields = {"backtest_run_id", "strategy_id", "strategy_version_id"}
        expected = dict(run.performance_analysis.to_dict())
        actual = dict(result.performance_summary)
        for field in identity_fields:
            expected.pop(field, None)
            actual.pop(field, None)
        try:
            return canonical_json(expected) == canonical_json(actual)
        except (TypeError, ValueError):
            return False

    def _protocol_candidate_set_id(self, protocol_id: str) -> str:
        candidate_sets = self._protocols.list_candidate_sets(protocol_id)
        if len(candidate_sets) != 1:
            self._fail("protocol must have exactly one candidate set", "CANDIDATE_SET_NOT_FOUND")
        return candidate_sets[0].candidate_set_id

    @classmethod
    def _assert_safe_is_only(cls, value: object) -> None:
        forbidden = re.compile(r"(?:^|[_\-.\s])oos(?:$|[_\-.\s])", re.IGNORECASE)
        sensitive = re.compile(
            r"(?:api[_-]?key|access[_-]?token|authorization|credential|password|secret)",
            re.IGNORECASE,
        )

        def visit(item: object) -> None:
            if isinstance(item, Mapping):
                for key, child in item.items():
                    if not isinstance(key, str) or forbidden.search(key) or sensitive.search(key):
                        cls._fail(
                            "handoff provenance contains prohibited data", "PROVENANCE_UNSAFE"
                        )
                    visit(child)
            elif isinstance(item, (tuple, list)):
                for child in item:
                    visit(child)
            elif isinstance(item, float) and not math.isfinite(item):
                cls._fail("handoff provenance contains a non-finite number", "PROVENANCE_UNSAFE")
            elif isinstance(item, str) and (forbidden.search(item) or sensitive.search(item)):
                cls._fail("handoff provenance contains prohibited data", "PROVENANCE_UNSAFE")

        visit(value)
        try:
            canonical_json(value)
        except (TypeError, ValueError) as exc:
            raise ExperimentSelectionHandoffError(
                "handoff provenance is not canonical", code="PROVENANCE_UNSAFE"
            ) from exc

    @staticmethod
    def _require(value: Any, label: str, code: str) -> Any:
        if value is None:
            raise ExperimentSelectionHandoffError(f"{label} was not found", code=code)
        return value

    @staticmethod
    def _fail(message: str, code: str) -> None:
        raise ExperimentSelectionHandoffError(message, code=code)


__all__ = ["ExperimentSelectionHandoffError", "ExperimentSelectionHandoffService"]
