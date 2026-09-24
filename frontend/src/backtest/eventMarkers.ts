import type { BacktestEventMarker, BacktestMarkerReport, BacktestMarkerType } from "./types";
import type { ChartMarker, ChartMarkerEvent } from "./reportCharts";

export const MARKER_FILTERS: Array<{ type: BacktestMarkerType; label: string }> = [
  { type: "SIGNAL", label: "Signals" },
  { type: "EXECUTION", label: "Executions" },
  { type: "REBALANCE_DECISION", label: "Rebalances" },
  { type: "CONTRIBUTION", label: "Contributions" },
  { type: "TARGET_ALLOCATION_TRANSITION", label: "Target Allocations" },
  { type: "ACTUAL_ALLOCATION_TRANSITION", label: "Actual Allocations" },
  { type: "REGIME_TRANSITION", label: "Regimes" },
];

export const DEFAULT_MARKER_TYPES = new Set<BacktestMarkerType>([
  "EXECUTION",
  "TARGET_ALLOCATION_TRANSITION",
  "REGIME_TRANSITION",
]);

const COLORS: Record<BacktestMarkerType | "GROUP", string> = {
  SIGNAL: "#f1c878",
  EXECUTION: "#6bd7d0",
  REBALANCE_DECISION: "#e49867",
  CONTRIBUTION: "#d2a956",
  TARGET_ALLOCATION_TRANSITION: "#8cb8ff",
  ACTUAL_ALLOCATION_TRANSITION: "#b997d9",
  REGIME_TRANSITION: "#85d49a",
  GROUP: "#d8e1e5",
};

const KIND: Record<BacktestMarkerType, ChartMarker["kind"]> = {
  SIGNAL: "signal",
  EXECUTION: "execution",
  REBALANCE_DECISION: "rebalance",
  CONTRIBUTION: "contribution",
  TARGET_ALLOCATION_TRANSITION: "target-allocation",
  ACTUAL_ALLOCATION_TRANSITION: "actual-allocation",
  REGIME_TRANSITION: "regime",
};

const EVENT_ORDER: Record<BacktestMarkerType, number> = {
  CONTRIBUTION: 0,
  SIGNAL: 1,
  REGIME_TRANSITION: 2,
  TARGET_ALLOCATION_TRANSITION: 3,
  ACTUAL_ALLOCATION_TRANSITION: 4,
  REBALANCE_DECISION: 5,
  EXECUTION: 6,
};

export interface GroupedChartMarker extends ChartMarker {
  kind: "group";
  groupId: string;
  markerCount: number;
  markerIds: string[];
  markerTypes: BacktestMarkerType[];
  events: ChartMarkerEvent[];
}

export interface MarkerProjection {
  markers: ChartMarker[];
  visibleEventCount: number;
  hiddenEventCount: number;
}

export function projectEventMarkers(
  report: BacktestMarkerReport | null,
  selectedTypes: ReadonlySet<BacktestMarkerType>,
  majorOnly: boolean,
  majorThreshold: number,
): MarkerProjection {
  if (!report) return { markers: [], visibleEventCount: 0, hiddenEventCount: 0 };
  const selected = report.markers.filter((marker) => selectedTypes.has(marker.marker_type));
  const visible = selected.filter((marker) => !majorOnly || isMajor(marker, majorThreshold));
  const byDate = new Map<string, BacktestEventMarker[]>();
  for (const marker of visible) {
    const current = byDate.get(marker.event_date) ?? [];
    current.push(marker);
    byDate.set(marker.event_date, current);
  }
  const groupsByDate = new Map(report.groups.map((group) => [group.date, group]));
  const markers = [...byDate.entries()].sort(([left], [right]) => left.localeCompare(right)).map(([date, events]) => {
    const ordered = [...events].sort(markerOrder);
    if (ordered.length === 1) return chartMarker(ordered[0]);
    const backendGroup = groupsByDate.get(date);
    const grouped: GroupedChartMarker = {
      date,
      kind: "group",
      label: `${ordered.length} Events`,
      shortLabel: String(ordered.length),
      color: COLORS.GROUP,
      categories: ordered.map((item) => item.marker_type),
      details: ordered.map((item) => `${item.title}: ${item.summary}`),
      groupId: backendGroup?.group_id ?? ordered[0].group_key,
      markerCount: ordered.length,
      markerIds: ordered.map((item) => item.marker_id),
      markerTypes: ordered.map((item) => item.marker_type),
      events: ordered.map(markerEvent),
    };
    return grouped;
  });
  return {
    markers,
    visibleEventCount: visible.length,
    hiddenEventCount: report.counts.hidden + report.markers.length - visible.length,
  };
}

function isMajor(marker: BacktestEventMarker, threshold: number): boolean {
  if (marker.significance.always_major) return true;
  const metric = marker.significance.metric_value;
  return metric !== null && Number.isFinite(metric) && metric >= threshold;
}

function chartMarker(marker: BacktestEventMarker): ChartMarker {
  const execution = marker.marker_type === "EXECUTION" ? executionPresentation(marker) : null;
  return {
    date: marker.event_date,
    kind: KIND[marker.marker_type],
    label: execution?.label ?? `${marker.title} · ${marker.summary}`,
    shortLabel: execution?.shortLabel ?? marker.short_label,
    color: execution?.color ?? COLORS[marker.marker_type],
    categories: [marker.marker_type],
    details: markerDetails(marker),
    markerId: marker.marker_id,
    markerIds: [marker.marker_id],
    markerTypes: [marker.marker_type],
    markerCount: 1,
    direction: execution?.direction,
    events: [markerEvent(marker)],
  };
}

function markerOrder(left: BacktestEventMarker, right: BacktestEventMarker): number {
  return EVENT_ORDER[left.marker_type] - EVENT_ORDER[right.marker_type]
    || left.source_event_reference.localeCompare(right.source_event_reference)
    || left.marker_id.localeCompare(right.marker_id);
}

function markerEvent(marker: BacktestEventMarker): ChartMarkerEvent {
  return {
    markerId: marker.marker_id,
    markerType: marker.marker_type,
    title: marker.title,
    summary: marker.summary,
    sourceEventType: marker.source_event_type,
    sourceEventReference: marker.source_event_reference,
    details: markerDetails(marker),
  };
}

function executionPresentation(marker: BacktestEventMarker): {
  label: string;
  shortLabel: string;
  color: string;
  direction?: "BUY" | "SELL" | "MIXED";
} {
  const fills = Array.isArray(marker.details.fills) ? marker.details.fills.filter(isRecord) : [];
  const sides = [...new Set(fills.map((fill) => text(fill.side).toUpperCase()).filter((side) => side === "BUY" || side === "SELL"))];
  const symbols = [...new Set(fills.map((fill) => text(fill.symbol)).filter((symbol) => symbol !== "N/A"))];
  const direction = sides.length === 1 ? sides[0] as "BUY" | "SELL" : sides.length > 1 ? "MIXED" : undefined;
  const sideLabel = direction === "MIXED" ? "BUY / SELL" : direction ?? "EXECUTION";
  const symbolLabel = symbols.length === 1 ? ` ${symbols[0]}` : symbols.length > 1 ? ` ${symbols.length} ASSETS` : "";
  return {
    label: `${sideLabel}${symbolLabel}`,
    shortLabel: `${sideLabel}${symbolLabel}`,
    color: direction === "SELL" ? "#ff8b8b" : direction === "BUY" ? COLORS.EXECUTION : COLORS.GROUP,
    direction,
  };
}

function markerDetails(marker: BacktestEventMarker): string[] {
  const details = marker.details;
  if (marker.marker_type === "EXECUTION") {
    const fills = Array.isArray(details.fills) ? details.fills : [];
    return [
      `Execution date: ${text(details.execution_date)}`,
      `Related signal date: ${text(details.related_signal_date)}`,
      `Cause: ${text(details.rebalance_cause)}`,
      ...fills.flatMap((fill) => {
        if (!isRecord(fill)) return [];
        return [
          `${text(fill.side).toUpperCase()} ${text(fill.symbol)}`,
          `Quantity: ${text(fill.quantity)}`,
          `Fill price: ${money(fill.fill_price)}`,
          `Notional: ${money(fill.notional)}`,
          `Commission: ${money(fill.commission)}`,
          `Slippage: ${money(fill.slippage)}`,
        ];
      }),
    ];
  }
  if (marker.marker_type === "SIGNAL") {
    return [
      `Signal date: ${text(details.signal_date)}`,
      `Reason: ${text(details.regime_transition_id ?? details.matched_rule_id ?? details.strategy_source)}`,
      `Target: ${allocation(details.target_allocation)}`,
      details.expected_execution_date ? `Expected execution: ${text(details.expected_execution_date)}` : `Execution: ${text(details.execution_status)}`,
    ];
  }
  if (marker.marker_type === "REGIME_TRANSITION") {
    const zone = isRecord(details.value_zone) ? details.value_zone : null;
    const zoneEvidence = Array.isArray(zone?.evidence) && isRecord(zone.evidence[0]) ? zone.evidence[0] : null;
    return [
      `Evaluation date: ${text(details.evaluation_date)}`,
      `Transition: ${text(details.from_state)} → ${text(details.to_state)}`,
      `Trigger: ${text(details.transition_id)}`,
      `Target: ${allocation(details.target_allocation)}`,
      ...(zone?.matched_zone_id ? [`Value zone: ${text(zone.matched_zone_id)}`] : []),
      ...(zoneEvidence ? [`Zone evidence: ${text(zoneEvidence.asset)} ${text(zoneEvidence.timeframe)} · ${text(zoneEvidence.indicator_kind)}(${text(zoneEvidence.period)}) = ${text(zoneEvidence.indicator_value)} · source ${text(zoneEvidence.source_date)}`] : []),
      ...(zoneEvidence ? [`Proximity: ${percent(zoneEvidence.distance)} · entry ${percent(zoneEvidence.entry_threshold)} · exit ${percent(zoneEvidence.exit_threshold)}`] : []),
    ];
  }
  if (marker.marker_type === "CONTRIBUTION") {
    return [
      `Effective date: ${text(details.effective_date)}`,
      `Requested date: ${text(details.requested_date)}`,
      `Amount: ${money(details.amount)}`,
      "External cash flow · not a strategy signal",
    ];
  }
  if (marker.marker_type === "REBALANCE_DECISION") {
    return [
      `Decision: ${text(details.decision).toUpperCase()}`,
      `Reasons: ${Array.isArray(details.reasons) ? details.reasons.map(text).join(", ") : "N/A"}`,
      `Target change: ${percent(details.target_change_metric)}`,
      `Drift: ${percent(details.drift_metric)}`,
      ...(details.suppression_reason ? [`Suppression: ${text(details.suppression_reason)}`] : []),
    ];
  }
  return [
    `Before: ${allocation(details.before)}`,
    `After: ${allocation(details.after)}`,
    `Source: ${marker.source_event_type}`,
  ];
}

function allocation(value: unknown): string {
  if (!isRecord(value)) return "N/A";
  return Object.entries(value).sort(([left], [right]) => {
    if (left === "CASH") return 1;
    if (right === "CASH") return -1;
    return left.localeCompare(right);
  }).map(([symbol, weight]) => `${symbol} ${percent(weight)}`).join(" · ") || "N/A";
}

function percent(value: unknown): string {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? `${(numeric * 100).toFixed(2)}%` : "N/A";
}

function money(value: unknown): string {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric.toLocaleString(undefined, { style: "currency", currency: "USD", maximumFractionDigits: 2 }) : "N/A";
}

function text(value: unknown): string {
  return value === null || value === undefined || value === "" ? "N/A" : String(value);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
