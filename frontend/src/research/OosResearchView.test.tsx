import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { OosResearchView } from "./OosResearchView";

const oosView = {
  read_only: true,
  protocol: { protocol_id: "protocol-1", status: "oos_evaluated" },
  selection_decision: { selection_id: "selection-1" },
  strategy_freeze: { freeze_id: "freeze-1" },
  oos_result: {
    oos_result_id: "oos-result-1",
    protocol_id: "protocol-1",
    execution_id: "execution-1",
    selection_decision_id: "selection-1",
    strategy_freeze_id: "freeze-1",
    strategy_version_id: "strategy-v1",
    strategy_content_hash: "s".repeat(64),
    backtest_run_id: "backtest-1",
    oos_start: "2024-01-01",
    oos_end: "2024-01-03",
    warmup_start: "2023-12-01",
    price_field_used: "adjusted_close",
    configuration_hash: "c".repeat(64),
    engine_version: "phase-3",
    analytics_version: "phase-4i",
    data_provenance: { symbol: "QQQ" },
    performance_summary: {
      metrics: {
        total_return: { value: 0.12, status: "available", reason: null },
        sharpe_ratio: { value: null, status: "not_evaluable", reason: "Insufficient observations." },
      },
    },
    result_hash: "r".repeat(64),
    created_at: "2026-09-13T00:00:00Z",
  },
  backtest_run: {
    backtest_run_id: "backtest-1",
    strategy_id: "strategy-1",
    strategy_version_id: "strategy-v1",
    created_at: "2026-09-13T00:00:00Z",
    strategy_version_content_hash: "s".repeat(64),
    backtest_result: {
      start_date: "2024-01-01",
      end_date: "2024-01-03",
      initial_capital: 10000,
      final_equity: 11200,
      equity_curve: [
        { date: "2024-01-01", cash: 10000, asset_values: {}, total_equity: 10000 },
        { date: "2024-01-03", cash: 0, asset_values: { QQQ: 11200 }, total_equity: 11200 },
      ],
      orders: [],
      fills: [],
      trades: [],
      positions: [],
      allocation_history: [],
    },
    performance_analysis: { drawdown_curve: [{ date: "2024-01-01", value: 0 }] },
  },
  provenance: {
    protocol_id: "protocol-1",
    oos_result_id: "oos-result-1",
    strategy_version_id: "strategy-v1",
    strategy_content_hash: "s".repeat(64),
    backtest_run_id: "backtest-1",
    selection_decision_id: "selection-1",
    strategy_freeze_id: "freeze-1",
    execution_id: "execution-1",
    is_start: "2023-01-01",
    is_end: "2023-12-31",
    warmup_start: "2023-12-01",
    oos_start: "2024-01-01",
    oos_end: "2024-01-03",
    price_field_used: "adjusted_close",
    initial_capital: 10000,
    commission: 0,
    slippage: 0,
    execution_rule: "next_trading_day_open",
    fractional_shares: false,
    rebalance_policy: "daily",
    engine_version: "phase-3",
    analytics_version: "phase-4i",
    data_provenance: { symbol: "QQQ" },
    asset_universe: ["QQQ"],
  },
};

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("OOS Research View", () => {
  it("renders official persisted analytics as read-only and preserves not-evaluable metrics", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify(oosView), { status: 200, headers: { "Content-Type": "application/json" } }),
    );

    render(<OosResearchView onBack={vi.fn()} />);
    fireEvent.change(screen.getByRole("textbox", { name: "OOS Protocol ID" }), {
      target: { value: "protocol-1" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Load official OOS" }));

    await waitFor(() => expect(screen.getByText("FINALIZED · READ ONLY")).toBeInTheDocument());
    expect(screen.getByText("12.0000%")).toBeInTheDocument();
    expect(screen.getAllByText("Not Evaluable").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Official backend series").length).toBe(2);
    expect(screen.queryByRole("button", { name: /rerun|select|recommend|winner/i })).not.toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
