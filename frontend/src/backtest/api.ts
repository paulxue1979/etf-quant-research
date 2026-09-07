import type { StrategyVersionSummary } from "../strategy/types";
import type { BacktestRequest, BacktestRun, StrategyCatalogItem } from "./types";

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
    const detail = typeof payload === "object" && payload !== null && "detail" in payload
      ? String(payload.detail)
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
};
