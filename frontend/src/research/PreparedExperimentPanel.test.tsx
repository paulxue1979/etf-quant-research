import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { researchApi } from "./api";
import { canApprovePreparedExperiment, PreparedExperimentPanel } from "./PreparedExperimentPanel";
import type { PendingExperimentConfiguration } from "./types";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

function plan(overrides: Partial<PendingExperimentConfiguration> = {}): PendingExperimentConfiguration {
  return {
    config_id: "experiment-config-1", configuration_hash: "h".repeat(64), source: "CODEX_MCP",
    strategy_id: "strategy-1", strategy_name: "Demo", strategy_version_id: "strategy-1-v1", strategy_version_hash: "v".repeat(64),
    protocol_id: "protocol-1", requested_is_start: "2020-01-01", requested_is_end: "2020-12-31",
    oos_start: "2021-01-01", oos_end: "2021-12-31", initial_capital: 10000,
    axes: [{ semantic_target_id: "transition_value_threshold", unit: "percent", values: [-20, -25], canonical_values: [-0.2, -0.25], value_type: "relative_threshold" }],
    candidate_count: 2, experiment_id: "experiment-1", experiment_snapshot: { backtest_configuration: { price_field_used: "adjusted_close", execution_rule: "next_trading_day_open", commission: { rate: 0, per_order: 0 }, slippage: 0, rebalance_policy: { frequency: "daily", threshold: null }, fractional_shares: false } }, validation_status: "READY", validation_issues: [],
    grid_preflight: { status: "ready", theoretical_count: 2, executable_count: 2, pruned_count: 0, duplicate_count: 0 },
    invalid_candidates: [], data_availability: { status: "READY", effective_start_expected: "2020-01-02", effective_end_expected: "2020-12-31", blocking_issues: [] },
    approval_status: "PROPOSED", execution_status: "NOT_STARTED", error_code: null,
    ...overrides,
  };
}

describe("Codex prepared experiment approval gate", () => {
  it("enables a fully ready proposal", async () => {
    vi.spyOn(researchApi, "listPreparedExperiments").mockResolvedValue([plan()]);
    render(<PreparedExperimentPanel versionId="strategy-1-v1" onOpenResults={() => {}} />);
    expect(await screen.findByRole("button", { name: "Approve & Run IS Grid" })).toBeEnabled();
    expect(canApprovePreparedExperiment(plan())).toBe(true);
  });

  it("disables BLOCKED, pruned, and rejected configurations", async () => {
    const blocked = plan({ validation_status: "BLOCKED", validation_issues: ["DATA_PREFLIGHT_BLOCKED"], data_availability: { status: "BLOCKED", effective_start_expected: null, effective_end_expected: null, blocking_issues: ["CACHE_MISS"] } });
    vi.spyOn(researchApi, "listPreparedExperiments").mockResolvedValue([blocked]);
    render(<PreparedExperimentPanel versionId="strategy-1-v1" onOpenResults={() => {}} />);
    await waitFor(() => expect(screen.getByRole("button", { name: "Approve & Run IS Grid" })).toBeDisabled());
    expect(screen.getByText("DATA_PREFLIGHT_BLOCKED")).toBeInTheDocument();
    expect(canApprovePreparedExperiment(plan({ grid_preflight: { status: "ready", theoretical_count: 2, executable_count: 1, pruned_count: 1, duplicate_count: 0 } }))).toBe(false);
    expect(canApprovePreparedExperiment(plan({ approval_status: "REJECTED" }))).toBe(false);
  });
});
