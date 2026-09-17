from __future__ import annotations

from backend.app.strategy_execution_provenance import strategy_execution_provenance
from backtest import run_strategy_backtest
from tests.unit.test_strategy_backtest_integration import _config, _dataset, _timeline, _version


def test_strategy_provenance_is_compact_and_keeps_signal_execution_separate() -> None:
    version = _version(("QQQ", "TQQQ", "SGOV"), weights=(("QQQ", 0.6), ("TQQQ", 0.3)))
    data = {
        symbol: _dataset(symbol, (10.0, 10.0, 10.0, 10.0)) for symbol in ("QQQ", "TQQQ", "SGOV")
    }
    end_date = next(iter(data.values())).points[-1].date
    integration = run_strategy_backtest(
        version,
        _timeline(version, data),
        data,
        _config(version, end=end_date),
    )

    payload = strategy_execution_provenance(integration.signal_records)

    assert payload["source"] == "StrategyBacktestResult.signal_records"
    assert payload["records"][0]["signal_date"] != payload["records"][0]["execution_date"]
    assert payload["records"][0]["target_allocation"] == {"QQQ": 0.6, "TQQQ": 0.3}
    assert payload["records"][-1]["execution_status"] == "omitted"
    assert payload["records"][-1]["execution_date"] is None
