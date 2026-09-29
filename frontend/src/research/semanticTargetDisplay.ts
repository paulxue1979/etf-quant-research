import type { SemanticTargetPreview, StrategyPayload } from "../strategy/types";

export type ParameterLabels = Record<string, { label: string; unit: string }>;

export function parameterValueDisplay(value: unknown, unit?: string): string {
  if (unit === "%" && typeof value === "number") return `${Number((value * 100).toFixed(10))}%`;
  const text = typeof value === "string" ? value : JSON.stringify(value);
  return `${text}${unit === "bars" ? " bars" : ""}`;
}

const stateNames: Record<string, string> = {
  PROTECTIVE: "风险保护", APPROACH: "接近价值", VALUE: "价值区",
  DEEP_VALUE: "深度价值", RECOVERY_HOLD: "恢复持有", FULL_RISK_ON: "全风险开启",
};

function record(value: unknown): Record<string, unknown> {
  return typeof value === "object" && value !== null ? value as Record<string, unknown> : {};
}

export function stateDisplayName(id: unknown, strategy: StrategyPayload): string {
  if (typeof id !== "string") return "未命名状态";
  const canonicalId = id.replaceAll("-", "_").toUpperCase();
  const state = strategy.regimes?.map(record).find((item) => item.state_id === id || item.state_id === canonicalId);
  return stateNames[canonicalId] ?? (typeof state?.display_name === "string" ? state.display_name : "未命名状态");
}

export function semanticTargetDisplay(target: SemanticTargetPreview, strategy: StrategyPayload) {
  const descriptor = target.descriptor;
  const unit = target.value_type === "positive_integer" ? "bars" : "%";
  if (target.target_kind === "state_allocation_weight") {
    const state = stateDisplayName(descriptor.state_id, strategy);
    return { label: `${state}｜${String(descriptor.symbol ?? "Asset")} 目标仓位`, help: `${state}中的目标资产权重。`, unit };
  }
  if (target.target_kind.startsWith("value_zone")) {
    const zone = strategy.value_zones?.map(record).find((item) => item.zone_id === descriptor.zone_id);
    const name = typeof zone?.display_name === "string" ? zone.display_name : "未命名价值区";
    const meaning = target.target_kind.includes("period") ? "指标周期" : target.target_kind.includes("entry") ? "入场阈值" : "退出阈值";
    return { label: `${name}｜${meaning}`, help: `${name}的${meaning}。`, unit };
  }
  const transition = strategy.transitions?.map(record).find((item) => item.transition_id === descriptor.transition_id || item.id === descriptor.transition_id);
  const relationship = typeof transition?.from_state === "string" && typeof transition?.to_state === "string"
    ? `${stateDisplayName(transition.from_state, strategy)} → ${stateDisplayName(transition.to_state, strategy)}`
    : "未命名状态转换";
  const meaning = target.target_kind.includes("period") ? "指标周期" : "触发阈值";
  return { label: `${relationship}｜${meaning}`, help: `控制${relationship}的${meaning}。`, unit };
}
