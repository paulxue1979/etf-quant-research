import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ExperimentResearch } from "./ExperimentResearch";
import { freezeGateReason } from "./FreezeSelectionPanel";
import type { ExperimentResults, ResearchProtocolDetail, SelectionDecision } from "./types";

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
  derived_strategy_version_hash: "d".repeat(64),
  experiment_result_id: `result-${index}`,
  result_hash: "r".repeat(64),
  selection_eligibility: status === "completed"
    ? { eligible: true, failure_code: null, eligibility_reason: "candidate is eligible" }
    : { eligible: false, failure_code: "CANDIDATE_NOT_TERMINAL", eligibility_reason: "candidate execution is not completed" },
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

const persistedSelection: SelectionDecision = {
  selection_id: "selection-1", selection_hash: "s".repeat(64), experiment_id: "experiment-1", protocol_id: "protocol-1",
  selected_candidate_id: "candidate-0", selected_candidate_index: 0, selected_parameter_set_hash: "0".repeat(64),
  selected_experiment_result_id: "result-0", selected_result_hash: "r".repeat(64), selected_derived_strategy_version_id: "derived-v0",
  selected_derived_strategy_content_hash: "d".repeat(64), objective_hash: "o".repeat(64), candidate_set_hash: "c".repeat(64),
  parameter_space_hash: "p".repeat(64), selection_method: "researcher_judgment", researcher_rationale: "Reviewed frozen IS evidence.", evidence: {}, created_at: "2026-09-13T00:00:00Z",
};
const protocol: ResearchProtocolDetail = {
  protocol: { protocol_id: "protocol-1", status: "is_evaluated", is_start_date: "2020-01-01", is_end_date: "2020-12-31", oos_start_date: "2021-01-01", oos_end_date: "2022-12-31" },
  candidate_sets: [], selections: [], freezes: [], oos_evaluations: [],
};
const frozenResponse = { experiment_id: "experiment-1", protocol_id: "protocol-1", selection_decision: {}, strategy_freeze: { freeze_id: "freeze-1" }, identity: { strategy_version_id: "derived-v0", strategy_version_content_hash: "d".repeat(64) }, provenance: { oos_started: false } };

function json(value: unknown, status = 200) {
  return new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });
}

function mockResearchApi({ selection = false, handoff = false, semanticHash, resultsValue = results, freezeReadFailure = false, peakOos = false }: { selection?: boolean; handoff?: boolean; semanticHash?: string; resultsValue?: unknown; freezeReadFailure?: boolean; peakOos?: boolean } = {}) {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = String(input);
    if (url.endsWith("/results")) return json(resultsValue);
    if (url.endsWith("/protocols/protocol-1")) return json({ ...protocol, freezes: handoff ? [frozenResponse.strategy_freeze] : [] });
    if (semanticHash && url.endsWith("/research/experiments/experiment-1")) return json({ strategy_definition_id: "base", base_strategy_version_id: "base-v1", base_strategy_version_hash: results.base_strategy_version_hash });
    if (semanticHash && url.endsWith("/versions/base-v1")) return json({ content_hash: semanticHash, configuration: { transitions: [{ transition_id: "transition-10", from_state: "RECOVERY_HOLD", to_state: "FULL_RISK_ON" }] } });
    if (semanticHash && url.endsWith("/semantic-targets")) return json({ strategy_version_hash: semanticHash, targets: [
      { parameter_name: "nested_transition-10_0_threshold", target_kind: "nested_condition_threshold", value_type: "relative_threshold", descriptor: { transition_id: "transition-10" }, supported: true },
      { parameter_name: "state_FULL_RISK_ON_QQQ_weight", target_kind: "state_allocation_weight", value_type: "allocation_weight", descriptor: { state_id: "FULL_RISK_ON", symbol: "QQQ" }, supported: true },
    ] });
    if (url.endsWith("/comparison")) return json({ status: "compatible", is_compatible: true, experiment_id: "experiment-1", protocol_id: "protocol-1", reference_candidate_id: "candidate-0", compared_candidate_ids: ["candidate-1"], unavailable_candidate_ids: ["candidate-2"], checked_dimensions: [], mismatches: [] });
    if (url.endsWith("/objective-evaluation")) return json(objective);
    if (url.endsWith("/selection") && init?.method === "POST") return json(persistedSelection);
    if (url.endsWith("/selection")) return selection ? json(persistedSelection) : json({ detail: { code: "SELECTION_NOT_FOUND", message: "experiment selection was not found" } }, 404);
    if (url.endsWith("/handoff") && init?.method === "POST") return json(frozenResponse);
    if (url.endsWith("/handoff")) return freezeReadFailure ? json({ detail: { code: "HANDOFF_READ_UNAVAILABLE" } }, 503) : handoff ? json(frozenResponse) : json({ detail: { code: "HANDOFF_NOT_FOUND", message: "experiment selection has not been handed off" } }, 404);
    if (peakOos && url.endsWith("/oos/prepare") && init?.method === "POST") return json({ execution: { execution_id: "oos-execution-1", status: "prepared", attempt_count: 0, warmup_start: "1900-01-01" }, history_requirement: "ALL_TIME_TO_DATE" });
    return json({}, 404);
  });
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("Experiment Research", () => {
  it("shows the frozen peak history mode after official OOS preparation", async () => {
    mockResearchApi({ selection: true, handoff: true, peakOos: true });
    render(<ExperimentResearch onBack={vi.fn()} initialProtocolId="protocol-1" initialExperimentId="experiment-1" />);
    await screen.findByRole("button", { name: "Prepare Official OOS" });
    fireEvent.click(screen.getByRole("button", { name: "Prepare Official OOS" }));
    expect(await screen.findByText("All-time to date")).toBeInTheDocument();
    expect(screen.getByText("Peak Drawdown")).toBeInTheDocument();
    expect(screen.getByText("1900-01-01")).toBeInTheDocument();
  });

  it.each([true, false])("uses only matching frozen-version display metadata (matching=%s) and keeps all minimum metrics", async (matching) => {
    const parameters = { values: { "nested_transition-10_0_threshold": 0.03, state_FULL_RISK_ON_QQQ_weight: 0.9 } };
    const persisted = { ...results, candidates: [{ ...candidate(0), parameter_set: parameters, performance_summary: {
      analysis_version: "v1", engine_version: "v1", is_start: "2020-01-01", is_end: "2020-12-31",
      cagr: { value: 0.12, status: "available" }, total_return: { value: 2.91, status: "available" }, max_drawdown: { value: -0.37, status: "available" },
    } }] };
    const snapshot = JSON.stringify(persisted);
    const fetchMock = mockResearchApi({ semanticHash: matching ? results.base_strategy_version_hash : "wrong-hash", resultsValue: persisted });
    render(<ExperimentResearch onBack={vi.fn()} />);
    fireEvent.change(screen.getByRole("textbox", { name: "Protocol ID" }), { target: { value: "protocol-1" } });
    fireEvent.change(screen.getByRole("textbox", { name: "Experiment ID" }), { target: { value: "experiment-1" } });
    fireEvent.click(screen.getByRole("button", { name: "Load experiment" }));
    await screen.findByText("Total Return");
    expect(screen.getByText("2.9100")).toBeInTheDocument();
    expect(screen.getByText("CAGR")).toBeInTheDocument();
    expect(screen.getByText("Max Drawdown")).toBeInTheDocument();
    await waitFor(() => expect(fetchMock.mock.calls.some(([input]) => String(input).endsWith("/semantic-targets"))).toBe(true));
    if (matching) {
      expect(await screen.findByText("恢复持有 → 全风险开启｜触发阈值：3%")).toBeInTheDocument();
      expect(screen.getByText("全风险开启｜QQQ 目标仓位：90%")).toBeInTheDocument();
    } else {
      expect(screen.getByText("nested_transition-10_0_threshold：0.03")).toBeInTheDocument();
      expect(screen.queryByText("恢复持有 → 全风险开启｜触发阈值：3%")).not.toBeInTheDocument();
    }
    expect(screen.getByText("Canonical parameters").parentElement).toHaveTextContent("state_FULL_RISK_ON_QQQ_weight");
    expect(JSON.stringify(persisted)).toBe(snapshot);
  });

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
    expect(screen.getAllByText("CAGR")).not.toHaveLength(0);
    expect(screen.queryByText(/rank|winner|recommend|best|score/i)).not.toBeInTheDocument();
  });

  it("requires explicit freeze confirmation, sends no identity overrides, and locks selection", async () => {
    const fetchMock = mockResearchApi();
    render(<ExperimentResearch onBack={vi.fn()} />);
    fireEvent.change(screen.getByRole("textbox", { name: "Protocol ID" }), { target: { value: "protocol-1" } });
    fireEvent.change(screen.getByRole("textbox", { name: "Experiment ID" }), { target: { value: "experiment-1" } });
    fireEvent.click(screen.getByRole("button", { name: "Load experiment" }));
    await waitFor(() => expect(screen.getAllByRole("button", { name: "Select candidate" })[0]).toBeEnabled());
    fireEvent.click(screen.getAllByRole("button", { name: "Select candidate" })[0]);
    expect(screen.getByText("Review before persisting")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Confirm selection" })).toBeEnabled();
    fireEvent.change(screen.getByRole("textbox", { name: "Researcher rationale" }), { target: { value: "Reviewed frozen IS evidence." } });
    fireEvent.click(screen.getByRole("button", { name: "Confirm selection" }));
    await waitFor(() => expect(screen.getByText("Active decision")).toBeInTheDocument());
    const selectionCall = fetchMock.mock.calls.find(([input, init]) => String(input).endsWith("/selection") && init?.method === "POST");
    expect(selectionCall).toBeDefined();
    expect(JSON.parse(String(selectionCall?.[1]?.body))).toEqual({ selected_candidate_id: "candidate-0", selection_method: "researcher_judgment", researcher_rationale: "Reviewed frozen IS evidence." });
    expect(screen.queryByText(/PHASE 7|handoff/i)).not.toBeInTheDocument();
    const freezePanel = within(screen.getByRole("region", { name: "Freeze selection for Official OOS" }));
    expect(freezePanel.getByText("#0")).toBeInTheDocument();
    expect(freezePanel.getByText("d".repeat(64))).toBeInTheDocument();
    expect(freezePanel.getByText("2020-01-01 → 2020-12-31")).toBeInTheDocument();
    expect(freezePanel.getByText("2021-01-01 → 2022-12-31")).toBeInTheDocument();
    expect(freezePanel.getByRole("note")).toHaveTextContent("如需重新选择参数，应创建新的 Experiment");
    fireEvent.click(screen.getByRole("button", { name: "Freeze for Official OOS" }));
    expect(screen.getByRole("group", { name: "Confirm Official OOS freeze" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Confirm freeze" }));
    await screen.findByText("FROZEN FOR OFFICIAL OOS");
    expect(screen.getByRole("button", { name: "Selected / Frozen" })).toBeDisabled();
    screen.getAllByRole("button", { name: "Change selection" }).forEach((button) => expect(button).toBeDisabled());
    const handoffCall = fetchMock.mock.calls.find(([input, init]) => String(input).endsWith("/handoff") && init?.method === "POST");
    expect(handoffCall).toBeDefined();
    expect(handoffCall?.[1]?.body).toBeUndefined();
  });

  it("does not allow freeze before persisted selection", async () => {
    mockResearchApi();
    render(<ExperimentResearch onBack={vi.fn()} />);
    fireEvent.change(screen.getByRole("textbox", { name: "Protocol ID" }), { target: { value: "protocol-1" } });
    fireEvent.change(screen.getByRole("textbox", { name: "Experiment ID" }), { target: { value: "experiment-1" } });
    fireEvent.click(screen.getByRole("button", { name: "Load experiment" }));
    await waitFor(() => expect(screen.getByText("Persisted IS evidence")).toBeInTheDocument());
    expect(screen.queryByRole("button", { name: "Freeze for Official OOS" })).not.toBeInTheDocument();
  });

  it("restores frozen identity and ranges from GET responses on remount", async () => {
    const fetchMock = mockResearchApi({ selection: true, handoff: true });
    for (let reload = 0; reload < 2; reload++) {
      const rendered = render(<ExperimentResearch onBack={vi.fn()} initialProtocolId="protocol-1" initialExperimentId="experiment-1" />);
      await screen.findByText("FROZEN FOR OFFICIAL OOS");
      const panel = within(screen.getByRole("region", { name: "Freeze selection for Official OOS" }));
      expect(panel.getByText("#0 · Selected / Frozen")).toBeInTheDocument();
      expect(panel.getByText("derived-v0")).toBeInTheDocument();
      expect(panel.getByText("d".repeat(64))).toBeInTheDocument();
      expect(panel.getByText("2021-01-01 → 2022-12-31")).toBeInTheDocument();
      screen.getAllByRole("button", { name: "Change selection" }).forEach((button) => expect(button).toBeDisabled());
      rendered.unmount();
    }
    expect(fetchMock.mock.calls.some(([, init]) => init?.method === "POST" && String(init.body).includes("strategy_version"))).toBe(false);
  });

  it("fails closed when persisted freeze state cannot be loaded", async () => {
    mockResearchApi({ selection: true, freezeReadFailure: true });
    render(<ExperimentResearch onBack={vi.fn()} initialProtocolId="protocol-1" initialExperimentId="experiment-1" />);
    expect(await screen.findByRole("button", { name: "Freeze for Official OOS" })).toBeDisabled();
    expect(screen.getAllByRole("alert").some((item) => item.textContent?.includes("FREEZE_STATE_UNAVAILABLE"))).toBe(true);
    screen.getAllByRole("button", { name: "Change selection" }).forEach((button) => expect(button).toBeDisabled());
  });

  it("uses human-readable parameters in the freeze review without changing canonical values", async () => {
    mockResearchApi({ selection: true, semanticHash: results.base_strategy_version_hash, resultsValue: { ...results, candidates: [{ ...candidate(0), parameter_set: { values: { "nested_transition-10_0_threshold": -0.15, state_FULL_RISK_ON_QQQ_weight: 1 } } }] } });
    render(<ExperimentResearch onBack={vi.fn()} initialProtocolId="protocol-1" initialExperimentId="experiment-1" />);
    const panel = within(await screen.findByRole("region", { name: "Freeze selection for Official OOS" }));
    expect(await panel.findByText("恢复持有 → 全风险开启｜触发阈值：-15%")).toBeInTheDocument();
    expect(panel.getByText("全风险开启｜QQQ 目标仓位：100%")).toBeInTheDocument();
  });

  it.each([
    ["selection absent", "SELECTION_NOT_FOUND"], ["ineligible", "CANDIDATE_NOT_TERMINAL"],
    ["derived missing", "DERIVED_VERSION_MISSING"], ["hash mismatch", "DERIVED_STRATEGY_INTEGRITY_ERROR"],
    ["incomplete", "EXPERIMENT_NOT_COMPLETED"], ["protocol missing", "PROTOCOL_UNAVAILABLE"],
    ["invalid oos", "OOS_BOUNDARY_ERROR"], ["overlap", "OOS_BOUNDARY_ERROR"],
    ["protocol draft", "PROTOCOL_INVALID_STATE"], ["freeze exists", "STRATEGY_FREEZE_CONFLICT"],
  ])("blocks freeze for %s using %s", (scenario, code) => {
    const model = structuredClone(results) as ExperimentResults;
    const detail = structuredClone(protocol);
    const decision = structuredClone(persistedSelection);
    const chosen = model.candidates.find((item) => item.candidate_id === "candidate-0")!;
    if (scenario === "ineligible" || scenario === "derived missing") chosen.selection_eligibility = { eligible: false, failure_code: code, eligibility_reason: "canonical reason" };
    if (scenario === "hash mismatch") decision.selected_derived_strategy_content_hash = "a".repeat(64);
    if (scenario === "incomplete") model.summary.complete = false;
    if (scenario === "invalid oos") detail.protocol.oos_start_date = "2021-02-30";
    if (scenario === "overlap") detail.protocol.oos_start_date = "2020-12-31";
    if (scenario === "protocol draft") detail.protocol.status = "draft";
    if (scenario === "freeze exists") detail.freezes = [{ freeze_id: "freeze-other" }];
    expect(freezeGateReason(model, scenario === "selection absent" ? null : decision, scenario === "protocol missing" ? null : detail, null)).toContain(code);
  });
});
