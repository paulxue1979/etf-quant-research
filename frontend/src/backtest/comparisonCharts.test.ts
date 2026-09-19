import { describe, expect, it } from "vitest";

import {
  COMPARISON_COLORS,
  buildComparisonSeries,
  comparisonFullRange,
  comparisonPresetRange,
  comparisonTooltipRows,
  visibleComparisonSeries,
} from "./comparisonCharts";
import type { ComparisonCompatibilityStatus, ResearchComparison } from "./types";

function comparison(count = 2, status: ComparisonCompatibilityStatus = "COMPARABLE"): ResearchComparison {
  const runIds = Array.from({ length: count }, (_, index) => `run-${index + 1}`);
  return {
    comparison_schema_version: "2.0",
    ordering: "request_order",
    include: { twr: true, drawdown: false, portfolio_value: false, metrics: false },
    compatibility: { twr: { status, reason_codes: [], human_readable_reasons: [], dimensions: {} } },
    runs: runIds.map((runId, index) => ({
      backtest_run_id: runId,
      strategy_id: `strategy-${index + 1}`,
      strategy_version_id: `version-${index + 1}`,
      strategy_name: `Strategy ${index + 1}`,
      strategy_version: `v${index + 1}`,
      short_display_label: `Strategy ${index + 1} · v${index + 1} · ${runId}`,
      start_date: index === 1 ? "2020-01-03" : "2020-01-02",
      end_date: "2025-01-03",
      asset_universe: ["QQQ"],
    })),
    series: runIds.map((runId, index) => ({
      backtest_run_id: runId,
      strategy_version_id: `version-${index + 1}`,
      start_date: index === 1 ? "2020-01-03" : "2020-01-02",
      end_date: "2025-01-03",
      twr: { status: "available", points: index === 1
        ? [{ date: "2020-01-03", value: 100 }, { date: "2025-01-03", value: 110 }]
        : [{ date: "2020-01-02", value: 100 }, { date: "2025-01-03", value: 120 }] },
      drawdown: { status: "excluded", points: [] },
      portfolio_value: { status: "excluded", points: [] },
    })),
    provenance_notice: "Immutable persisted runs.",
  };
}

describe("multi-strategy comparison chart projection", () => {
  it("preserves backend request order, values, dates, and stable colors without index alignment", () => {
    const projected = buildComparisonSeries(comparison());

    expect(projected.map((item) => item.runId)).toEqual(["run-1", "run-2"]);
    expect(projected.map((item) => item.color)).toEqual(COMPARISON_COLORS.slice(0, 2));
    expect(projected[0].points[0]).toEqual({ date: "2020-01-02", value: 100 });
    expect(projected[1].points[0]).toEqual({ date: "2020-01-03", value: 100 });
  });

  it("shows N/A for a missing real date and excludes hidden or focused-out runs", () => {
    const projected = buildComparisonSeries(comparison());

    expect(comparisonTooltipRows(projected, "2020-01-02", new Set(), null).map((row) => row.value)).toEqual([100, null]);
    expect(comparisonTooltipRows(projected, "2020-01-02", new Set(["run-1"]), null).map((row) => row.runId)).toEqual(["run-2"]);
    expect(comparisonTooltipRows(projected, "2025-01-03", new Set(), "run-2").map((row) => row.runId)).toEqual(["run-2"]);
  });

  it("derives union MAX and bounded calendar presets from real backend dates", () => {
    const range = comparisonFullRange(buildComparisonSeries(comparison()));

    expect(range).toEqual({ from: "2020-01-02", to: "2025-01-03" });
    expect(comparisonPresetRange(range, "1Y")).toEqual({ from: "2024-01-03", to: "2025-01-03" });
    expect(comparisonPresetRange(range, "5Y")).toEqual({ from: "2020-01-03", to: "2025-01-03" });
    expect(comparisonPresetRange(range, "MAX")).toEqual(range);
  });

  it("keeps unavailable TWR explicit and out of visible chart series", () => {
    const payload = comparison();
    payload.series[1].twr = { status: "not_available", reason: "canonical TWR missing", points: [] };
    const projected = buildComparisonSeries(payload);

    expect(projected[1]).toMatchObject({ status: "not_available", reason: "canonical TWR missing" });
    expect(visibleComparisonSeries(projected, new Set(), null).map((item) => item.runId)).toEqual(["run-1"]);
  });

  it("keeps colors stable when visibility changes", () => {
    const projected = buildComparisonSeries(comparison(3));
    const colors = new Map(projected.map((item) => [item.runId, item.color]));
    const visible = visibleComparisonSeries(projected, new Set(["run-2"]), null);

    expect(visible.map((item) => item.color)).toEqual([colors.get("run-1"), colors.get("run-3")]);
  });

  it("returns no fabricated range when every TWR capability is empty", () => {
    const payload = comparison();
    payload.series.forEach((item) => { item.twr.points = []; });
    expect(comparisonFullRange(buildComparisonSeries(payload))).toBeNull();
  });

  it.each([2, 5, 10])("projects %i x 6000-point histories without downsampling or point fabrication", (runCount) => {
    const payload = comparison(runCount);
    payload.series.forEach((item, runIndex) => {
      item.twr.points = Array.from({ length: 6000 }, (_, pointIndex) => ({
        date: `${2000 + Math.floor(pointIndex / 365)}-${String(Math.floor(pointIndex / 31) % 12 + 1).padStart(2, "0")}-${String(pointIndex % 28 + 1).padStart(2, "0")}`,
        value: 100 + runIndex + pointIndex / 100,
      }));
    });

    const projected = buildComparisonSeries(payload);
    expect(projected).toHaveLength(runCount);
    expect(projected.every((item) => item.points.length === 6000)).toBe(true);
    expect(projected.at(-1)?.points[5999].value).toBe(payload.series.at(-1)?.twr.points[5999].value);
  });
});
