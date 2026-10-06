import { describe, expect, it } from "vitest";

import { buildParameters, isTransitionTarget, parameterRow, type ParameterRow } from "./experimentParameters";

const targets = [
  {
    parameter_name: "transition_threshold",
    value_type: "relative_threshold",
    target_kind: "nested_condition_threshold",
    label: "Normal approaching threshold",
    descriptor: { target_kind: "nested_condition_threshold", transition_id: "normal-approaching", condition_path: [0] },
    supported: true,
  },
  {
    parameter_name: "full_risk_on_qqq_weight",
    value_type: "allocation_weight",
    target_kind: "state_allocation_weight",
    label: "FULL_RISK_ON QQQ allocation",
    descriptor: { target_kind: "state_allocation_weight", state_id: "full-risk-on", symbol: "QQQ" },
    supported: true,
  },
];

describe("experiment parameter builder", () => {
  it("offers active peak drawdown thresholds in the transition parameter group", () => {
    expect(isTransitionTarget("peak_drawdown_threshold")).toBe(true);
    expect(isTransitionTarget("nested_condition_threshold")).toBe(true);
    expect(isTransitionTarget("state_allocation_weight")).toBe(false);
  });

  it("uses conservative threshold and full-risk allocation defaults", () => {
    expect(parameterRow(targets[0]).values).toBe("3, 5");
    expect(parameterRow(targets[1]).values).toBe("90, 100");
  });

  it("preserves semantic bindings and creates an exact 2x2 grid", () => {
    const rows: ParameterRow[] = [
      { id: "threshold", targetName: "transition_threshold", values: "3, 5", minimum: "", maximum: "" },
      { id: "allocation", targetName: "full_risk_on_qqq_weight", values: "90, 100", minimum: "", maximum: "" },
    ];
    const result = buildParameters(rows, targets, 100);
    expect(result.counts).toEqual([2, 2]);
    expect(result.count).toBe(4);
    expect(result.parameter_space.parameters[0].allowed_values).toEqual([0.03, 0.05]);
    expect(result.parameter_space.parameters[1].allowed_values).toEqual([0.9, 1]);
    expect(result.grid_definition.semantic_bindings).toHaveLength(2);
    expect(result.grid_definition.semantic_bindings.map((item) => item.parameter_name)).toEqual([
      "transition_threshold",
      "full_risk_on_qqq_weight",
    ]);
  });

  it("rejects duplicate semantic targets and duplicate values", () => {
    const duplicate = { id: "same", targetName: "transition_threshold", values: "3, 3", minimum: "", maximum: "" };
    expect(() => buildParameters([duplicate], targets, 100)).toThrow(/Duplicate values/);
    expect(() => buildParameters([duplicate, { ...duplicate, id: "other", values: "3, 5" }], targets, 100)).toThrow(/Duplicate semantic target/);
  });
});
