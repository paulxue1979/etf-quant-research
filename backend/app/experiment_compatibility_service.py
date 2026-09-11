"""Read-only compatibility diagnostics for PHASE 8E-1B."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from research.experiment_compatibility import (
    CompatibilityDiagnostic,
    CompatibilityMismatch,
    CompatibilityReasonCode,
    CompatibilityStatus,
)
from research.experiment_read_model import (
    ExperimentCandidateView,
    ExperimentResultsReadModel,
    ExperimentResultStatus,
)


class ExperimentCompatibilityService:
    """Compare frozen research conditions without ranking or recomputing results."""

    DIMENSIONS = (
        "experiment_id",
        "protocol_id",
        "is_start",
        "is_end",
        "price_field_used",
        "initial_capital",
        "commission",
        "slippage",
        "execution_rule",
        "fractional_shares",
        "rebalance_policy",
        "engine_version",
        "analysis_version",
        "backtest_configuration_hash",
        "data_snapshot_reference",
    )

    def diagnose(self, model: ExperimentResultsReadModel) -> CompatibilityDiagnostic:
        if not isinstance(model, ExperimentResultsReadModel):
            raise TypeError("model must be an ExperimentResultsReadModel")
        completed = sorted(
            (
                candidate
                for candidate in model.candidates
                if candidate.result_status is ExperimentResultStatus.COMPLETED
            ),
            key=lambda item: item.candidate_index,
        )
        unavailable = sorted(
            (
                candidate
                for candidate in model.candidates
                if candidate.result_status is not ExperimentResultStatus.COMPLETED
            ),
            key=lambda item: item.candidate_index,
        )
        binding_mismatches = [
            mismatch
            for candidate in model.candidates
            for mismatch in self._binding_mismatches(model, candidate)
        ]
        if not completed:
            mismatches = tuple(
                binding_mismatches
                + [self._unavailable(candidate) for candidate in unavailable]
            )
            return CompatibilityDiagnostic(
                status=(
                    CompatibilityStatus.INCOMPATIBLE
                    if binding_mismatches
                    else CompatibilityStatus.NOT_EVALUABLE
                ),
                experiment_id=model.experiment_id,
                protocol_id=model.protocol_id,
                reference_candidate_id=None,
                compared_candidate_ids=(),
                unavailable_candidate_ids=tuple(item.candidate_id for item in unavailable),
                checked_dimensions=self.DIMENSIONS,
                mismatches=mismatches,
            )

        reference = min(completed, key=lambda item: item.candidate_index)
        compared = completed[1:]
        mismatches: list[CompatibilityMismatch] = binding_mismatches + [
            self._unavailable(candidate) for candidate in unavailable
        ]
        if not reference.data_snapshot_reference:
            mismatches.append(self._missing_provenance(reference))
        for candidate in compared:
            mismatches.extend(self._compare(reference, candidate))
        semantic_mismatches = [
            item
            for item in mismatches
            if item.reason_code
            not in {
                CompatibilityReasonCode.RESULT_NOT_AVAILABLE,
                CompatibilityReasonCode.EXECUTION_FAILED,
            }
            and not self._missing_provenance_mismatch(item)
        ]
        provenance_incomplete = any(
            not candidate.data_snapshot_reference for candidate in completed
        )
        status = (
            CompatibilityStatus.INCOMPATIBLE
            if semantic_mismatches
            else (
                CompatibilityStatus.NOT_EVALUABLE
                if provenance_incomplete
                else CompatibilityStatus.COMPATIBLE
            )
        )
        return CompatibilityDiagnostic(
            status=status,
            experiment_id=model.experiment_id,
            protocol_id=model.protocol_id,
            reference_candidate_id=reference.candidate_id,
            compared_candidate_ids=tuple(item.candidate_id for item in compared),
            unavailable_candidate_ids=tuple(item.candidate_id for item in unavailable),
            checked_dimensions=self.DIMENSIONS,
            mismatches=tuple(mismatches),
        )

    @staticmethod
    def _binding_mismatches(
        model: ExperimentResultsReadModel, candidate: ExperimentCandidateView
    ) -> list[CompatibilityMismatch]:
        mismatches: list[CompatibilityMismatch] = []
        if candidate.experiment_id != model.experiment_id:
            mismatches.append(
                CompatibilityMismatch(
                    candidate_id=candidate.candidate_id,
                    dimension="experiment_id",
                    reason_code=CompatibilityReasonCode.EXPERIMENT_BINDING_MISMATCH,
                    reference_value=model.experiment_id,
                    candidate_value=candidate.experiment_id,
                    message="candidate is bound to a different experiment",
                )
            )
        if candidate.protocol_id != model.protocol_id:
            mismatches.append(
                CompatibilityMismatch(
                    candidate_id=candidate.candidate_id,
                    dimension="protocol_id",
                    reason_code=CompatibilityReasonCode.PROTOCOL_BINDING_MISMATCH,
                    reference_value=model.protocol_id,
                    candidate_value=candidate.protocol_id,
                    message="candidate is bound to a different protocol",
                )
            )
        return mismatches

    @staticmethod
    def _missing_provenance_mismatch(item: CompatibilityMismatch) -> bool:
        return (
            item.reason_code is CompatibilityReasonCode.DATA_PROVENANCE_MISMATCH
            and item.candidate_value == "missing"
        )

    def _compare(
        self, reference: ExperimentCandidateView, candidate: ExperimentCandidateView
    ) -> list[CompatibilityMismatch]:
        reference_values = self._values(reference)
        candidate_values = self._values(candidate)
        if (
            reference.backtest_configuration_hash
            == candidate.backtest_configuration_hash
            and reference_values["configuration_semantics"]
            != candidate_values["configuration_semantics"]
        ):
            return [
                CompatibilityMismatch(
                    candidate_id=candidate.candidate_id,
                    dimension="backtest_configuration",
                    reason_code=CompatibilityReasonCode.INTEGRITY_MISMATCH,
                    reference_value=reference_values["configuration_semantics"],
                    candidate_value=candidate_values["configuration_semantics"],
                    message="configuration hash matches but semantic fields differ",
                )
            ]
        mismatches: list[CompatibilityMismatch] = []
        for dimension in self.DIMENSIONS:
            if dimension == "backtest_configuration_hash":
                continue
            reference_value = reference_values[dimension]
            candidate_value = candidate_values[dimension]
            if (
                dimension == "data_snapshot_reference"
                and (not reference_value or not candidate_value)
            ):
                continue
            if reference_value == candidate_value:
                continue
            reason_code = self._reason_code(dimension)
            mismatches.append(
                CompatibilityMismatch(
                    candidate_id=candidate.candidate_id,
                    dimension=dimension,
                    reason_code=reason_code,
                    reference_value=reference_value,
                    candidate_value=candidate_value,
                    message=f"{dimension} differs from canonical reference",
                )
            )
        if not candidate.data_snapshot_reference:
            mismatches.append(self._missing_provenance(candidate, reference_values))
        if reference.backtest_configuration_hash != candidate.backtest_configuration_hash:
            mismatches.append(
                CompatibilityMismatch(
                    candidate_id=candidate.candidate_id,
                    dimension="backtest_configuration_hash",
                    reason_code=CompatibilityReasonCode.BACKTEST_CONFIGURATION_MISMATCH,
                    reference_value=reference.backtest_configuration_hash,
                    candidate_value=candidate.backtest_configuration_hash,
                    message="backtest configuration hash differs from canonical reference",
                )
            )
        return mismatches

    @staticmethod
    def _values(candidate: ExperimentCandidateView) -> dict[str, Any]:
        snapshot = dict(candidate.configuration_snapshot)
        commission = snapshot.get("commission", {})
        rebalance = snapshot.get("rebalance_policy", {})
        if not isinstance(commission, Mapping):
            commission = {}
        if not isinstance(rebalance, Mapping):
            rebalance = {}
        semantics = {
            "is_start": snapshot.get("start_date"),
            "is_end": snapshot.get("end_date"),
            "price_field_used": snapshot.get("price_field_used"),
            "initial_capital": snapshot.get("initial_capital"),
            "commission": {
                "rate": commission.get("rate"),
                "per_order": commission.get("per_order"),
            },
            "slippage": snapshot.get("slippage"),
            "execution_rule": snapshot.get("execution_rule"),
            "fractional_shares": snapshot.get("fractional_shares"),
            "rebalance_policy": {
                "frequency": rebalance.get("frequency"),
                "threshold": rebalance.get("threshold"),
            },
        }
        return {
            "experiment_id": candidate.experiment_id,
            "protocol_id": candidate.protocol_id,
            "is_start": candidate.is_start,
            "is_end": candidate.is_end,
            "price_field_used": (
                candidate.price_field_used.value if candidate.price_field_used is not None else None
            ),
            **semantics,
            "engine_version": candidate.engine_version,
            "analysis_version": candidate.analysis_version,
            "backtest_configuration_hash": candidate.backtest_configuration_hash,
            "data_snapshot_reference": dict(candidate.data_snapshot_reference),
            "data_provenance_status": (
                "present" if candidate.data_snapshot_reference else "missing"
            ),
            "configuration_semantics": semantics,
        }

    @staticmethod
    def _reason_code(dimension: str) -> CompatibilityReasonCode:
        return {
            "experiment_id": CompatibilityReasonCode.EXPERIMENT_BINDING_MISMATCH,
            "protocol_id": CompatibilityReasonCode.PROTOCOL_BINDING_MISMATCH,
            "is_start": CompatibilityReasonCode.IS_RANGE_MISMATCH,
            "is_end": CompatibilityReasonCode.IS_RANGE_MISMATCH,
            "price_field_used": CompatibilityReasonCode.PRICE_FIELD_MISMATCH,
            "initial_capital": CompatibilityReasonCode.INITIAL_CAPITAL_MISMATCH,
            "commission": CompatibilityReasonCode.COMMISSION_MISMATCH,
            "slippage": CompatibilityReasonCode.SLIPPAGE_MISMATCH,
            "execution_rule": CompatibilityReasonCode.EXECUTION_RULE_MISMATCH,
            "fractional_shares": CompatibilityReasonCode.FRACTIONAL_SHARES_MISMATCH,
            "rebalance_policy": CompatibilityReasonCode.REBALANCE_POLICY_MISMATCH,
            "engine_version": CompatibilityReasonCode.ENGINE_VERSION_MISMATCH,
            "analysis_version": CompatibilityReasonCode.ANALYTICS_VERSION_MISMATCH,
            "data_snapshot_reference": CompatibilityReasonCode.DATA_PROVENANCE_MISMATCH,
        }[dimension]

    @staticmethod
    def _unavailable(candidate: ExperimentCandidateView) -> CompatibilityMismatch:
        reason = (
            CompatibilityReasonCode.EXECUTION_FAILED
            if candidate.result_status is ExperimentResultStatus.FAILED
            else CompatibilityReasonCode.RESULT_NOT_AVAILABLE
        )
        return CompatibilityMismatch(
            candidate_id=candidate.candidate_id,
            dimension="result",
            reason_code=reason,
            reference_value=None,
            candidate_value=candidate.result_status.value,
            message="candidate has no comparable completed official result",
        )

    @staticmethod
    def _missing_provenance(
        candidate: ExperimentCandidateView,
        reference_values: dict[str, Any] | None = None,
    ) -> CompatibilityMismatch:
        return CompatibilityMismatch(
            candidate_id=candidate.candidate_id,
            dimension="data_snapshot_reference",
            reason_code=CompatibilityReasonCode.DATA_PROVENANCE_MISMATCH,
            reference_value=(
                reference_values["data_provenance_status"]
                if reference_values is not None
                else "present"
            ),
            candidate_value="missing",
            message="data provenance is insufficient for a fair comparison",
        )


ExperimentResultCompatibilityService = ExperimentCompatibilityService


__all__ = ["ExperimentCompatibilityService", "ExperimentResultCompatibilityService"]
