"""Codex preparation and browser-only execution of exact-version backtests."""

from __future__ import annotations

import math
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import ValidationError

from backend.app.backtest_repository import BacktestRepository
from backend.app.backtest_service import BacktestService, create_default_backtest_service
from backend.app.controlled_backtest_repository import (
    ControlledBacktestRepository,
    ControlledBacktestStorageError,
)
from backend.app.operator_read_service import OperatorReadError, OperatorReadService
from backend.app.strategy_repository import StrategyPersistenceError, StrategyRepository


class ControlledBacktestError(RuntimeError):
    """A stable code suitable for MCP and browser responses."""


class ControlledBacktestService:
    def __init__(
        self,
        *,
        plans: ControlledBacktestRepository | None = None,
        strategies: StrategyRepository | None = None,
        read: OperatorReadService | None = None,
        backtests: BacktestRepository | None = None,
        executor: BacktestService | None = None,
    ) -> None:
        self._read = read or OperatorReadService()
        self._plans = plans or ControlledBacktestRepository(self._read._strategies.db_path)
        self._strategies = strategies or self._read._strategies
        self._backtests = backtests
        self._executor = executor
        run_path = backtests.db_path if backtests else self._read._strategies.db_path
        if self._plans.path.resolve() != run_path.resolve():
            raise ControlledBacktestError("ATOMICITY_UNAVAILABLE")

    def get_strategy_version(self, strategy_id: str, version_id: str = "") -> dict[str, Any]:
        try:
            if version_id:
                version = self._strategies.get(strategy_id, version_id)
            else:
                records = self._strategies.list_version_records(strategy_id)
                version = records[-1].version if records else None
        except StrategyPersistenceError as exc:
            raise ControlledBacktestError("PLAN_VERSION_MISMATCH") from exc
        if version is None:
            raise ControlledBacktestError("VERSION_NOT_FOUND")
        return {
            "strategy_id": version.strategy_id,
            "strategy_version_id": version.version_id,
            "strategy_version_hash": version.content_hash,
            "price_field": version.configuration.price_field.value,
            "asset_universe": [asset.symbol for asset in version.configuration.assets],
            "rebalance_policy": version.configuration.rebalance_policy.to_dict(),
            "execution_rule": "next_trading_day_open",
        }

    @staticmethod
    def _date(value: str) -> date:
        try:
            parsed = date.fromisoformat(value)
            if parsed.isoformat() != value:
                raise ValueError
            return parsed
        except (TypeError, ValueError) as exc:
            raise ControlledBacktestError("BACKTEST_PLAN_AMBIGUOUS_DATE") from exc

    def prepare(
        self,
        *,
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
        from backend.app.backtest_lab import BacktestRequest

        start, end = self._date(requested_start), self._date(requested_end)
        if start > end:
            raise ControlledBacktestError("PLAN_INVALID_DATE_RANGE")
        if (
            isinstance(initial_capital, bool)
            or not isinstance(initial_capital, (int, float))
            or not math.isfinite(initial_capital)
            or initial_capital <= 0
        ):
            raise ControlledBacktestError("PLAN_INVALID_CAPITAL")
        version = self.get_strategy_version(strategy_id, strategy_version_id)
        schedule: dict[str, Any] | None = None
        if contribution_enabled:
            if contribution_frequency not in ("monthly", "one_time") or contribution_amount is None:
                raise ControlledBacktestError("PLAN_INVALID_CONTRIBUTION")
            if contribution_frequency == "monthly":
                if (
                    contribution_start_date not in (None, requested_start)
                    or contribution_end_date not in (None, requested_end)
                    or contribution_requested_date is not None
                ):
                    raise ControlledBacktestError("PLAN_UNSUPPORTED_CONTRIBUTION_WINDOW")
                schedule = {"frequency": "monthly", "amount": contribution_amount}
            else:
                requested = self._date(contribution_requested_date or "")
                if requested < start or requested > end:
                    raise ControlledBacktestError("PLAN_INVALID_CONTRIBUTION")
                if contribution_start_date or contribution_end_date:
                    raise ControlledBacktestError("PLAN_UNSUPPORTED_CONTRIBUTION_WINDOW")
                schedule = {
                    "frequency": "one_time",
                    "amount": contribution_amount,
                    "requested_date": requested.isoformat(),
                }
        elif any(
            (
                contribution_amount,
                contribution_frequency,
                contribution_start_date,
                contribution_end_date,
                contribution_requested_date,
            )
        ):
            raise ControlledBacktestError("PLAN_INVALID_CONTRIBUTION")

        try:
            request = BacktestRequest.model_validate(
                {
                    "strategy_id": strategy_id,
                    "strategy_version_id": version["strategy_version_id"],
                    "start_date": requested_start,
                    "end_date": requested_end,
                    "initial_capital": initial_capital,
                    "price_field_used": version["price_field"],
                    "contribution_schedule": schedule,
                }
            )
        except (ValidationError, ValueError) as exc:
            raise ControlledBacktestError("PLAN_INVALID_CONTRIBUTION") from exc
        try:
            availability = self._read.get_data_availability(
                strategy_id, version["strategy_version_id"], start, end
            )
        except OperatorReadError as exc:
            raise ControlledBacktestError("PLAN_DATA_BLOCKED") from exc
        now = datetime.now(UTC).isoformat()
        body = {
            "config_id": f"backtest-config-{uuid4().hex}",
            "source": "CODEX_MCP",
            **version,
            "requested_start": requested_start,
            "requested_end": requested_end,
            "initial_capital": request.initial_capital,
            "contribution_enabled": contribution_enabled,
            "contribution_amount": str(request.contribution_schedule.amount)
            if request.contribution_schedule
            else None,
            "contribution_frequency": request.contribution_schedule.frequency.value
            if request.contribution_schedule
            else None,
            "contribution_start_date": requested_start
            if schedule and contribution_frequency == "monthly"
            else contribution_requested_date
            if schedule
            else None,
            "contribution_end_date": requested_end
            if schedule and contribution_frequency == "monthly"
            else contribution_requested_date
            if schedule
            else None,
            "contribution_timing": "first_common_trading_date_on_or_after_month_start"
            if contribution_frequency == "monthly"
            else "next_common_trading_date"
            if schedule
            else None,
            "price_field": request.price_field_used.value,
            "fees": request.commission.model_dump(mode="json"),
            "slippage": request.slippage,
            "fractional_shares": request.fractional_shares,
            "position_rebalance_policy": None,
            "data_availability": availability,
            "expected_effective_start": availability.get("effective_start_expected"),
            "expected_effective_end": availability.get("effective_end_expected"),
            "history_requirement": availability.get("warmup_start"),
            "validation_status": availability["status"],
            "validation_issues": availability.get("blocking_issues", []),
            "backtest_request": request.model_dump(mode="json"),
            "created_at": now,
        }
        return self._plans.insert(body)

    def get(self, config_id: str) -> dict[str, Any]:
        return self._plans.get(config_id)

    def list(self) -> list[dict[str, Any]]:
        return self._plans.list()

    def reject(self, config_id: str) -> dict[str, Any]:
        return self._plans.transition(config_id, "PROPOSED", "REJECTED")

    def approve_and_run(self, config_id: str) -> dict[str, Any]:
        from backend.app.backtest_lab import BacktestRequest

        plan = self._plans.get(config_id)
        if plan["status"] == "COMPLETED":
            return plan
        if plan["status"] != "PROPOSED":
            raise ControlledBacktestError(
                "PLAN_ALREADY_EXECUTED" if plan["status"] == "RUNNING" else "PLAN_NOT_PROPOSED"
            )
        preflight_status = plan["data_availability"].get("status")
        if preflight_status != plan["validation_status"] or preflight_status not in (
            "READY",
            "READY_WITH_WARNINGS",
        ):
            raise ControlledBacktestError("PLAN_DATA_BLOCKED")
        try:
            exact = self.get_strategy_version(plan["strategy_id"], plan["strategy_version_id"])
        except ControlledBacktestError as exc:
            self._plans.transition(config_id, "PROPOSED", "STALE")
            raise ControlledBacktestError("PLAN_VERSION_MISMATCH") from exc
        if exact["strategy_version_hash"] != plan["strategy_version_hash"]:
            self._plans.transition(config_id, "PROPOSED", "STALE")
            raise ControlledBacktestError("PLAN_VERSION_MISMATCH")
        try:
            request = BacktestRequest.model_validate(plan["backtest_request"])
        except ValidationError as exc:
            self._plans.transition(config_id, "PROPOSED", "STALE")
            raise ControlledBacktestError("PLAN_INVALID_CONFIGURATION") from exc
        if (
            request.strategy_version_id != exact["strategy_version_id"]
            or request.price_field_used.value != exact["price_field"]
        ):
            self._plans.transition(config_id, "PROPOSED", "STALE")
            raise ControlledBacktestError("PLAN_VERSION_MISMATCH")
        try:
            availability = self._read.get_data_availability(
                plan["strategy_id"],
                plan["strategy_version_id"],
                request.start_date,
                request.end_date,
            )
        except OperatorReadError as exc:
            raise ControlledBacktestError("PLAN_DATA_BLOCKED") from exc
        if availability["status"] == "BLOCKED":
            raise ControlledBacktestError("PLAN_DATA_BLOCKED")
        for key in (
            "effective_start_expected",
            "effective_end_expected",
            "data_snapshot_reference",
        ):
            if availability.get(key) != plan["data_availability"].get(key):
                self._plans.transition(config_id, "PROPOSED", "STALE")
                raise ControlledBacktestError("PLAN_DATA_CHANGED")
        if (
            self._executor is None
            and self._plans.path.resolve()
            != (Path(__file__).resolve().parents[2] / "data" / "strategy.db").resolve()
        ):
            raise ControlledBacktestError("ATOMICITY_UNAVAILABLE")
        execution_id = f"backtest-execution-{uuid4().hex}"
        try:
            self._plans.transition(
                config_id,
                "PROPOSED",
                "RUNNING",
                execution_id=execution_id,
                approved_at=datetime.now(UTC).isoformat(),
            )
        except ControlledBacktestStorageError as exc:
            current = self._plans.get(config_id)
            if current["status"] == "COMPLETED":
                return current
            raise ControlledBacktestError(str(exc)) from exc
        try:
            executor = self._executor or create_default_backtest_service()
            run = executor.build_run(request)
            run = replace(
                run,
                provenance={
                    **run.provenance,
                    "prepared_by": "CODEX_MCP",
                    "approved_by": "HUMAN",
                    "config_id": config_id,
                },
            )
            return self._plans.complete_with_run(config_id, execution_id, run)
        except Exception as exc:
            current = self._plans.get(config_id)
            if current["status"] == "COMPLETED":
                return current
            self._plans.transition(
                config_id,
                "RUNNING",
                "FAILED",
                failure_code="BACKTEST_EXECUTION_FAILED",
                failure_stage="EXECUTION",
                failure_message="Backtest execution failed.",
            )
            raise ControlledBacktestError("BACKTEST_EXECUTION_FAILED") from exc

    def get_result(self, config_id: str) -> dict[str, Any]:
        plan = self._plans.get(config_id)
        if plan["status"] != "COMPLETED" or not plan["backtest_run_id"]:
            raise ControlledBacktestError("RESULT_NOT_AVAILABLE")
        run = self._plans.get_run(plan["backtest_run_id"])
        if run is None or run.provenance.get("config_id") != config_id:
            raise ControlledBacktestError("RESULT_NOT_AVAILABLE")
        result = run.backtest_result
        metrics = run.performance_analysis
        return {
            "config_id": config_id,
            "run_id": run.backtest_run_id,
            "status": plan["status"],
            "requested_start": plan["requested_start"],
            "requested_end": plan["requested_end"],
            "effective_start": result.effective_start_date.isoformat(),
            "effective_end": result.effective_end_date.isoformat(),
            "initial_capital": plan["initial_capital"],
            "contribution_schedule": plan["backtest_request"].get("contribution_schedule"),
            "total_contributions": result.cumulative_contributions,
            "final_equity": result.final_equity,
            "total_return": metrics.total_return.to_dict(),
            "cagr": metrics.cagr.to_dict(),
            "max_drawdown": metrics.max_drawdown.to_dict(),
            "sharpe": metrics.sharpe_ratio.to_dict(),
            "calmar": metrics.calmar_ratio.to_dict(),
            "volatility": metrics.annualized_volatility.to_dict(),
            "turnover": metrics.turnover.to_dict(),
            "trade_count": metrics.trade_metrics.number_of_closed_trades,
            "fill_count": len(result.fills),
        }


__all__ = ["ControlledBacktestError", "ControlledBacktestService"]
