import { describe, expect, it } from "vitest";

import {
  allocationTotalPercent,
  createDefaultEditorState,
  createBlankRegimeEditor,
  createDefaultRegimeEditor,
  editorReducer,
  fromDraftResponse,
  fromStrategyPayload,
  getAssetReferences,
  toStrategyPayload,
  toDraftPayload,
  validateEditorState,
} from "./editor";
import type { EditorState, StrategyDraftResponse, StrategyPayload } from "./types";

function firstCondition(state: EditorState) {
  const condition = state.rules[0].condition.children[0];
  if (condition.type !== "condition") throw new Error("expected a condition");
  return condition;
}

describe("strategy editor serialization", () => {
  it("keeps the demo strategy in the domain wire shape", () => {
    const state = createDefaultEditorState();
    const payload = toStrategyPayload(state);
    const conditions = payload.rules[0].condition.children;

    expect(payload.price_field).toBe("adjusted_close");
    expect(payload.strategy_schema_version).toBe("2.0");
    expect(payload.assets).toEqual([
      { symbol: "QQQ", role: "both" },
      { symbol: "TQQQ", role: "both" },
      { symbol: "SGOV", role: "both" },
    ]);
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

  it.each([
    ["price", "daily"],
    ["price", "weekly"],
    ["ma", "daily"],
    ["ma", "weekly"],
    ["ema", "daily"],
    ["ema", "weekly"],
  ] as const)("serializes %s %s timeframe explicitly", (type, timeframe) => {
    const state = createDefaultEditorState();
    const condition = firstCondition(state);
    const operand = { ...condition.left, type, timeframe, period: type === "price" ? "" : "20" };
    state.rules[0].condition.children[0] = { ...condition, left: operand };

    const serialized = toStrategyPayload(state).rules[0].condition.children[0];
    expect(serialized.type).toBe("condition");
    if (serialized.type !== "condition") return;
    expect(serialized.left).toMatchObject({ type, timeframe });
  });

  it("does not expose or serialize a timeframe for CONSTANT operands", () => {
    const state = createDefaultEditorState();
    const condition = firstCondition(state);
    state.rules[0].condition.children[0] = {
      ...condition,
      left: { ...condition.left, type: "constant", value: "1.25", timeframe: "weekly" },
    };

    const serialized = toStrategyPayload(state).rules[0].condition.children[0];
    expect(serialized.type).toBe("condition");
    if (serialized.type !== "condition") return;
    expect(serialized.left).toEqual({ type: "constant", asset: "QQQ", value: 1.25 });
  });

  it("supports mixed timeframe operands without applying a frontend restriction", () => {
    const state = createDefaultEditorState();
    const condition = firstCondition(state);
    state.rules[0].condition.children[0] = {
      ...condition,
      left: { ...condition.left, timeframe: "daily" },
      right: { ...condition.right, timeframe: "weekly", period: "200" },
    };

    const serialized = toStrategyPayload(state).rules[0].condition.children[0];
    expect(serialized.type).toBe("condition");
    if (serialized.type !== "condition") return;
    expect(serialized.left.timeframe).toBe("daily");
    expect(serialized.right.timeframe).toBe("weekly");
  });

  it.each([
    ["3", 0.03],
    ["-4", -0.04],
    ["0", 0],
    ["2.5", 0.025],
  ] as const)("serializes relative threshold %s%% as %s", (input, expected) => {
    const state = createDefaultEditorState();
    const condition = firstCondition(state);
    state.rules[0].condition.children[0] = { ...condition, thresholdPercent: input };
    const serialized = toStrategyPayload(state).rules[0].condition.children[0];
    expect(serialized.type).toBe("condition");
    if (serialized.type !== "condition") return;
    expect(serialized.threshold).toEqual({ type: "relative", value: expected });
  });

  it("preserves absent threshold separately from explicit zero on a no-edit round trip", () => {
    const absent = toStrategyPayload(createDefaultEditorState());
    const absentCondition = absent.rules[0].condition.children[0];
    expect(absentCondition.type).toBe("condition");
    if (absentCondition.type !== "condition") return;
    absentCondition.threshold = null;
    delete absentCondition.left.timeframe;
    delete absentCondition.right.timeframe;
    delete absent.strategy_schema_version;
    absent.unknown_field = { preserved: true };

    const zero = JSON.parse(JSON.stringify(absent)) as StrategyPayload;
    const zeroCondition = zero.rules[0].condition.children[0];
    expect(zeroCondition.type).toBe("condition");
    if (zeroCondition.type !== "condition") return;
    zeroCondition.threshold = { type: "relative", value: 0 };

    expect(toStrategyPayload(fromStrategyPayload(absent))).toEqual(absent);
    expect(toStrategyPayload(fromStrategyPayload(zero))).toEqual(zero);
  });

  it("preserves current schema, regime fields, and unknown source fields until an edit", () => {
    const payload = toStrategyPayload(createDefaultEditorState());
    payload.strategy_mode = "regime_state_machine";
    payload.initial_regime = "defensive";
    payload.regimes = [{ state_id: "defensive", metadata: { owner: "research" } }];
    payload.transitions = [{ transition_id: "recover" }];
    payload.value_zones = [{ zone_id: "value-1" }];
    payload.extra_metadata = { source: "fixture" };

    const restored = fromStrategyPayload(payload);
    expect(restored.strategyMode).toBe("regime_state_machine");
    expect(restored.isDirty).toBe(false);
    expect(toStrategyPayload(restored)).toEqual(payload);
  });

  it("tracks only user edits as dirty and turns an intentional timeframe change into a payload change", () => {
    const source = toStrategyPayload(createDefaultEditorState());
    const restored = fromStrategyPayload(source);
    expect(restored.isDirty).toBe(false);
    const condition = firstCondition(restored);
    const edited = editorReducer(restored, {
      type: "condition",
      ruleId: restored.rules[0].id,
      conditionId: condition.id,
      condition: { ...condition, left: { ...condition.left, timeframe: "weekly" } },
    });

    expect(edited.isDirty).toBe(true);
    expect(toStrategyPayload(edited).rules[0].condition.children[0]).toMatchObject({
      type: "condition",
      left: { timeframe: "weekly" },
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

    expect(payload.assets).toEqual([{ symbol: "QQQ", role: "both" }]);
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

  it("omits inactive fallback from the active payload while preserving the draft", () => {
    const state = createDefaultEditorState();
    state.noMatchBehavior = "hold_previous_allocation";
    state.fallbackAllocations[0].symbol = "NOT_DECLARED";

    const payload = toStrategyPayload(state);
    const draft = toDraftPayload(state);

    expect(payload.fallback).toBeUndefined();
    expect(draft.active_strategy_payload?.fallback).toBeUndefined();
    expect(draft.rule_based_draft.editor_state).toMatchObject({
      fallbackAllocations: [{ symbol: "NOT_DECLARED" }],
    });
  });

  it("returns only active-mode typed asset references", () => {
    let state = createDefaultEditorState();
    const ruleReferences = getAssetReferences(state, "SGOV");
    expect(ruleReferences).toEqual(expect.arrayContaining([
      expect.objectContaining({ reference_type: "RULE_ALLOCATION" }),
      expect.objectContaining({ reference_type: "FALLBACK_ALLOCATION" }),
    ]));

    state = editorReducer(state, { type: "regime", value: createBlankRegimeEditor(["QQQ"]) });
    state = editorReducer(state, { type: "strategyMode", value: "regime_state_machine" });
    expect(getAssetReferences(state, "SGOV")).toEqual([]);
  });

  it("does not treat preserved fallback as active while hold-previous is selected", () => {
    const state = createDefaultEditorState();
    state.assets.push("SPY");
    state.ruleBasedAssets.push("SPY");
    state.fallbackAllocations[0].symbol = "SPY";
    expect(getAssetReferences(state, "SPY")).toEqual([
      expect.objectContaining({ reference_type: "FALLBACK_ALLOCATION" }),
    ]);

    state.noMatchBehavior = "hold_previous_allocation";
    expect(getAssetReferences(state, "SPY")).toEqual([]);
  });

  it("describes regime state, transition, and value-zone references deterministically", () => {
    let state = createDefaultEditorState();
    state = editorReducer(state, { type: "regime", value: createDefaultRegimeEditor(state.assets) });

    const references = getAssetReferences(state, "QQQ");

    expect(references.map((reference) => reference.reference_type)).toEqual(expect.arrayContaining([
      "STATE_ALLOCATION",
      "TRANSITION_CONDITION",
      "VALUE_ZONE",
    ]));
    expect(references.every((reference) => reference.mode === "regime_state_machine")).toBe(true);
    expect(references.every((reference) => reference.symbol === "QQQ")).toBe(true);
  });

  it("retains independent mode assets and restores both mode drafts", () => {
    let state = createDefaultEditorState();
    state = editorReducer(state, { type: "strategyMode", value: "regime_state_machine" });
    state = editorReducer(state, { type: "assets", assets: ["QQQ", "TQQQ"] });
    state = editorReducer(state, { type: "strategyMode", value: "rule_based" });

    expect(state.assets).toEqual(["QQQ", "TQQQ", "SGOV"]);
    expect(state.ruleBasedAssets).toEqual(["QQQ", "TQQQ", "SGOV"]);
    expect(state.regimeAssets).toEqual(["QQQ", "TQQQ"]);
  });

  it("allows an invalid draft to serialize without creating an active version payload", () => {
    const state = createDefaultEditorState();
    state.rules[0].allocations[0].targetWeightPercent = "not-a-number";

    const draft = toDraftPayload(state);

    expect(draft.active_strategy_payload).toBeNull();
    expect(draft.rule_based_draft.editor_state).toMatchObject({ rules: state.rules });
  });

  it("round-trips a saved draft as clean state with its optimistic revision", () => {
    const initial = createDefaultEditorState();
    const draft = toDraftPayload(initial);
    const response = {
      ...draft,
      draft_revision: 3,
      created_at: "2026-09-26T00:00:00Z",
      updated_at: "2026-09-26T00:00:00Z",
      draft_hash: "hash",
    } as StrategyDraftResponse;

    const restored = fromDraftResponse(response);

    expect(restored.draftRevision).toBe(3);
    expect(restored.isDirty).toBe(false);
    expect(restored.strategyId).toBe(initial.strategyId);
    expect(restored.ruleBasedAssets).toEqual(initial.ruleBasedAssets);
    expect(restored.regimeAssets).toEqual(initial.regimeAssets);
  });
});
