import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function mockApi() {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = String(input);
    if (url.endsWith("/versions") && init?.method !== "POST") {
      return new Response("[]", { status: 200, headers: { "Content-Type": "application/json" } });
    }
    if (url.endsWith("/validate")) {
      return new Response(JSON.stringify({ is_valid: true, errors: [], warnings: [] }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }
    if (url.endsWith("/versions")) {
      return new Response(
        JSON.stringify({
          strategy_id: "qqq-tqqq-sgov-dynamic-allocation",
          version_id: "strategy-v1",
          version_number: 1,
          created_at: "2026-09-07T00:00:00Z",
          content_hash: "abcdef1234567890",
          status: "draft",
          configuration: {},
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    }
    return new Response("{}", { status: 404 });
  });
}

describe("Strategy Lab", () => {
  it("renders structured strategy configuration and supports adding assets", async () => {
    mockApi();
    render(<App />);

    expect(screen.getByRole("heading", { name: "Strategy Lab" })).toBeInTheDocument();
    expect(screen.getByText("QQQ / TQQQ / SGOV Dynamic Allocation")).toBeInTheDocument();

    fireEvent.change(screen.getByRole("textbox", { name: "New asset symbol" }), {
      target: { value: "SPY" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Add asset" }));

    expect(screen.getAllByText("SPY").length).toBeGreaterThan(0);
    await waitFor(() => expect(screen.queryByText("No saved versions in this process.")).toBeInTheDocument());
  });

  it("disables relative thresholds for exact equality and sends validation through the API", async () => {
    const fetchMock = mockApi();
    render(<App />);

    fireEvent.change(screen.getAllByRole("combobox", { name: "Condition operator" })[0], {
      target: { value: "equal" },
    });
    expect(screen.getAllByRole("textbox", { name: "Relative threshold percent" })[0]).toBeDisabled();

    await waitFor(() => expect(screen.getByText("No saved versions in this process.")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Validate strategy" }));
    await waitFor(() => expect(screen.getByText("Strategy configuration is valid.")).toBeInTheDocument());
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("/strategy-lab/validate"),
      expect.objectContaining({ method: "POST" }),
    );
  });
});
