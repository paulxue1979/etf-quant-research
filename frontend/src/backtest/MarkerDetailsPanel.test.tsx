import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { MarkerDetailsPanel } from "./MarkerDetailsPanel";

describe("MarkerDetailsPanel", () => {
  it("renders every underlying grouped event as a separate readable item", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(<MarkerDetailsPanel marker={{
      date: "2025-01-03",
      kind: "group",
      label: "3 Events",
      shortLabel: "3",
      color: "#d8e1e5",
      details: [],
      groupId: "group-1",
      markerCount: 3,
      markerIds: ["r-1", "t-1", "e-1"],
      markerTypes: ["REGIME_TRANSITION", "TARGET_ALLOCATION_TRANSITION", "EXECUTION"],
      events: [
        { markerId: "r-1", markerType: "REGIME_TRANSITION", title: "Regime", summary: "DEEP_VALUE -> RECOVERY_HOLD", sourceEventType: "Canonical.Regime", sourceEventReference: "regime-1", details: ["From state: DEEP_VALUE", "To state: RECOVERY_HOLD"] },
        { markerId: "t-1", markerType: "TARGET_ALLOCATION_TRANSITION", title: "Target Allocation", summary: "QQQ 40% · Cash 60%", sourceEventType: "Canonical.Target", sourceEventReference: "target-1", details: ["After: QQQ 40.00% · CASH 60.00%"] },
        { markerId: "e-1", markerType: "EXECUTION", title: "Execution", summary: "BUY QQQ", sourceEventType: "BacktestResult.fills", sourceEventReference: "fill-1", details: ["BUY QQQ", "Quantity: 120", "Fill price: $100.00", "Notional: $12,000.00", "Commission: $1.00", "Slippage: $0.00"] },
      ],
    }} onClose={onClose} />);

    expect(screen.getAllByRole("article")).toHaveLength(3);
    expect(screen.getByText("REGIME TRANSITION")).toBeInTheDocument();
    expect(screen.getByText("TARGET ALLOCATION TRANSITION")).toBeInTheDocument();
    expect(screen.getByText("EXECUTION")).toBeInTheDocument();
    expect(screen.getAllByText("BUY QQQ")).toHaveLength(2);
    await user.click(screen.getByRole("button", { name: "Close event details" }));
    expect(onClose).toHaveBeenCalledOnce();
  });
});
