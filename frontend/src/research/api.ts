import type {
  CompatibilityDiagnostic,
  ExperimentResults,
  HandoffResponse,
  GridSearchPreflight,
  GridSearchProgress,
  GridSearchTemplate,
  ObjectiveEvaluationResponse,
  SelectionDecision,
  SelectionRequest,
  OosResearchView,
  OfficialOosExecutionResponse,
  OfficialOosPreparation,
  OptimizationFilter,
  OptimizationHeatmap,
  OptimizationMetricRegistry,
  OptimizationPareto,
  OptimizationResults,
  OptimizationSensitivity,
  OptimizationStability,
  ExperimentWorkflowList,
  ExperimentWorkflowRecord,
  ResearchProtocolDetail,
  PendingExperimentConfiguration,
} from "./types";

import { apiBaseUrl } from "../apiConfig";

export class ResearchApiError extends Error {
  constructor(
    public readonly kind: "network" | "server" | "malformed",
    message: string,
    public readonly code: string = "RESEARCH_REQUEST_FAILED",
    public readonly status?: number,
    public readonly endpoint?: string,
  ) {
    super(message);
    this.name = "ResearchApiError";
  }
}

function safeMessage(payload: unknown, status: number): { code: string; message: string } {
  if (typeof payload === "object" && payload !== null && "detail" in payload) {
    const detail = payload.detail;
    if (typeof detail === "object" && detail !== null) {
      const code = "code" in detail && typeof detail.code === "string" ? detail.code : "RESEARCH_REQUEST_FAILED";
      const message = "message" in detail && typeof detail.message === "string" ? detail.message : "Research request failed.";
      return { code, message };
    }
  }
  return { code: `HTTP_${status}`, message: status >= 500 ? `Server processing error (${status}).` : `Research request failed (${status}).` };
}

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${apiBaseUrl}${path}`, {
      ...init,
      headers: { "Content-Type": "application/json", ...init?.headers },
    });
  } catch {
    throw new ResearchApiError("network", "Unable to reach the Experiment Research service (backend unreachable or browser CORS blocked).", "NETWORK_OR_CORS_FAILURE", undefined, path);
  }
  if (!response.ok) {
    let payload: unknown = null;
    try {
      payload = await response.json();
    } catch {
      throw new ResearchApiError("server", response.status >= 500 ? `Server processing error (${response.status}).` : `Research request failed (${response.status}).`, `HTTP_${response.status}`, response.status, path);
    }
    const error = safeMessage(payload, response.status);
    throw new ResearchApiError("server", error.message, error.code, response.status, path);
  }
  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    throw new ResearchApiError("malformed", "Experiment Research returned an invalid response.", "INVALID_RESPONSE", response.status, path);
  }
  return payload as T;
}

const path = (protocolId: string, experimentId: string, suffix: string) =>
  `/research/protocols/${encodeURIComponent(protocolId)}/experiments/${encodeURIComponent(experimentId)}/${suffix}`;

export const researchApi = {
  listPreparedExperiments() {
    return requestJson<PendingExperimentConfiguration[]>("/experiment-operator/configurations");
  },
  getPreparedExperiment(configId: string) {
    return requestJson<PendingExperimentConfiguration>(`/experiment-operator/configurations/${encodeURIComponent(configId)}`);
  },
  rejectPreparedExperiment(configId: string) {
    return requestJson<PendingExperimentConfiguration>(`/experiment-operator/configurations/${encodeURIComponent(configId)}/reject`, { method: "POST" });
  },
  approvePreparedExperiment(configId: string) {
    return requestJson<PendingExperimentConfiguration>(`/experiment-operator/configurations/${encodeURIComponent(configId)}/approve-run`, { method: "POST" });
  },
  createProtocol(request: Record<string, unknown>) {
    return requestJson<ResearchProtocolDetail>("/research/protocols", {
      method: "POST", body: JSON.stringify(request),
    });
  },
  getProtocol(protocolId: string) {
    return requestJson<ResearchProtocolDetail>(`/research/protocols/${encodeURIComponent(protocolId)}`);
  },
  createCandidateSet(protocolId: string, strategyVersionIds: string[]) {
    return requestJson<ResearchProtocolDetail>(`/research/protocols/${encodeURIComponent(protocolId)}/candidate-sets`, {
      method: "POST", body: JSON.stringify({ strategy_version_ids: strategyVersionIds }),
    });
  },
  lockCandidateSet(candidateSetId: string) {
    return requestJson<ResearchProtocolDetail>(`/research/candidate-sets/${encodeURIComponent(candidateSetId)}/lock`, { method: "POST" });
  },
  transitionProtocol(protocolId: string, status: "frozen" | "is_evaluated" | "selection_recorded" | "oos_evaluated" | "closed") {
    return requestJson<ResearchProtocolDetail>(`/research/protocols/${encodeURIComponent(protocolId)}/transitions`, {
      method: "POST", body: JSON.stringify({ status }),
    });
  },
  listExperiments() {
    return requestJson<ExperimentWorkflowList>("/research/experiments");
  },
  getExperiment(experimentId: string) {
    return requestJson<ExperimentWorkflowRecord>(`/research/experiments/${encodeURIComponent(experimentId)}`);
  },
  preflightExperiment(request: Record<string, unknown>) {
    return requestJson<GridSearchPreflight>("/research/experiments/preflight", { method: "POST", body: JSON.stringify(request) });
  },
  createExperiment(request: Record<string, unknown>) {
    return requestJson<ExperimentWorkflowRecord>("/research/experiments", { method: "POST", body: JSON.stringify(request) });
  },
  getGridTemplate(experimentId: string) {
    return requestJson<GridSearchTemplate>(
      `/research/experiments/${encodeURIComponent(experimentId)}/grid/template`,
    );
  },
  preflightGrid(experimentId: string, definition: Record<string, unknown>) {
    return requestJson<GridSearchPreflight>(
      `/research/experiments/${encodeURIComponent(experimentId)}/grid/preflight`,
      { method: "POST", body: JSON.stringify(definition) },
    );
  },
  prepareGrid(experimentId: string, definition: Record<string, unknown>) {
    return requestJson<GridSearchPreflight>(
      `/research/experiments/${encodeURIComponent(experimentId)}/grid/prepare`,
      { method: "POST", body: JSON.stringify(definition) },
    );
  },
  executeGrid(experimentId: string) {
    return requestJson<GridSearchProgress>(
      `/research/experiments/${encodeURIComponent(experimentId)}/grid/execute`,
      { method: "POST" },
    );
  },
  cancelGrid(experimentId: string) {
    return requestJson<GridSearchProgress>(
      `/research/experiments/${encodeURIComponent(experimentId)}/grid/cancel`,
      { method: "POST" },
    );
  },
  getGridProgress(experimentId: string) {
    return requestJson<GridSearchProgress>(
      `/research/experiments/${encodeURIComponent(experimentId)}/grid/progress`,
    );
  },
  getExperimentResults(protocolId: string, experimentId: string) {
    return requestJson<ExperimentResults>(path(protocolId, experimentId, "results"));
  },
  getExperimentCompatibility(protocolId: string, experimentId: string) {
    return requestJson<CompatibilityDiagnostic>(path(protocolId, experimentId, "comparison"));
  },
  getExperimentObjectiveEvaluation(protocolId: string, experimentId: string) {
    return requestJson<ObjectiveEvaluationResponse>(path(protocolId, experimentId, "objective-evaluation"));
  },
  createExperimentSelection(protocolId: string, experimentId: string, request: SelectionRequest) {
    return requestJson<SelectionDecision>(path(protocolId, experimentId, "selection"), {
      method: "POST",
      body: JSON.stringify(request),
    });
  },
  getExperimentSelection(protocolId: string, experimentId: string) {
    return requestJson<SelectionDecision>(path(protocolId, experimentId, "selection"));
  },
  handoffExperimentSelection(protocolId: string, experimentId: string) {
    return requestJson<HandoffResponse>(path(protocolId, experimentId, "handoff"), { method: "POST" });
  },
  getExperimentHandoff(protocolId: string, experimentId: string) {
    return requestJson<HandoffResponse>(path(protocolId, experimentId, "handoff"));
  },
  prepareOfficialOos(protocolId: string) {
    return requestJson<OfficialOosPreparation>(`/research/protocols/${encodeURIComponent(protocolId)}/oos/prepare`, { method: "POST" });
  },
  getOfficialOosExecution(protocolId: string) {
    return requestJson<{ execution: OfficialOosExecutionResponse["execution"]; events: Array<Record<string, unknown>>; history_requirement?: OfficialOosPreparation["history_requirement"] | null }>(`/research/protocols/${encodeURIComponent(protocolId)}/oos/execution`);
  },
  executeOfficialOos(protocolId: string) {
    return requestJson<OfficialOosExecutionResponse>(`/research/protocols/${encodeURIComponent(protocolId)}/oos/execute`, { method: "POST" });
  },
  getOfficialOosResearchView(protocolId: string) {
    return requestJson<OosResearchView>(`/research/protocols/${encodeURIComponent(protocolId)}/oos`);
  },
  getOptimizationMetricRegistry() {
    return requestJson<OptimizationMetricRegistry>("/research/optimization/metrics");
  },
  getOptimizationResults(
    protocolId: string,
    experimentId: string,
    filter: OptimizationFilter = {},
  ) {
    return requestJson<OptimizationResults>(path(protocolId, experimentId, "optimization/results"), {
      method: "POST",
      body: JSON.stringify({ filter }),
    });
  },
  getOptimizationHeatmap(
    protocolId: string,
    experimentId: string,
    request: {
      x_parameter: string;
      y_parameter: string;
      metric_id: string;
      fixed_parameter_values: Record<string, unknown>;
      filter: OptimizationFilter;
    },
  ) {
    return requestJson<OptimizationHeatmap>(path(protocolId, experimentId, "optimization/heatmap"), {
      method: "POST",
      body: JSON.stringify(request),
    });
  },
  getOptimizationPareto(
    protocolId: string,
    experimentId: string,
    objectiveIds: string[],
    filter: OptimizationFilter,
  ) {
    return requestJson<OptimizationPareto>(path(protocolId, experimentId, "optimization/pareto"), {
      method: "POST",
      body: JSON.stringify({ objective_ids: objectiveIds, filter }),
    });
  },
  getOptimizationStability(
    protocolId: string,
    experimentId: string,
    centerCandidateId: string,
    metricId: string,
    filter: OptimizationFilter,
  ) {
    return requestJson<OptimizationStability>(path(protocolId, experimentId, "optimization/stability"), {
      method: "POST",
      body: JSON.stringify({ center_candidate_id: centerCandidateId, metric_id: metricId, filter }),
    });
  },
  getOptimizationSensitivity(
    protocolId: string,
    experimentId: string,
    request: {
      parameter: string;
      metric_ids: string[];
      fixed_parameter_values: Record<string, unknown>;
      filter: OptimizationFilter;
    },
  ) {
    return requestJson<OptimizationSensitivity>(path(protocolId, experimentId, "optimization/sensitivity"), {
      method: "POST",
      body: JSON.stringify(request),
    });
  },
};
