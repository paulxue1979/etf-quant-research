import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { createDefaultEditorState, toStrategyPayload } from "./editor";
import { StrategyVersionView } from "./StrategyVersionView";
import type { StrategyVersion, StrategyVersionSummary, VersionDiff } from "./types";

const version = (): StrategyVersion => {
  const configuration = toStrategyPayload(createDefaultEditorState());
  return {
    strategy_id: configuration.strategy_id,
    version_id: "strategy-v1",
    version_number: 1,
    created_at: "2026-01-01T00:00:00Z",
    content_hash: "hash-1",
    status: "draft",
    active_mode: "rule_based",
    configuration,
  };
};

describe("StrategyVersionView", () => {
  it("keeps historical versions read-only and renders semantic diffs", () => {
    const current = version();
    const other: StrategyVersionSummary = { ...current, version_id: "strategy-v2", version_number: 2 };
    const diff: VersionDiff = { strategy_id: current.strategy_id, from_version_id: "strategy-v2", to_version_id: current.version_id, from_version_number: 2, to_version_number: 1, items: [{ path: "$.rules[0].priority", change: "changed", before: 2, after: 1 }] };
    render(<StrategyVersionView version={current} versions={[current, other]} diff={diff} onBack={vi.fn()} onCreateDraft={vi.fn()} onClone={vi.fn()} onCompare={vi.fn()} onRunBacktest={vi.fn()} />);

    expect(screen.getByText("READ ONLY")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Create Draft From Version" })).toBeInTheDocument();
    expect(screen.getByText("$.rules[0].priority")).toBeInTheDocument();
    expect(screen.queryByRole("textbox", { name: "Strategy name" })).not.toBeInTheDocument();
  });
});
