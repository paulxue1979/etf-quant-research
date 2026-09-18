import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";

import { CapitalResearchPanel } from "./CapitalResearchPanel";
import type { BacktestReport } from "./types";

function report(): BacktestReport {
  return {
    report_schema_version: "1.0",
    identity: {
      backtest_run_id: "run-1",
      strategy_id: "strategy-1",
      strategy_version_id: "version-1",
      strategy_version_content_hash: "hash-1",
      created_at: "2026-01-01T00:00:00Z",
      engine_version: "phase-3.0",
    },
    availability: { contributions: "available", xirr: "available" },
    summary_period: { start_date: "2026-01-01", end_date: "2026-03-31" },
    summary: {
      account: { ending_value: 13_000, unit: "USD" },
      strategy_performance: {},
      benchmark: {
        status: "available",
        ending_value: { value: 12_500, status: "available", reason: null },
        provenance: { same_contribution_schedule: true },
      },
    },
    capital: {
      initial_capital: 10_000,
      cumulative_contributions: 2_000,
      total_capital_invested: 12_000,
    },
    profit: { investment_profit: 1_000 },
    performance: {
      twr_total_return: { value: 0.1, status: "available", reason: null },
    },
    investor_experience: {
      xirr: { value: 0.08, status: "available", reason: null },
      xirr_unit: "annualized decimal return",
    },
    contribution_report: {
      status: "available",
      schedule: {
        enabled: true,
        frequency: "monthly",
        amount: "1000",
        requested_date: null,
        currency: "USD",
        requested_date_semantics: "month_start",
      },
      event_count: 2,
      integrity: {
        status: "consistent",
        event_amount_total: "2000",
        cumulative_contributions: 2_000,
      },
      events: [
        {
          sequence: 1,
          requested_date: "2026-02-01",
          effective_date: "2026-02-02",
          amount: "1000",
          currency: "USD",
          frequency: "monthly",
          source: "ContributionEvent",
          strategy_signal: false,
          deployment: {
            cause: "contribution",
            status: "rebalance_executed",
            order_count: 1,
            fill_count: 1,
            symbols: ["QQQ"],
          },
        },
        {
          sequence: 2,
          requested_date: "2026-03-01",
          effective_date: "2026-03-02",
          amount: "1000",
          currency: "USD",
          frequency: "monthly",
          source: "ContributionEvent",
          strategy_signal: false,
          deployment: {
            cause: "contribution",
            status: "no_contribution_rebalance_execution",
            order_count: 0,
            fill_count: 0,
            symbols: [],
          },
        },
      ],
    },
    contributions: [],
    series_metadata: {},
    strategy_provenance: {},
    allocations: {},
    holdings: {},
    trades: {},
    configuration: {},
    provenance: {},
  };
}

afterEach(cleanup);

describe("CapitalResearchPanel", () => {
  it("separates capital, profit, strategy TWR, and investor XIRR", () => {
    render(<CapitalResearchPanel report={report()} />);

    expect(screen.getByText("Initial Capital").nextSibling).toHaveTextContent("$10,000.00");
    expect(screen.getByText("Cumulative Contributions").nextSibling).toHaveTextContent("$2,000.00");
    expect(screen.getByText("Total Capital Invested").nextSibling).toHaveTextContent("$12,000.00");
    expect(screen.getByText("Ending Portfolio Value").nextSibling).toHaveTextContent("$13,000.00");
    expect(screen.getByText("Investment Profit").nextSibling).toHaveTextContent("$1,000.00");
    expect(screen.getByRole("heading", { name: "Strategy Performance (TWR)" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Investor Experience (XIRR)" })).toBeInTheDocument();
    expect(screen.getByText("10.00%")).toBeInTheDocument();
    expect(screen.getByText("8.00%")).toBeInTheDocument();
    expect(screen.getByText("Same cash-flow schedule")).toBeInTheDocument();
  });

  it("shows requested and effective dates without promoting contributions to signals", () => {
    render(<CapitalResearchPanel report={report()} />);

    expect(screen.getByText("Monthly Contribution")).toBeInTheDocument();
    expect(screen.getByText("Month Start")).toBeInTheDocument();
    expect(screen.getByText("2026-02-01")).toBeInTheDocument();
    expect(screen.getByText("2026-02-02")).toBeInTheDocument();
    expect(screen.getAllByText("NONE")).toHaveLength(2);
    expect(screen.getByText("Portfolio rebalance triggered")).toBeInTheDocument();
    expect(screen.getByText("No contribution-triggered rebalance execution")).toBeInTheDocument();
  });

  it("presents one-time configuration and negative dollar profit without calling it return", () => {
    const value = report();
    value.contribution_report.schedule = {
      enabled: true,
      frequency: "one_time",
      amount: "2000",
      requested_date: "2026-02-01",
      currency: "USD",
      requested_date_semantics: "explicit_date",
    };
    value.summary.account = { ending_value: 9_000, unit: "USD" };
    value.capital.total_capital_invested = 12_000;
    value.profit.investment_profit = -3_000;
    render(<CapitalResearchPanel report={value} />);

    expect(screen.getByText("One-Time Contribution")).toBeInTheDocument();
    expect(screen.getAllByText("2026-02-01").length).toBeGreaterThan(0);
    expect(screen.getByText("-$3,000.00")).toBeInTheDocument();
  });

  it("distinguishes no contributions from unavailable legacy provenance", () => {
    const empty = report();
    empty.capital.cumulative_contributions = 0;
    empty.capital.total_capital_invested = 10_000;
    empty.contribution_report = {
      ...empty.contribution_report,
      schedule: { enabled: false, frequency: null, amount: null, requested_date: null, currency: "USD", requested_date_semantics: null },
      event_count: 0,
      events: [],
    };
    const { rerender } = render(<CapitalResearchPanel report={empty} />);
    expect(screen.getByText("No external contributions")).toBeInTheDocument();

    rerender(<CapitalResearchPanel report={{ ...empty, contribution_report: { ...empty.contribution_report, status: "not_available", reason: "legacy contribution provenance was not persisted" } }} />);
    expect(screen.getByText(/legacy contribution provenance/)).toBeInTheDocument();
    expect(screen.queryByText("No external contributions")).not.toBeInTheDocument();
  });

  it("renders not-evaluable XIRR with its backend reason and paginates long timelines", async () => {
    const value = report();
    value.investor_experience.xirr = {
      value: null,
      status: "not_evaluable",
      reason: "XIRR requires dated positive and negative cash flows",
    };
    value.contribution_report.events = Array.from({ length: 300 }, (_, index) => ({
      ...value.contribution_report.events[index % 2],
      sequence: index + 1,
      requested_date: `2026-${String((index % 9) + 1).padStart(2, "0")}-01`,
      effective_date: `2026-${String((index % 9) + 1).padStart(2, "0")}-02`,
    }));
    value.contribution_report.event_count = 300;
    const user = userEvent.setup();
    render(<CapitalResearchPanel report={value} />);

    expect(screen.getByText("Not evaluable")).toBeInTheDocument();
    expect(screen.getByText(/XIRR requires dated/)).toBeInTheDocument();
    expect(screen.getByText("1–25 of 300")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Next contribution page" }));
    expect(screen.getByText("26–50 of 300")).toBeInTheDocument();
  });

  it("keeps loading, error, and non-finite values explicit", () => {
    const { rerender } = render(<CapitalResearchPanel report={null} loading />);
    expect(screen.getByText("Loading capital research...")).toBeInTheDocument();

    rerender(<CapitalResearchPanel report={null} error="capital report unavailable" />);
    expect(screen.getByText("capital report unavailable")).toBeInTheDocument();

    const value = report();
    value.profit.investment_profit = Number.NaN;
    rerender(<CapitalResearchPanel report={value} />);
    expect(screen.queryByText("NaN")).not.toBeInTheDocument();
    expect(screen.getAllByText("Not available").length).toBeGreaterThan(0);
  });
});
