import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { GridSearchPanel } from "./GridSearchPanel";

const definition = {
  schema_version: "phase-11h.1",
  experiment_id: "experiment-grid-1",
  experiment_hash: "e".repeat(64),
  strategy_bindings: [],
  backtest_bindings: [],
  fixed_parameters: { price_field: "adjusted_close", is_start: "2020-01-01", is_end: "2020-12-31" },
  constraints: [],
  max_candidates: 100,
  theoretical_guard: 1_000_000,
};

const preflight = {
  definition_hash: "d".repeat(64),
  parameter_space_hash: "p".repeat(64),
  theoretical_count: 12,
  pruned_count: 2,
  valid_count: 10,
  duplicate_count: 1,
  executable_count: 9,
  max_allowed: 100,
  hard_maximum: 1000,
  theoretical_guard: 1_000_000,
  pruning_reasons: { SUM_CONSTRAINT_FAILED: 2 },
  status: "ready",
  can_execute: true,
  issues: [],
};

const preparedProgress = {
  experiment_id: "experiment-grid-1",
  definition_hash: "d".repeat(64),
  status: "prepared",
  cancel_requested: false,
  theoretical: 12,
  pruned: 2,
  valid: 10,
  duplicate: 1,
  executable: 9,
  pending: 9,
  running: 0,
  completed: 0,
  failed: 0,
  retryable: 0,
  cancelled: 0,
  progress_percentage: 0,
};

function json(value: unknown, status = 200) {
  return new Response(JSON.stringify(value), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("GridSearchPanel", () => {
  it("requires deterministic preflight and explicit preparation before execution", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = String(input);
      if (url.endsWith("/grid/template")) {
        return json({
          definition,
          parameter_space: { parameters: [{ name: "weekly_period" }] },
          fixed_parameters: definition.fixed_parameters,
          tunable_parameters: [{ name: "weekly_period" }],
        });
      }
      if (url.endsWith("/grid/preflight")) return json(preflight);
      if (url.endsWith("/grid/prepare")) return json(preflight);
      if (url.endsWith("/grid/progress")) return json(preparedProgress);
      if (url.endsWith("/grid/execute")) {
        return json({ ...preparedProgress, status: "completed", pending: 0, completed: 9, progress_percentage: 100 });
      }
      return json({}, 404);
    });

    render(<GridSearchPanel experimentId="experiment-grid-1" />);
    expect(screen.queryByRole("button", { name: "Execute IS grid" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Load frozen definition" }));
    await waitFor(() => expect(
      (screen.getByRole("textbox", { name: "Grid definition" }) as HTMLTextAreaElement).value,
    ).toContain("phase-11h.1"));
    expect(screen.getByRole("button", { name: "Execute IS grid" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Run preflight" }));
    await waitFor(() => expect(screen.getByLabelText("Grid preflight counts")).toHaveTextContent("Theoretical12"));
    expect(screen.getByLabelText("Grid preflight counts")).toHaveTextContent("Executable9");
    expect(screen.getByRole("button", { name: "Execute IS grid" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Prepare candidate set" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Execute IS grid" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "Execute IS grid" }));
    await waitFor(() => expect(screen.getByLabelText("Grid execution progress")).toHaveTextContent("100.0%"));

    const executeCall = fetchMock.mock.calls.find(([input]) => String(input).endsWith("/grid/execute"));
    expect(executeCall?.[1]?.method).toBe("POST");
    expect(executeCall?.[1]?.body).toBeUndefined();
    expect(screen.queryByText(/best|winner|rank/i)).not.toBeInTheDocument();
  });

  it("does not prepare or silently truncate an over-limit grid", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = String(input);
      if (url.endsWith("/grid/template")) {
        return json({ definition, parameter_space: {}, fixed_parameters: {}, tunable_parameters: [] });
      }
      return json({
        ...preflight,
        theoretical_count: 101,
        valid_count: 101,
        executable_count: 101,
        status: "failed",
        can_execute: false,
        issues: ["VALID_CANDIDATE_COUNT_EXCEEDS_LIMIT"],
      });
    });

    render(<GridSearchPanel experimentId="experiment-grid-1" />);
    fireEvent.click(screen.getByRole("button", { name: "Load frozen definition" }));
    await screen.findByRole("textbox", { name: "Grid definition" });
    fireEvent.click(screen.getByRole("button", { name: "Run preflight" }));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("VALID_CANDIDATE_COUNT_EXCEEDS_LIMIT"));
    expect(screen.getByLabelText("Grid preflight counts")).toHaveTextContent("Executable101");
    expect(screen.getByRole("button", { name: "Prepare candidate set" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Execute IS grid" })).toBeDisabled();
  });

  it("cancels through an empty command request after preparation", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = String(input);
      if (url.endsWith("/grid/template")) return json({ definition, parameter_space: {}, fixed_parameters: {}, tunable_parameters: [] });
      if (url.endsWith("/grid/preflight") || url.endsWith("/grid/prepare")) return json(preflight);
      if (url.endsWith("/grid/cancel")) return json({ ...preparedProgress, status: "cancelled", pending: 0, cancelled: 9, progress_percentage: 100 });
      return json(preparedProgress);
    });

    render(<GridSearchPanel experimentId="experiment-grid-1" />);
    fireEvent.click(screen.getByRole("button", { name: "Load frozen definition" }));
    await screen.findByRole("textbox", { name: "Grid definition" });
    fireEvent.click(screen.getByRole("button", { name: "Run preflight" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Prepare candidate set" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "Prepare candidate set" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Cancel" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.getByText("CANCELLED")).toBeInTheDocument());

    const cancelCall = fetchMock.mock.calls.find(([input]) => String(input).endsWith("/grid/cancel"));
    expect(cancelCall?.[1]?.body).toBeUndefined();
  });
});
