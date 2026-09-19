import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";

import { MultiStrategyMetricsTable } from "./MultiStrategyMetricsTable";
import type { ComparisonChartSeries } from "./comparisonCharts";
import type { ComparisonRunIdentity, ResearchComparison } from "./types";

afterEach(cleanup);

function metric(value: unknown, status: "available" | "not_available" | "not_evaluable" = "available", reason: string | null = null) {
  return { value, status, reason };
}

function identity(id: string, cagr: number | null): ComparisonRunIdentity {
  return {
    backtest_run_id: id,
    strategy_id: `strategy-${id}`,
    strategy_version_id: `version-${id}`,
    strategy_name: `Strategy ${id}`,
    strategy_version: "v1",
    short_display_label: `Strategy ${id}`,
    start_date: "2000-01-03",
    end_date: "2025-01-03",
    asset_universe: ["QQQ"],
    metrics_status: "included",
    metrics: {
      cagr: cagr === null ? metric(null, "not_evaluable", "CAGR unavailable") : metric(cagr),
      total_twr_return: metric(1.25),
      max_drawdown: metric(-0.2),
      sharpe_ratio: metric(1.2),
      sortino_ratio: metric(1.8),
      calmar_ratio: metric(0.6),
      exposure: metric({ average_gross_exposure: 0.75 }),
      portfolio_turnover: metric(0.32),
      xirr: metric(0.09),
      trade_count: metric(14),
      holding_period_count: metric(8),
    },
  };
}

function setup() {
  const runs = [identity("low", 0.05), identity("missing", null), identity("high", 0.2)];
  const comparison = { runs } as ResearchComparison;
  const colors: ComparisonChartSeries[] = runs.map((run, index) => ({
    runId: run.backtest_run_id,
    label: run.short_display_label,
    color: ["#111111", "#222222", "#333333"][index],
    status: "available",
    points: [],
  }));
  return { comparison, colors };
}

describe("MultiStrategyMetricsTable", () => {
  it("renders all canonical metric groups and formats strategy, behavior, and investor values", () => {
    const { comparison, colors } = setup();
    render(<MultiStrategyMetricsTable comparison={comparison} colors={colors} hiddenRunIds={new Set()} focusRunId={null} />);

    for (const label of ["CAGR", "Total TWR Return", "Max Drawdown", "Sharpe", "Sortino", "Calmar", "Exposure", "Turnover", "XIRR", "Trade Count", "Holding Period Count"]) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
    const row = screen.getByRole("row", { name: /Strategy low/ });
    for (const value of ["5.00%", "125.00%", "-20.00%", "1.20", "1.80", "0.60", "75.00%", "32.00%", "9.00%", "14", "8"]) {
      expect(row).toHaveTextContent(value);
    }
    expect(screen.getByText(/XIRR is investor experience/)).toBeInTheDocument();
  });

  it("sorts by canonical CAGR, leaves N/A last, and keeps run color identity stable", async () => {
    const user = userEvent.setup();
    const { comparison, colors } = setup();
    render(<MultiStrategyMetricsTable comparison={comparison} colors={colors} hiddenRunIds={new Set()} focusRunId={null} />);

    await user.click(screen.getByRole("button", { name: "Sort by CAGR" }));
    let rows = screen.getAllByRole("row").slice(1);
    expect(rows.map((row) => within(row).getByRole("rowheader").textContent)).toEqual(["Strategy high", "Strategy low", "Strategy missing"]);
    expect(within(rows[0]).getByRole("rowheader").querySelector("i")).toHaveStyle({ background: "#333333" });
    expect(rows[2]).toHaveTextContent("N/A");
    expect(within(rows[2]).getByTitle("CAGR unavailable")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Sort by CAGR" }));
    rows = screen.getAllByRole("row").slice(1);
    expect(rows.map((row) => within(row).getByRole("rowheader").textContent)).toEqual(["Strategy low", "Strategy high", "Strategy missing"]);
  });

  it("retains hidden rows and highlights the focused run", () => {
    const { comparison, colors } = setup();
    render(<MultiStrategyMetricsTable comparison={comparison} colors={colors} hiddenRunIds={new Set(["low"])} focusRunId="high" />);

    expect(screen.getByRole("row", { name: /Strategy low/ })).toHaveClass("is-hidden");
    expect(screen.getByRole("row", { name: /Strategy low/ })).toHaveTextContent("Hidden");
    expect(screen.getByRole("row", { name: /Strategy high/ })).toHaveClass("is-focused");
    expect(screen.getByRole("row", { name: /Strategy high/ })).toHaveTextContent("Focus");
  });
});
