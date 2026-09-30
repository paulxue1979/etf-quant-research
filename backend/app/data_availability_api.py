"""Read-only data availability preflight for research workflows."""

from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, Body, HTTPException

from backend.app.strategy_repository import StrategyRepository
from data.cache import DiskCache
from data.config import TiingoSettings
from data.exceptions import DataEngineError
from data.models import HistoricalDataRequest, HistoricalDataSet, PriceField
from data.service import HistoricalDataService
from data.tiingo import TiingoClient
from research.data_availability import project_data_availability, warmup_start_for_strategy

router = APIRouter(prefix="/research", tags=["data-availability"])
_strategies = StrategyRepository()
_data = HistoricalDataService(TiingoClient(TiingoSettings.from_environment()), DiskCache())


def _error(code: str, message: str, status_code: int = 422) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})


@router.post("/data-availability")
def data_availability(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Project availability using the same requested range and warm-up contract."""
    strategy_id = str(body.get("strategy_id", "")).strip()
    version_id = str(body.get("strategy_version_id", "")).strip()
    try:
        requested_start = date.fromisoformat(str(body["requested_start"]))
        requested_end = date.fromisoformat(str(body["requested_end"]))
        price_field = PriceField(str(body.get("price_field", PriceField.ADJUSTED_CLOSE.value)))
    except (KeyError, TypeError, ValueError) as exc:
        raise _error(
            "INVALID_DATA_AVAILABILITY_REQUEST",
            "valid requested dates and price field are required",
        ) from exc
    version = _strategies.get(strategy_id, version_id)
    if version is None:
        raise _error(
            "STRATEGY_VERSION_NOT_FOUND", "the immutable strategy version was not found", 404
        )
    warmup_start = warmup_start_for_strategy(requested_start, version)
    symbols = {asset.symbol for asset in version.configuration.assets}
    from strategies.strategy_evaluation import required_indicators

    symbols.update(item.symbol for item in required_indicators(version))
    data_by_symbol: dict[str, HistoricalDataSet | None] = {}
    for symbol in sorted(symbols):
        try:
            data_by_symbol[symbol] = _data.get_history(
                HistoricalDataRequest(
                    symbol=symbol,
                    start_date=warmup_start,
                    end_date=requested_end,
                    price_field_used=price_field,
                )
            )
        except (DataEngineError, ValueError):
            # Availability is a read-only preflight. Preserve the failure as a
            # blocking projection instead of leaking an exception object into
            # the pure projection contract.
            data_by_symbol[symbol] = None
    projection = project_data_availability(
        version,
        requested_start=requested_start,
        requested_end=requested_end,
        data_by_symbol=data_by_symbol,
    )
    payload = dict(projection)
    payload.update(
        {
            "strategy_id": strategy_id,
            "strategy_version_id": version.version_id,
            "strategy_version_content_hash": version.content_hash,
            "price_field": price_field.value,
        }
    )
    return payload


__all__ = ["router"]
