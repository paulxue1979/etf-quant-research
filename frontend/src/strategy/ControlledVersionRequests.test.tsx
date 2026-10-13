import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ControlledVersionRequests } from "./ControlledVersionRequests";
import type { ControlledVersionRequest } from "./types";

const request: ControlledVersionRequest = {
  request_id: "version-request-abc",
  source: "CODEX_MCP",
  strategy_id: "exact-strategy-id",
  strategy_name: "Same display name",
  expected_draft_revision: 2,
  expected_draft_hash: "a".repeat(64),
  base_latest_version_id: "exact-strategy-id-v1",
  base_latest_version_hash: "b".repeat(64),
  active_mode: "rule_based",
  draft_snapshot: {
    strategy_id: "exact-strategy-id",
    name: "Same display name",
    description: "",
    assets: [{ symbol: "QQQ" }],
    price_field: "adjusted_close",
    rules: [],
    fallback: { name: "QQQ", allocations: [{ symbol: "QQQ", target_weight: 1 }] },
    rebalance_policy: { frequency: "weekly", threshold: 0.05 },
  },
  prepared_at: "2026-10-13T00:00:00Z",
  updated_at: "2026-10-13T00:00:00Z",
  approved_at: null,
  rejected_at: null,
  status: "PROPOSED",
  created_version_id: null,
  created_version_hash: null,
  error_code: null,
  approval_gate: { can_approve: true, reason: null },
};

function mockApi(initial: ControlledVersionRequest = request) {
  let current = initial;
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    if (path.endsWith("/approve") && init?.method === "POST") {
      current = { ...current, status: "CREATED", created_version_id: "exact-strategy-id-v2", created_version_hash: "c".repeat(64) };
      return new Response(JSON.stringify(current), { status: 200 });
    }
    if (path.endsWith("/reject") && init?.method === "POST") {
      current = { ...current, status: "REJECTED" };
      return new Response(JSON.stringify(current), { status: 200 });
    }
    return new Response(JSON.stringify([current]), { status: 200 });
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("controlled version requests", () => {
  it("fails closed when the request list response is not an array", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ unexpected: true }), { status: 200 })));
    render(<ControlledVersionRequests strategyId="exact-strategy-id" draftRevision={2} isDirty={false} onCreated={vi.fn()} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Approval is unavailable");
    expect(screen.queryByRole("button", { name: "Approve & Create Version" })).not.toBeInTheDocument();
  });

  it("shows exact identity and a readable saved Draft before browser approval", async () => {
    const fetchMock = mockApi();
    render(<ControlledVersionRequests strategyId="exact-strategy-id" draftRevision={2} isDirty={false} onCreated={vi.fn()} />);
    expect(await screen.findByText("Same display name")).toBeInTheDocument();
    expect(screen.getByText("exact-strategy-id-v1")).toBeInTheDocument();
    expect(screen.getByText("a".repeat(64))).toBeInTheDocument();
    expect(screen.getByText("b".repeat(64))).toBeInTheDocument();
    expect(screen.getByText("QQQ")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Approve & Create Version" })).toBeEnabled();
    expect(fetchMock.mock.calls[0][0]).toContain("/strategies/exact-strategy-id");
  });

  it.each([
    [{ ...request, approval_gate: { can_approve: false, reason: "DRAFT_STALE" } }, 2, false],
    [request, 3, false],
    [request, 2, true],
    [{ ...request, status: "REJECTED" as const, approval_gate: { can_approve: false, reason: "REJECTED" } }, 2, false],
    [{ ...request, status: "CREATED" as const, approval_gate: { can_approve: false, reason: "CREATED" } }, 2, false],
  ])("disables approval when the request or local Draft is not eligible", async (item, revision, dirty) => {
    mockApi(item);
    render(<ControlledVersionRequests strategyId="exact-strategy-id" draftRevision={revision} isDirty={dirty} onCreated={vi.fn()} />);
    await screen.findByText("Same display name");
    const approve = screen.queryByRole("button", { name: "Approve & Create Version" });
    if (item.status === "PROPOSED") expect(approve).toBeDisabled();
    else expect(approve).not.toBeInTheDocument();
  });

  it("refreshes the request and version history after one explicit approval", async () => {
    const fetchMock = mockApi();
    const onCreated = vi.fn();
    render(<ControlledVersionRequests strategyId="exact-strategy-id" draftRevision={2} isDirty={false} onCreated={onCreated} />);
    fireEvent.click(await screen.findByRole("button", { name: "Approve & Create Version" }));
    await waitFor(() => expect(onCreated).toHaveBeenCalledOnce());
    expect(await screen.findByText("exact-strategy-id-v2")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Approve & Create Version" })).not.toBeInTheDocument();
    expect(fetchMock.mock.calls.some((call) => String(call[0]).endsWith("/version-request-abc/approve"))).toBe(true);
  });

  it("rejects without creating a version", async () => {
    mockApi();
    const onCreated = vi.fn();
    render(<ControlledVersionRequests strategyId="exact-strategy-id" draftRevision={2} isDirty={false} onCreated={onCreated} />);
    fireEvent.click(await screen.findByRole("button", { name: "Reject" }));
    await screen.findByText("REJECTED");
    expect(onCreated).not.toHaveBeenCalled();
  });

  it("keeps the backend refusal visible after refreshing the request", async () => {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      if (String(input).endsWith("/approve")) {
        return new Response(JSON.stringify({ detail: { code: "DRAFT_STALE" } }), { status: 409 });
      }
      return new Response(JSON.stringify([request]), { status: 200 });
    }));
    render(<ControlledVersionRequests strategyId="exact-strategy-id" draftRevision={2} isDirty={false} onCreated={vi.fn()} />);
    fireEvent.click(await screen.findByRole("button", { name: "Approve & Create Version" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Draft revision or hash changed");
    expect(screen.getByRole("button", { name: "Approve & Create Version" })).toBeInTheDocument();
  });
});
