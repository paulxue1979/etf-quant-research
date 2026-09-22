import { afterEach, describe, expect, it, vi } from "vitest";

import { BacktestApiError, backtestApi } from "./api";

afterEach(() => vi.restoreAllMocks());

describe("comparison API v2", () => {
  it("requests canonical performance and opt-in wealth capabilities while preserving caller order", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({
      comparison_schema_version: "2.0",
      ordering: "request_order",
      include: { twr: true, drawdown: true, portfolio_value: false, metrics: true },
      compatibility: { twr: { status: "COMPARABLE", reason_codes: [], human_readable_reasons: [], dimensions: {} } },
      runs: [],
      series: [],
      provenance_notice: "persisted",
    }), { status: 200 }));

    await backtestApi.compare(["run-b", "run-a"]);

    const body = JSON.parse(String(fetchMock.mock.calls[0][1]?.body));
    expect(body).toEqual({
      backtest_run_ids: ["run-b", "run-a"],
      include: {
        twr: true,
        drawdown: true,
        portfolio_value: true,
        capital_invested: true,
        investment_profit: true,
        metrics: true,
      },
    });
  });

  it("accepts schema 2.1 without rejecting schema 2.0 fallback responses", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch");
    for (const version of ["2.1", "2.0"] as const) {
      fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({
        comparison_schema_version: version,
        ordering: "request_order",
        include: { twr: true, drawdown: true, portfolio_value: version === "2.1", metrics: true },
        compatibility: { twr: { status: "COMPARABLE", reason_codes: [], human_readable_reasons: [], dimensions: {} } },
        runs: [],
        series: [],
        provenance_notice: "persisted",
      }), { status: 200 }));
      await expect(backtestApi.compare(["run-a", "run-b"])).resolves.toMatchObject({ comparison_schema_version: version });
    }
  });

  it("rejects a legacy or malformed comparison response", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({ comparable: true, runs: [], series: [] }), { status: 200 }));

    await expect(backtestApi.compare(["run-a", "run-b"])).rejects.toEqual(expect.objectContaining<Partial<BacktestApiError>>({
      kind: "malformed",
      message: "Backtest Lab returned an unsupported comparison contract.",
    }));
  });
});
