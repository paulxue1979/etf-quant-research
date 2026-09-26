import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { StrategyLibrary } from "./StrategyLibrary";
import type { StrategyRecord } from "./types";

const strategies: StrategyRecord[] = [
  {
    strategy_id: "qqq-sma200",
    name: "QQQ SMA200",
    description: "Long-term trend draft",
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    status: "active",
    metadata: {},
    draft_revision: 2,
    draft_status: "saved",
    draft_active_mode: "regime_state_machine",
    latest_version_id: "qqq-sma200-v1",
    latest_version_number: 1,
  },
  {
    strategy_id: "cash-rule",
    name: "Cash Rule",
    description: "A rule based draft",
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    status: "active",
    metadata: {},
    draft_status: "not_saved",
  },
];

describe("StrategyLibrary", () => {
  it("filters by name/id and exposes draft/version actions", () => {
    const onOpenDraft = vi.fn();
    render(<StrategyLibrary strategies={strategies} loading={false} onNew={vi.fn()} onOpenDraft={onOpenDraft} onOpenLatest={vi.fn()} onOpenHistory={vi.fn()} onClone={vi.fn()} onBack={vi.fn()} />);

    expect(screen.getByText("QQQ SMA200")).toBeInTheDocument();
    expect(screen.getByText("Cash Rule")).toBeInTheDocument();
    fireEvent.change(screen.getByRole("textbox", { name: "Search strategies" }), { target: { value: "sma200" } });
    expect(screen.getByText("QQQ SMA200")).toBeInTheDocument();
    expect(screen.queryByText("Cash Rule")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Open Draft" }));
    expect(onOpenDraft).toHaveBeenCalledWith("qqq-sma200");
  });
});
