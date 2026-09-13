import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ExperimentResearch } from "./ExperimentResearch";

const candidate = (index: number, status = "completed") => ({
  candidate_id: `candidate-${index}`,
  candidate_index: index,
  parameter_set: { period: index + 20 },
  parameter_set_hash: `${index}`.repeat(64),
  candidate_set_hash: "c".repeat(64),
  parameter_space_hash: "p".repeat(64),
  objective_spec_hash: "o".repeat(64),
  execution_status: status,
  result_status: status === "completed" ? "completed" : status === "failed" ? "failed" : "not_evaluable",
  failure_summary: status === "failed" ? "Execution failed safely." : null,
  derived_strategy_version_id: `derived-v${index}`,
  performance_summary: status === "completed" ? { cagr: { value: 0.12 + index / 100, status: "available", reason: null } } : {},
});

const results = {
  experiment_id: "experiment-1",
  protocol_id: "protocol-1",
  experiment_status: "completed",
  is_start: "2020-01-01",
  is_end: "2020-12-31",
  parameter_space_hash: "p".repeat(64),
  objective_spec_hash: "o".repeat(64),
  base_strategy_version_id: "base-v1",
  base_strategy_version_hash: "b".repeat(64),
  engine_version: "phase-3",
  analysis_version: "phase-4i",
  candidates: [candidate(1), candidate(0), candidate(2, "failed")],
  summary: { candidate_count: 3, completed_count: 2, failed_count: 1, not_evaluable_count: 0, running_count: 0, pending_count: 0, result_count: 2, complete: true, completeness_status: "complete" },
};

const objective = {
  experiment_id: "experiment-1",
  protocol_id: "protocol-1",
  objective_spec_hash: "o".repeat(64),
  objective_specification: { hard_constraints: { cagr: 0.1 } },
  evaluations: [
    { candidate_id: "candidate-0", candidate_index: 0, evaluation: { experiment_id: "experiment-1", candidate_id: "candidate-0", objective_hash: "o".repeat(64), experiment_result_id: "result-0", result_hash: "r".repeat(64), constraint_results: [], overall_constraint_state: "pass" } },
    { candidate_id: "candidate-1", candidate_index: 1, evaluation: { experiment_id: "experiment-1", candidate_id: "candidate-1", objective_hash: "o".repeat(64), experiment_result_id: "result-1", result_hash: "r".repeat(64), constraint_results: [], overall_constraint_state: "fail" } },
  ],
};

function json(value: unknown, status = 200) {
  return new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });
}

function mockResearchApi({ selection = false, handoff = false }: { selection?: boolean; handoff?: boolean } = {}) {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = String(input);
    if (url.endsWith("/results")) return json(results);
    if (url.endsWith("/comparison")) return json({ status: "compatible", is_compatible: true, experiment_id: "experiment-1", protocol_id: "protocol-1", reference_candidate_id: "candidate-0", compared_candidate_ids: ["candidate-1"], unavailable_candidate_ids: ["candidate-2"], checked_dimensions: [], mismatches: [] });
    if (url.endsWith("/objective-evaluation")) return json(objective);
    if (url.endsWith("/selection") && init?.method === "POST") return json({ selection_id: "selection-1", selection_hash: "s".repeat(64), experiment_id: "experiment-1", protocol_id: "protocol-1", selected_candidate_id: "candidate-0", selected_candidate_index: 0, selected_parameter_set_hash: "0".repeat(64), selected_experiment_result_id: "result-0", selected_result_hash: "r".repeat(64), selected_derived_strategy_version_id: "derived-v0", selected_derived_strategy_content_hash: "d".repeat(64), objective_hash: "o".repeat(64), candidate_set_hash: "c".repeat(64), parameter_space_hash: "p".repeat(64), selection_method: "researcher_judgment", researcher_rationale: "Reviewed frozen IS evidence.", evidence: {}, created_at: "2026-09-13T00:00:00Z" });
    if (url.endsWith("/selection")) return selection ? json({}) : json({ detail: { code: "SELECTION_NOT_FOUND", message: "experiment selection was not found" } }, 404);
    if (url.endsWith("/handoff") && init?.method === "POST") return json({ experiment_id: "experiment-1", protocol_id: "protocol-1", selection_decision: {}, strategy_freeze: {}, identity: { strategy_version_id: "derived-v0", strategy_version_content_hash: "d".repeat(64) }, provenance: { oos_started: false } });
    if (url.endsWith("/handoff")) return handoff ? json({}) : json({ detail: { code: "HANDOFF_NOT_FOUND", message: "experiment selection has not been handed off" } }, 404);
    return json({}, 404);
  });
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("Experiment Research", () => {
  it("renders deterministic candidate order, failed evidence, and explicit objective state", async () => {
    mockResearchApi();
    render(<ExperimentResearch onBack={vi.fn()} />);
    fireEvent.change(screen.getByRole("textbox", { name: "Protocol ID" }), { target: { value: "protocol-1" } });
    fireEvent.change(screen.getByRole("textbox", { name: "Experiment ID" }), { target: { value: "experiment-1" } });
    fireEvent.click(screen.getByRole("button", { name: "Load experiment" }));
    await waitFor(() => expect(screen.getByText("experiment-1")).toBeInTheDocument());
    const rows = screen.getAllByRole("row");
    expect(rows[1]).toHaveTextContent("0");
    expect(rows[2]).toHaveTextContent("1");
    expect(screen.getByText("FAILED")).toBeInTheDocument();
    expect(screen.getAllByText("FAIL")).not.toHaveLength(0);
    expect(screen.getAllByText("cagr")).not.toHaveLength(0);
    expect(screen.queryByText(/rank|winner|recommend|best|score/i)).not.toBeInTheDocument();
  });

  it("requires confirmation rationale, sends only allowed selection fields, and makes it read-only", async () => {
    const fetchMock = mockResearchApi();
    render(<ExperimentResearch onBack={vi.fn()} />);
    fireEvent.change(screen.getByRole("textbox", { name: "Protocol ID" }), { target: { value: "protocol-1" } });
    fireEvent.change(screen.getByRole("textbox", { name: "Experiment ID" }), { target: { value: "experiment-1" } });
    fireEvent.click(screen.getByRole("button", { name: "Load experiment" }));
    await waitFor(() => expect(screen.getAllByRole("button", { name: "Select candidate" })[0]).toBeEnabled());
    fireEvent.click(screen.getAllByRole("button", { name: "Select candidate" })[0]);
    expect(screen.getByText("Review before persisting")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Confirm selection" })).toBeDisabled();
    fireEvent.change(screen.getByRole("textbox", { name: "Researcher rationale" }), { target: { value: "Reviewed frozen IS evidence." } });
    fireEvent.click(screen.getByRole("button", { name: "Confirm selection" }));
    await waitFor(() => expect(screen.getByText("Read-only decision")).toBeInTheDocument());
    const selectionCall = fetchMock.mock.calls.find(([input, init]) => String(input).endsWith("/selection") && init?.method === "POST");
    expect(selectionCall).toBeDefined();
    expect(JSON.parse(String(selectionCall?.[1]?.body))).toEqual({ selected_candidate_id: "candidate-0", selection_method: "researcher_judgment", researcher_rationale: "Reviewed frozen IS evidence." });
    fireEvent.click(screen.getByRole("button", { name: "Proceed to PHASE 7 Handoff" }));
    expect(screen.getByRole("dialog", { name: "Confirm PHASE 7 handoff" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Confirm handoff" }));
    await waitFor(() => expect(screen.getByText("Freeze recorded")).toBeInTheDocument());
    const handoffCall = fetchMock.mock.calls.find(([input, init]) => String(input).endsWith("/handoff") && init?.method === "POST");
    expect(handoffCall).toBeDefined();
    expect(handoffCall?.[1]?.body).toBeUndefined();
  });

  it("does not allow handoff before persisted selection and sends an empty body", async () => {
    mockResearchApi();
    render(<ExperimentResearch onBack={vi.fn()} />);
    fireEvent.change(screen.getByRole("textbox", { name: "Protocol ID" }), { target: { value: "protocol-1" } });
    fireEvent.change(screen.getByRole("textbox", { name: "Experiment ID" }), { target: { value: "experiment-1" } });
    fireEvent.click(screen.getByRole("button", { name: "Load experiment" }));
    await waitFor(() => expect(screen.getByText("Persisted IS evidence")).toBeInTheDocument());
    expect(screen.queryByRole("button", { name: "Confirm handoff" })).not.toBeInTheDocument();
  });
});
