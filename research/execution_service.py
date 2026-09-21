"""PHASE 8D-3 IS-only orchestration for one frozen experiment candidate."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from typing import Any

from analytics.performance import analyze_backtest
from backend.app.candidate_execution_repository import CandidateExecutionRepository
from backend.app.experiment_repository import ExperimentCandidateRecord, ExperimentRepository
from backend.app.research_protocol import (
    ProtocolStatus,
    ResearchEvaluationConfig,
    ResearchProtocolRepository,
)
from backend.app.strategy_repository import StrategyRepository
from backtest.integration import StrategyBacktestResult, run_strategy_backtest
from backtest.models import BacktestConfig
from data.exceptions import (
    DataEngineError,
    TiingoNetworkError,
    TiingoRateLimitError,
    TiingoServiceError,
    TiingoTimeoutError,
)
from data.models import HistoricalDataRequest, HistoricalDataSet, PriceField
from indicators import exponential_moving_average, moving_average
from indicators.models import IndicatorKind, IndicatorSeries
from research.canonical import sha256_hash
from research.enums import ExperimentStatus
from research.execution import CandidateExecution, CandidateExecutionStatus, candidate_id_for
from research.execution_outcome import (
    ExperimentExecutionOutcome,
    ExperimentExecutionOutcomeStatus,
)
from research.experiments import Experiment, ParameterSet
from research.materialization import (
    ParameterBinding,
    ParameterBindingSet,
    materialize_strategy_version,
)
from strategies.enums import StrategyEvaluationStatus
from strategies.evaluation import EvaluationContext
from strategies.strategy_evaluation import evaluate_strategy, required_indicators

ENGINE_SERVICE_VERSION = "phase-8d-3-v1"
_EXECUTABLE_EXPERIMENT_STATES = {
    ExperimentStatus.SPACE_FROZEN,
    ExperimentStatus.CANDIDATES_GENERATED,
    ExperimentStatus.RUNNING,
}
_DEFAULT_LEASE_DURATION = timedelta(minutes=5)


class ExperimentExecutionService:
    """Execute exactly one frozen candidate without persisting a result record.

    The repository tracks only the candidate lease lifecycle.  Backtest and
    analytics products remain in the returned immutable outcome for a later
    phase to persist as ExperimentResult records.
    """

    def __init__(
        self,
        *,
        experiment_repository: ExperimentRepository,
        protocol_repository: ResearchProtocolRepository,
        strategy_repository: StrategyRepository,
        candidate_execution_repository: CandidateExecutionRepository,
        data_service: Any,
        clock: Callable[[], datetime] | None = None,
        lease_duration: timedelta = _DEFAULT_LEASE_DURATION,
    ) -> None:
        if lease_duration <= timedelta(0):
            raise ValueError("lease_duration must be positive")
        self._experiments = experiment_repository
        self._protocols = protocol_repository
        self._strategies = strategy_repository
        self._executions = candidate_execution_repository
        self._data = data_service
        self._clock = clock or (lambda: datetime.now(UTC))
        self._lease_duration = lease_duration

    def execute(
        self,
        *,
        experiment_id: str,
        candidate_index: int,
        parameter_bindings: ParameterBindingSet | Sequence[ParameterBinding],
        claimed_by: str = "phase-8d-3-worker",
    ) -> ExperimentExecutionOutcome:
        """Run one candidate within immutable Protocol IS boundaries only."""
        started_at = self._now()
        try:
            prepared = self._prepare(
                experiment_id,
                candidate_index,
                parameter_bindings,
                claimed_by=claimed_by,
                started_at=started_at,
            )
        except _ExecutionFailure as failure:
            return failure.outcome
        except Exception:
            return self._preflight_failure(
                experiment_id=experiment_id,
                candidate_index=candidate_index,
                code="INVALID_EXPERIMENT",
                prepared=None,
            )

        try:
            self._executions.recover_expired_executions(started_at)
            execution = self._executions.create_execution(prepared.execution)
            if execution.status is CandidateExecutionStatus.FAILED:
                execution = self._executions.retry_failed(
                    execution.execution_id, actor=claimed_by, now=started_at
                )
            if execution.status is CandidateExecutionStatus.COMPLETED:
                return self._outcome(
                    prepared,
                    status=ExperimentExecutionOutcomeStatus.FAILED,
                    execution_id=execution.execution_id,
                    failure=_RuntimeFailure("CANDIDATE_EXECUTION_CONFLICT", retryable=False),
                )
            claimed = self._executions.claim_candidate(
                prepared.experiment.experiment_id,
                prepared.candidate.candidate_index,
                claimed_by,
                started_at,
                started_at + self._lease_duration,
            )
        except Exception:
            return self._preflight_failure(
                experiment_id=prepared.experiment.experiment_id,
                candidate_index=prepared.candidate.candidate_index,
                code="CANDIDATE_EXECUTION_CONFLICT",
                prepared=prepared,
            )

        try:
            data = self._load_is_data(prepared)
            indicators = self._calculate_indicators(prepared.derived_strategy, data)
            context = EvaluationContext.from_components(data, indicators)
            timeline = evaluate_strategy(
                prepared.derived_strategy,
                context,
                prepared.experiment.is_start_date,
                prepared.experiment.is_end_date,
                source_data_reference=prepared.source_data_reference,
            )
            if not any(
                item.status is StrategyEvaluationStatus.EVALUATED for item in timeline.evaluations
            ):
                raise _RuntimeFailure("INDICATOR_NOT_EVALUABLE", retryable=False)
            if any(item.status is StrategyEvaluationStatus.ERROR for item in timeline.evaluations):
                raise _RuntimeFailure("STRATEGY_EVALUATION_ERROR", retryable=False)
            integration = run_strategy_backtest(
                prepared.derived_strategy,
                timeline,
                data,
                prepared.backtest_config,
            )
            self._assert_is_only_backtest(integration, prepared.experiment)
        except _RuntimeFailure as failure:
            return self._runtime_failure(prepared, claimed, claimed_by, failure)
        except DataEngineError as exc:
            retryable = isinstance(
                exc,
                (TiingoNetworkError, TiingoRateLimitError, TiingoServiceError, TiingoTimeoutError),
            )
            return self._runtime_failure(
                prepared,
                claimed,
                claimed_by,
                _RuntimeFailure("MISSING_MARKET_DATA", retryable=retryable),
            )
        except Exception:
            return self._runtime_failure(
                prepared,
                claimed,
                claimed_by,
                _RuntimeFailure("BACKTEST_ERROR", retryable=False),
            )

        try:
            analysis = analyze_backtest(integration.backtest_result)
            self._assert_is_only_analysis(analysis, prepared.experiment)
        except _RuntimeFailure as failure:
            return self._runtime_failure(prepared, claimed, claimed_by, failure)
        except Exception:
            return self._runtime_failure(
                prepared,
                claimed,
                claimed_by,
                _RuntimeFailure("ANALYTICS_ERROR", retryable=False),
            )

        completed_at = self._now()
        try:
            completed = self._executions.mark_completed(
                claimed.execution_id, claimed_by, completed_at, completed_at
            )
        except Exception:
            return self._runtime_failure(
                prepared,
                claimed,
                claimed_by,
                _RuntimeFailure("CANDIDATE_EXECUTION_PERSISTENCE_ERROR", retryable=True),
            )
        return self._outcome(
            prepared,
            status=ExperimentExecutionOutcomeStatus.COMPLETED,
            execution_id=completed.execution_id,
            integration=integration,
            analysis=analysis,
        )

    def _prepare(
        self,
        experiment_id: str,
        candidate_index: int,
        parameter_bindings: ParameterBindingSet | Sequence[ParameterBinding],
        *,
        claimed_by: str,
        started_at: datetime,
    ) -> _PreparedExecution:
        if not isinstance(experiment_id, str) or not experiment_id.strip():
            raise _ExecutionFailure(self._preflight_failure("unknown", 0, "INVALID_EXPERIMENT"))
        if (
            isinstance(candidate_index, bool)
            or not isinstance(candidate_index, int)
            or candidate_index < 0
        ):
            raise _ExecutionFailure(
                self._preflight_failure(experiment_id, 0, "INVALID_PARAMETER_SET")
            )
        experiment = self._experiments.get(experiment_id)
        if experiment is None or experiment.status not in _EXECUTABLE_EXPERIMENT_STATES:
            raise _ExecutionFailure(
                self._preflight_failure(experiment_id, candidate_index, "INVALID_EXPERIMENT")
            )
        protocol = self._protocols.get_protocol(experiment.protocol_id)
        if protocol is None or protocol.status != ProtocolStatus.FROZEN:
            raise _ExecutionFailure(
                self._preflight_failure(experiment_id, candidate_index, "INVALID_EXPERIMENT")
            )
        self._validate_is_bounds(experiment, protocol)
        self._validate_frozen_config(experiment)

        candidate_set = self._experiments.get_candidate_set(experiment_id)
        candidates = self._experiments.list_candidates(experiment_id)
        if candidate_set is None or candidate_index >= len(candidates):
            raise _ExecutionFailure(
                self._preflight_failure(experiment_id, candidate_index, "INVALID_PARAMETER_SET")
            )
        candidate = candidates[candidate_index]
        parameter_set = candidate_set.candidates[candidate_index]
        if (
            candidate.parameter_set_hash != parameter_set.content_hash
            or candidate.candidate_set_hash != candidate_set.candidate_set_hash
            or parameter_set.parameter_space != experiment.parameter_space
        ):
            raise _ExecutionFailure(
                self._preflight_failure(experiment_id, candidate_index, "INVALID_PARAMETER_SET")
            )
        base = self._strategies.get(
            experiment.strategy_definition_id, experiment.base_strategy_version_id
        )
        if base is None or base.content_hash != experiment.base_strategy_version_hash:
            raise _ExecutionFailure(
                self._preflight_failure(experiment_id, candidate_index, "INVALID_EXPERIMENT")
            )
        try:
            bindings = (
                parameter_bindings
                if isinstance(parameter_bindings, ParameterBindingSet)
                else ParameterBindingSet(parameter_bindings)
            )
        except Exception:
            raise _ExecutionFailure(
                self._preflight_failure(experiment_id, candidate_index, "INVALID_BINDING")
            ) from None
        try:
            derived = materialize_strategy_version(base, parameter_set, bindings)
            # The result and any subsequent PHASE 7 freeze must reference this
            # exact immutable object, not a later equivalent re-materialization.
            derived = self._strategies.persist_exact_strategy_version(derived)
        except Exception:
            raise _ExecutionFailure(
                self._persist_materialization_failure(
                    experiment=experiment,
                    candidate=candidate,
                    parameter_set=parameter_set,
                    bindings=bindings,
                    base_strategy_version=base,
                    claimed_by=claimed_by,
                    started_at=started_at,
                )
            ) from None
        if (
            derived.configuration.price_field
            is not experiment.backtest_configuration.price_field_used
        ):
            raise _ExecutionFailure(
                self._preflight_failure(
                    experiment_id,
                    candidate_index,
                    "PRICE_FIELD_MISMATCH",
                    experiment,
                    candidate,
                    parameter_set,
                    bindings,
                    derived,
                )
            )
        if derived.materialization_provenance is None:
            raise _ExecutionFailure(
                self._preflight_failure(
                    experiment_id,
                    candidate_index,
                    "INVALID_MATERIALIZED_STRATEGY",
                    experiment,
                    candidate,
                    parameter_set,
                    bindings,
                )
            )
        config = replace(
            experiment.backtest_configuration,
            strategy_version_id=derived.version_id,
        )
        if (
            config.start_date != experiment.is_start_date
            or config.end_date != experiment.is_end_date
        ):
            raise _ExecutionFailure(
                self._preflight_failure(
                    experiment_id,
                    candidate_index,
                    "IS_RANGE_VIOLATION",
                    experiment,
                    candidate,
                    parameter_set,
                    bindings,
                    derived,
                )
            )
        warmup_start = _warmup_start(experiment.is_start_date, derived)
        execution = CandidateExecution.pending(
            experiment_id=experiment.experiment_id,
            experiment_hash=experiment.content_hash,
            candidate_id=candidate_id_for(
                experiment.experiment_id, candidate.candidate_index, parameter_set.content_hash
            ),
            candidate_index=candidate.candidate_index,
            parameter_set_hash=parameter_set.content_hash,
            parameter_binding_hash=bindings.binding_hash,
            base_strategy_version_id=base.version_id,
            base_strategy_version_hash=base.content_hash or "",
            derived_strategy_version_id=derived.version_id,
            # This is the exact persisted StrategyVersion content hash.  The
            # separate deterministic materialization identity remains in the
            # version's immutable materialization provenance.
            derived_strategy_version_hash=derived.content_hash,
            created_at=self._now(),
        )
        source = (
            f"experiment:{experiment.experiment_id}:candidate:{candidate.candidate_index}:"
            f"is:{experiment.is_start_date.isoformat()}:{experiment.is_end_date.isoformat()}"
        )
        return _PreparedExecution(
            experiment=experiment,
            candidate=candidate,
            parameter_set=parameter_set,
            candidate_set_hash=candidate_set.candidate_set_hash,
            bindings=bindings,
            derived_strategy=derived,
            execution=execution,
            backtest_config=config,
            warmup_start=warmup_start,
            source_data_reference=source,
        )

    def _load_is_data(self, prepared: _PreparedExecution) -> dict[str, HistoricalDataSet]:
        strategy = prepared.derived_strategy.configuration
        data: dict[str, HistoricalDataSet] = {}
        for asset in strategy.assets:
            request = HistoricalDataRequest(
                symbol=asset.symbol,
                start_date=prepared.warmup_start,
                end_date=prepared.experiment.is_end_date,
                price_field_used=prepared.backtest_config.price_field_used,
            )
            if request.end_date > prepared.experiment.is_end_date:
                raise _RuntimeFailure("OOS_DATA_REQUEST", retryable=False)
            try:
                dataset = self._data.get_history(request)
            except DataEngineError:
                raise
            except (KeyError, LookupError, TypeError, ValueError) as exc:
                raise _RuntimeFailure("MISSING_MARKET_DATA", retryable=False) from exc
            self._validate_dataset_boundary(dataset, request, prepared.experiment)
            data[asset.symbol] = dataset
        if set(data) != {asset.symbol for asset in strategy.assets}:
            raise _RuntimeFailure("MISSING_MARKET_DATA", retryable=False)
        common_is_dates = set.intersection(
            *(
                {
                    point.date
                    for point in dataset.points
                    if prepared.experiment.is_start_date
                    <= point.date
                    <= prepared.experiment.is_end_date
                }
                for dataset in data.values()
            )
        )
        if not common_is_dates:
            raise _RuntimeFailure("MISSING_MARKET_DATA", retryable=False)
        return data

    def _validate_dataset_boundary(
        self,
        dataset: object,
        request: HistoricalDataRequest,
        experiment: Experiment,
    ) -> None:
        if not isinstance(dataset, HistoricalDataSet):
            raise _RuntimeFailure("MISSING_MARKET_DATA", retryable=False)
        if dataset.price_field_used is not request.price_field_used:
            raise _RuntimeFailure("PRICE_FIELD_MISMATCH", retryable=False)
        if dataset.request != request:
            raise _RuntimeFailure("MISSING_MARKET_DATA", retryable=False)
        if not dataset.points or not any(
            experiment.is_start_date <= point.date <= experiment.is_end_date
            for point in dataset.points
        ):
            raise _RuntimeFailure("MISSING_MARKET_DATA", retryable=False)
        if any(point.date > experiment.is_end_date for point in dataset.points):
            raise _RuntimeFailure("OOS_DATA_REQUEST", retryable=False)
        if any(point.date < request.start_date for point in dataset.points):
            raise _RuntimeFailure("MISSING_MARKET_DATA", retryable=False)

    def _calculate_indicators(
        self, strategy_version: Any, data: Mapping[str, HistoricalDataSet]
    ) -> tuple[tuple[str, IndicatorSeries], ...]:
        series: list[tuple[str, IndicatorSeries]] = []
        for item in required_indicators(strategy_version):
            if item.timeframe.value != "daily":
                raise _RuntimeFailure("UNSUPPORTED_TIMEFRAME", retryable=False)
            dataset = data.get(item.symbol)
            if dataset is None:
                raise _RuntimeFailure("MISSING_MARKET_DATA", retryable=False)
            if item.price_field is not dataset.price_field_used:
                raise _RuntimeFailure("PRICE_FIELD_MISMATCH", retryable=False)
            if item.kind is IndicatorKind.MOVING_AVERAGE:
                value = moving_average(dataset, period=item.period, price_field=item.price_field)
            elif item.kind is IndicatorKind.EXPONENTIAL_MOVING_AVERAGE:
                value = exponential_moving_average(
                    dataset, period=item.period, price_field=item.price_field
                )
            else:
                raise _RuntimeFailure("INDICATOR_NOT_EVALUABLE", retryable=False)
            series.append((item.symbol, value))
        return tuple(series)

    def _validate_is_bounds(self, experiment: Experiment, protocol: Any) -> None:
        if (
            experiment.is_start_date != protocol.is_start_date
            or experiment.is_end_date != protocol.is_end_date
            or experiment.is_start_date < protocol.is_start_date
            or experiment.is_end_date > protocol.is_end_date
        ):
            raise _ExecutionFailure(
                self._preflight_failure(
                    experiment.experiment_id, 0, "IS_RANGE_VIOLATION", experiment
                )
            )

    def _validate_frozen_config(self, experiment: Experiment) -> None:
        frozen = self._protocols.get_frozen_evaluation_config(experiment.protocol_id)
        if frozen is None:
            raise _ExecutionFailure(
                self._preflight_failure(
                    experiment.experiment_id,
                    0,
                    "INVALID_EXPERIMENT",
                    experiment,
                )
            )
        actual = ResearchEvaluationConfig(
            price_field_used=experiment.backtest_configuration.price_field_used.value,
            initial_capital=experiment.backtest_configuration.initial_capital,
            commission={
                "rate": experiment.backtest_configuration.commission.rate,
                "per_order": experiment.backtest_configuration.commission.per_order,
            },
            slippage=experiment.backtest_configuration.slippage,
            execution_rule=experiment.backtest_configuration.execution_rule.value,
            fractional_shares=experiment.backtest_configuration.fractional_shares,
            rebalance_policy={
                "frequency": experiment.backtest_configuration.rebalance_policy.frequency.value,
                "threshold": experiment.backtest_configuration.rebalance_policy.threshold,
            },
            engine_version=experiment.engine_version,
            contribution_schedule=(
                experiment.backtest_configuration.contribution_schedule.to_dict()
                if experiment.backtest_configuration.contribution_schedule is not None
                else None
            ),
        )
        if frozen.mismatch_fields(actual):
            raise _ExecutionFailure(
                self._preflight_failure(
                    experiment.experiment_id,
                    0,
                    "INVALID_EXPERIMENT",
                    experiment,
                )
            )

    def _assert_is_only_backtest(
        self, integration: StrategyBacktestResult, experiment: Experiment
    ) -> None:
        result = integration.backtest_result
        if (
            result.start_date != experiment.is_start_date
            or result.end_date != experiment.is_end_date
        ):
            raise _RuntimeFailure("IS_RANGE_VIOLATION", retryable=False)
        if any(order.date > experiment.is_end_date for order in result.orders):
            raise _RuntimeFailure("IS_RANGE_VIOLATION", retryable=False)
        if any(point.date > experiment.is_end_date for point in result.equity_curve):
            raise _RuntimeFailure("IS_RANGE_VIOLATION", retryable=False)

    def _assert_is_only_analysis(self, analysis: Any, experiment: Experiment) -> None:
        if (
            analysis.start_date != experiment.is_start_date
            or analysis.end_date != experiment.is_end_date
        ):
            raise _RuntimeFailure("IS_RANGE_VIOLATION", retryable=False)

    def _runtime_failure(
        self,
        prepared: _PreparedExecution,
        claimed: CandidateExecution,
        claimed_by: str,
        failure: _RuntimeFailure,
    ) -> ExperimentExecutionOutcome:
        now = self._now()
        try:
            failed = self._executions.mark_failed(
                claimed.execution_id,
                claimed_by,
                now,
                now,
                failure.code,
                _failure_message(failure.code),
                failure.retryable,
            )
            execution_id = failed.execution_id
        except Exception:
            execution_id = claimed.execution_id
            failure = _RuntimeFailure("CANDIDATE_EXECUTION_PERSISTENCE_ERROR", retryable=True)
        return self._outcome(
            prepared,
            status=ExperimentExecutionOutcomeStatus.FAILED,
            execution_id=execution_id,
            failure=failure,
        )

    def _persist_materialization_failure(
        self,
        *,
        experiment: Experiment,
        candidate: ExperimentCandidateRecord,
        parameter_set: ParameterSet,
        bindings: ParameterBindingSet,
        base_strategy_version: Any,
        claimed_by: str,
        started_at: datetime,
    ) -> ExperimentExecutionOutcome:
        """Record a known, permanent preflight failure through the 8D-2 lifecycle.

        At this point candidate, parameter-set, base-version, and binding hashes
        have all passed verification. The repository can therefore safely retain
        the immutable failed execution identity without creating an ExperimentResult.
        """
        execution = CandidateExecution.pending(
            experiment_id=experiment.experiment_id,
            experiment_hash=experiment.content_hash,
            candidate_id=candidate_id_for(
                experiment.experiment_id,
                candidate.candidate_index,
                parameter_set.content_hash,
            ),
            candidate_index=candidate.candidate_index,
            parameter_set_hash=parameter_set.content_hash,
            parameter_binding_hash=bindings.binding_hash,
            base_strategy_version_id=base_strategy_version.version_id,
            base_strategy_version_hash=base_strategy_version.content_hash or "",
            created_at=started_at,
        )
        try:
            stored = self._executions.create_execution(execution)
            if stored.status is CandidateExecutionStatus.PENDING:
                claimed = self._executions.claim_candidate(
                    experiment.experiment_id,
                    candidate.candidate_index,
                    claimed_by,
                    started_at,
                    started_at + self._lease_duration,
                )
                stored = self._executions.mark_failed(
                    claimed.execution_id,
                    claimed_by,
                    started_at,
                    started_at,
                    "INVALID_MATERIALIZED_STRATEGY",
                    _failure_message("INVALID_MATERIALIZED_STRATEGY"),
                    False,
                )
            if stored.status is CandidateExecutionStatus.FAILED:
                return self._preflight_failure(
                    experiment.experiment_id,
                    candidate.candidate_index,
                    "INVALID_MATERIALIZED_STRATEGY",
                    experiment,
                    candidate,
                    parameter_set,
                    bindings,
                    candidate_execution_id=stored.execution_id,
                    scope="preflight_lifecycle",
                )
            return self._preflight_failure(
                experiment.experiment_id,
                candidate.candidate_index,
                "CANDIDATE_EXECUTION_CONFLICT",
                experiment,
                candidate,
                parameter_set,
                bindings,
                candidate_execution_id=stored.execution_id,
                scope="preflight_lifecycle",
            )
        except Exception:
            return self._preflight_failure(
                experiment.experiment_id,
                candidate.candidate_index,
                "CANDIDATE_EXECUTION_PERSISTENCE_ERROR",
                experiment,
                candidate,
                parameter_set,
                bindings,
                scope="preflight_lifecycle",
            )

    def _preflight_failure(
        self,
        experiment_id: str,
        candidate_index: int,
        code: str,
        experiment: Experiment | None = None,
        candidate: ExperimentCandidateRecord | None = None,
        parameter_set: ParameterSet | None = None,
        bindings: ParameterBindingSet | None = None,
        derived: Any | None = None,
        candidate_execution_id: str | None = None,
        scope: str = "preflight",
    ) -> ExperimentExecutionOutcome:
        # Preflight has no materialized identity safe enough to persist as a lease.
        # It is intentionally returned without creating an ExperimentResult or record.
        base_id = experiment.base_strategy_version_id if experiment else "unavailable"
        base_hash = experiment.base_strategy_version_hash if experiment else "0" * 64
        parameter_space_hash = experiment.parameter_space_hash if experiment else "0" * 64
        return ExperimentExecutionOutcome(
            experiment_id=experiment.experiment_id if experiment else experiment_id or "unknown",
            candidate_id=(
                candidate_id_for(
                    experiment.experiment_id,
                    candidate.candidate_index,
                    parameter_set.content_hash,
                )
                if experiment and candidate and parameter_set
                else "0" * 64
            ),
            candidate_index=candidate.candidate_index if candidate else max(candidate_index, 0),
            parameter_set_hash=parameter_set.content_hash if parameter_set else "0" * 64,
            parameter_space_hash=parameter_space_hash,
            candidate_set_hash=candidate.candidate_set_hash if candidate else "0" * 64,
            base_strategy_version_id=base_id,
            base_strategy_version_hash=base_hash,
            derived_strategy_version_id=derived.version_id if derived else None,
            derived_strategy_version_hash=derived.content_hash if derived else None,
            binding_hash=bindings.binding_hash if bindings else "0" * 64,
            warmup_start=None,
            warmup_end=None,
            is_start=experiment.is_start_date if experiment else date.min,
            is_end=experiment.is_end_date if experiment else date.min,
            price_field_used=(
                experiment.backtest_configuration.price_field_used
                if experiment
                else PriceField.ADJUSTED_CLOSE
            ),
            backtest_configuration_hash=(
                sha256_hash(experiment.backtest_configuration.snapshot({}))
                if experiment
                else "0" * 64
            ),
            engine_version=experiment.engine_version if experiment else ENGINE_SERVICE_VERSION,
            analysis_version=experiment.analysis_version if experiment else "unavailable",
            status=ExperimentExecutionOutcomeStatus.FAILED,
            candidate_execution_id=candidate_execution_id,
            failure_code=code,
            failure_message=_failure_message(code),
            provenance={"service_version": ENGINE_SERVICE_VERSION, "scope": scope},
        )

    def _outcome(
        self,
        prepared: _PreparedExecution,
        *,
        status: ExperimentExecutionOutcomeStatus,
        execution_id: str,
        integration: StrategyBacktestResult | None = None,
        analysis: Any | None = None,
        failure: _RuntimeFailure | None = None,
    ) -> ExperimentExecutionOutcome:
        experiment = prepared.experiment
        warmup_end = experiment.is_start_date - timedelta(days=1)
        return ExperimentExecutionOutcome(
            experiment_id=experiment.experiment_id,
            candidate_id=prepared.execution.candidate_id,
            candidate_index=prepared.candidate.candidate_index,
            parameter_set_hash=prepared.parameter_set.content_hash,
            parameter_space_hash=experiment.parameter_space_hash,
            candidate_set_hash=prepared.candidate_set_hash,
            base_strategy_version_id=experiment.base_strategy_version_id,
            base_strategy_version_hash=experiment.base_strategy_version_hash,
            derived_strategy_version_id=prepared.derived_strategy.version_id,
            derived_strategy_version_hash=prepared.derived_strategy.content_hash,
            binding_hash=prepared.bindings.binding_hash,
            warmup_start=prepared.warmup_start,
            warmup_end=warmup_end,
            is_start=experiment.is_start_date,
            is_end=experiment.is_end_date,
            price_field_used=prepared.backtest_config.price_field_used,
            backtest_configuration_hash=sha256_hash(experiment.backtest_configuration.snapshot({})),
            engine_version=experiment.engine_version,
            analysis_version=experiment.analysis_version,
            status=status,
            candidate_execution_id=execution_id,
            backtest_result=integration,
            performance_analysis_result=analysis,
            failure_code=failure.code if failure else None,
            failure_message=_failure_message(failure.code) if failure else None,
            provenance={
                "service_version": ENGINE_SERVICE_VERSION,
                "data_snapshot_reference": (
                    dict(experiment.provenance.data_snapshot_reference)
                    if experiment.provenance is not None
                    else {}
                ),
                "source_data_reference": prepared.source_data_reference,
                "is_evaluation_range": {
                    "start_date": experiment.is_start_date.isoformat(),
                    "end_date": experiment.is_end_date.isoformat(),
                },
                "warmup_range": {
                    "start_date": prepared.warmup_start.isoformat(),
                    "end_date": warmup_end.isoformat(),
                },
                "execution_rule": prepared.backtest_config.execution_rule.value,
            },
        )

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("clock must return a timezone-aware datetime")
        return value


def _warmup_start(is_start: date, strategy_version: Any) -> date:
    max_period = max((item.period for item in required_indicators(strategy_version)), default=1)
    return is_start - timedelta(days=max_period * 3 + 10)


def _failure_message(code: str) -> str:
    return f"Candidate execution failed with {code}."


class _RuntimeFailure(Exception):
    def __init__(self, code: str, *, retryable: bool) -> None:
        self.code = code
        self.retryable = retryable


class _ExecutionFailure(Exception):
    def __init__(self, outcome: ExperimentExecutionOutcome) -> None:
        self.outcome = outcome


class _PreparedExecution:
    def __init__(
        self,
        *,
        experiment: Experiment,
        candidate: ExperimentCandidateRecord,
        parameter_set: ParameterSet,
        candidate_set_hash: str,
        bindings: ParameterBindingSet,
        derived_strategy: Any,
        execution: CandidateExecution,
        backtest_config: BacktestConfig,
        warmup_start: date,
        source_data_reference: str,
    ) -> None:
        self.experiment = experiment
        self.candidate = candidate
        self.parameter_set = parameter_set
        self.candidate_set_hash = candidate_set_hash
        self.bindings = bindings
        self.derived_strategy = derived_strategy
        self.execution = execution
        self.backtest_config = backtest_config
        self.warmup_start = warmup_start
        self.source_data_reference = source_data_reference


__all__ = ["ENGINE_SERVICE_VERSION", "ExperimentExecutionService"]
