"""Read-only API for versioned backtest report projections."""

from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from backend.app.backtest_report_projection import (
    BacktestReportProjectionError,
    BacktestReportProjectionService,
)
from backend.app.backtest_repository import BacktestPersistenceError, BacktestRepository

router = APIRouter(prefix="/research/backtests", tags=["backtest-report"])
backtest_repository = BacktestRepository()
_projection = BacktestReportProjectionService()


def _run_or_404(backtest_run_id: str) -> Any:
    try:
        run = backtest_repository.get(backtest_run_id)
    except BacktestPersistenceError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "REPORT_SERVICE_UNAVAILABLE",
                "message": "backtest report service is unavailable",
            },
        ) from exc
    if run is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "REPORT_NOT_FOUND", "message": "backtest run was not found"},
        )
    return run


@router.get("/{backtest_run_id}/report")
def get_backtest_report(backtest_run_id: str) -> dict[str, Any]:
    """Return a bounded report summary over an existing immutable run."""
    return _projection.project(_run_or_404(backtest_run_id))


@router.get("/{backtest_run_id}/report/series")
def get_backtest_report_series(
    backtest_run_id: str,
    include: str = Query(default="equity,capital,drawdown"),
    from_date: date | None = Query(default=None, alias="from"),
    to_date: date | None = Query(default=None, alias="to"),
) -> dict[str, Any]:
    """Return selected report series in a viewport without altering run metrics."""
    names = tuple(item.strip() for item in include.split(","))
    if from_date is not None and to_date is not None and from_date > to_date:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "INVALID_REPORT_DATE_RANGE",
                "message": "report series from date must be on or before to date",
            },
        )
    try:
        return _projection.series(
            _run_or_404(backtest_run_id),
            include=names,
            start=from_date,
            end=to_date,
        )
    except BacktestReportProjectionError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "INVALID_REPORT_SERIES_INCLUDE", "message": str(exc)},
        ) from exc


__all__ = ["backtest_repository", "router"]
