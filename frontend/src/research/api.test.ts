import { afterEach, describe, expect, it, vi } from "vitest";

import { researchApi } from "./api";

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
    });
  });
});
