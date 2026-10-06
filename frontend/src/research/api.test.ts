import { afterEach, describe, expect, it, vi } from "vitest";

import { ResearchApiError, researchApi } from "./api";

describe("research API error classification", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("classifies a non-JSON HTTP 500 as a server error", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: false,
      status: 500,
      json: vi.fn().mockRejectedValue(new Error("plain text")),
    }));

    await expect(researchApi.listExperiments()).rejects.toMatchObject({
      kind: "server",
      code: "HTTP_500",
      status: 500,
      message: "Server processing error (500).",
    });
  });

  it("keeps a real network failure distinct from an HTTP server failure", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("connection refused")));

    await expect(researchApi.listExperiments()).rejects.toMatchObject({
      kind: "network",
      code: "NETWORK_OR_CORS_FAILURE",
    } satisfies Partial<ResearchApiError>);
  });

  it("uses server-controlled official OOS workflow endpoints without overrides", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: vi.fn().mockResolvedValue({ execution: { execution_id: "oos-1", status: "pending" } }),
    });
    vi.stubGlobal("fetch", fetchMock);

    await researchApi.prepareOfficialOos("protocol/1");
    await researchApi.executeOfficialOos("protocol/1");

    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      "http://127.0.0.1:8000/research/protocols/protocol%2F1/oos/prepare",
      expect.objectContaining({ method: "POST" }),
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      "http://127.0.0.1:8000/research/protocols/protocol%2F1/oos/execute",
      expect.objectContaining({ method: "POST" }),
    );
  });
});
