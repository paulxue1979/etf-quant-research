import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("./BacktestReportCharts", () => ({
  BacktestReportCharts: ({ error }: { error?: string | null }) => (
    <section aria-label="Core financial charts">{error}</section>
  ),
}));

import { BacktestLab } from "./BacktestLab";

vi.mock("./MultiStrategyTwrChart", () => ({
  MultiStrategyTwrChart: ({ visibleSeries }: { visibleSeries: unknown[] }) => <section aria-label="Multi-strategy TWR comparison" data-series-count={visibleSeries.length} />,
  MultiStrategyDrawdownChart: ({ visibleSeries }: { visibleSeries: unknown[] }) => <section aria-label="Multi-strategy drawdown comparison" data-series-count={visibleSeries.length} />,
}));
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
    comparison_schema_version: "2.0",
    ordering: "request_order",
    include: { twr: true, drawdown: true, portfolio_value: false, metrics: true },
    compatibility: {
      twr: { status: "COMPARABLE", reason_codes: [], human_readable_reasons: [], dimensions: {} },
    },
    runs: runs.map((run) => ({
      ...run,
      strategy_name: run.strategy_id,
      strategy_version: "v1",
      short_display_label: `${run.strategy_id} · v1 · ${run.backtest_run_id.slice(-8)}`,
      asset_universe: ["QQQ"],
      metrics_status: "included",
      metrics: {
        cagr: run.metrics.cagr,
        total_twr_return: run.metrics.total_return,
        max_drawdown: run.metrics.max_drawdown,
        sharpe_ratio: run.metrics.sharpe_ratio,
        sortino_ratio: run.metrics.sortino_ratio,
        calmar_ratio: run.metrics.calmar_ratio,
        exposure: { value: { average_gross_exposure: 0.75 }, status: "available", reason: null },
        portfolio_turnover: run.metrics.turnover,
        xirr: { value: 0.08, status: "available", reason: null },
        trade_count: { value: 2, status: "available", reason: null },
        holding_period_count: { value: 2, status: "available", reason: null },
      },
    })),
    series: runs.map((run) => ({
      backtest_run_id: run.backtest_run_id,
      strategy_version_id: run.strategy_version_id,
      start_date: run.start_date,
      end_date: run.end_date,
      twr: { status: "available", unit: "base_100_wealth_index", points: [{ date: "2025-01-01", value: 100 }, { date: "2025-01-03", value: 103 }] },
      drawdown: { status: "available", unit: "decimal", points: [{ date: "2025-01-01", value: 0 }, { date: "2025-01-03", value: -0.05 }] },
      portfolio_value: { status: "excluded", points: [] },
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
    researchRun("backtest-research-h", "allocation-h"),
    researchRun("backtest-research-i", "allocation-i"),
    researchRun("backtest-research-j", "allocation-j"),
    researchRun("backtest-research-k", "allocation-k"),
  ];
  return { items, total: items.length, limit: 50, offset: 0, sort_by: "created_at", order: "desc" };
}

function reportPayload() {
  return {
    report_schema_version: "1.0",
    identity: { backtest_run_id: "backtest-demo-1", strategy_id: "demo", strategy_version_id: "demo-v1", strategy_version_content_hash: "hash-demo", created_at: "2026-09-07T00:00:00Z", engine_version: "phase-3.0" },
    availability: { contributions: "available", xirr: "available" },
    summary_period: { start_date: "2025-01-01", end_date: "2025-01-03" },
    summary: { account: { ending_value: 103000, unit: "USD" }, strategy_performance: {}, benchmark: { status: "not_available" } },
    capital: { initial_capital: 100000, cumulative_contributions: 0, total_capital_invested: 100000 },
    profit: { investment_profit: 3000 },
    performance: { twr_total_return: { value: 0.03, status: "available", reason: null } },
    investor_experience: { xirr: { value: 0.03, status: "available", reason: null }, xirr_unit: "annualized decimal return" },
    series_metadata: {},
    contributions: [],
    contribution_report: {
      status: "available",
      schedule: { enabled: false, frequency: null, amount: null, requested_date: null, currency: "USD", requested_date_semantics: null },
      event_count: 0,
      events: [],
      integrity: { status: "consistent", event_amount_total: "0", external_cash_flow_total: "0", cumulative_contributions: 0 },
    },
    strategy_provenance: {},
    allocations: {},
    holdings: {},
    trades: {},
    configuration: {},
    provenance: {},
  };
}

function reportSeriesPayload() {
  const points = [{ date: "2025-01-01", value: 100000 }, { date: "2025-01-03", value: 103000 }];
  return {
    report_schema_version: "1.0",
    identity: { backtest_run_id: "backtest-demo-1", strategy_version_id: "demo-v1" },
    summary_period: { start_date: "2025-01-01", end_date: "2025-01-03" },
    window: { from: null, to: null },
    series: {
      equity: { status: "available", points },
      capital: { status: "available", points: [{ date: "2025-01-01", value: 100000 }, { date: "2025-01-03", value: 100000 }] },
      twr: { status: "available", points: [{ date: "2025-01-01", value: 1 }, { date: "2025-01-03", value: 1.03 }] },
      drawdown: { status: "available", points: [{ date: "2025-01-01", value: 0 }, { date: "2025-01-03", value: 0 }] },
    },
  };
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

function mockBacktestApi(options: { seriesFails?: boolean; paginatedHistory?: boolean } = {}) {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = String(input);
    if (url.endsWith("/strategies") && !url.includes("strategy-lab")) return new Response(JSON.stringify(catalog), { status: 200 });
    if (url.includes("/strategy-lab/strategies/demo/versions")) return new Response(JSON.stringify(versions), { status: 200 });
    if (url.includes("/report/series?") && options.seriesFails) return new Response(JSON.stringify({ detail: { message: "report series unavailable" } }), { status: 503 });
    if (url.includes("/report/series?")) return new Response(JSON.stringify(reportSeriesPayload()), { status: 200 });
    if (url.includes("/report/markers")) return new Response(JSON.stringify({
      marker_schema_version: "1.0",
      identity: { backtest_run_id: "backtest-demo-1", strategy_version_id: "demo-v1" },
      source: "derived_from_immutable_backtest_run",
      persisted: false,
      filters: { types: [], from: null, to: null, group_same_day: true, major_only: false, major_threshold: null },
      counts: { markers: 0, groups: 0, by_type: {}, hidden: 0, truncated: false },
      markers: [],
      groups: [],
    }), { status: 200 });
    if (url.endsWith("/report")) return new Response(JSON.stringify(reportPayload()), { status: 200 });
    if (url.includes("/report/holdings")) return new Response(JSON.stringify({
      report_schema_version: "1.0",
      identity: { backtest_run_id: "backtest-demo-1", strategy_version_id: "demo-v1" },
      status: "available",
      source: "BacktestResult.holding_segments",
      summary: { open_count: 0, closed_count: 0 },
      total: 0,
      limit: 25,
      offset: 0,
      filters: { status: "ALL", symbol: null },
      sort: { by: "entry_date", order: "asc" },
      duration_basis: "calendar_days_between_execution_dates",
      metric_availability: {},
      items: [],
    }), { status: 200 });
    if (url.includes("/research/backtests")) {
      const history = researchHistory();
      if (options.paginatedHistory) {
        const offset = Number(new URL(url).searchParams.get("offset") ?? 0);
        const items = offset === 50 ? [researchRun("backtest-research-z", "allocation-z")] : history.items;
        return new Response(JSON.stringify({ ...history, items, total: 51, offset }), { status: 200 });
      }
      return new Response(JSON.stringify(history), { status: 200 });
    }
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
  it("keeps capital contributions off and hides schedule inputs by default", async () => {
    mockBacktestApi();
    render(<BacktestLab onBack={vi.fn()} />);

    await waitFor(() => expect(screen.getByRole("option", { name: /Demo strategy/ })).toBeInTheDocument());
    const toggle = screen.getByRole("checkbox", { name: "Enable contributions" });
    expect(toggle).not.toBeChecked();
    expect(screen.getByText("Contributions = Off")).toBeInTheDocument();
    expect(screen.getByText("No external cash flows will be added. Backtest configuration remains unchanged.")).toBeInTheDocument();
    expect(screen.queryByLabelText("Contribution frequency")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Contribution amount USD")).not.toBeInTheDocument();
    expect(toggle).toHaveAccessibleDescription("Add external cash flows during this backtest.");
  });

  it("loads strategies and versions, submits inputs, and renders backend results", async () => {
    const fetchMock = mockBacktestApi();
    render(<BacktestLab onBack={vi.fn()} />);

    await waitFor(() => expect(screen.getByRole("option", { name: /Demo strategy/ })).toBeInTheDocument());
    await waitFor(() => expect(screen.getByRole("option", { name: /v1/ })).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Run backtest" }));

    await waitFor(() => expect(screen.getByText("backtest-demo-1")).toBeInTheDocument());
    expect(screen.getAllByText("3.00%", { selector: "strong" }).length).toBeGreaterThan(0);
    expect(screen.getByRole("img", { name: "Equity curve" })).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "Drawdown curve" })).toBeInTheDocument();
    expect(screen.getAllByText("QQQ").length).toBeGreaterThan(0);
    expect(fetchMock).toHaveBeenCalledWith(expect.stringContaining("/backtests"), expect.objectContaining({ method: "POST" }));
  });

  it("submits an explicit benchmark symbol with the research run", async () => {
    const fetchMock = mockBacktestApi();
    render(<BacktestLab onBack={vi.fn()} />);

    await waitFor(() => expect(screen.getByRole("option", { name: /Demo strategy/ })).toBeInTheDocument());
    await waitFor(() => expect(screen.getByRole("option", { name: /v1/ })).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText("Benchmark symbol"), { target: { value: "SPY" } });
    fireEvent.click(screen.getByRole("button", { name: "Run backtest" }));

    await waitFor(() => expect(screen.getByText("Capital Summary")).toBeInTheDocument());
    const request = fetchMock.mock.calls.find(([input, init]) =>
      String(input).endsWith("/backtests") && init?.method === "POST"
    );
    expect(JSON.parse(String(request?.[1]?.body))).toMatchObject({ benchmark_symbol: "SPY" });
  });

  it("keeps report summary available when the series endpoint fails", async () => {
    mockBacktestApi({ seriesFails: true });
    render(<BacktestLab onBack={vi.fn()} />);

    await waitFor(() => expect(screen.getByRole("option", { name: /Demo strategy/ })).toBeInTheDocument());
    await waitFor(() => expect(screen.getByRole("option", { name: /v1/ })).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Run backtest" }));

    await waitFor(() => expect(screen.getByText("Capital Summary")).toBeInTheDocument());
    expect(screen.getByText("report series unavailable")).toBeInTheDocument();
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

  it("loads saved history, preserves selection order, limits selection to ten, and displays v2 TWR", async () => {
    const fetchMock = mockBacktestApi();
    render(<BacktestLab onBack={vi.fn()} />);

    await waitFor(() => expect(screen.getByText("Saved backtest runs")).toBeInTheDocument());
    expect(screen.getAllByText("hash-allocation-a").length).toBeGreaterThan(0);
    fireEvent.click(screen.getByRole("checkbox", { name: "Select research run backtest-research-a" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Select research run backtest-research-b" }));
    fireEvent.click(screen.getByRole("button", { name: "Compare selected" }));

    await waitFor(() => expect(screen.getByText("Comparable TWR context")).toBeInTheDocument());
    expect(screen.getByLabelText("Multi-strategy TWR comparison")).toHaveAttribute("data-series-count", "2");
    expect(screen.getByText(/complete immutable market-data snapshot versioning/)).toBeInTheDocument();
    const comparisonRequest = fetchMock.mock.calls.find(([input, init]) => String(input).endsWith("/research/comparisons") && init?.method === "POST");
    expect(JSON.parse(String(comparisonRequest?.[1]?.body))).toEqual({
      backtest_run_ids: ["backtest-research-a", "backtest-research-b"],
      include: {
        twr: true,
        drawdown: true,
        portfolio_value: true,
        capital_invested: true,
        investment_profit: true,
        metrics: true,
      },
    });

    for (const runId of ["backtest-research-c", "backtest-research-d", "backtest-research-e", "backtest-research-f", "backtest-research-g", "backtest-research-h", "backtest-research-i", "backtest-research-j"]) {
      fireEvent.click(screen.getByRole("checkbox", { name: `Select research run ${runId}` }));
    }
    expect(screen.getByRole("checkbox", { name: "Select research run backtest-research-k" })).toBeDisabled();
    expect(screen.getByRole("status")).toHaveTextContent("Maximum 10 runs selected");
  });

  it("pages research history in the API while preserving selected run ids", async () => {
    const fetchMock = mockBacktestApi({ paginatedHistory: true });
    render(<BacktestLab onBack={vi.fn()} />);

    await waitFor(() => expect(screen.getByText("1-11 of 51")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("checkbox", { name: "Select research run backtest-research-a" }));
    fireEvent.click(screen.getByRole("button", { name: "Next" }));

    await waitFor(() => expect(screen.getByText("backtest-research-z")).toBeInTheDocument());
    expect(screen.getByText("1 / 10 selected")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("offset=50"),
      expect.any(Object),
    );
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
    expect(screen.getByText("Contributions = On")).toBeInTheDocument();
    expect(screen.getByText("Contribution is external cash flow, not a strategy signal.")).toBeInTheDocument();
    expect(screen.getByText(/It enters Cash, then the current strategy target determines rebalance and execution/)).toBeInTheDocument();
    expect(screen.getByText(/It does not automatically buy QQQ/)).toBeInTheDocument();
    expect(screen.getByText(/Under a Cash 100% target, it remains cash/)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Contribution frequency"), { target: { value: "monthly" } });
    fireEvent.change(screen.getByLabelText("Contribution amount USD"), { target: { value: "500" } });
    expect(screen.getByText(/Monthly requests use month start and map to the next common trading date/)).toBeInTheDocument();
    expect(screen.getByText(/Effective Date is produced by the backtest and is not an editable input/)).toBeInTheDocument();
    expect(screen.queryByLabelText("Contribution requested date")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Run backtest" }));

    await waitFor(() => expect(screen.getByText("Capital Summary")).toBeInTheDocument());
    const request = fetchMock.mock.calls.find(([input, init]) =>
      String(input).endsWith("/backtests") && init?.method === "POST"
    );
    expect(JSON.parse(String(request?.[1]?.body))).toMatchObject({
      contribution_schedule: { frequency: "monthly", amount: "500" },
    });
    expect(screen.getByText("Cumulative Contributions")).toBeInTheDocument();
    expect(screen.getByText("Investment Profit")).toBeInTheDocument();
  });

  it("shows one-time date semantics and preserves schedule values across toggles and rerenders", async () => {
    mockBacktestApi();
    const view = render(<BacktestLab onBack={vi.fn()} />);

    await waitFor(() => expect(screen.getByRole("option", { name: /Demo strategy/ })).toBeInTheDocument());
    const toggle = screen.getByRole("checkbox", { name: "Enable contributions" });
    toggle.focus();
    expect(toggle).toHaveFocus();
    fireEvent.click(toggle);
    fireEvent.change(screen.getByLabelText("Contribution frequency"), { target: { value: "one_time" } });
    fireEvent.change(screen.getByLabelText("Contribution amount USD"), { target: { value: "750" } });
    fireEvent.change(screen.getByLabelText("Contribution requested date"), { target: { value: "2025-02-01" } });

    expect(screen.getByText(/A Requested Date that is not a common trading date maps to the next common trading date/)).toBeInTheDocument();
    fireEvent.click(toggle);
    expect(screen.queryByLabelText("Contribution amount USD")).not.toBeInTheDocument();
    fireEvent.click(toggle);
    view.rerender(<BacktestLab onBack={vi.fn()} />);

    expect(screen.getByLabelText("Contribution frequency")).toHaveValue("one_time");
    expect(screen.getByLabelText("Contribution amount USD")).toHaveValue(750);
    expect(screen.getByLabelText("Contribution requested date")).toHaveValue("2025-02-01");
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
