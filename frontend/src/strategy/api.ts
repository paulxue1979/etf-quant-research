import type {
  StrategyPayload,
  StrategyDraftPayload,
  StrategyDraftResponse,
  StrategyRecord,
  StrategyVersion,
  StrategyVersionSummary,
  SemanticTargetCatalog,
  VersionDiff,
  ValidationResult,
  BulkStrategyTrashResponse,
  StrategyDependency,
  StrategyLifecycleResponse,
  StrategyDiagnostics,
  OperatorChangeSet,
  OperatorChangeSetDecision,
} from "./types";

import { apiBaseUrl } from "../apiConfig";

export class StrategyApiError extends Error {
  constructor(
    public readonly kind: "network" | "server" | "malformed",
    message: string,
    public readonly status?: number,
    public readonly code?: string,
  ) {
    super(message);
    this.name = "StrategyApiError";
  }
}

async function requestJson<T>(path: string, init?: RequestInit, acceptedStatuses: number[] = []): Promise<T> {
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

  if (!response.ok && !acceptedStatuses.includes(response.status)) {
    const detail = typeof payload === "object" && payload !== null && "detail" in payload
      ? (payload as { detail?: unknown }).detail
      : undefined;
    throw new StrategyApiError(
      "server",
      `Strategy Lab request failed (${response.status}).`,
      response.status,
      typeof detail === "string" ? detail : undefined,
    );
  }
  return payload as T;
}

export const strategyApi = {
  createStrategy(request: Pick<StrategyRecord, "strategy_id" | "name" | "description"> & { metadata?: Record<string, unknown> }): Promise<StrategyRecord> {
    return requestJson<StrategyRecord>("/strategy-lab/strategies", {
      method: "POST",
      body: JSON.stringify(request),
    });
  },

  getStrategy(strategyId: string): Promise<StrategyRecord> {
    return requestJson<StrategyRecord>(
      `/strategy-lab/strategies/${encodeURIComponent(strategyId)}`,
    );
  },

  listStrategies(lifecycle: "ACTIVE" | "TRASHED" | "ALL" = "ALL"): Promise<StrategyRecord[]> {
    return requestJson<StrategyRecord[]>(`/strategy-lab/strategies?lifecycle=${lifecycle}`);
  },

  listDependencies(strategyId: string): Promise<StrategyDependency[]> {
    return requestJson<StrategyDependency[]>(
      `/strategy-lab/strategies/${encodeURIComponent(strategyId)}/dependencies`,
    );
  },

  trashStrategy(strategyId: string): Promise<StrategyLifecycleResponse> {
    return requestJson<StrategyLifecycleResponse>(
      `/strategy-lab/strategies/${encodeURIComponent(strategyId)}/trash`,
      { method: "POST" },
    );
  },

  restoreStrategy(strategyId: string): Promise<StrategyLifecycleResponse> {
    return requestJson<StrategyLifecycleResponse>(
      `/strategy-lab/strategies/${encodeURIComponent(strategyId)}/restore`,
      { method: "POST" },
    );
  },

  setStrategyProtected(strategyId: string, protectedValue: boolean): Promise<StrategyLifecycleResponse> {
    return requestJson<StrategyLifecycleResponse>(
      `/strategy-lab/strategies/${encodeURIComponent(strategyId)}/${protectedValue ? "protect" : "unprotect"}`,
      { method: "POST" },
    );
  },

  permanentlyDeleteStrategy(strategyId: string): Promise<StrategyLifecycleResponse> {
    return requestJson<StrategyLifecycleResponse>(
      `/strategy-lab/strategies/${encodeURIComponent(strategyId)}`,
      { method: "DELETE" },
    );
  },

  bulkTrashStrategies(strategyIds: string[]): Promise<BulkStrategyTrashResponse> {
    return requestJson<BulkStrategyTrashResponse>("/strategy-lab/strategies/bulk-trash", {
      method: "POST",
      body: JSON.stringify({ strategy_ids: strategyIds }),
    });
  },

  diagnostics(): Promise<StrategyDiagnostics> {
    return requestJson<StrategyDiagnostics>("/strategy-lab/diagnostics");
  },

  async getDraft(strategyId: string): Promise<StrategyDraftResponse | null> {
    try {
      return await requestJson<StrategyDraftResponse>(
        `/strategy-lab/strategies/${encodeURIComponent(strategyId)}/draft`,
      );
    } catch (error) {
      if (error instanceof StrategyApiError && error.status === 404 && error.code === "DRAFT_NOT_FOUND") {
        return null;
      }
      throw error;
    }
  },

  listChangeSets(strategyId: string): Promise<OperatorChangeSet[]> {
    return requestJson<OperatorChangeSet[]>(
      `/strategy-lab/strategies/${encodeURIComponent(strategyId)}/change-sets`,
    );
  },

  approveChangeSet(strategyId: string, changeSetId: string): Promise<OperatorChangeSetDecision> {
    return requestJson<OperatorChangeSetDecision>(
      `/strategy-lab/strategies/${encodeURIComponent(strategyId)}/change-sets/${encodeURIComponent(changeSetId)}/approve`,
      { method: "POST" },
    );
  },

  rejectChangeSet(strategyId: string, changeSetId: string): Promise<OperatorChangeSetDecision> {
    return requestJson<OperatorChangeSetDecision>(
      `/strategy-lab/strategies/${encodeURIComponent(strategyId)}/change-sets/${encodeURIComponent(changeSetId)}/reject`,
      { method: "POST" },
    );
  },

  saveDraft(strategyId: string, draft: StrategyDraftPayload): Promise<StrategyDraftResponse> {
    return requestJson<StrategyDraftResponse>(
      `/strategy-lab/strategies/${encodeURIComponent(strategyId)}/draft`,
      { method: "PUT", body: JSON.stringify(draft) },
    );
  },

  createVersionFromDraft(strategyId: string, expectedRevision: number): Promise<StrategyVersion | ValidationResult> {
    return requestJson<StrategyVersion | ValidationResult>(
      `/strategy-lab/strategies/${encodeURIComponent(strategyId)}/versions/from-draft`,
      { method: "POST", body: JSON.stringify({ expected_revision: expectedRevision }) },
      [422],
    );
  },

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

  getSemanticTargets(strategyId: string, versionId: string): Promise<SemanticTargetCatalog> {
    return requestJson<SemanticTargetCatalog>(
      `/strategy-lab/strategies/${encodeURIComponent(strategyId)}/versions/${encodeURIComponent(versionId)}/semantic-targets`,
    );
  },

  createDraftFromVersion(
    strategyId: string,
    versionId: string,
    expectedRevision: number,
    replaceCurrent = false,
  ): Promise<StrategyDraftResponse> {
    return requestJson<StrategyDraftResponse>(
      `/strategy-lab/strategies/${encodeURIComponent(strategyId)}/drafts/from-version/${encodeURIComponent(versionId)}`,
      {
        method: "POST",
        body: JSON.stringify({ expected_revision: expectedRevision, replace_current: replaceCurrent }),
      },
    );
  },

  cloneFromVersion(
    strategyId: string,
    versionId: string,
    request: { new_strategy_id: string; name: string; description: string },
  ): Promise<StrategyRecord> {
    return requestJson<StrategyRecord>(
      `/strategy-lab/strategies/${encodeURIComponent(strategyId)}/clone-from-version/${encodeURIComponent(versionId)}`,
      { method: "POST", body: JSON.stringify(request) },
    );
  },

  diffVersions(strategyId: string, fromVersionId: string, toVersionId: string): Promise<VersionDiff> {
    return requestJson<VersionDiff>(
      `/strategy-lab/strategies/${encodeURIComponent(strategyId)}/versions/${encodeURIComponent(fromVersionId)}/diff/${encodeURIComponent(toVersionId)}`,
    );
  },
};
