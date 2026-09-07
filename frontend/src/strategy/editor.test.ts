import { describe, expect, it } from "vitest";

import {
  allocationTotalPercent,
  createDefaultEditorState,
  editorReducer,
  toStrategyPayload,
} from "./editor";

describe("strategy editor serialization", () => {
  it("keeps the demo strategy in the domain wire shape", () => {
    const state = createDefaultEditorState();
    const payload = toStrategyPayload(state);
    const conditions = payload.rules[0].condition.children;

    expect(payload.price_field).toBe("adjusted_close");
    expect(payload.assets).toEqual([{ symbol: "QQQ" }, { symbol: "TQQQ" }, { symbol: "SGOV" }]);
    expect(conditions[0]).toMatchObject({
      type: "condition",
      operator: "greater_than",
      threshold: { type: "relative", value: 0.04 },
    });
    expect(payload.rules[0].allocations).toEqual([
      { symbol: "QQQ", target_weight: 0.6 },
      { symbol: "TQQQ", target_weight: 0.3 },
      { symbol: "SGOV", target_weight: 0.1 },
    ]);
  });

  it("serializes signed relative thresholds, constants, and nested groups", () => {
    let state = createDefaultEditorState();
    const ruleId = state.rules[0].id;
    const rootId = state.rules[0].condition.id;
    state = editorReducer(state, { type: "addGroup", ruleId, groupId: rootId });
    const nested = state.rules[0].condition.children.at(-1);
    expect(nested?.type).toBe("group");
    if (nested?.type !== "group") return;

    const condition = nested.children[0];
    expect(condition.type).toBe("condition");
    if (condition.type !== "condition") return;
    state = editorReducer(state, {
      type: "condition",
      ruleId,
      conditionId: condition.id,
      condition: {
        ...condition,
        operator: "less_than",
        thresholdPercent: "-3",
        left: { ...condition.left, type: "constant", value: "-1.25", asset: "QQQ" },
      },
    });

    const serialized = toStrategyPayload(state).rules[0].condition.children.at(-1);
    expect(serialized?.type).toBe("group");
    if (serialized?.type !== "group") return;
    expect(serialized.children[0]).toMatchObject({
      operator: "less_than",
      threshold: { type: "relative", value: -0.03 },
      left: { type: "constant", value: -1.25 },
    });
  });

  it("clears a threshold when equality is selected", () => {
    const initial = createDefaultEditorState();
    const ruleId = initial.rules[0].id;
    const condition = initial.rules[0].condition.children[0];
    expect(condition.type).toBe("condition");
    if (condition.type !== "condition") return;
    const changed = editorReducer(initial, {
      type: "condition",
      ruleId,
      conditionId: condition.id,
      condition: { ...condition, operator: "equal", thresholdPercent: "" },
    });

    expect(toStrategyPayload(changed).rules[0].condition.children[0]).toMatchObject({
      operator: "equal",
      threshold: null,
    });
  });

  it("calculates display totals without changing domain fractions", () => {
    const state = createDefaultEditorState();
    expect(allocationTotalPercent(state.rules[0].allocations)).toBe(100);
    expect(toStrategyPayload(state).rebalance_policy.threshold).toBe(0.05);
  });
});
