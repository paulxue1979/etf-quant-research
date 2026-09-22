import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { OptimizationResultsExplorer } from "./OptimizationResultsExplorer";

const metrics = [
  { metric_id: "cagr", display_name: "CAGR", source_path: ["cagr"], direction: "maximize", format: "percent", category: "strategy", nullable: true, heatmap_eligible: true, pareto_eligible: true },
  { metric_id: "max_drawdown", display_name: "Max Drawdown", source_path: ["max_drawdown"], direction: "maximize", format: "percent", category: "risk", nullable: true, heatmap_eligible: true, pareto_eligible: true },
  { metric_id: "sharpe_ratio", display_name: "Sharpe Ratio", source_path: ["sharpe_ratio"], direction: "maximize", format: "ratio", category: "risk", nullable: true, heatmap_eligible: true, pareto_eligible: true },
  { metric_id: "calmar_ratio", display_name: "Calmar Ratio", source_path: ["calmar_ratio"], direction: "maximize", format: "ratio", category: "risk", nullable: true, heatmap_eligible: true, pareto_eligible: true },
  { metric_id: "turnover", display_name: "Realized Turnover", source_path: ["turnover"], direction: "minimize", format: "percent", category: "trading", nullable: true, heatmap_eligible: true, pareto_eligible: true },
  { metric_id: "trade_count", display_name: "Closed Trades", source_path: ["trade_metrics", "number_of_closed_trades"], direction: "maximize", format: "integer", category: "trading", nullable: true, heatmap_eligible: true, pareto_eligible: false },
] as const;

const projected = (metric_id: string, value: number | null, status = value === null ? "not_applicable" : "available") => ({ metric_id, value, status, reason: value === null ? "insufficient observations" : null });
const candidate = (index: number, status = "completed") => ({
  experiment_id: "experiment-grid",
  candidate_id: `candidate-${index}`,
  candidate_index: index,
  parameter_set_hash: `parameter-${index}`,
  candidate_set_hash: "candidate-set",
  parameter_values: { mode: "qqq", period: index === 0 ? 10 : 20, threshold: 0.1 },
  execution_status: status,
  result_status: status,
  experiment_result_id: status === "completed" ? `result-${index}` : null,
  result_hash: status === "completed" ? `hash-${index}` : null,
  derived_strategy_version_id: `derived-${index}`,
  derived_strategy_version_hash: `derived-hash-${index}`,
  backtest_configuration_hash: `config-${index}`,
  backtest_run_id: status === "completed" ? `run-${index}` : null,
  is_start: "2020-01-01",
  is_end: "2023-12-31",
  engine_version: "engine-v1",
  analysis_version: "analytics-v1",
  failure_code: status === "failed" ? "EXECUTION_FAILED" : null,
  failure_summary: status === "failed" ? "safe failure" : null,
  metrics: {
    cagr: projected("cagr", status === "completed" ? 0.1 + index / 100 : null, status === "failed" ? "failed" : undefined),
    max_drawdown: projected("max_drawdown", status === "completed" ? -0.2 : null, status === "failed" ? "failed" : undefined),
    sharpe_ratio: projected("sharpe_ratio", status === "completed" ? 1.2 : null),
    calmar_ratio: projected("calmar_ratio", status === "completed" ? 0.5 : null),
    turnover: projected("turnover", status === "completed" ? 0.3 : null),
    trade_count: projected("trade_count", status === "completed" ? 12 : null),
  },
});

const results = {
  schema_version: "phase-11i.1",
  experiment_id: "experiment-grid",
  protocol_id: "protocol-grid",
  is_start: "2020-01-01",
  is_end: "2023-12-31",
  is_only: true,
  parameter_space: { parameters: [
    { name: "mode", type: "enum", min: null, max: null, step: null, precision: null, allowed_values: ["qqq", "tqqq"] },
    { name: "period", type: "integer", min: 10, max: 20, step: 10, precision: null, allowed_values: [] },
    { name: "threshold", type: "discrete", min: null, max: null, step: null, precision: null, allowed_values: [0.1, 0.2] },
  ] },
  source_count: 3,
  filtered_count: 3,
  applied_filter: {},
  candidates: [candidate(0), candidate(1), candidate(2, "failed")],
  exclusions: {},
};

const registry = { schema_version: "phase-11i.1", metrics, max_drawdown_semantics: "canonical_negative", turnover_semantics: "realized" };
const heatmap = {
  schema_version: "phase-11i.1", experiment_id: "experiment-grid", metric: metrics[0], x_parameter: "period", y_parameter: "threshold", x_values: [10, 20], y_values: [0.1, 0.2], fixed_parameter_values: { mode: "qqq" }, applied_filter: {}, source_count: 3, filtered_count: 3, aggregation: null, interpolation: false,
  cells: [
    { x_value: 10, y_value: 0.1, candidate_id: "candidate-0", candidate_index: 0, parameter_set_hash: "parameter-0", metric: projected("cagr", 0.1), cell_status: "available", supporting_metrics: {} },
    { x_value: 20, y_value: 0.1, candidate_id: "candidate-1", candidate_index: 1, parameter_set_hash: "parameter-1", metric: projected("cagr", 0.11), cell_status: "available", supporting_metrics: {} },
    { x_value: 10, y_value: 0.2, candidate_id: null, candidate_index: null, parameter_set_hash: null, metric: projected("cagr", null, "unavailable"), cell_status: "pruned", supporting_metrics: {} },
    { x_value: 20, y_value: 0.2, candidate_id: "candidate-2", candidate_index: 2, parameter_set_hash: "parameter-2", metric: projected("cagr", null, "failed"), cell_status: "failed", supporting_metrics: {} },
  ],
};

const analysisPoint = (index: number) => ({ candidate_id: `candidate-${index}`, candidate_index: index, parameter_values: { period: index ? 20 : 10 }, metrics: { cagr: projected("cagr", 0.1 + index / 100), max_drawdown: projected("max_drawdown", -0.2 - index / 100) } });
const pareto = { schema_version: "phase-11i.1", objectives: [metrics[0], metrics[1]], source_count: 3, filtered_count: 3, eligible_count: 2, excluded_count: 1, frontier: [analysisPoint(0)], dominated: [analysisPoint(1)], exclusions: { "candidate-2": ["failed"] }, applied_filter: {} };
const stability = { schema_version: "phase-11i.1", center_candidate_id: "candidate-0", metric: metrics[0], center_metric: projected("cagr", 0.1), topology: "one_parameter_one_canonical_step", topology_uses_filtered_universe: false, statistics: { neighbor_count_expected: 2, neighbor_count_available: 1, neighbor_median: 0.11, neighbor_mad: 0 }, neighbors: [{ changed_parameter: "period", from_value: 10, to_value: 20, candidate_id: "candidate-1", candidate_index: 1, status: "available", metric: projected("cagr", 0.11) }, { changed_parameter: "threshold", from_value: 0.1, to_value: 0.2, candidate_id: null, candidate_index: null, status: "pruned", metric: projected("cagr", null, "unavailable") }] };
const sensitivity = { schema_version: "phase-11i.1", parameter: "period", parameter_values: [10, 20, 30], metrics: [metrics[0], metrics[1]], fixed_parameter_values: { mode: "qqq", threshold: 0.1 }, interpolation: false, points: [{ parameter_value: 10, candidate_id: "candidate-0", candidate_index: 0, status: "available", metrics: { cagr: projected("cagr", 0.1), max_drawdown: projected("max_drawdown", -0.2) } }, { parameter_value: 20, candidate_id: null, candidate_index: null, status: "pruned", metrics: { cagr: projected("cagr", null, "unavailable"), max_drawdown: projected("max_drawdown", null, "unavailable") } }, { parameter_value: 30, candidate_id: "candidate-1", candidate_index: 1, status: "available", metrics: { cagr: projected("cagr", 0.12), max_drawdown: projected("max_drawdown", -0.25) } }] };

function json(value: unknown, status = 200) { return new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } }); }

function mockApi(overrides: { heatmapError?: boolean; resultsValue?: typeof results } = {}) {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = String(input);
    if (url.endsWith("/optimization/metrics")) return json(registry);
    if (url.endsWith("/optimization/results")) return json(overrides.resultsValue ?? results);
    if (url.endsWith("/optimization/heatmap")) return overrides.heatmapError ? json({ detail: { code: "AMBIGUOUS_SLICE", message: "fixed_parameter_values must cover every non-axis parameter exactly" } }, 422) : json(heatmap);
    if (url.endsWith("/optimization/pareto")) return json(pareto);
    if (url.endsWith("/optimization/stability")) return json(stability);
    if (url.endsWith("/optimization/sensitivity")) return json(sensitivity);
    return json({ init }, 404);
  });
}

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe("OptimizationResultsExplorer", () => {
  it("loads a lightweight result table with failed and unavailable candidates", async () => {
    mockApi();
    render(<OptimizationResultsExplorer protocolId="protocol-grid" experimentId="experiment-grid" />);
    await screen.findByText("Parameter structure explorer");
    await waitFor(() => expect(screen.getByText("Source").parentElement).toHaveTextContent("3"));
    expect(screen.getAllByText("failed").length).toBeGreaterThan(0);
    expect(screen.getAllByText("N/A").length).toBeGreaterThan(0);
    expect(screen.getByText("Not loaded")).toBeInTheDocument();
    expect(screen.queryByText(/winner|optimal|best strategy/i)).not.toBeInTheDocument();
  });

  it("sends typed filter values to the backend", async () => {
    const fetchMock = mockApi();
    render(<OptimizationResultsExplorer protocolId="protocol-grid" experimentId="experiment-grid" />);
    await screen.findByRole("textbox", { name: "Minimum trades" });
    fireEvent.change(screen.getByRole("textbox", { name: "Minimum trades" }), { target: { value: "12" } });
    fireEvent.change(screen.getByRole("textbox", { name: "Maximum turnover" }), { target: { value: "0.4" } });
    fireEvent.click(screen.getByRole("button", { name: "Apply filters" }));
    await waitFor(() => expect(fetchMock.mock.calls.filter(([input]) => String(input).endsWith("/optimization/results"))).toHaveLength(2));
    const call = fetchMock.mock.calls.filter(([input]) => String(input).endsWith("/optimization/results"))[1];
    expect(JSON.parse(String(call[1]?.body)).filter).toMatchObject({ minimum_trade_count: 12, maximum_turnover: 0.4 });
  });

  it("renders a backend-owned heatmap with fixed slice and explicit gaps", async () => {
    const fetchMock = mockApi();
    render(<OptimizationResultsExplorer protocolId="protocol-grid" experimentId="experiment-grid" />);
    await screen.findByRole("tab", { name: "heatmap" });
    fireEvent.click(screen.getByRole("tab", { name: "heatmap" }));
    fireEvent.change(screen.getByRole("combobox", { name: "X parameter" }), { target: { value: "period" } });
    fireEvent.change(screen.getByRole("combobox", { name: "Y parameter" }), { target: { value: "threshold" } });
    fireEvent.click(screen.getByRole("button", { name: "Analyze" }));
    await screen.findByTestId("optimization-heatmap");
    expect(screen.getByText(/Aggregation: NONE/)).toBeInTheDocument();
    expect(screen.getByText("pruned")).toBeInTheDocument();
    expect(screen.getByText("failed")).toBeInTheDocument();
    const call = fetchMock.mock.calls.find(([input]) => String(input).endsWith("/optimization/heatmap"));
    expect(JSON.parse(String(call?.[1]?.body)).fixed_parameter_values).toEqual({ mode: "qqq" });
  });

  it("shows backend ambiguity errors instead of choosing a cell", async () => {
    mockApi({ heatmapError: true });
    render(<OptimizationResultsExplorer protocolId="protocol-grid" experimentId="experiment-grid" />);
    await screen.findByRole("tab", { name: "heatmap" });
    fireEvent.click(screen.getByRole("tab", { name: "heatmap" }));
    fireEvent.change(screen.getByRole("combobox", { name: "X parameter" }), { target: { value: "period" } });
    fireEvent.change(screen.getByRole("combobox", { name: "Y parameter" }), { target: { value: "threshold" } });
    fireEvent.click(screen.getByRole("button", { name: "Analyze" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("fixed_parameter_values must cover");
  });

  it("renders Pareto membership returned by the backend", async () => {
    mockApi(); render(<OptimizationResultsExplorer protocolId="protocol-grid" experimentId="experiment-grid" />);
    await screen.findByRole("tab", { name: "pareto" }); fireEvent.click(screen.getByRole("tab", { name: "pareto" })); fireEvent.click(screen.getByRole("button", { name: "Analyze" }));
    await screen.findByTestId("pareto-plot");
    expect(screen.getByText("Frontier 1")).toBeInTheDocument(); expect(screen.getByText("Dominated 1")).toBeInTheDocument();
  });

  it("renders direct-neighbor stability statistics without filtered topology", async () => {
    mockApi(); render(<OptimizationResultsExplorer protocolId="protocol-grid" experimentId="experiment-grid" />);
    await screen.findByRole("tab", { name: "stability" }); fireEvent.click(screen.getByRole("tab", { name: "stability" })); fireEvent.click(screen.getByRole("button", { name: "Analyze" }));
    await screen.findByTestId("stability-panel");
    expect(screen.getByText("neighbor count expected")).toBeInTheDocument(); expect(screen.getByText("pruned")).toBeInTheDocument();
  });

  it("renders sensitivity gaps without connecting unavailable points", async () => {
    mockApi(); render(<OptimizationResultsExplorer protocolId="protocol-grid" experimentId="experiment-grid" />);
    await screen.findByRole("tab", { name: "sensitivity" }); fireEvent.click(screen.getByRole("tab", { name: "sensitivity" })); fireEvent.change(screen.getByRole("combobox", { name: "Sweep parameter" }), { target: { value: "period" } }); fireEvent.click(screen.getByRole("button", { name: "Analyze" }));
    const chart = await screen.findByTestId("sensitivity-chart");
    expect(screen.getByText("Gap · pruned")).toBeInTheDocument(); expect(chart.querySelectorAll("line")).toHaveLength(0);
  });

  it("handles 1000 summaries and remains responsive at a narrow viewport", async () => {
    Object.defineProperty(window, "innerWidth", { configurable: true, value: 390 });
    const large = { ...results, source_count: 1000, filtered_count: 1000, candidates: Array.from({ length: 1000 }, (_, index) => candidate(index)) };
    mockApi({ resultsValue: large });
    render(<OptimizationResultsExplorer protocolId="protocol-grid" experimentId="experiment-grid" />);
    await waitFor(() => expect(screen.getByText("Source").parentElement).toHaveTextContent("1,000"));
    expect(screen.getAllByRole("row")).toHaveLength(1001);
  });

  it("reports loading failures safely", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new Error("network secret"));
    render(<OptimizationResultsExplorer protocolId="protocol-grid" experimentId="experiment-grid" />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Unable to reach");
    expect(screen.queryByText("network secret")).not.toBeInTheDocument();
  });
});
