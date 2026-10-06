import type { SemanticTargetPreview } from "../strategy/types";

export interface ParameterRow {
  id: string;
  targetName: string;
  values: string;
  minimum: string;
  maximum: string;
}

export function percentageType(type: string): boolean {
  return ["relative_threshold", "allocation_weight"].includes(type);
}

export function isTransitionTarget(targetKind: string): boolean {
  return targetKind.includes("transition") || targetKind.includes("condition") || targetKind === "peak_drawdown_threshold";
}

export function parameterRow(target: SemanticTargetPreview): ParameterRow {
  return {
    id: crypto.randomUUID(),
    targetName: target.parameter_name,
    values: target.value_type === "allocation_weight"
      ? "90, 100"
      : percentageType(target.value_type)
        ? "3, 5"
        : "150, 200",
    minimum: "",
    maximum: "",
  };
}

function typedValues(text: string, type: string): number[] {
  const values = text.split(",").map((token) => {
    if (!token.trim() || !Number.isFinite(Number(token))) {
      throw new Error("Each value must be a finite number.");
    }
    const value = Number(token);
    if (["positive_integer", "integer"].includes(type) && (!Number.isInteger(value) || (type === "positive_integer" && value <= 0))) {
      throw new Error("Periods must be positive integers.");
    }
    if (type === "allocation_weight" && (value < 0 || value > 100)) {
      throw new Error("Allocation weights must be between 0 and 100%.");
    }
    return percentageType(type) ? value / 100 : value;
  });
  if (new Set(values).size !== values.length) throw new Error("Duplicate values are not allowed.");
  return values;
}

export function buildParameters(rows: ParameterRow[], targets: SemanticTargetPreview[], limit: number) {
  if (!rows.length) throw new Error("At least one tunable parameter is required.");
  if (new Set(rows.map((row) => row.targetName)).size !== rows.length) throw new Error("Duplicate semantic target.");
  if (!Number.isInteger(limit) || limit < 1 || limit > 1000) throw new Error("Candidate limit must be between 1 and 1000.");
  const entries = rows.map((row) => {
    const target = targets.find((item) => item.parameter_name === row.targetName && item.supported);
    if (!target) throw new Error("Select a supported semantic target.");
    return { row, target, values: typedValues(row.values, target.value_type) };
  });
  const counts = entries.map((entry) => entry.values.length);
  const count = counts.reduce((total, size) => total * size, 1);
  if (count > 1_000_000) throw new Error("Theoretical candidate guard exceeded (1000000).");
  const bindings = entries.map(({ target }) => ({
    parameter_name: target.parameter_name,
    value_type: target.value_type,
    binding_version: "phase-13e-1-v1",
    descriptor: target.descriptor,
  }));
  const constraints = entries.flatMap(({ row, target }) => row.minimum || row.maximum ? [{
    constraint_type: "bound",
    parameter: target.parameter_name,
    minimum: row.minimum ? typedValues(row.minimum, target.value_type)[0] : null,
    maximum: row.maximum ? typedValues(row.maximum, target.value_type)[0] : null,
  }] : []);
  return {
    counts,
    count,
    parameter_space: {
      parameters: entries.map(({ target, values }) => ({ name: target.parameter_name, type: "discrete", allowed_values: values })),
      constraints: [],
      max_candidates: limit,
    },
    grid_definition: {
      semantic_bindings: bindings,
      fixed_parameters: { price_field: "adjusted_close" },
      constraints,
      max_candidates: limit,
      theoretical_guard: 1_000_000,
    },
  };
}
