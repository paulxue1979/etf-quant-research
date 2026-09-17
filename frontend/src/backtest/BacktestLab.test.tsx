import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { BacktestLab } from "./BacktestLab";
import { backtestApi } from "./api";

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
      cumulative_contributions: 0,
      total_capital_invested: 100000,
      investment_profit: 3000,
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

function researchRun(runId: string, strategyId: string) {
  const payload = runPayload();
  const analysis = payload.performance_analysis;
  return {
    backtest_run_id: runId,
    strategy_id: strategyId,
    strategy_version_id: `${strategyId}-v1`,
    strategy_version_content_hash: `hash-${strategyId}`,
    created_at: "2026-09-07T00:00:00Z",
    start_date: "2025-01-01",
    end_date: "2025-01-03",
    initial_capital: 100000,
    final_equity: 103000,
    price_field_used: "adjusted_close",
    engine_version: "phase-3.0",
    analysis_version: "phase-4i.0",
    configuration_snapshot: { execution_rule: "next_trading_day_open" },
    data_snapshot_reference: { QQQ: { source: "fixture" } },
    provenance: { source: "BacktestService" },
    metrics: {
      total_return: analysis.total_return,
      cagr: analysis.cagr,
      annualized_volatility: analysis.annualized_volatility,
      sharpe_ratio: analysis.sharpe_ratio,
      sortino_ratio: analysis.sortino_ratio,
      max_drawdown: analysis.max_drawdown,
      calmar_ratio: analysis.calmar_ratio,
      win_rate: analysis.trade_metrics.win_rate,
      profit_factor: analysis.trade_metrics.profit_factor,
      average_trade_return: analysis.trade_metrics.average_trade_return,
      best_trade: analysis.trade_metrics.best_trade,
      worst_trade: analysis.trade_metrics.worst_trade,
      average_holding_period: analysis.trade_metrics.average_holding_period,
      turnover: analysis.trade_metrics.turnover,
    },
  };
}

function researchPayload() {
  const runs = [researchRun("backtest-research-a", "allocation-a"), researchRun("backtest-research-b", "allocation-b")];
  return {
    comparable: true,
    incompatibility_reasons: [],
    runs,
    series: runs.map((run) => ({
      backtest_run_id: run.backtest_run_id,
      equity_curve: [
        { date: "2025-01-01", total_equity: 100000 },
        { date: "2025-01-03", total_equity: 103000 },
      ],
      drawdown_curve: [{ date: "2025-01-01", value: 0 }, { date: "2025-01-03", value: -0.01 }],
    })),
    provenance_notice: "Data provenance recorded; complete immutable market-data snapshot versioning is not yet implemented.",
  };
}

function researchHistory() {
  const items = [
    researchRun("backtest-research-a", "allocation-a"),
    researchRun("backtest-research-b", "allocation-b"),
    researchRun("backtest-research-c", "allocation-c"),
    researchRun("backtest-research-d", "allocation-d"),
    researchRun("backtest-research-e", "allocation-e"),
    researchRun("backtest-research-f", "allocation-f"),
    researchRun("backtest-research-g", "allocation-g"),
  ];
  return { items, total: items.length, limit: 50, offset: 0, sort_by: "created_at", order: "desc" };
}

function protocolDetail(status = "draft", candidateStatus?: "open" | "locked") {
  return {
    protocol: {
      protocol_id: "protocol-demo",
      protocol_version: 1,
      created_at: "2026-09-08T00:00:00Z",
      is_start_date: "2020-01-01",
      is_end_date: "2023-12-29",
      oos_start_date: "2024-01-02",
      oos_end_date: "2025-12-31",
      split_type: "holdout",
      split_policy: "calendar_date_non_overlapping",
      timezone: "America/New_York",
      gap_days: 0,
      embargo_days: 0,
      selection_rules: ["Human review of IS evidence only"],
      allowed_metrics: ["cagr"],
      forbidden_actions: ["oos_back_selection"],
      strategy_freeze_required: true,
      data_policy: {},
      execution_policy: { execution_rule: "next_trading_day_open" },
      evaluation_policy: { oos_selection_allowed: false },
      evaluation_config: {
        price_field_used: "adjusted_close",
        initial_capital: 100000,
        commission: { rate: 0, per_order: 0 },
        slippage: 0,
        execution_rule: "next_trading_day_open",
        fractional_shares: false,
        rebalance_policy: { frequency: "daily", threshold: null },
        engine_version: "phase-3.0",
      },
      provenance: {},
      status,
    },
    candidate_sets: candidateStatus ? [{
      candidate_set_id: "candidate-demo",
      protocol_id: "protocol-demo",
      strategy_version_ids: ["demo-v1"],
      strategy_version_content_hashes: { "demo-v1": "hash-demo" },
      created_at: "2026-09-08T00:00:00Z",
      status: candidateStatus,
    }] : [],
    selections: [],
    freezes: [],
    oos_evaluations: [],
    data_provenance_notice: "Data provenance is recorded from immutable backtest runs.",
  };
}

function mockBacktestApi() {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = String(input);
    if (url.endsWith("/strategies") && !url.includes("strategy-lab")) return new Response(JSON.stringify(catalog), { status: 200 });
    if (url.includes("/strategy-lab/strategies/demo/versions")) return new Response(JSON.stringify(versions), { status: 200 });
    if (url.includes("/research/backtests")) return new Response(JSON.stringify(researchHistory()), { status: 200 });
    if (url.endsWith("/research/protocols") && (!init || !init.method)) return new Response(JSON.stringify([]), { status: 200 });
    if (url.endsWith("/research/protocols") && init?.method === "POST") return new Response(JSON.stringify(protocolDetail()), { status: 201 });
    if (url.endsWith("/candidate-sets") && init?.method === "POST") return new Response(JSON.stringify(protocolDetail("draft", "open")), { status: 201 });
    if (url.includes("/candidate-sets/candidate-demo/lock") && init?.method === "POST") return new Response(JSON.stringify(protocolDetail("draft", "locked")), { status: 200 });
    if (url.includes("/transitions") && init?.method === "POST") return new Response(JSON.stringify(protocolDetail("frozen", "locked")), { status: 200 });
    if (url.endsWith("/research/comparisons") && init?.method === "POST") return new Response(JSON.stringify(researchPayload()), { status: 200 });
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

  it("loads saved history, limits selection to six, and displays a backend comparison", async () => {
    mockBacktestApi();
    render(<BacktestLab onBack={vi.fn()} />);

    await waitFor(() => expect(screen.getByText("Saved backtest runs")).toBeInTheDocument());
    expect(screen.getAllByText("hash-allocation-a").length).toBeGreaterThan(0);
    fireEvent.click(screen.getByRole("checkbox", { name: "Select research run backtest-research-a" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Select research run backtest-research-b" }));
    fireEvent.click(screen.getByRole("button", { name: "Compare selected" }));

    await waitFor(() => expect(screen.getByText("Strictly comparable")).toBeInTheDocument());
    expect(screen.getByRole("img", { name: "Equity curve comparison" })).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "Drawdown curve comparison" })).toBeInTheDocument();
    expect(screen.getByText(/complete immutable market-data snapshot versioning/)).toBeInTheDocument();
    expect(screen.getAllByText("N/A").length).toBeGreaterThan(0);

    for (const runId of ["backtest-research-c", "backtest-research-d", "backtest-research-e", "backtest-research-f"]) {
      fireEvent.click(screen.getByRole("checkbox", { name: `Select research run ${runId}` }));
    }
    expect(screen.getByRole("checkbox", { name: "Select research run backtest-research-g" })).toBeDisabled();
  });

  it("creates and locks an OOS research protocol before IS evaluation", async () => {
    const fetchMock = mockBacktestApi();
    render(<BacktestLab onBack={vi.fn()} />);

    await waitFor(() => expect(screen.getByRole("button", { name: "Create protocol" })).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Create protocol" }));
    await waitFor(() => expect(screen.getByText("DRAFT")).toBeInTheDocument());
    const recordCandidateSetButton = screen.getByRole("button", { name: "Record candidate set" });
    await waitFor(() => expect(recordCandidateSetButton).toBeEnabled());
    fireEvent.click(recordCandidateSetButton);
    await waitFor(() => expect(screen.getByRole("button", { name: "Lock candidate set" })).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Lock candidate set" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Freeze research protocol" })).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Freeze research protocol" }));
    await waitFor(() => expect(screen.getByText("FROZEN")).toBeInTheDocument());

    expect(fetchMock).toHaveBeenCalledWith(expect.stringContaining("/research/protocols"), expect.objectContaining({ method: "POST" }));
    expect(screen.getByText("Signal(T) to T+1 trading day open")).toBeInTheDocument();
    expect(screen.getByText("adjusted_close · $100,000 · next_trading_day_open")).toBeInTheDocument();
    expect(screen.getByText("Commission 0 / 0 · Slippage 0")).toBeInTheDocument();
  });

  it("preserves structured OOS rejection messages from the backend", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          detail: {
            code: "OOS_CONFIGURATION_MISMATCH",
            message: "OOS backtest configuration does not match the frozen protocol contract",
          },
        }),
        { status: 422 },
      ),
    );

    await expect(backtestApi.observeOos("protocol-demo", "freeze-demo", "run-demo"))
      .rejects.toMatchObject({
        kind: "server",
        message: "OOS backtest configuration does not match the frozen protocol contract",
      });
  });

  it("submits monthly capital contributions and renders capital accounting", async () => {
    const fetchMock = mockBacktestApi();
    render(<BacktestLab onBack={vi.fn()} />);

    await waitFor(() => expect(screen.getByRole("option", { name: /Demo strategy/ })).toBeInTheDocument());
    await waitFor(() => expect(screen.getByRole("option", { name: /v1/ })).toBeInTheDocument());
    fireEvent.click(screen.getByRole("checkbox", { name: "Enable contributions" }));
    fireEvent.change(screen.getByLabelText("Contribution frequency"), { target: { value: "monthly" } });
    fireEvent.change(screen.getByLabelText("Contribution amount USD"), { target: { value: "500" } });
    fireEvent.click(screen.getByRole("button", { name: "Run backtest" }));

    await waitFor(() => expect(screen.getByText("Capital accounting")).toBeInTheDocument());
    const request = fetchMock.mock.calls.find(([input, init]) =>
      String(input).endsWith("/backtests") && init?.method === "POST"
    );
    expect(JSON.parse(String(request?.[1]?.body))).toMatchObject({
      contribution_schedule: { frequency: "monthly", amount: "500" },
    });
    expect(screen.getByText("Cumulative contributions")).toBeInTheDocument();
    expect(screen.getByText("Investment profit")).toBeInTheDocument();
  });

  it("rejects invalid enabled contribution input before calling the backend", async () => {
    const fetchMock = mockBacktestApi();
    render(<BacktestLab onBack={vi.fn()} />);

    await waitFor(() => expect(screen.getByRole("option", { name: /Demo strategy/ })).toBeInTheDocument());
    await waitFor(() => expect(screen.getByRole("option", { name: /v1/ })).toBeInTheDocument());
    fireEvent.click(screen.getByRole("checkbox", { name: "Enable contributions" }));
    const contributionAmount = screen.getByLabelText("Contribution amount USD");
    fireEvent.change(contributionAmount, { target: { value: "0" } });
    expect(contributionAmount).toBeInvalid();
    fireEvent.click(screen.getByRole("button", { name: "Run backtest" }));

    expect(fetchMock.mock.calls.some(([input, init]) => String(input).endsWith("/backtests") && init?.method === "POST")).toBe(false);
  });
});
