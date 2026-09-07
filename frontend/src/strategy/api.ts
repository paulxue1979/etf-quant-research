import type {
  StrategyPayload,
  StrategyVersion,
  StrategyVersionSummary,
  ValidationResult,
} from "./types";

const apiBaseUrl = import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8000";

export class StrategyApiError extends Error {
  constructor(
    public readonly kind: "network" | "server" | "malformed",
    message: string,
  ) {
    super(message);
    this.name = "StrategyApiError";
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
    throw new StrategyApiError("network", "Unable to reach the Strategy Lab service.");
  }

  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    throw new StrategyApiError("malformed", "Strategy Lab returned an invalid response.");
  }

  if (!response.ok) {
    throw new StrategyApiError("server", `Strategy Lab request failed (${response.status}).`);
  }
  return payload as T;
}

export const strategyApi = {
  validate(strategy: StrategyPayload): Promise<ValidationResult> {
    return requestJson<ValidationResult>("/strategy-lab/validate", {
      method: "POST",
      body: JSON.stringify({ strategy }),
    });
  },

  async saveVersion(strategy: StrategyPayload): Promise<StrategyVersion | ValidationResult> {
    let response: Response;
    try {
      response = await fetch(`${apiBaseUrl}/strategy-lab/versions`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ strategy }),
      });
    } catch {
      throw new StrategyApiError("network", "Unable to reach the Strategy Lab service.");
    }
    let payload: unknown;
    try {
      payload = await response.json();
    } catch {
      throw new StrategyApiError("malformed", "Strategy Lab returned an invalid response.");
    }
    if (response.status === 422) {
      return payload as ValidationResult;
    }
    if (!response.ok) {
      throw new StrategyApiError("server", `Strategy Lab request failed (${response.status}).`);
    }
    return payload as StrategyVersion;
  },

  listVersions(strategyId: string): Promise<StrategyVersionSummary[]> {
    return requestJson<StrategyVersionSummary[]>(
      `/strategy-lab/strategies/${encodeURIComponent(strategyId)}/versions`,
    );
  },

  getVersion(strategyId: string, versionId: string): Promise<StrategyVersion> {
    return requestJson<StrategyVersion>(
      `/strategy-lab/strategies/${encodeURIComponent(strategyId)}/versions/${encodeURIComponent(versionId)}`,
    );
  },
};
