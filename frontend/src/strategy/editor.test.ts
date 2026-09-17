import { describe, expect, it } from "vitest";

import {
  allocationTotalPercent,
  createDefaultEditorState,
  editorReducer,
  fromStrategyPayload,
  toStrategyPayload,
  validateEditorState,
} from "./editor";
import type { EditorState, StrategyPayload } from "./types";

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

  it("serializes hold-previous behavior with an implicit-cash initial allocation", () => {
    const state = {
      ...createDefaultEditorState(),
      noMatchBehavior: "hold_previous_allocation",
      initialAllocations: [],
    } as EditorState & {
      noMatchBehavior: "hold_previous_allocation";
      initialAllocations: [];
    };

    expect(toStrategyPayload(state)).toMatchObject({
      no_match_behavior: "hold_previous_allocation",
      initial_allocation: { allocations: [] },
    });
  });

  it("hydrates legacy payloads as use-fallback without injecting canonical defaults", () => {
    const legacyPayload = toStrategyPayload(createDefaultEditorState());
    const restored = fromStrategyPayload(legacyPayload) as EditorState & {
      noMatchBehavior?: string;
      initialAllocations?: unknown[];
    };

    expect(restored.noMatchBehavior).toBe("use_fallback");
    expect(restored.initialAllocations).toEqual([]);
    expect(toStrategyPayload(restored)).not.toHaveProperty("no_match_behavior");
    expect(toStrategyPayload(restored)).not.toHaveProperty("initial_allocation");
  });

  it("round-trips the golden stateful strategy fields and signed thresholds", () => {
    const payload = toStrategyPayload(createDefaultEditorState()) as StrategyPayload & {
      no_match_behavior?: "hold_previous_allocation";
      initial_allocation?: { allocations: [] };
    };
    payload.no_match_behavior = "hold_previous_allocation";
    payload.initial_allocation = { allocations: [] };
    payload.rebalance_policy.frequency = "on_signal_change";
    const firstCondition = payload.rules[0].condition.children[0];
    expect(firstCondition.type).toBe("condition");
    if (firstCondition.type !== "condition") return;
    firstCondition.threshold = { type: "relative", value: 0.04 };

    const restored = fromStrategyPayload(payload);
    const serialized = toStrategyPayload(restored);

    expect(serialized.no_match_behavior).toBe("hold_previous_allocation");
    expect(serialized.initial_allocation).toEqual({ allocations: [] });
    expect(serialized.rebalance_policy.frequency).toBe("on_signal_change");
    expect(serialized.rules[0].condition.children[0]).toMatchObject({
      threshold: { type: "relative", value: 0.04 },
    });
  });

  it("constructs the complete SMA200 hysteresis strategy without a cash ticker", () => {
    let state = createDefaultEditorState();
    const buyCondition = state.rules[0].condition.children[0];
    expect(buyCondition.type).toBe("condition");
    if (buyCondition.type !== "condition") return;
    state = {
      ...state,
      strategyId: "qqq-sma200-hysteresis",
      name: "QQQ SMA200 Hysteresis",
      assets: ["QQQ"],
      rules: [
        {
          ...state.rules[0],
          ruleId: "buy",
          name: "Buy",
          priority: "100",
          condition: {
            ...state.rules[0].condition,
            children: [
              {
                ...buyCondition,
                operator: "greater_than",
                right: { ...buyCondition.right, type: "ma", asset: "QQQ", period: "200" },
                thresholdPercent: "4",
              },
            ],
          },
          allocations: [{ id: "buy-qqq", symbol: "QQQ", targetWeightPercent: "100" }],
        },
      ],
      noMatchBehavior: "hold_previous_allocation",
      fallbackAllocations: [],
      initialAllocations: [],
      rebalanceFrequency: "on_signal_change",
      rebalanceThresholdPercent: "",
    };
    state = editorReducer(state, { type: "addRule" });
    const sellRule = state.rules[1];
    const sellCondition = sellRule.condition.children[0];
    expect(sellCondition.type).toBe("condition");
    if (sellCondition.type !== "condition") return;
    state = editorReducer(state, {
      type: "condition",
      ruleId: sellRule.id,
      conditionId: sellCondition.id,
      condition: {
        ...sellCondition,
        operator: "less_than",
        right: { ...sellCondition.right, type: "ma", asset: "QQQ", period: "200" },
        thresholdPercent: "-3",
      },
    });
    state = editorReducer(state, {
      type: "rule",
      ruleId: sellRule.id,
      field: "name",
      value: "Sell",
    });
    state = editorReducer(state, {
      type: "removeAllocation",
      ruleId: sellRule.id,
      allocationId: sellRule.allocations[0].id,
    });

    const payload = toStrategyPayload(state);

    expect(payload.assets).toEqual([{ symbol: "QQQ" }]);
    expect(payload.assets).not.toContainEqual({ symbol: "CASH" });
    expect(payload.rules).toHaveLength(2);
    expect(payload.rules[0]).toMatchObject({
      name: "Buy",
      allocations: [{ symbol: "QQQ", target_weight: 1 }],
    });
    expect(payload.rules[0].condition.children[0]).toMatchObject({
      operator: "greater_than",
      right: { type: "ma", asset: "QQQ", period: 200 },
      threshold: { type: "relative", value: 0.04 },
    });
    expect(payload.rules[1]).toMatchObject({ name: "Sell", allocations: [] });
    expect(payload.rules[1].condition.children[0]).toMatchObject({
      operator: "less_than",
      right: { type: "ma", asset: "QQQ", period: 200 },
      threshold: { type: "relative", value: -0.03 },
    });
    expect(payload).toMatchObject({
      price_field: "adjusted_close",
      no_match_behavior: "hold_previous_allocation",
      initial_allocation: { allocations: [] },
      rebalance_policy: { frequency: "on_signal_change", threshold: null },
    });
  });

  it.each(["NaN", "Infinity", "-Infinity"])(
    "rejects non-finite allocation input %s before serialization",
    (targetWeightPercent) => {
      const state = createDefaultEditorState();
      state.rules[0].allocations[0].targetWeightPercent = targetWeightPercent;

      expect(validateEditorState(state)).toEqual(
        expect.arrayContaining([
          expect.objectContaining({
            code: "InvalidNumericInput",
            path: "rules[0].allocations[0].target_weight",
          }),
        ]),
      );
    },
  );

  it("rejects negative, over-allocated, and unknown-asset initial allocations", () => {
    const state = createDefaultEditorState();
    state.noMatchBehavior = "hold_previous_allocation";
    state.initialAllocations = [
      { id: "negative", symbol: "QQQ", targetWeightPercent: "-1" },
      { id: "unknown", symbol: "SPY", targetWeightPercent: "102" },
    ];

    expect(validateEditorState(state)).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ code: "InvalidAllocationWeight" }),
        expect.objectContaining({ code: "UnknownAsset" }),
        expect.objectContaining({ code: "AllocationExceeds100Percent" }),
      ]),
    );
  });

  it("does not validate the hidden fallback while hold-previous is active", () => {
    const state = createDefaultEditorState();
    state.noMatchBehavior = "hold_previous_allocation";
    state.fallbackAllocations[0].targetWeightPercent = "NaN";

    expect(validateEditorState(state)).toEqual([]);
  });
});
