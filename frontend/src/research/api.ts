import type {
  CompatibilityDiagnostic,
  ExperimentResults,
  HandoffResponse,
  ObjectiveEvaluationResponse,
  SelectionDecision,
  SelectionRequest,
} from "./types";

const apiBaseUrl = import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8000";

export class ResearchApiError extends Error {
  constructor(
    public readonly kind: "network" | "server" | "malformed",
    message: string,
    public readonly code: string = "RESEARCH_REQUEST_FAILED",
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
  return { code: `HTTP_${status}`, message: `Research request failed (${status}).` };
}

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${apiBaseUrl}${path}`, {
      ...init,
      headers: { "Content-Type": "application/json", ...init?.headers },
    });
  } catch {
    throw new ResearchApiError("network", "Unable to reach the Experiment Research service.", "NETWORK_ERROR");
  }
  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    throw new ResearchApiError("malformed", "Experiment Research returned an invalid response.", "MALFORMED_RESPONSE");
  }
  if (!response.ok) {
    const error = safeMessage(payload, response.status);
    throw new ResearchApiError("server", error.message, error.code);
  }
  return payload as T;
}

const path = (protocolId: string, experimentId: string, suffix: string) =>
  `/research/protocols/${encodeURIComponent(protocolId)}/experiments/${encodeURIComponent(experimentId)}/${suffix}`;

export const researchApi = {
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
};
