"""Backtest Lab request/response models and REST endpoints."""

from __future__ import annotations

import math
import re
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator, model_validator

from backend.app.backtest_models import BacktestRun
from backend.app.backtest_repository import BacktestPersistenceError, BacktestRepository
from backend.app.backtest_service import (
    BacktestService,
    BacktestServiceError,
    create_default_backtest_service,
)
from backend.app.research import (
    ComparisonInclude,
    compare_records,
    sorted_records,
    summary_payload,
)
from backend.app.strategy_repository import StrategyPersistenceError, StrategyRepository
from backtest.models import ContributionFrequency, ContributionSchedule, ExecutionRule
from data.models import PriceField

_BENCHMARK_SYMBOL_PATTERN = re.compile(r"^[A-Z][A-Z0-9.-]{0,15}$")


class CommissionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rate: float = Field(default=0.0, ge=0.0)
    per_order: float = Field(default=0.0, ge=0.0)

    @field_validator("rate", "per_order")
    @classmethod
    def finite(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("commission values must be finite")
        return value


class ContributionScheduleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    frequency: ContributionFrequency
    amount: Decimal
    requested_date: date | None = None
    currency: Literal["USD"] = "USD"

    @model_validator(mode="after")
    def valid_schedule(self) -> ContributionScheduleRequest:
        ContributionSchedule(
            frequency=self.frequency,
            amount=self.amount,
            requested_date=self.requested_date,
            currency=self.currency,
        )
        return self

    def to_domain(self) -> ContributionSchedule:
        return ContributionSchedule(
            frequency=self.frequency,
            amount=self.amount,
            requested_date=self.requested_date,
            currency=self.currency,
        )


class AllocationConstraintRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str = Field(min_length=1)
    minimum_weight: float = Field(default=0.0, ge=0.0, le=1.0)
    maximum_weight: float = Field(default=1.0, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def ordered_bounds(self) -> AllocationConstraintRequest:
        if self.minimum_weight > self.maximum_weight:
            raise ValueError("minimum_weight cannot exceed maximum_weight")
        return self


class PositionRebalancePolicyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    policy_version: str = Field(default="position-rebalance-policy-v1", min_length=1)
    minimum_allocation_change: float | None = Field(default=None, ge=0.0)
    drift_threshold: float | None = Field(default=None, ge=0.0)
    maximum_turnover: float | None = Field(default=None, ge=0.0)
    minimum_cash_reserve: float = Field(default=0.0, ge=0.0, le=1.0)
    allocation_constraints: tuple[AllocationConstraintRequest, ...] = ()

    def to_domain(self) -> Any:
        from backtest.models import AllocationConstraint, PositionRebalancePolicy

        return PositionRebalancePolicy(
            policy_version=self.policy_version,
            minimum_allocation_change=self.minimum_allocation_change,
            drift_threshold=self.drift_threshold,
            maximum_turnover=self.maximum_turnover,
            minimum_cash_reserve=self.minimum_cash_reserve,
            allocation_constraints=tuple(
                AllocationConstraint(
                    symbol=item.symbol,
                    minimum_weight=item.minimum_weight,
                    maximum_weight=item.maximum_weight,
                )
                for item in self.allocation_constraints
            ),
        )


class BacktestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    strategy_id: str = Field(min_length=1)
    strategy_version_id: str = Field(min_length=1)
    start_date: date
    end_date: date
    initial_capital: float = Field(gt=0)
    commission: CommissionRequest = Field(default_factory=CommissionRequest)
    slippage: float = Field(default=0.0, ge=0.0, lt=1.0)
    price_field_used: PriceField
    execution_rule: ExecutionRule = ExecutionRule.NEXT_TRADING_DAY_OPEN
    fractional_shares: bool = False
    contribution_schedule: ContributionScheduleRequest | None = None
    position_rebalance_policy: PositionRebalancePolicyRequest | None = None
    benchmark_symbol: str | None = None

    @field_validator("initial_capital", "slippage")
    @classmethod
    def finite(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("numeric values must be finite")
        return value

    @field_validator("strategy_id", "strategy_version_id")
    @classmethod
    def non_blank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("identifier must not be blank")
        return normalized

    @field_validator("benchmark_symbol")
    @classmethod
    def valid_benchmark_symbol(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().upper()
        if not _BENCHMARK_SYMBOL_PATTERN.fullmatch(normalized):
            raise ValueError("benchmark_symbol must be a valid market ticker")
        return normalized

    @field_validator("end_date")
    @classmethod
    def date_order(cls, value: date, info: Any) -> date:
        start = info.data.get("start_date")
        if start is not None and value < start:
            raise ValueError("end_date must be on or after start_date")
        return value

    def to_config(self, version_id: str, rebalance_policy: Any) -> Any:
        from backtest.models import BacktestConfig, CommissionPolicy, PositionRebalancePolicy

        return BacktestConfig(
            strategy_version_id=version_id,
            start_date=self.start_date,
            end_date=self.end_date,
            initial_capital=self.initial_capital,
            price_field_used=self.price_field_used,
            commission=CommissionPolicy(
                rate=self.commission.rate,
                per_order=self.commission.per_order,
            ),
            slippage=self.slippage,
            execution_rule=self.execution_rule,
            rebalance_policy=rebalance_policy,
            fractional_shares=self.fractional_shares,
            contribution_schedule=(
                self.contribution_schedule.to_domain()
                if self.contribution_schedule is not None
                else None
            ),
            position_rebalance_policy=(
                self.position_rebalance_policy.to_domain()
                if self.position_rebalance_policy is not None
                else PositionRebalancePolicy()
            ),
        )


class StrategyCatalogItem(BaseModel):
    strategy_id: str
    name: str
    version_count: int
    latest_version: int | None


class BacktestRunSummary(BaseModel):
    backtest_run_id: str
    strategy_id: str
    strategy_version_id: str
    created_at: datetime
    start_date: date
    end_date: date
    final_equity: float
    price_field_used: PriceField


class ResearchComparisonIncludeRequest(BaseModel):
    """Series and metric capabilities to include in the comparison payload."""

    model_config = ConfigDict(extra="forbid")
    twr: StrictBool = True
    drawdown: StrictBool = True
    portfolio_value: StrictBool = False
    metrics: StrictBool = True

    def to_domain(self) -> ComparisonInclude:
        return ComparisonInclude(
            twr=self.twr,
            drawdown=self.drawdown,
            portfolio_value=self.portfolio_value,
            metrics=self.metrics,
        )


class ResearchComparisonRequest(BaseModel):
    """A bounded set of immutable run identifiers for a read-only comparison."""

    model_config = ConfigDict(extra="forbid")
    backtest_run_ids: list[str] = Field(min_length=2, max_length=10)
    include: ResearchComparisonIncludeRequest = Field(
        default_factory=ResearchComparisonIncludeRequest
    )

    @field_validator("backtest_run_ids")
    @classmethod
    def unique_non_blank_ids(cls, value: list[str]) -> list[str]:
        normalized = [item.strip() if isinstance(item, str) else "" for item in value]
        if not all(normalized):
            raise ValueError("backtest run ids must be non-empty strings")
        if len(set(normalized)) != len(normalized):
            raise ValueError("backtest run ids must be unique")
        return normalized


def _run_summary(run: BacktestRun) -> BacktestRunSummary:
    return BacktestRunSummary(
        backtest_run_id=run.backtest_run_id,
        strategy_id=run.strategy_id,
        strategy_version_id=run.strategy_version_id,
        created_at=run.created_at,
        start_date=run.backtest_result.start_date,
        end_date=run.backtest_result.end_date,
        final_equity=run.backtest_result.final_equity,
        price_field_used=run.performance_analysis.price_field_used,
    )


def _legacy_run_payload(run: BacktestRun) -> dict[str, Any]:
    """Keep established Backtest Lab responses independent of report storage."""
    payload = run.to_dict()
    payload.pop("strategy_provenance", None)
    return payload


def _comparison_strategy_metadata(records: tuple[Any, ...]) -> dict[str, dict[str, Any]]:
    """Resolve optional labels without making version metadata a critical result dependency."""
    getter = getattr(strategy_repository, "get_any_version", None)
    if not callable(getter):
        return {}
    metadata: dict[str, dict[str, Any]] = {}
    for record in records:
        version_id = record.run.strategy_version_id
        try:
            version = getter(version_id)
        except StrategyPersistenceError:
            continue
        if version is not None:
            metadata[version_id] = {
                "strategy_name": version.configuration.name,
                "version_number": version.version_number,
            }
    return metadata


strategy_repository = StrategyRepository()
backtest_repository = BacktestRepository()
backtest_service: BacktestService | None = None
router = APIRouter(tags=["backtest-lab"])


def _service() -> BacktestService:
    global backtest_service
    if backtest_service is None:
        backtest_service = create_default_backtest_service()
    return backtest_service


@router.get("/strategies", response_model=list[StrategyCatalogItem])
def list_strategies() -> list[StrategyCatalogItem]:
    try:
        return [
            StrategyCatalogItem(
                strategy_id=item["strategy_id"],
                name=item["name"],
                version_count=item["version_count"],
                latest_version=item["latest_version"],
            )
            for item in strategy_repository.catalog()
        ]
    except StrategyPersistenceError as exc:
        raise HTTPException(status_code=500, detail="strategy catalog is unavailable") from exc


@router.post("/backtests", status_code=status.HTTP_201_CREATED)
def create_backtest(request: BacktestRequest) -> dict[str, Any]:
    try:
        run = _service().run(request)
    except (BacktestServiceError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail="backtest service is unavailable") from exc
    return _legacy_run_payload(run)


@router.get("/backtests/{backtest_run_id}")
def get_backtest(backtest_run_id: str) -> dict[str, Any]:
    try:
        run = backtest_repository.get(backtest_run_id)
    except BacktestPersistenceError as exc:
        raise HTTPException(status_code=500, detail="backtest persistence is unavailable") from exc
    if run is None:
        raise HTTPException(status_code=404, detail="backtest run was not found")
    return _legacy_run_payload(run)


@router.get("/strategies/{strategy_id}/backtests", response_model=list[BacktestRunSummary])
def list_backtests(strategy_id: str) -> list[BacktestRunSummary]:
    try:
        return [_run_summary(run) for run in backtest_repository.list(strategy_id)]
    except BacktestPersistenceError as exc:
        raise HTTPException(status_code=500, detail="backtest persistence is unavailable") from exc


@router.get("/research/backtests")
def list_research_backtests(
    strategy_id: str | None = Query(default=None, min_length=1),
    sort_by: Literal[
        "created_at",
        "cagr",
        "sharpe_ratio",
        "sortino_ratio",
        "max_drawdown",
        "total_return",
        "annualized_volatility",
        "calmar_ratio",
        "win_rate",
        "profit_factor",
        "average_trade_return",
        "best_trade",
        "worst_trade",
        "average_holding_period",
        "turnover",
    ] = "created_at",
    order: Literal["asc", "desc"] = "desc",
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    """List persisted research runs only; this route never runs a backtest or fetches data."""
    try:
        records = sorted_records(
            backtest_repository.list_records(strategy_id),
            sort_by=sort_by,
            order=order,
        )
    except BacktestPersistenceError as exc:
        raise HTTPException(status_code=500, detail="backtest persistence is unavailable") from exc
    return {
        "items": [summary_payload(record) for record in records[offset : offset + limit]],
        "total": len(records),
        "limit": limit,
        "offset": offset,
        "sort_by": sort_by,
        "order": order,
    }


@router.post("/research/comparisons")
def compare_research_backtests(request: ResearchComparisonRequest) -> dict[str, Any]:
    """Return a transient comparison view over existing immutable run artifacts."""
    run_ids = tuple(request.backtest_run_ids)
    try:
        records = backtest_repository.get_records(run_ids)
    except BacktestPersistenceError as exc:
        raise HTTPException(status_code=500, detail="backtest persistence is unavailable") from exc
    if len(records) != len(run_ids):
        raise HTTPException(status_code=404, detail="one or more backtest runs were not found")
    return compare_records(
        records,
        include=request.include.to_domain(),
        strategy_metadata=_comparison_strategy_metadata(records),
    ).to_dict()


__all__ = [
    "BacktestRequest",
    "ContributionScheduleRequest",
    "ResearchComparisonIncludeRequest",
    "ResearchComparisonRequest",
    "backtest_repository",
    "backtest_service",
    "router",
]
