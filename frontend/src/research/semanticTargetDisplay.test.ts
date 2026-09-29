import { describe, expect, it } from "vitest";

import { createDefaultEditorState, toStrategyPayload } from "../strategy/editor";
import { semanticTargetDisplay } from "./semanticTargetDisplay";

describe("semantic target display", () => {
  it("resolves internal transition ids to canonical state relationships", () => {
    const strategy = toStrategyPayload(createDefaultEditorState());
    strategy.transitions = [{ transition_id: "transition-10", from_state: "RECOVERY_HOLD", to_state: "FULL_RISK_ON" }];
    const target = {
      parameter_name: "threshold",
      value_type: "relative_threshold",
      target_kind: "nested_condition_threshold",
      label: "Transition-10",
      descriptor: { transition_id: "transition-10" },
      supported: true,
    };

    const display = semanticTargetDisplay(target, strategy);

    expect(display.label).toContain("恢复持有 → 全风险开启｜触发阈值");
    expect(display.label).not.toContain("Transition-10");
  });

  it("keeps canonical asset identity while displaying a readable state name", () => {
    const strategy = toStrategyPayload(createDefaultEditorState());
    const target = {
      parameter_name: "weight",
      value_type: "allocation_weight",
      target_kind: "state_allocation_weight",
      label: "internal target",
      descriptor: { state_id: "FULL_RISK_ON", symbol: "QQQ" },
      supported: true,
    };

    expect(semanticTargetDisplay(target, strategy).label).toBe("全风险开启｜QQQ 目标仓位");
  });
});
