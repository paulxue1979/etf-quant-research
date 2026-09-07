import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { BacktestLab } from "./BacktestLab";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const catalog = [{ strategy_id: "demo", name: "Demo strategy", version_count: 1, latest_version: 1 }];
const versions = [{
  strategy_id: "demo",
  version_id: "demo-v1",
  version_number: 1,
  created_at: "2026-09-07T00:00:00Z",
  content_hash: "hash-demo",
  status: "draft",
}];

function runPayload() {
  return {
    backtest_run_id: "backtest-demo-1",
    strategy_id: "demo",
    strategy_version_id: "demo-v1",
    created_at: "2026-09-07T00:00:00Z",
    strategy_version_content_hash: "hash-demo",
    backtest_result: {
      start_date: "2025-01-01",
      end_date: "2025-01-03",
      initial_capital: 100000,
      final_equity: 103000,
      equity_curve: [
        { date: "2025-01-01", cash: 0, asset_values: { QQQ: 100000 }, total_equity: 100000 },
        { date: "2025-01-02", cash: 0, asset_values: { QQQ: 101000 }, total_equity: 101000 },
        { date: "2025-01-03", cash: 0, asset_values: { QQQ: 103000 }, total_equity: 103000 },
      ],
      orders: [],
      fills: [],
      trades: [{ symbol: "QQQ", entry_date: "2025-01-01", exit_date: "2025-01-03", entry_price: 100, exit_price: 103, quantity: 100, pnl: 300, pnl_pct: 0.03, holding_period: 2 }],
      positions: [{ as_of_date: "2025-01-03", cash: 0, total_equity: 103000, positions: [{ symbol: "QQQ", quantity: 100, market_price: 103, market_value: 103000, unrealized_pnl: 300 }] }],
      allocation_history: [{ date: "2025-01-01", symbol: "QQQ", target_weight: 1, actual_weight: 1 }],
    },
    performance_analysis: {
      backtest_run_id: "backtest-demo-1",
      strategy_id: "demo",
      strategy_version_id: "demo-v1",
      start_date: "2025-01-01",
      end_date: "2025-01-03",
      price_field_used: "adjusted_close",
      rebalance_frequency: "daily",
      observation_frequency: "daily",
      initial_capital: 100000,
      final_equity: 103000,
      total_return: { value: 0.03, status: "available", reason: null },
      cagr: { value: null, status: "not_evaluable", reason: "requires a positive elapsed calendar period" },
      annualized_volatility: { value: 0.01, status: "available", reason: null },
      sharpe_ratio: { value: 1.2, status: "available", reason: null },
      sortino_ratio: { value: null, status: "not_evaluable", reason: "no downside observations" },
      max_drawdown: { value: -0.01, status: "available", reason: null },
      max_drawdown_duration: { value: 1, status: "available", reason: null },
      recovery_duration: { value: 1, status: "available", reason: null },
      max_drawdown_recovered: true,
      calmar_ratio: { value: 2, status: "available", reason: null },
      trade_metrics: {
        number_of_closed_trades: 1,
        winning_trades: 1,
        losing_trades: 0,
        win_rate: { value: 1, status: "available", reason: null },
        profit_factor: { value: 3, status: "available", reason: null },
        average_trade_return: { value: 0.03, status: "available", reason: null },
        best_trade: { value: 0.03, status: "available", reason: null },
        worst_trade: { value: 0.03, status: "available", reason: null },
        average_holding_period: { value: 2, status: "available", reason: null },
        turnover: { value: null, status: "not_evaluable", reason: "not available" },
      },
      drawdown_curve: [{ date: "2025-01-01", value: 0 }, { date: "2025-01-02", value: -0.01 }, { date: "2025-01-03", value: 0 }],
    },
    provenance: { source: "BacktestService" },
  };
}

function mockBacktestApi() {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = String(input);
    if (url.endsWith("/strategies") && !url.includes("strategy-lab")) return new Response(JSON.stringify(catalog), { status: 200 });
    if (url.includes("/strategy-lab/strategies/demo/versions")) return new Response(JSON.stringify(versions), { status: 200 });
    if (url.endsWith("/backtests") && init?.method === "POST") return new Response(JSON.stringify(runPayload()), { status: 201 });
    return new Response("{}", { status: 404 });
  });
}

describe("Backtest Lab", () => {
  it("loads strategies and versions, submits inputs, and renders backend results", async () => {
    const fetchMock = mockBacktestApi();
    render(<BacktestLab onBack={vi.fn()} />);

    await waitFor(() => expect(screen.getByRole("option", { name: /Demo strategy/ })).toBeInTheDocument());
    await waitFor(() => expect(screen.getByRole("option", { name: /v1/ })).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Run backtest" }));

    await waitFor(() => expect(screen.getByText("backtest-demo-1")).toBeInTheDocument());
    expect(screen.getByText("3.00%", { selector: "strong" })).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "Equity curve" })).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "Drawdown curve" })).toBeInTheDocument();
    expect(screen.getAllByText("QQQ").length).toBeGreaterThan(0);
    expect(fetchMock).toHaveBeenCalledWith(expect.stringContaining("/backtests"), expect.objectContaining({ method: "POST" }));
  });

  it("shows N/A with the backend reason for unavailable metrics", async () => {
    mockBacktestApi();
    render(<BacktestLab onBack={vi.fn()} />);

    await waitFor(() => expect(screen.getByRole("option", { name: /Demo strategy/ })).toBeInTheDocument());
    await waitFor(() => expect(screen.getByRole("option", { name: /v1/ })).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Run backtest" }));
    await waitFor(() => expect(screen.getByText("requires a positive elapsed calendar period")).toBeInTheDocument());
    expect(screen.getAllByText("N/A").length).toBeGreaterThan(0);
  });
});
