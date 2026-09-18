import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { HoldingPeriodReport } from "./HoldingPeriodReport";

const available = {
  report_schema_version: "1.0",
  identity: { backtest_run_id: "run-1", strategy_version_id: "version-1" },
  status: "available",
  source: "BacktestResult.holding_segments",
  summary: { open_count: 1, closed_count: 1 },
  total: 2,
  limit: 25,
  offset: 0,
  filters: { status: "ALL", symbol: null },
  sort: { by: "entry_date", order: "asc" },
  duration_basis: "calendar_days_between_execution_dates",
  metric_availability: {
    mfe: { status: "not_available", value: null, reason: "price path not persisted" },
    mae: { status: "not_available", value: null, reason: "price path not persisted" },
    holding_drawdown: { status: "not_available", value: null, reason: "price path not persisted" },
  },
  items: [
    {
      holding_id: "holding-open",
      lot_id: "lot-1",
      symbol: "SGOV",
      status: "OPEN",
      quantity: 25,
      entry_fill_id: "buy-2",
      entry_signal_date: null,
      entry_execution_date: "2025-01-03",
      entry_price: 100,
      entry_execution_cause: "contribution",
      entry_matched_rule_id: null,
      entry_allocation_source: null,
      exit_fill_id: null,
      exit_signal_date: null,
      exit_execution_date: null,
      exit_price: null,
      exit_execution_cause: null,
      exit_matched_rule_id: null,
      exit_allocation_source: null,
      report_end_date: "2025-01-10",
      ending_price: 101,
      market_value: 2525,
      holding_days: 7,
      holding_days_basis: "calendar_days",
      realized_pnl: null,
      unrealized_pnl: 25,
      pnl: 25,
      pnl_type: "unrealized",
      holding_return: 0.01,
    },
    {
      holding_id: "holding-closed",
      lot_id: "lot-2",
      symbol: "QQQ",
      status: "CLOSED",
      quantity: 40,
      entry_fill_id: "buy-1",
      entry_signal_date: "2025-01-02",
      entry_execution_date: "2025-01-03",
      entry_price: 100,
      entry_execution_cause: "target",
      entry_matched_rule_id: "buy",
      entry_allocation_source: "rule_match",
      exit_fill_id: "sell-1",
      exit_signal_date: "2025-01-08",
      exit_execution_date: "2025-01-09",
      exit_price: 110,
      exit_execution_cause: "target",
      exit_matched_rule_id: "sell",
      exit_allocation_source: "rule_match",
      report_end_date: null,
      ending_price: null,
      market_value: null,
      holding_days: 6,
      holding_days_basis: "calendar_days",
      realized_pnl: 400,
      unrealized_pnl: null,
      pnl: 400,
      pnl_type: "realized",
      holding_return: 0.1,
    },
  ],
};

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("HoldingPeriodReport", () => {
  it("renders OPEN and CLOSED lots without inventing a contribution signal", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify(available), { status: 200 }),
    );

    render(<HoldingPeriodReport backtestRunId="run-1" />);

    expect(await screen.findByText("SGOV")).toBeInTheDocument();
    expect(screen.getByText("QQQ")).toBeInTheDocument();
    expect(screen.getByText("OPEN")).toBeInTheDocument();
    expect(screen.getByText("CLOSED")).toBeInTheDocument();
    expect(screen.getByTestId("holding-open-entry-signal")).toHaveTextContent("N/A");
    expect(screen.getByTestId("holding-open-exit-execution")).toHaveTextContent("—");
    expect(screen.getByText("CONTRIBUTION")).toBeInTheDocument();
  });

  it("sends backend status, symbol, sorting, and pagination controls", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async () =>
      new Response(JSON.stringify({ ...available, total: 30 }), { status: 200 }),
    );
    const user = userEvent.setup();
    render(<HoldingPeriodReport backtestRunId="run-1" />);
    await screen.findByText("SGOV");

    await user.selectOptions(screen.getByLabelText("Holding status"), "OPEN");
    await user.selectOptions(screen.getByLabelText("Holding sort"), "pnl");
    await user.type(screen.getByLabelText("Holding symbol"), "sgov");
    await user.click(screen.getByRole("button", { name: "Apply" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(4));

    const lastUrl = String(fetchMock.mock.calls.at(-1)?.[0]);
    expect(lastUrl).toContain("status=OPEN");
    expect(lastUrl).toContain("symbol=SGOV");
    expect(lastUrl).toContain("sort_by=pnl");

    await user.click(screen.getByRole("button", { name: "Next" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(5));
    expect(String(fetchMock.mock.calls.at(-1)?.[0])).toContain("offset=25");
  });

  it("renders legacy provenance as unavailable instead of reconstructing lots", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({
        ...available,
        status: "not_available",
        reason: "canonical FIFO holding provenance was not persisted for this legacy run",
        summary: { open_count: null, closed_count: null },
        total: 0,
        items: [],
      }), { status: 200 }),
    );

    render(<HoldingPeriodReport backtestRunId="legacy-run" />);

    expect(await screen.findByText(/canonical FIFO holding provenance/)).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });
});
