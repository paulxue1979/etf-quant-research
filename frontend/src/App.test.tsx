import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";
import { createDefaultEditorState, toStrategyPayload } from "./strategy/editor";
import type { StrategyPayload, StrategyVersionSummary } from "./strategy/types";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function mockApi() {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = String(input);
    if (url.endsWith("/strategies") && init?.method === "POST") {
      const body = JSON.parse(String(init.body));
      return new Response(JSON.stringify({
        strategy_id: body.strategy_id,
        name: body.name,
        description: body.description,
        created_at: "2026-09-07T00:00:00Z",
        updated_at: "2026-09-07T00:00:00Z",
        status: "active",
        metadata: {},
      }), { status: 201, headers: { "Content-Type": "application/json" } });
    }
    if (url.endsWith("/draft") && init?.method === "GET") {
      return new Response(JSON.stringify({ detail: "DRAFT_NOT_FOUND" }), { status: 404 });
    }
    if (url.endsWith("/draft") && init?.method === "PUT") {
      const body = JSON.parse(String(init.body));
      return new Response(JSON.stringify({
        ...body,
        draft_revision: body.expected_revision + 1,
        created_at: "2026-09-07T00:00:00Z",
        updated_at: "2026-09-07T00:00:00Z",
        draft_hash: "draft-hash",
      }), { status: 200, headers: { "Content-Type": "application/json" } });
    }
    if (url.includes("/versions/from-draft")) {
      return new Response(JSON.stringify({
        strategy_id: "qqq-tqqq-sgov-dynamic-allocation",
        version_id: "strategy-v1",
        version_number: 1,
        created_at: "2026-09-07T00:00:00Z",
        content_hash: "abcdef1234567890",
        status: "draft",
        configuration: {},
      }), { status: 200, headers: { "Content-Type": "application/json" } });
    }
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
  it("switches between fallback and hold-previous allocation controls", async () => {
    mockApi();
    render(<App />);

    expect(screen.getByRole("heading", { name: "Fallback allocation" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Starting Allocation" })).not.toBeInTheDocument();
    expect(screen.queryByText(/You do not re-enter that allocation/)).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("radio", { name: "Hold Previous Allocation" }));

    expect(screen.queryByRole("heading", { name: "Fallback allocation" })).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Starting Allocation" })).toBeInTheDocument();
    expect(
      screen.getByText(/If no rule matches, the strategy keeps its most recently resolved target/),
    ).toBeInTheDocument();
    expect(screen.getByText(/Used only before the strategy has resolved its first target/)).toBeInTheDocument();
    expect(screen.getByText(/It is not reapplied on every Hold Previous decision/)).toBeInTheDocument();
    expect(screen.getByText("Cash 100.00%")).toBeInTheDocument();
  });

  it("preserves the starting allocation while switching no-match behavior", () => {
    mockApi();
    render(<App />);

    fireEvent.click(screen.getByRole("radio", { name: "Hold Previous Allocation" }));
    fireEvent.click(screen.getByRole("button", { name: /Add starting allocation/ }));
    fireEvent.change(screen.getByRole("combobox", { name: "Starting allocation asset" }), { target: { value: "SGOV" } });
    fireEvent.change(screen.getByRole("textbox", { name: "Starting allocation weight percent" }), { target: { value: "35" } });

    fireEvent.click(screen.getByRole("radio", { name: "Use Fallback Allocation" }));
    expect(screen.queryByText(/You do not re-enter that allocation/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("radio", { name: "Hold Previous Allocation" }));

    expect(screen.getByRole("combobox", { name: "Starting allocation asset" })).toHaveValue("SGOV");
    expect(screen.getByRole("textbox", { name: "Starting allocation weight percent" })).toHaveValue("35");
    expect(screen.getByText("Cash 65.00%")).toBeInTheDocument();
  });

  it("saves hold-previous behavior with a cash-only initial allocation", async () => {
    const fetchMock = mockApi();
    render(<App />);
    await waitFor(() => expect(screen.getByText("No saved versions yet.")).toBeInTheDocument());

    fireEvent.click(screen.getByRole("radio", { name: "Hold Previous Allocation" }));
    fireEvent.click(screen.getByRole("button", { name: "Save Draft" }));
    await waitFor(() => expect(screen.getByText("Draft saved (revision 1).")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Save new version" }));

    await waitFor(() => expect(screen.getByText("Saved immutable version v1.")).toBeInTheDocument());
    const saveCall = fetchMock.mock.calls.find(
      ([input, init]) => String(input).endsWith("/draft") && init?.method === "PUT",
    );
    const body = JSON.parse(String(saveCall?.[1]?.body));
    expect(body.active_strategy_payload.no_match_behavior).toBe("hold_previous_allocation");
    expect(body.active_strategy_payload.initial_allocation).toEqual({ allocations: [] });
  });

  it("shows structured backend validation errors in the workflow", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = String(input);
      if (url.endsWith("/versions")) return new Response("[]", { status: 200 });
      if (url.endsWith("/validate")) {
        return new Response(
          JSON.stringify({
            is_valid: false,
            errors: [
              {
                code: "MissingInitialAllocation",
                path: "initial_allocation",
                message: "Hold Previous Allocation requires an Initial Allocation.",
              },
            ],
            warnings: [],
          }),
          { status: 200 },
        );
      }
      return new Response("{}", { status: 404 });
    });
    render(<App />);
    await waitFor(() => expect(screen.getByText("No saved versions yet.")).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: "Validate strategy" }));

    await waitFor(() =>
      expect(
        screen.getByText("Hold Previous Allocation requires an Initial Allocation."),
      ).toBeInTheDocument(),
    );
  });

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
    await waitFor(() => expect(screen.queryByText("No saved versions yet.")).toBeInTheDocument());
  });

  it("shows active-mode asset reference diagnostics before removal", async () => {
    mockApi();
    render(<App />);

    fireEvent.click(screen.getByRole("button", { name: "Remove SGOV" }));

    expect(screen.getByRole("heading", { name: "Cannot remove SGOV" })).toBeInTheDocument();
    expect(screen.getByText("Rule Based → Fallback Allocation")).toBeInTheDocument();
    fireEvent.click(screen.getAllByRole("button", { name: "Locate Reference" })[0]);
    expect(screen.getByRole("heading", { name: "Fallback allocation" })).toBeInTheDocument();
  });

  it("disables relative thresholds for exact equality and sends validation through the API", async () => {
    const fetchMock = mockApi();
    render(<App />);

    fireEvent.change(screen.getAllByRole("combobox", { name: "Condition operator" })[0], {
      target: { value: "equal" },
    });
    expect(screen.getAllByRole("textbox", { name: "Relative threshold percent" })[0]).toBeDisabled();

    await waitFor(() => expect(screen.getByText("No saved versions yet.")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Validate strategy" }));
    await waitFor(() => expect(screen.getByText("Strategy configuration is valid.")).toBeInTheDocument());
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("/strategy-lab/validate"),
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("persists versions, reloads history, loads a snapshot, and creates the next version", async () => {
    const configuration = toStrategyPayload(createDefaultEditorState());
    const savedConfiguration: StrategyPayload = { ...configuration, name: "Persisted strategy" };
    const versions: StrategyVersionSummary[] = [];
    let saveCount = 0;
    let draftRevision = 0;
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.endsWith("/strategies") && init?.method === "POST") {
        const body = JSON.parse(String(init.body));
        return new Response(JSON.stringify({
          strategy_id: body.strategy_id, name: body.name, description: body.description,
          created_at: "2026-09-07T00:00:00Z", updated_at: "2026-09-07T00:00:00Z",
          status: "active", metadata: {},
        }), { status: 201 });
      }
      if (url.endsWith("/draft") && init?.method === "GET") {
        return new Response(JSON.stringify({ detail: "DRAFT_NOT_FOUND" }), { status: 404 });
      }
      if (url.endsWith("/draft") && init?.method === "PUT") {
        const body = JSON.parse(String(init.body));
        draftRevision = body.expected_revision + 1;
        return new Response(JSON.stringify({
          ...body, draft_revision: draftRevision, created_at: "2026-09-07T00:00:00Z",
          updated_at: "2026-09-07T00:00:00Z", draft_hash: `draft-${draftRevision}`,
        }), { status: 200 });
      }
      if (url.includes("/drafts/from-version/")) {
        return new Response(JSON.stringify({
          strategy_id: savedConfiguration.strategy_id,
          draft_revision: draftRevision + 1,
          active_mode: "rule_based",
          name: savedConfiguration.name,
          description: savedConfiguration.description,
          price_field: "adjusted_close",
          rebalance_frequency: "weekly",
          rebalance_threshold_percent: "5",
          rule_based_draft: { assets: ["QQQ", "TQQQ", "SGOV"], editor_state: { rules: [], fallbackAllocations: [] } },
          regime_draft: { assets: [], editor_state: {} },
          active_strategy_payload: savedConfiguration,
          source_metadata: {},
          created_at: "2026-09-07T00:00:00Z",
          updated_at: "2026-09-07T00:00:00Z",
          draft_hash: "draft-from-version",
        }), { status: 200 });
      }
      if (url.includes("/versions/from-draft")) {
        saveCount += 1;
        const version = {
          strategy_id: savedConfiguration.strategy_id,
          version_id: `strategy-v${saveCount}`,
          version_number: saveCount,
          created_at: `2026-09-07T00:0${saveCount}:00Z`,
          content_hash: `hash-${saveCount}`,
          status: "draft",
          configuration: saveCount === 1 ? savedConfiguration : configuration,
        };
        versions.push({ ...version });
        return new Response(JSON.stringify(version), { status: 200 });
      }
      if (url.endsWith("/versions") && init?.method !== "POST") {
        return new Response(JSON.stringify(versions), { status: 200 });
      }
      if (url.endsWith("/validate")) {
        return new Response(JSON.stringify({ is_valid: true, errors: [], warnings: [] }), { status: 200 });
      }
      if (url.endsWith("/versions") && init?.method === "POST") {
        saveCount += 1;
        const version = {
          strategy_id: savedConfiguration.strategy_id,
          version_id: `strategy-v${saveCount}`,
          version_number: saveCount,
          created_at: `2026-09-07T00:0${saveCount}:00Z`,
          content_hash: `hash-${saveCount}`,
          status: "draft",
          configuration: saveCount === 1 ? savedConfiguration : configuration,
        };
        versions.push({ ...version });
        return new Response(JSON.stringify(version), { status: 200 });
      }
      if (url.includes("/versions/strategy-v1")) {
        return new Response(
          JSON.stringify({
            strategy_id: savedConfiguration.strategy_id,
            version_id: "strategy-v1",
            version_number: 1,
            created_at: "2026-09-07T00:01:00Z",
            content_hash: "hash-1",
            status: "draft",
            configuration: savedConfiguration,
          }),
          { status: 200 },
        );
      }
      return new Response("{}", { status: 404 });
    });

    const firstRender = render(<App />);
    await waitFor(() => expect(screen.getByText("No saved versions yet.")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Save Draft" }));
    await waitFor(() => expect(screen.getByText("Draft saved (revision 1).")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Save new version" }));
    await waitFor(() => expect(screen.getByText("Saved immutable version v1.")).toBeInTheDocument());
    expect(saveCount).toBe(1);
    expect(screen.getByRole("button", { name: /v1/ })).toBeInTheDocument();

    firstRender.unmount();
    render(<App />);
    await waitFor(() => expect(screen.getByRole("button", { name: /v1/ })).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: /v1/ }));
    await waitFor(() => expect(screen.getByText("READ ONLY")).toBeInTheDocument());
    expect(screen.getByText("This snapshot cannot be edited. Create a draft to continue editing.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Create Draft From Version" }));
    await waitFor(() => expect(screen.getByDisplayValue("Persisted strategy")).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: "Save Draft" }));
    await waitFor(() => expect(screen.getByText("Draft saved (revision 3).")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Save new version" }));
    await waitFor(() => expect(screen.getByText("Saved immutable version v2.")).toBeInTheDocument());
    expect(saveCount).toBe(2);
    expect(versions.map((version) => version.version_number)).toEqual([1, 2]);
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("/strategy-lab/strategies/"),
      expect.objectContaining({ method: "POST" }),
    );
  });
});
