import type { StrategyVersionSummary } from "../strategy/types";
import type {
  BacktestRequest,
  BacktestRun,
  ResearchBacktestList,
  ResearchComparison,
  ResearchProtocolCreateRequest,
  ResearchProtocolDetail,
  ResearchProtocolStatus,
  ResearchSortBy,
  StrategyCatalogItem,
} from "./types";

const apiBaseUrl = import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8000";

export class BacktestApiError extends Error {
  constructor(
    public readonly kind: "network" | "server" | "malformed",
    message: string,
  ) {
    super(message);
    this.name = "BacktestApiError";
  }
}

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${apiBaseUrl}${path}`, {
      ...init,
      headers: { "Content-Type": "application/json", ...init?.headers },
    });
  } catch {
    throw new BacktestApiError("network", "Unable to reach the Backtest Lab service.");
  }

  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    throw new BacktestApiError("malformed", "Backtest Lab returned an invalid response.");
  }

  if (!response.ok) {
    const detailValue = typeof payload === "object" && payload !== null && "detail" in payload
      ? payload.detail
      : null;
    const detail = typeof detailValue === "object" && detailValue !== null && "message" in detailValue
      ? String(detailValue.message)
      : typeof detailValue === "string"
        ? detailValue
        : `Backtest Lab request failed (${response.status}).`;
    throw new BacktestApiError("server", detail);
  }
  return payload as T;
}

export const backtestApi = {
  listStrategies(): Promise<StrategyCatalogItem[]> {
    return requestJson<StrategyCatalogItem[]>("/strategies");
  },

  listVersions(strategyId: string): Promise<StrategyVersionSummary[]> {
    return requestJson<StrategyVersionSummary[]>(
      `/strategy-lab/strategies/${encodeURIComponent(strategyId)}/versions`,
    );
  },

  create(request: BacktestRequest): Promise<BacktestRun> {
    return requestJson<BacktestRun>("/backtests", {
      method: "POST",
      body: JSON.stringify(request),
    });
  },

  get(runId: string): Promise<BacktestRun> {
    return requestJson<BacktestRun>(`/backtests/${encodeURIComponent(runId)}`);
  },

  listResearchRuns(sortBy: ResearchSortBy, order: "asc" | "desc"): Promise<ResearchBacktestList> {
    const query = new URLSearchParams({ limit: "50", sort_by: sortBy, order });
    return requestJson<ResearchBacktestList>(`/research/backtests?${query.toString()}`);
  },

  compare(runIds: string[]): Promise<ResearchComparison> {
    return requestJson<ResearchComparison>("/research/comparisons", {
      method: "POST",
      body: JSON.stringify({ backtest_run_ids: runIds }),
    });
  },

  listResearchProtocols(): Promise<ResearchProtocolDetail["protocol"][]> {
    return requestJson<ResearchProtocolDetail["protocol"][]>("/research/protocols");
  },

  getResearchProtocol(protocolId: string): Promise<ResearchProtocolDetail> {
    return requestJson<ResearchProtocolDetail>(`/research/protocols/${encodeURIComponent(protocolId)}`);
  },

  createResearchProtocol(request: ResearchProtocolCreateRequest): Promise<ResearchProtocolDetail> {
    return requestJson<ResearchProtocolDetail>("/research/protocols", {
      method: "POST",
      body: JSON.stringify(request),
    });
  },

  createCandidateSet(protocolId: string, strategyVersionIds: string[]): Promise<ResearchProtocolDetail> {
    return requestJson<ResearchProtocolDetail>(`/research/protocols/${encodeURIComponent(protocolId)}/candidate-sets`, {
      method: "POST",
      body: JSON.stringify({ strategy_version_ids: strategyVersionIds }),
    });
  },

  lockCandidateSet(candidateSetId: string): Promise<ResearchProtocolDetail> {
    return requestJson<ResearchProtocolDetail>(`/research/candidate-sets/${encodeURIComponent(candidateSetId)}/lock`, {
      method: "POST",
    });
  },

  transitionResearchProtocol(protocolId: string, status: ResearchProtocolStatus): Promise<ResearchProtocolDetail> {
    return requestJson<ResearchProtocolDetail>(`/research/protocols/${encodeURIComponent(protocolId)}/transitions`, {
      method: "POST",
      body: JSON.stringify({ status }),
    });
  },

  createSelectionDecision(
    protocolId: string,
    request: {
      candidate_set_id: string;
      selected_strategy_version_id: string;
      is_backtest_run_ids: string[];
      rationale: string;
    },
  ): Promise<ResearchProtocolDetail> {
    return requestJson<ResearchProtocolDetail>(`/research/protocols/${encodeURIComponent(protocolId)}/selection-decisions`, {
      method: "POST",
      body: JSON.stringify(request),
    });
  },

  freezeResearchStrategy(protocolId: string, selectionDecisionId: string, reason: string): Promise<ResearchProtocolDetail> {
    return requestJson<ResearchProtocolDetail>(`/research/protocols/${encodeURIComponent(protocolId)}/freeze`, {
      method: "POST",
      body: JSON.stringify({ selection_decision_id: selectionDecisionId, reason }),
    });
  },

  observeOos(
    protocolId: string,
    freezeId: string,
    backtestRunId: string,
  ): Promise<ResearchProtocolDetail> {
    return requestJson<ResearchProtocolDetail>(`/research/protocols/${encodeURIComponent(protocolId)}/oos-evaluations`, {
      method: "POST",
      body: JSON.stringify({ freeze_id: freezeId, backtest_run_id: backtestRunId }),
    });
  },
};
