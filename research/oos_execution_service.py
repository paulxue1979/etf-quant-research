"""Orchestration for the in-memory PHASE 8F-3 controlled OOS execution."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime, timedelta
from typing import Any

from analytics.models import PerformanceAnalysisResult
from analytics.performance import analyze_backtest
from backtest.integration import run_strategy_backtest
from backtest.models import BacktestConfig, CommissionPolicy
from data.exceptions import (
    DataValidationError,
    TiingoNetworkError,
    TiingoRateLimitError,
    TiingoServiceError,
    TiingoTimeoutError,
)
from data.models import HistoricalDataRequest, HistoricalDataSet, PriceField
from data.validation import validate_historical_data
from indicators import exponential_moving_average, moving_average
from indicators.models import IndicatorKind
from research.exceptions import (
    OosConfigurationMismatchError,
    OosExecutionError,
    OosExecutionStaleWriterError,
    OosOfficialResultExistsError,
    OosPreconditionError,
    OosRangeMismatchError,
    OosResultIntegrityError,
    OosStrategyIdentityMismatchError,
)
from research.oos import (
    OosDataProvenance,
    OosEvaluationConfig,
    OosEvaluationIdentity,
    OosEvaluationSpec,
    OosExecutionStatus,
    validate_oos_configuration,
    validate_oos_preconditions,
)
from research.oos_execution import OosExecution
from research.oos_execution_outcome import OosExecutionOutcome
from strategies.evaluation import EvaluationContext
from strategies.strategy_evaluation import evaluate_strategy, required_indicators

ANALYTICS_VERSION = "phase-4i.0"
_RETRYABLE_DATA_ERRORS = (
    TiingoNetworkError,
    TiingoRateLimitError,
    TiingoServiceError,
    TiingoTimeoutError,
)


class OosExecutionService:
    """Connect frozen research records to existing calculation engines."""

    def __init__(
        self,
        *,
        protocol_repository: Any,
        execution_repository: Any,
        strategy_repository: Any,
        data_service: Any,
        clock: Callable[[], datetime] | None = None,
        spec_resolver: Callable[..., OosEvaluationSpec] | None = None,
        lease_duration: timedelta = timedelta(minutes=5),
        analytics_version: str = ANALYTICS_VERSION,
    ) -> None:
        if lease_duration <= timedelta(0):
            raise ValueError("lease_duration must be positive")
        self._protocols = protocol_repository
        self._executions = execution_repository
        self._strategies = strategy_repository
        self._data = data_service
        self._clock = clock or (lambda: datetime.now(UTC))
        self._spec_resolver = spec_resolver
        self._lease_duration = lease_duration
        self._analytics_version = analytics_version

    def execute(
        self,
        *,
        protocol_id: str,
        execution_id: str,
        lease_token: str,
        spec: OosEvaluationSpec | None = None,
    ) -> OosExecutionOutcome:
        """Execute one claimed OOS run; ``spec`` is an internal test seam only."""
        now = self._now()
        execution = self._load_claim(execution_id, protocol_id, lease_token, now)
        try:
            outcome = self._execute_claimed(
                protocol_id=protocol_id,
                execution=execution,
                lease_token=lease_token,
                spec=spec,
            )
            return outcome
        except Exception as exc:
            self._record_failure(execution, lease_token, now, exc)
            if isinstance(exc, OosExecutionError):
                raise
            code, message = self._safe_failure(exc)
            raise OosExecutionError(message, code=code) from exc

    def execute_oos(self, **kwargs: Any) -> OosExecutionOutcome:
        """Named alias for callers following the PHASE 8F-3 contract."""
        return self.execute(**kwargs)

    def _load_claim(
        self, execution_id: str, protocol_id: str, lease_token: str, now: datetime
    ) -> OosExecution:
        execution = self._executions.get_execution(execution_id)
        if execution is None:
            raise OosExecutionError("OOS execution was not found", code="OOS_EXECUTION_NOT_FOUND")
        if execution.protocol_id != protocol_id:
            raise OosExecutionError(
                "OOS execution protocol does not match request",
                code="OOS_EXECUTION_IDENTITY_MISMATCH",
            )
        if execution.status is not OosExecutionStatus.RUNNING:
            raise OosExecutionError(
                "OOS execution is not RUNNING", code="OOS_EXECUTION_NOT_RUNNING"
            )
        if execution.lease_token != lease_token:
            raise OosExecutionError(
                "OOS execution lease token does not match",
                code="OOS_EXECUTION_LEASE_MISMATCH",
            )
        if execution.lease_expires_at is None or execution.lease_expires_at <= now:
            raise OosExecutionStaleWriterError("OOS execution lease has expired")
        return execution

    def _execute_claimed(
        self,
        *,
        protocol_id: str,
        execution: OosExecution,
        lease_token: str,
        spec: OosEvaluationSpec | None,
    ) -> OosExecutionOutcome:
        del lease_token  # Lease was checked before any data request; no mutation follows.
        protocol = self._protocols.get_protocol(protocol_id)
        if protocol is None:
            raise OosPreconditionError(
                "research protocol was not found", code="OOS_PRECONDITION_FAILED"
            )
        if protocol.status != "selection_recorded":
            raise OosPreconditionError(
                "research protocol is not selection_recorded", code="OOS_PROTOCOL_STATE_INVALID"
            )
        official = self._protocols.list_oos_evaluations(protocol_id)
        if any(item.status in ("observed", "sealed") for item in official):
            raise OosOfficialResultExistsError("official OOS observation already exists")

        selection = self._protocols.get_selection(execution.selection_decision_id)
        freeze = self._protocols.get_freeze(execution.strategy_freeze_id)
        strategy = self._strategies.get_any_version(execution.strategy_version_id)
        identity = validate_oos_preconditions(
            protocol,
            selection,
            freeze,
            strategy,
            requested_oos_start=execution.oos_start,
            requested_oos_end=execution.oos_end,
        )
        resolved_spec = self._resolve_spec(protocol, execution, identity, spec)
        self._validate_execution_binding(execution, resolved_spec)
        frozen_research_config = getattr(protocol, "evaluation_config", None)
        if frozen_research_config is None:
            frozen_research_config = self._protocols.get_frozen_evaluation_config(protocol_id)
        if frozen_research_config is None:
            raise OosConfigurationMismatchError(
                "frozen evaluation configuration was not found",
                code="OOS_CONFIGURATION_MISMATCH",
            )
        expected_config = OosEvaluationConfig.from_research_evaluation_config(
            frozen_research_config,
            analytics_version=resolved_spec.configuration.analytics_version,
        )
        validate_oos_configuration(expected_config, resolved_spec.configuration)
        data, actual_provenance = self._load_data(strategy, resolved_spec)
        indicators = self._prepare_indicators(
            strategy, data, resolved_spec.configuration.price_field_used
        )
        context = EvaluationContext.from_components(data, indicators)
        oos_start = resolved_spec.evaluation_range.oos_start
        oos_end = resolved_spec.evaluation_range.oos_end
        source_reference = (
            f"oos-data:{protocol_id}:{resolved_spec.evaluation_range.warmup_start.isoformat()}"
            f":{oos_end.isoformat()}"
        )
        timeline = evaluate_strategy(
            strategy,
            context,
            oos_start,
            oos_end,
            source_data_reference=source_reference,
        )
        self._validate_timeline(timeline, oos_start, oos_end)
        backtest_config = self._backtest_config(strategy.version_id, resolved_spec)
        integration = run_strategy_backtest(strategy, timeline, data, backtest_config)
        backtest_result = integration.backtest_result
        self._validate_backtest_bounds(backtest_result, oos_start, oos_end)
        analysis = analyze_backtest(backtest_result, strategy_id=strategy.strategy_id)
        self._validate_analysis(analysis, strategy.version_id, oos_start, oos_end)
        return OosExecutionOutcome(
            execution_id=execution.execution_id,
            protocol_id=protocol_id,
            oos_spec_hash=resolved_spec.spec_hash,
            strategy_version_id=strategy.version_id,
            strategy_content_hash=strategy.content_hash or "",
            backtest_result=backtest_result,
            performance_analysis=analysis,
            data_provenance=actual_provenance,
            warmup_request_start=resolved_spec.evaluation_range.warmup_start,
            oos_start=oos_start,
            oos_end=oos_end,
            engine_version=resolved_spec.configuration.engine_version,
            analytics_version=resolved_spec.configuration.analytics_version,
        )

    def _resolve_spec(
        self,
        protocol: Any,
        execution: OosExecution,
        identity: OosEvaluationIdentity,
        supplied: OosEvaluationSpec | None,
    ) -> OosEvaluationSpec:
        if supplied is not None:
            if not isinstance(supplied, OosEvaluationSpec):
                raise OosExecutionError("OOS spec is invalid", code="OOS_SPEC_INVALID")
            return supplied
        if self._spec_resolver is not None:
            try:
                return self._spec_resolver(
                    protocol=protocol, execution=execution, identity=identity
                )
            except TypeError:
                return self._spec_resolver(protocol, execution)
        raise OosPreconditionError(
            "server-side frozen OOS spec resolver is required",
            code="OOS_SPEC_RESOLUTION_REQUIRED",
        )

    @staticmethod
    def _validate_execution_binding(execution: OosExecution, spec: OosEvaluationSpec) -> None:
        identity = spec.identity
        if (
            identity.protocol_id != execution.protocol_id
            or identity.selection_decision_id != execution.selection_decision_id
            or identity.strategy_freeze_id != execution.strategy_freeze_id
            or identity.strategy_version_id != execution.strategy_version_id
            or identity.strategy_content_hash != execution.strategy_content_hash
        ):
            raise OosStrategyIdentityMismatchError(
                "OOS spec identity does not match claimed execution",
                code="OOS_STRATEGY_IDENTITY_MISMATCH",
            )
        evaluation_range = spec.evaluation_range
        if (
            evaluation_range.oos_start != execution.oos_start
            or evaluation_range.oos_end != execution.oos_end
            or evaluation_range.warmup_start != execution.warmup_start
        ):
            raise OosRangeMismatchError(
                "OOS spec range does not match claimed execution", code="OOS_RANGE_MISMATCH"
            )
        if spec.spec_hash != execution.oos_spec_hash:
            raise OosExecutionError(
                "OOS spec hash does not match claimed execution",
                code="OOS_SPEC_HASH_MISMATCH",
            )
        if spec.configuration.configuration_hash != execution.configuration_hash:
            raise OosConfigurationMismatchError(
                "OOS configuration hash does not match claimed execution",
                code="OOS_CONFIGURATION_MISMATCH",
            )

    def _load_data(
        self, strategy: Any, spec: OosEvaluationSpec
    ) -> tuple[dict[str, HistoricalDataSet], OosDataProvenance]:
        config = spec.configuration
        warmup_start = spec.evaluation_range.warmup_start
        oos_end = spec.evaluation_range.oos_end
        datasets: dict[str, HistoricalDataSet] = {}
        references: dict[str, object] = {}
        sources: set[str] = set()
        for asset in strategy.configuration.assets:
            request = HistoricalDataRequest(
                symbol=asset.symbol,
                start_date=warmup_start,
                end_date=oos_end,
                frequency="daily",
                price_field_used=config.price_field_used,
            )
            dataset = self._data.get_history(request)
            self._validate_dataset(dataset, request)
            datasets[asset.symbol] = dataset
            sources.add(dataset.source.value)
            references[asset.symbol] = {
                "source": dataset.source.value,
                "symbol": asset.symbol,
                "requested_start": warmup_start.isoformat(),
                "requested_end": oos_end.isoformat(),
                "rows": len(dataset.points),
            }
        if set(datasets) != {asset.symbol for asset in strategy.configuration.assets}:
            raise OosResultIntegrityError("market data assets do not match strategy")
        provenance = OosDataProvenance(
            source="HistoricalDataService",
            data_reference={"assets": references, "sources": sorted(sources)},
            requested_start=spec.evaluation_range.oos_start,
            requested_end=oos_end,
            warmup_start=warmup_start,
            frequency="daily",
            price_field=config.price_field_used,
        )
        return datasets, provenance

    @staticmethod
    def _validate_dataset(dataset: Any, request: HistoricalDataRequest) -> None:
        if not isinstance(dataset, HistoricalDataSet):
            raise DataValidationError(f"data set for {request.symbol} is invalid")
        if dataset.request != request:
            raise DataValidationError(
                f"data request for {request.symbol} does not match frozen bounds"
            )
        validate_historical_data(dataset)
        if any(
            point.date < request.start_date or point.date > request.end_date
            for point in dataset.points
        ):
            raise DataValidationError(f"data for {request.symbol} exceeds frozen date bounds")

    @staticmethod
    def _prepare_indicators(
        strategy: Any, data: Mapping[str, HistoricalDataSet], price_field: PriceField
    ) -> tuple[tuple[Any, Any], ...]:
        prepared: list[tuple[Any, Any]] = []
        for requirement in required_indicators(strategy):
            if requirement.price_field is not price_field:
                raise OosConfigurationMismatchError(
                    "indicator price field does not match frozen price field",
                    code="PRICE_FIELD_MISMATCH",
                )
            dataset = data.get(requirement.symbol)
            if dataset is None:
                raise OosResultIntegrityError(
                    f"indicator data is missing for {requirement.symbol}",
                    code="OOS_DATA_MISSING",
                )
            if requirement.kind is IndicatorKind.MOVING_AVERAGE:
                series = moving_average(dataset, period=requirement.period, price_field=price_field)
            elif requirement.kind is IndicatorKind.EXPONENTIAL_MOVING_AVERAGE:
                series = exponential_moving_average(
                    dataset, period=requirement.period, price_field=price_field
                )
            else:
                raise OosResultIntegrityError(
                    f"unsupported indicator {requirement.kind.value}",
                    code="OOS_INDICATOR_UNSUPPORTED",
                )
            prepared.append((requirement.asset, series))
        return tuple(prepared)

    @staticmethod
    def _backtest_config(strategy_version_id: str, spec: OosEvaluationSpec) -> BacktestConfig:
        config = spec.configuration
        return BacktestConfig(
            strategy_version_id=strategy_version_id,
            start_date=spec.evaluation_range.oos_start,
            end_date=spec.evaluation_range.oos_end,
            initial_capital=config.initial_capital,
            price_field_used=config.price_field_used,
            commission=CommissionPolicy(config.commission, config.commission_per_order),
            slippage=config.slippage,
            execution_rule=config.execution_rule,
            rebalance_policy=config.rebalance_policy,
            fractional_shares=config.fractional_shares,
        )

    @staticmethod
    def _validate_timeline(timeline: Any, start: date, end: date) -> None:
        if timeline.start_date != start or timeline.end_date != end:
            raise OosRangeMismatchError(
                "strategy evaluation escaped OOS range", code="OOS_RANGE_MISMATCH"
            )
        if any(item.date < start or item.date > end for item in timeline.evaluations):
            raise OosRangeMismatchError(
                "strategy evaluation contains a non-OOS date", code="OOS_RANGE_MISMATCH"
            )

    @staticmethod
    def _validate_backtest_bounds(result: Any, start: date, end: date) -> None:
        if result.start_date != start or result.end_date != end:
            raise OosRangeMismatchError(
                "backtest result escaped OOS range", code="OOS_RANGE_MISMATCH"
            )
        dates = [
            *(item.date for item in result.equity_curve),
            *(item.date for item in result.orders),
            *(item.date for item in result.fills),
            *(item.date for item in result.allocation_history),
            *(item_date for item_date, _ in result.cash_history),
            *(item.as_of_date for item in result.positions),
            *(item.entry_date for item in result.trades),
            *(item.exit_date for item in result.trades),
        ]
        if any(day < start or day > end for day in dates):
            raise OosRangeMismatchError(
                "backtest artifact contains a non-OOS date", code="OOS_RANGE_MISMATCH"
            )

    @staticmethod
    def _validate_analysis(
        analysis: PerformanceAnalysisResult, strategy_version_id: str, start: date, end: date
    ) -> None:
        if (
            analysis.strategy_version_id != strategy_version_id
            or analysis.start_date != start
            or analysis.end_date != end
        ):
            raise OosResultIntegrityError("analytics identity or range is invalid")
        if any(point.date < start or point.date > end for point in analysis.drawdown_curve):
            raise OosRangeMismatchError(
                "drawdown analytics escaped OOS range", code="OOS_RANGE_MISMATCH"
            )

    def _record_failure(
        self, execution: OosExecution, lease_token: str, now: datetime, exc: Exception
    ) -> None:
        code, message = self._safe_failure(exc)
        try:
            self._executions.mark_failed(
                execution.execution_id,
                lease_token,
                now,
                code,
                message,
                retryable=isinstance(exc, _RETRYABLE_DATA_ERRORS),
            )
        except Exception:
            return

    @staticmethod
    def _safe_failure(exc: Exception) -> tuple[str, str]:
        code = getattr(exc, "code", None) or "OOS_EXECUTION_FAILED"
        if not isinstance(code, str) or not code.replace("_", "").isalnum():
            code = "OOS_EXECUTION_FAILED"
        if isinstance(exc, _RETRYABLE_DATA_ERRORS):
            return code, "historical market data service is temporarily unavailable"
        return code, "controlled OOS execution failed validation or calculation"

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("clock must return a timezone-aware datetime")
        return value.astimezone(UTC)


__all__ = ["OosExecutionService"]
