"""Read-only projection for finalized official OOS research artifacts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.app.backtest_models import BacktestRun
from backend.app.backtest_repository import BacktestPersistenceError, BacktestRepository
from backend.app.oos_result_repository import OosResultRepository
from backend.app.research_protocol import (
    OOSObservationStatus,
    ProtocolStatus,
    ResearchPersistenceError,
    ResearchProtocolRepository,
)
from backend.app.strategy_repository import StrategyPersistenceError, StrategyRepository
from research.oos import (
    OosEvaluationConfig,
    OosEvaluationIdentity,
    OosEvaluationResult,
    OosPerformanceSummary,
    validate_oos_configuration,
    validate_oos_strategy_identity,
)


class OosResearchViewError(RuntimeError):
    """Safe, structured error raised while assembling the read model."""

    def __init__(self, code: str, message: str, *, status_code: int = 409) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


@dataclass(frozen=True)
class OosResearchViewService:
    """Compose official persisted records without executing or calculating research."""

    protocols: ResearchProtocolRepository
    results: OosResultRepository
    backtests: BacktestRepository
    strategies: StrategyRepository

    def get(self, protocol_id: str) -> dict[str, Any]:
        protocol_id = protocol_id.strip() if isinstance(protocol_id, str) else ""
        if not protocol_id:
            raise OosResearchViewError(
                "OOS_PROTOCOL_STATE_INVALID", "protocol_id is required", status_code=422
            )

        try:
            protocol = self.protocols.get_protocol(protocol_id)
        except ResearchPersistenceError as exc:
            raise OosResearchViewError(
                "OOS_RESEARCH_PERSISTENCE_UNAVAILABLE",
                "research protocol persistence is unavailable",
                status_code=503,
            ) from exc
        if protocol is None:
            raise OosResearchViewError(
                "OOS_RESULT_NOT_FOUND", "research protocol was not found", status_code=404
            )
        if protocol.status not in (ProtocolStatus.OOS_EVALUATED, ProtocolStatus.CLOSED):
            raise OosResearchViewError(
                "OOS_NOT_FINALIZED",
                "official OOS evaluation has not been finalized",
            )

        try:
            result = self.results.get_by_protocol(protocol_id)
        except Exception as exc:
            if isinstance(exc, OosResearchViewError):
                raise
            raise OosResearchViewError(
                "OOS_RESULT_INTEGRITY_ERROR",
                "official OOS result failed integrity validation",
            ) from exc
        if result is None:
            raise OosResearchViewError(
                "OOS_RESULT_NOT_FOUND",
                "official OOS result was not found",
                status_code=404,
            )

        try:
            run = self.backtests.get(result.backtest_run_id)
        except BacktestPersistenceError as exc:
            raise OosResearchViewError(
                "OOS_BACKTEST_RUN_NOT_FOUND",
                "official OOS backtest persistence is unavailable",
                status_code=503,
            ) from exc
        if run is None:
            raise OosResearchViewError(
                "OOS_BACKTEST_RUN_NOT_FOUND",
                "official OOS backtest run was not found",
                status_code=404,
            )

        try:
            selection = self.protocols.get_selection(result.selection_decision_id)
            freeze = self.protocols.get_freeze(result.strategy_freeze_id)
            strategy = self.strategies.get_any_version(result.strategy_version_id)
            self._validate_provenance(protocol, result, run, selection, freeze, strategy)
        except OosResearchViewError:
            raise
        except (
            ResearchPersistenceError,
            StrategyPersistenceError,
            BacktestPersistenceError,
        ) as exc:
            raise OosResearchViewError(
                "OOS_PROVENANCE_INTEGRITY_ERROR",
                "official OOS provenance could not be validated",
            ) from exc
        except Exception as exc:
            raise OosResearchViewError(
                "OOS_PROVENANCE_INTEGRITY_ERROR",
                "official OOS provenance failed integrity validation",
            ) from exc

        return self._projection(protocol, selection, freeze, result, run)

    def _validate_provenance(
        self,
        protocol: Any,
        result: OosEvaluationResult,
        run: BacktestRun,
        selection: Any,
        freeze: Any,
        strategy: Any,
    ) -> None:
        if selection is None or freeze is None or strategy is None:
            raise OosResearchViewError(
                "OOS_PROVENANCE_INTEGRITY_ERROR",
                "official OOS selection, freeze, or strategy version is missing",
            )
        if result.protocol_id != protocol.protocol_id:
            raise OosResearchViewError(
                "OOS_PROVENANCE_INTEGRITY_ERROR",
                "official OOS result does not belong to the requested protocol",
            )
        identity = OosEvaluationIdentity(
            protocol_id=result.protocol_id,
            selection_decision_id=result.selection_decision_id,
            strategy_freeze_id=result.strategy_freeze_id,
            strategy_version_id=result.strategy_version_id,
            strategy_content_hash=result.strategy_content_hash,
        )
        validate_oos_strategy_identity(selection, freeze, strategy, identity)
        if run.backtest_run_id != result.backtest_run_id:
            raise OosResearchViewError(
                "OOS_PROVENANCE_INTEGRITY_ERROR",
                "official OOS result references a different backtest run",
            )
        if run.strategy_version_id != result.strategy_version_id:
            raise OosResearchViewError(
                "OOS_PROVENANCE_INTEGRITY_ERROR",
                "official backtest strategy version does not match the result",
            )
        if run.strategy_version_content_hash != result.strategy_content_hash:
            raise OosResearchViewError(
                "OOS_PROVENANCE_INTEGRITY_ERROR",
                "official backtest strategy hash does not match the result",
            )
        backtest_result = run.backtest_result
        if (
            backtest_result.start_date != result.oos_start
            or backtest_result.end_date != result.oos_end
        ):
            raise OosResearchViewError(
                "OOS_PROVENANCE_INTEGRITY_ERROR",
                "official backtest range does not match the frozen OOS range",
            )
        if (
            backtest_result.configuration_snapshot.get("price_field_used")
            != result.price_field_used.value
        ):
            raise OosResearchViewError(
                "OOS_PROVENANCE_INTEGRITY_ERROR",
                "official backtest price field does not match the result",
            )
        try:
            frozen_config = OosEvaluationConfig.from_research_evaluation_config(
                protocol.evaluation_config,
                analytics_version=result.analytics_version,
            )
            if frozen_config.configuration_hash != result.configuration_hash:
                raise OosResearchViewError(
                    "OOS_PROVENANCE_INTEGRITY_ERROR",
                    "official result configuration hash does not match the protocol",
                )
            actual_payload = dict(backtest_result.configuration_snapshot)
            actual_payload["analytics_version"] = result.analytics_version
            actual_config = OosEvaluationConfig.from_dict(actual_payload)
            validate_oos_configuration(frozen_config, actual_config)
        except OosResearchViewError:
            raise
        except Exception as exc:
            raise OosResearchViewError(
                "OOS_PROVENANCE_INTEGRITY_ERROR",
                "official OOS configuration failed integrity validation",
            ) from exc
        expected_summary = OosPerformanceSummary.from_analysis(run.performance_analysis)
        if expected_summary.to_dict() != result.performance_summary.to_dict():
            raise OosResearchViewError(
                "OOS_PROVENANCE_INTEGRITY_ERROR",
                "official OOS summary does not match the persisted analytics",
            )
        for point in backtest_result.equity_curve:
            if point.date < result.oos_start or point.date > result.oos_end:
                raise OosResearchViewError(
                    "OOS_PROVENANCE_INTEGRITY_ERROR",
                    "official equity curve contains data outside the OOS range",
                )
        evaluations = self.protocols.list_oos_evaluations(protocol.protocol_id)
        observation = next(
            (item for item in evaluations if item.evaluation_id == result.oos_result_id), None
        )
        if observation is None or observation.status not in (
            OOSObservationStatus.OBSERVED,
            OOSObservationStatus.SEALED,
        ):
            raise OosResearchViewError(
                "OOS_PROVENANCE_INTEGRITY_ERROR",
                "official OOS observation is not finalized",
            )
        if observation.backtest_run_id != result.backtest_run_id:
            raise OosResearchViewError(
                "OOS_PROVENANCE_INTEGRITY_ERROR",
                "official OOS observation references a different backtest run",
            )

    @staticmethod
    def _asset_universe(run: BacktestRun) -> list[str]:
        assets: set[str] = set()
        for point in run.backtest_result.equity_curve:
            assets.update(str(symbol) for symbol in point.asset_values)
        for point in run.backtest_result.allocation_history:
            assets.add(point.symbol)
        for snapshot in run.backtest_result.positions:
            assets.update(position.symbol for position in snapshot.positions)
        return sorted(assets)

    def _projection(
        self,
        protocol: Any,
        selection: Any,
        freeze: Any,
        result: Any,
        run: BacktestRun,
    ) -> dict[str, Any]:
        config = dict(run.backtest_result.configuration_snapshot)
        return {
            "read_only": True,
            "protocol": protocol.to_dict(),
            "selection_decision": selection.to_dict(),
            "strategy_freeze": freeze.to_dict(),
            "oos_result": result.to_dict(),
            "backtest_run": run.to_dict(),
            "provenance": {
                "protocol_id": protocol.protocol_id,
                "selection_decision_id": result.selection_decision_id,
                "strategy_freeze_id": result.strategy_freeze_id,
                "strategy_version_id": result.strategy_version_id,
                "strategy_content_hash": result.strategy_content_hash,
                "oos_result_id": result.oos_result_id,
                "backtest_run_id": result.backtest_run_id,
                "execution_id": result.execution_id,
                "is_start": protocol.is_start_date.isoformat(),
                "is_end": protocol.is_end_date.isoformat(),
                "warmup_start": result.warmup_start.isoformat(),
                "oos_start": result.oos_start.isoformat(),
                "oos_end": result.oos_end.isoformat(),
                "price_field_used": result.price_field_used.value,
                "initial_capital": config.get("initial_capital"),
                "commission": config.get("commission"),
                "slippage": config.get("slippage"),
                "execution_rule": config.get("execution_rule", "next_trading_day_open"),
                "fractional_shares": config.get("fractional_shares"),
                "rebalance_policy": config.get("rebalance_policy"),
                "engine_version": result.engine_version,
                "analytics_version": result.analytics_version,
                "data_provenance": result.data_provenance.to_dict(),
                "asset_universe": self._asset_universe(run),
                "portfolio_initialization": "fresh_capital",
                "oos_signal_policy": "oos_signals_only",
                "no_lookahead": "Signal(T Close) -> T+1 Trading Day Open",
                "warmup_notice": (
                    "Warm-up only initializes indicators and is excluded from OOS performance."
                ),
                "source_of_truth": {
                    "summary": "OosEvaluationResult",
                    "detail": "BacktestRun",
                },
            },
        }


def create_default_oos_research_view_service() -> OosResearchViewService:
    from backend.app.backtest_lab import backtest_repository
    from backend.app.research_protocol_api import (
        research_protocol_repository,
        strategy_repository,
    )

    return OosResearchViewService(
        protocols=research_protocol_repository,
        results=OosResultRepository(),
        backtests=backtest_repository,
        strategies=strategy_repository,
    )


__all__ = [
    "OosResearchViewError",
    "OosResearchViewService",
    "create_default_oos_research_view_service",
]
