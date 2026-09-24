import { describe, expect, it } from "vitest";

import {
  createBlankRegimeEditor,
  createDefaultEditorState,
  createDefaultRegimeEditor,
  editorReducer,
  fromStrategyPayload,
  toStrategyPayload,
  validateRegimeEditor,
} from "./editor";

describe("regime state machine editor contract", () => {
  it("creates a blank state machine with no initial risk position", () => {
    const model = createBlankRegimeEditor(["QQQ", "TQQQ", "SGOV"]);
    expect(model.initialRegime).toBe("");
    expect(model.regimes).toHaveLength(1);
    expect(model.regimes[0].stateId).toBe("PROTECTIVE");
    expect(model.regimes[0].allocations[0].targetWeightPercent).toBe("0");
    expect(validateRegimeEditor({ ...model, initialRegime: "PROTECTIVE" }, ["QQQ", "TQQQ", "SGOV"])).toEqual([]);
  });

  it("creates the six-state template without NORMAL and keeps recovery/protective semantics distinct", () => {
    const model = createDefaultRegimeEditor(["QQQ", "TQQQ", "SGOV"]);
    expect(model.regimes.map((state) => state.stateId)).toEqual([
      "PROTECTIVE", "APPROACH", "VALUE", "DEEP_VALUE", "RECOVERY_HOLD", "FULL_RISK_ON",
    ]);
    expect(model.regimes.some((state) => state.stateId === "NORMAL")).toBe(false);
    expect(model.initialRegime).toBe("PROTECTIVE");
    expect(model.transitions).toEqual(expect.arrayContaining([
      expect.objectContaining({ fromState: "DEEP_VALUE", toState: "RECOVERY_HOLD" }),
      expect.objectContaining({ fromState: "RECOVERY_HOLD", toState: "FULL_RISK_ON" }),
      expect.objectContaining({ fromState: "FULL_RISK_ON", toState: "PROTECTIVE" }),
    ]));
    expect(model.transitions.some((transition) => transition.fromState === "FULL_RISK_ON" && transition.toState === "RECOVERY_HOLD")).toBe(false);
  });

  it("serializes implicit Cash separately from declared SGOV", () => {
    const base = createDefaultEditorState();
    const regime = createDefaultRegimeEditor(base.assets);
    const state = editorReducer(base, { type: "regime", value: regime });
    const payload = toStrategyPayload(state);
    const recovery = payload.regimes?.find((item) => (item as { state_id: string }).state_id === "RECOVERY_HOLD") as { target_allocation: { allocations: Array<{ symbol: string; target_weight: number }> } };
    expect(recovery.target_allocation.allocations).not.toContainEqual(expect.objectContaining({ symbol: "CASH" }));
    expect(recovery.target_allocation.allocations).toContainEqual({ symbol: "QQQ", target_weight: 0.4 });
    expect(recovery.target_allocation.allocations).toContainEqual({ symbol: "TQQQ", target_weight: 0.2 });
  });

  it("blocks duplicate priorities, self transitions, invalid allocations and zone priority conflicts", () => {
    const model = createDefaultRegimeEditor(["QQQ", "TQQQ", "SGOV"]);
    const broken = {
      ...model,
      transitions: [
        ...model.transitions,
        { ...model.transitions[0], id: "duplicate", transitionId: "bad-self", toState: "PROTECTIVE" },
      ],
      regimes: model.regimes.map((state, index) => index === 0 ? { ...state, allocations: [{ ...state.allocations[0], targetWeightPercent: "101" }] } : state),
      valueZones: model.valueZones.map((zone, index) => index === 1 ? { ...zone, priority: model.valueZones[0].priority } : zone),
    };
    const codes = validateRegimeEditor(broken, ["QQQ", "TQQQ", "SGOV"]).map((issue) => issue.code);
    expect(codes).toEqual(expect.arrayContaining(["AllocationExceeds100Percent", "SelfRegimeTransition", "RegimePriorityConflict", "ValueZonePriorityConflict"]));
  });

  it("round-trips regime fields and preserves source payload on no edit", () => {
    const base = createDefaultEditorState();
    const authored = editorReducer(base, { type: "regime", value: createDefaultRegimeEditor(base.assets) });
    const payload = toStrategyPayload(authored);
    payload.unknown_regime_metadata = { source: "fixture" };
    const restored = fromStrategyPayload(payload);
    expect(restored.isDirty).toBe(false);
    expect(restored.strategyMode).toBe("regime_state_machine");
    expect(restored.regime?.initialRegime).toBe("PROTECTIVE");
    expect(toStrategyPayload(restored)).toEqual(payload);
  });
});
