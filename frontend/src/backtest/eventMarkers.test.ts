import { describe, expect, it } from "vitest";

import { DEFAULT_MARKER_TYPES, projectEventMarkers } from "./eventMarkers";
import type { BacktestEventMarker, BacktestMarkerReport, BacktestMarkerType } from "./types";

function marker(
  marker_type: BacktestMarkerType,
  event_date: string,
  details: Record<string, unknown> = {},
  metric: number | null = 0.2,
): BacktestEventMarker {
  return {
    marker_id: `marker-${marker_type}-${event_date}`,
    marker_type,
    event_date,
    display_date: event_date,
    strategy_version_id: "version-1",
    run_id: "run-1",
    title: marker_type.replaceAll("_", " "),
    short_label: marker_type.slice(0, 1),
    summary: `${marker_type} summary`,
    significance: {
      always_major: marker_type === "REGIME_TRANSITION" || marker_type === "CONTRIBUTION",
      metric_name: metric === null ? null : "display_metric",
      metric_value: metric,
      is_major: false,
    },
    source_event_type: `Canonical.${marker_type}`,
    source_event_reference: `source-${marker_type}-${event_date}`,
    details,
    group_key: `run-1:${event_date}`,
  };
}

function report(markers: BacktestEventMarker[]): BacktestMarkerReport {
  return {
    marker_schema_version: "1.0",
    identity: { backtest_run_id: "run-1", strategy_version_id: "version-1" },
    source: "derived_from_immutable_backtest_run",
    persisted: false,
    filters: { types: [], from: null, to: null, group_same_day: true, major_only: false, major_threshold: null },
    counts: { markers: markers.length, groups: 0, by_type: {} as Record<BacktestMarkerType, number>, hidden: 0, truncated: false },
    markers,
    groups: [],
  };
}

describe("event marker chart projection", () => {
  it("groups same-day canonical events while preserving their details", () => {
    const payload = report([
      marker("CONTRIBUTION", "2025-01-03", { effective_date: "2025-01-03", requested_date: "2025-01-01", amount: "1000" }),
      marker("REGIME_TRANSITION", "2025-01-03", { evaluation_date: "2025-01-03", from_state: "DEEP_VALUE", to_state: "RECOVERY_HOLD", transition_id: "recover" }),
      marker("TARGET_ALLOCATION_TRANSITION", "2025-01-03", { before: { CASH: 1 }, after: { QQQ: 0.6, CASH: 0.4 } }),
      marker("REBALANCE_DECISION", "2025-01-03", { decision: "execute", reasons: ["contribution", "target_change"], target_change_metric: 0.6, drift_metric: 0.6 }),
      marker("EXECUTION", "2025-01-06", { execution_date: "2025-01-06", related_signal_date: "2025-01-03", rebalance_cause: "target", fills: [{ side: "BUY", symbol: "QQQ", quantity: 10, fill_price: 100, commission: 1 }] }),
    ]);
    const selected = new Set<BacktestMarkerType>(payload.markers.map((item) => item.marker_type));

    const projected = projectEventMarkers(payload, selected, false, 0.1);

    expect(projected.visibleEventCount).toBe(5);
    expect(projected.hiddenEventCount).toBe(0);
    expect(projected.markers).toHaveLength(2);
    expect(projected.markers[0]).toMatchObject({ date: "2025-01-03", kind: "group", label: "4 Events", shortLabel: "4" });
    expect(projected.markers[0].details).toContain("REGIME TRANSITION: REGIME_TRANSITION summary");
    expect(projected.markers[1].details).toContain("Related signal date: 2025-01-03");
  });

  it("uses explicit filters and major threshold without silently truncating", () => {
    const payload = report([
      marker("SIGNAL", "2025-01-03", { signal_date: "2025-01-03" }, 0.05),
      marker("EXECUTION", "2025-01-06", {}, 0.25),
      marker("REGIME_TRANSITION", "2025-01-03", {}, null),
      marker("CONTRIBUTION", "2025-02-03", {}, null),
    ]);

    const defaultProjection = projectEventMarkers(payload, DEFAULT_MARKER_TYPES, false, 0.1);
    const majorProjection = projectEventMarkers(
      payload,
      new Set(payload.markers.map((item) => item.marker_type)),
      true,
      0.2,
    );

    expect(defaultProjection.visibleEventCount).toBe(2);
    expect(defaultProjection.hiddenEventCount).toBe(2);
    expect(majorProjection.visibleEventCount).toBe(3);
    expect(majorProjection.hiddenEventCount).toBe(1);
    expect(majorProjection.markers.flatMap((item) => item.categories ?? [])).not.toContain("SIGNAL");
  });

  it("keeps signal and next-session execution on separate dates", () => {
    const payload = report([
      marker("SIGNAL", "2025-01-17", { signal_date: "2025-01-17", expected_execution_date: "2025-01-21", execution_status: "submitted", target_allocation: { QQQ: 1 } }),
      marker("EXECUTION", "2025-01-21", { execution_date: "2025-01-21", related_signal_date: "2025-01-17", fills: [{ side: "BUY", symbol: "QQQ", quantity: 1, fill_price: 100, commission: 0 }] }),
      marker("SIGNAL", "2025-01-31", { signal_date: "2025-01-31", execution_status: "NO_EXECUTION_SESSION", target_allocation: { CASH: 1 } }),
    ]);
    const projected = projectEventMarkers(payload, new Set(["SIGNAL", "EXECUTION"]), false, 0.1);

    expect(projected.markers.map((item) => item.date)).toEqual(["2025-01-17", "2025-01-21", "2025-01-31"]);
    expect(projected.markers.at(-1)?.details).toContain("Execution: NO_EXECUTION_SESSION");
  });

  it("preserves backend group identity and canonical execution sides", () => {
    const payload = report([
      marker("SIGNAL", "2025-01-03", { signal_date: "2025-01-03" }),
      marker("EXECUTION", "2025-01-03", { fills: [{ side: "BUY", symbol: "QQQ", quantity: 2, fill_price: 100, notional: 200 }] }),
      marker("EXECUTION", "2025-01-06", { fills: [{ side: "SELL", symbol: "TQQQ", quantity: 1, fill_price: 50, notional: 50 }] }),
    ]);
    payload.groups = [{
      group_id: "group-2025-01-03",
      date: "2025-01-03",
      marker_count: 2,
      marker_types: ["SIGNAL", "EXECUTION"],
      summary: "Signal and execution",
      marker_ids: [payload.markers[0].marker_id, payload.markers[1].marker_id],
    }];

    const projected = projectEventMarkers(payload, new Set(["SIGNAL", "EXECUTION"]), false, 0.1);

    expect(projected.markers[0]).toMatchObject({
      kind: "group",
      groupId: "group-2025-01-03",
      markerIds: [payload.markers[0].marker_id, payload.markers[1].marker_id],
      markerTypes: ["SIGNAL", "EXECUTION"],
      markerCount: 2,
    });
    expect(projected.markers[0].events?.map((event) => event.markerId)).toEqual(payload.groups[0].marker_ids);
    expect(projected.markers[1]).toMatchObject({ label: "SELL TQQQ", shortLabel: "SELL TQQQ", direction: "SELL" });
    expect(projected.markers[1].details).toEqual(expect.arrayContaining(["SELL TQQQ", "Notional: $50.00"]));
  });

  it("projects one thousand dense events without silent display truncation", () => {
    const start = new Date("2000-01-03T00:00:00Z");
    const events = Array.from({ length: 1_000 }, (_, index) => {
      const eventDate = new Date(start);
      eventDate.setUTCDate(start.getUTCDate() + index);
      return marker("EXECUTION", eventDate.toISOString().slice(0, 10), {
        execution_date: eventDate.toISOString().slice(0, 10),
        fills: [{ side: index % 2 ? "SELL" : "BUY", symbol: "QQQ", quantity: 1, fill_price: 100, commission: 0 }],
      });
    });

    const projected = projectEventMarkers(report(events), new Set(["EXECUTION"]), false, 0.1);

    expect(projected.visibleEventCount).toBe(1_000);
    expect(projected.hiddenEventCount).toBe(0);
    expect(projected.markers).toHaveLength(1_000);
  });
});
