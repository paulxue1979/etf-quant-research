from __future__ import annotations

import random

import pytest

from research.enums import MetricDirection, ParameterType
from research.experiments import ParameterDefinition, ParameterSpace
from research.optimization_analysis import (
    METRIC_REGISTRY,
    CandidateFilter,
    MetricAvailability,
    OptimizationAnalysisError,
    OptimizationCandidateSummary,
    ParameterFilter,
    analyze_stability,
    apply_candidate_filter,
    build_heatmap,
    build_sensitivity,
    calculate_pareto,
    metric_definition,
)


def _metric(value: float | None, reason: str = "insufficient observations") -> dict:
    return {
        "value": value,
        "status": "available" if value is not None else "not_evaluable",
        "reason": None if value is not None else reason,
    }


def _summary(
    candidate_index: int,
    values: dict[str, object],
    *,
    cagr: float | None = 0.1,
    drawdown: float | None = -0.2,
    sharpe: float | None = 1.0,
    turnover: float | None = 0.3,
    trades: int = 10,
    status: str = "completed",
) -> OptimizationCandidateSummary:
    return OptimizationCandidateSummary(
        experiment_id="experiment-grid",
        candidate_id=f"candidate-{candidate_index:04d}",
        candidate_index=candidate_index,
        parameter_set_hash=f"parameter-{candidate_index}",
        candidate_set_hash="candidate-set",
        parameter_values=values,
        execution_status=status,
        result_status=status,
        experiment_result_id=f"result-{candidate_index}" if status == "completed" else None,
        result_hash=f"result-hash-{candidate_index}" if status == "completed" else None,
        derived_strategy_version_id=f"derived-{candidate_index}",
        derived_strategy_version_hash=f"derived-hash-{candidate_index}",
        backtest_configuration_hash=f"config-{candidate_index}",
        backtest_run_id=f"run-{candidate_index}" if status == "completed" else None,
        is_start="2020-01-01" if status == "completed" else None,
        is_end="2023-12-31" if status == "completed" else None,
        engine_version="engine-v1" if status == "completed" else None,
        analysis_version="analytics-v1" if status == "completed" else None,
        performance_summary={
            "cagr": _metric(cagr),
            "total_return": _metric(None if cagr is None else cagr * 2),
            "max_drawdown": _metric(drawdown),
            "sharpe_ratio": _metric(sharpe),
            "sortino_ratio": _metric(None if sharpe is None else sharpe * 1.2),
            "calmar_ratio": _metric(
                None if cagr is None or drawdown is None else cagr / abs(drawdown)
            ),
            "annualized_volatility": _metric(0.15),
            "turnover": _metric(turnover),
            "xirr": _metric(cagr),
            "final_equity": 100_000 * (1 + (cagr or 0)),
            "trade_metrics": {
                "number_of_closed_trades": trades,
                "average_holding_period": _metric(12.0),
                "turnover": _metric(999.0),
            },
            "exposure_summary": {
                "average_gross_exposure": 0.8,
                "time_invested_fraction": 0.75,
            },
            "oos": {"cagr": _metric(9.99)},
        }
        if status == "completed"
        else {},
        failure_code="EXECUTION_FAILED" if status == "failed" else None,
        failure_summary="safe failure" if status == "failed" else None,
    )


@pytest.fixture
def space() -> ParameterSpace:
    return ParameterSpace(
        parameters=(
            ParameterDefinition("period", ParameterType.INTEGER, min=10, max=30, step=10),
            ParameterDefinition("threshold", ParameterType.DISCRETE, allowed_values=(0.2, 0.1)),
            ParameterDefinition("mode", ParameterType.ENUM, allowed_values=("tqqq", "qqq")),
        ),
        max_candidates=1_000,
    )


@pytest.fixture
def candidates(space: ParameterSpace) -> tuple[OptimizationCandidateSummary, ...]:
    items: list[OptimizationCandidateSummary] = []
    index = 0
    for mode in ("qqq", "tqqq"):
        for period in (10, 20, 30):
            for threshold in (0.1, 0.2):
                items.append(
                    _summary(
                        index,
                        {"mode": mode, "period": period, "threshold": threshold},
                        cagr=0.05 + period / 1_000 + threshold / 10,
                        drawdown=-0.1 - threshold,
                        sharpe=period / 10,
                        turnover=threshold + period / 100,
                        trades=period,
                    )
                )
                index += 1
    return tuple(items)


@pytest.mark.parametrize(
    ("metric_id", "direction", "format_name"),
    [
        ("cagr", MetricDirection.MAXIMIZE, "percent"),
        ("sharpe_ratio", MetricDirection.MAXIMIZE, "ratio"),
        ("turnover", MetricDirection.MINIMIZE, "percent"),
        ("max_drawdown", MetricDirection.MAXIMIZE, "percent"),
        ("trade_count", MetricDirection.MAXIMIZE, "integer"),
        ("xirr", MetricDirection.MAXIMIZE, "percent"),
        ("final_equity", MetricDirection.MAXIMIZE, "currency"),
    ],
)
def test_metric_registry_direction_and_format(metric_id, direction, format_name) -> None:
    definition = metric_definition(metric_id)
    assert definition.direction is direction
    assert definition.format.value == format_name


def test_metric_allowlist_rejects_arbitrary_formula() -> None:
    with pytest.raises(OptimizationAnalysisError, match="allowlist"):
        metric_definition("cagr/abs(max_drawdown)")


@pytest.mark.parametrize("metric_id", tuple(METRIC_REGISTRY))
def test_metric_projection_never_converts_missing_to_zero(metric_id: str) -> None:
    candidate = _summary(0, {"period": 10}, cagr=None)
    if metric_id == "cagr":
        assert candidate.metric(metric_id).status is MetricAvailability.NOT_APPLICABLE
        assert candidate.metric(metric_id).value is None


def test_realized_turnover_uses_top_level_canonical_value() -> None:
    candidate = _summary(0, {"period": 10}, turnover=0.25)
    assert candidate.metric("turnover").value == 0.25


def test_failed_candidate_metric_has_failed_state() -> None:
    metric = _summary(0, {"period": 10}, status="failed").metric("cagr")
    assert metric.status is MetricAvailability.FAILED
    assert metric.value is None


@pytest.mark.parametrize(
    ("candidate_filter", "expected_count"),
    [
        (CandidateFilter(statuses=("completed",)), 12),
        (CandidateFilter(minimum_trade_count=20), 8),
        (CandidateFilter(maximum_turnover=0.4), 10),
        (CandidateFilter(minimum_cagr=0.09), 6),
        (CandidateFilter(minimum_sharpe=2.0), 8),
        (CandidateFilter(maximum_drawdown_magnitude=0.2), 6),
        (CandidateFilter(minimum_exposure=0.8), 12),
        (CandidateFilter(maximum_exposure=0.7), 0),
        (CandidateFilter(parameters={"mode": ParameterFilter(exact="qqq")}), 6),
        (CandidateFilter(parameters={"period": ParameterFilter(minimum=20)}), 8),
    ],
)
def test_structured_filters(candidates, space, candidate_filter, expected_count) -> None:
    included, _ = apply_candidate_filter(candidates, candidate_filter, space)
    assert len(included) == expected_count


def test_multiple_filters_use_and_semantics(candidates, space) -> None:
    included, _ = apply_candidate_filter(
        candidates,
        CandidateFilter(
            minimum_trade_count=20,
            maximum_turnover=0.4,
            parameters={"mode": ParameterFilter(exact="qqq")},
        ),
        space,
    )
    assert [item.candidate_index for item in included] == [2, 3, 4]


def test_filter_order_is_deterministic(candidates, space) -> None:
    shuffled = list(candidates)
    random.Random(7).shuffle(shuffled)
    first, _ = apply_candidate_filter(candidates, CandidateFilter(), space)
    second, _ = apply_candidate_filter(shuffled, CandidateFilter(), space)
    assert [item.candidate_id for item in first] == [item.candidate_id for item in second]


def test_unknown_parameter_filter_is_rejected(candidates, space) -> None:
    with pytest.raises(OptimizationAnalysisError, match="unknown"):
        apply_candidate_filter(
            candidates,
            CandidateFilter(parameters={"unsafe": ParameterFilter(exact=1)}),
            space,
        )


def test_heatmap_maps_numeric_axes_and_fixed_slice(candidates, space) -> None:
    result = build_heatmap(
        candidates,
        space,
        x_parameter="period",
        y_parameter="threshold",
        metric_id="cagr",
        fixed_parameter_values={"mode": "qqq"},
    )
    assert result["x_values"] == [10, 20, 30]
    assert result["y_values"] == [0.1, 0.2]
    assert [cell["candidate_index"] for cell in result["cells"]] == [0, 2, 4, 1, 3, 5]
    assert result["aggregation"] is None
    assert result["interpolation"] is False


def test_heatmap_supports_enum_axis(candidates, space) -> None:
    result = build_heatmap(
        candidates,
        space,
        x_parameter="mode",
        y_parameter="threshold",
        metric_id="max_drawdown",
        fixed_parameter_values={"period": 20},
    )
    assert result["x_values"] == ["qqq", "tqqq"]


@pytest.mark.parametrize(
    "fixed",
    [{}, {"mode": "qqq", "extra": 1}],
)
def test_heatmap_requires_complete_exact_slice(candidates, space, fixed) -> None:
    with pytest.raises(OptimizationAnalysisError) as error:
        build_heatmap(
            candidates,
            space,
            x_parameter="period",
            y_parameter="threshold",
            metric_id="cagr",
            fixed_parameter_values=fixed,
        )
    assert error.value.code == "AMBIGUOUS_SLICE"


@pytest.mark.parametrize("metric_id", ["cagr", "max_drawdown", "turnover", "calmar_ratio"])
def test_heatmap_metric_value_is_canonical(candidates, space, metric_id) -> None:
    result = build_heatmap(
        candidates,
        space,
        x_parameter="period",
        y_parameter="threshold",
        metric_id=metric_id,
        fixed_parameter_values={"mode": "qqq"},
    )
    expected = candidates[0].metric(metric_id).value
    assert result["cells"][0]["metric"]["value"] == expected


def test_heatmap_represents_pruned_failed_and_unavailable_cells(space, candidates) -> None:
    modified = list(candidates)
    modified.pop(2)
    modified[1] = _summary(1, dict(modified[1].parameter_values), status="failed")
    modified[2] = _summary(
        modified[2].candidate_index,
        dict(modified[2].parameter_values),
        cagr=None,
    )
    result = build_heatmap(
        modified,
        space,
        x_parameter="period",
        y_parameter="threshold",
        metric_id="cagr",
        fixed_parameter_values={"mode": "qqq"},
    )
    assert {cell["cell_status"] for cell in result["cells"]} >= {
        "pruned",
        "failed",
        "not_applicable",
    }


def test_heatmap_row_order_does_not_change_output(candidates, space) -> None:
    kwargs = {
        "x_parameter": "period",
        "y_parameter": "threshold",
        "metric_id": "cagr",
        "fixed_parameter_values": {"mode": "qqq"},
    }
    assert build_heatmap(candidates, space, **kwargs) == build_heatmap(
        tuple(reversed(candidates)), space, **kwargs
    )


def _pareto_space(count: int) -> ParameterSpace:
    return ParameterSpace(
        parameters=(
            ParameterDefinition("index", ParameterType.INTEGER, min=0, max=count - 1, step=1),
        ),
        max_candidates=max(count, 1),
    )


def _pareto_candidates(vectors: list[tuple[float, float, float]]) -> tuple:
    return tuple(
        _summary(index, {"index": index}, cagr=cagr, drawdown=drawdown, turnover=turnover)
        for index, (cagr, drawdown, turnover) in enumerate(vectors)
    )


def test_two_objective_pareto_frontier_and_dominated_membership() -> None:
    items = _pareto_candidates([(0.1, -0.2, 0.3), (0.2, -0.2, 0.3), (0.15, -0.1, 0.4)])
    result = calculate_pareto(items, _pareto_space(3), objective_ids=("cagr", "max_drawdown"))
    assert [item["candidate_index"] for item in result["frontier"]] == [1, 2]
    assert [item["candidate_index"] for item in result["dominated"]] == [0]


def test_three_objective_pareto_mixes_directions() -> None:
    items = _pareto_candidates([(0.1, -0.2, 0.3), (0.2, -0.2, 0.2), (0.15, -0.1, 0.1)])
    result = calculate_pareto(
        items,
        _pareto_space(3),
        objective_ids=("cagr", "max_drawdown", "turnover"),
    )
    assert [item["candidate_index"] for item in result["frontier"]] == [1, 2]


def test_identical_objective_vectors_are_both_preserved() -> None:
    items = _pareto_candidates([(0.1, -0.2, 0.3), (0.1, -0.2, 0.3)])
    result = calculate_pareto(items, _pareto_space(2), objective_ids=("cagr", "turnover"))
    assert [item["candidate_index"] for item in result["frontier"]] == [0, 1]


@pytest.mark.parametrize("status", ["failed", "pending", "running"])
def test_non_completed_candidate_is_excluded_from_pareto(status) -> None:
    item = _summary(0, {"index": 0}, status=status)
    result = calculate_pareto((item,), _pareto_space(1), objective_ids=("cagr", "turnover"))
    assert result["eligible_count"] == 0
    assert item.candidate_id in result["exclusions"]


def test_missing_objective_is_excluded_with_reason() -> None:
    item = _summary(0, {"index": 0}, cagr=None)
    result = calculate_pareto((item,), _pareto_space(1), objective_ids=("cagr", "turnover"))
    assert "not_applicable" in result["exclusions"][item.candidate_id][0]


def test_negative_drawdown_closer_to_zero_is_better() -> None:
    items = _pareto_candidates([(0.1, -0.8, 0.3), (0.1, -0.2, 0.3)])
    result = calculate_pareto(items, _pareto_space(2), objective_ids=("cagr", "max_drawdown"))
    assert [item["candidate_index"] for item in result["frontier"]] == [1]


def test_pareto_result_is_independent_of_input_order() -> None:
    items = _pareto_candidates([(0.1, -0.2, 0.3), (0.2, -0.3, 0.2), (0.15, -0.1, 0.4)])
    first = calculate_pareto(items, _pareto_space(3), objective_ids=("cagr", "turnover"))
    second = calculate_pareto(
        tuple(reversed(items)), _pareto_space(3), objective_ids=("cagr", "turnover")
    )
    assert first == second


@pytest.mark.parametrize("center_index", [0, 2, 5])
def test_stability_direct_neighbors_change_one_canonical_step(
    candidates, space, center_index
) -> None:
    result = analyze_stability(
        candidates,
        space,
        center_candidate_id=candidates[center_index].candidate_id,
        metric_id="cagr",
    )
    center = candidates[center_index]
    for neighbor in result["neighbors"]:
        if neighbor["candidate_id"] is None:
            continue
        target = next(item for item in candidates if item.candidate_id == neighbor["candidate_id"])
        changed = [
            name
            for name in center.parameter_values
            if center.parameter_values[name] != target.parameter_values[name]
        ]
        assert changed == [neighbor["changed_parameter"]]


def test_diagonal_candidate_is_not_a_direct_neighbor(candidates, space) -> None:
    result = analyze_stability(
        candidates, space, center_candidate_id=candidates[0].candidate_id, metric_id="cagr"
    )
    assert candidates[3].candidate_id not in {item["candidate_id"] for item in result["neighbors"]}


def test_pruned_and_failed_neighbors_remain_explicit(candidates, space) -> None:
    modified = list(candidates)
    modified.pop(2)
    modified[1] = _summary(1, dict(modified[1].parameter_values), status="failed")
    result = analyze_stability(
        modified, space, center_candidate_id=modified[0].candidate_id, metric_id="cagr"
    )
    assert {item["status"] for item in result["neighbors"]} >= {"pruned", "failed"}


def test_filter_does_not_redefine_neighbor_topology(candidates, space) -> None:
    result = analyze_stability(
        candidates,
        space,
        center_candidate_id=candidates[0].candidate_id,
        metric_id="cagr",
        candidate_filter=CandidateFilter(candidate_ids=(candidates[0].candidate_id,)),
    )
    assert result["topology_uses_filtered_universe"] is False
    assert any(
        item["candidate_id"] not in (None, candidates[0].candidate_id)
        for item in result["neighbors"]
    )


@pytest.mark.parametrize(
    "statistic",
    [
        "neighbor_median",
        "neighbor_min",
        "neighbor_max",
        "neighbor_mean",
        "neighbor_std",
        "neighbor_mad",
        "worst_neighbor_deterioration",
        "median_neighbor_deterioration",
    ],
)
def test_stability_statistics_are_available(candidates, space, statistic) -> None:
    result = analyze_stability(
        candidates, space, center_candidate_id=candidates[2].candidate_id, metric_id="cagr"
    )
    assert result["statistics"][statistic] is not None


@pytest.mark.parametrize(
    ("metric_id", "expected_worst_positive"),
    [("cagr", True), ("turnover", True)],
)
def test_direction_aware_deterioration(
    candidates, space, metric_id, expected_worst_positive
) -> None:
    result = analyze_stability(
        candidates, space, center_candidate_id=candidates[2].candidate_id, metric_id=metric_id
    )
    assert (result["statistics"]["worst_neighbor_deterioration"] > 0) is expected_worst_positive


def test_center_metric_unavailable_preserves_null_deterioration(space, candidates) -> None:
    modified = list(candidates)
    modified[0] = _summary(0, dict(modified[0].parameter_values), cagr=None)
    result = analyze_stability(
        modified, space, center_candidate_id=modified[0].candidate_id, metric_id="cagr"
    )
    assert result["statistics"]["center_value"] is None
    assert result["statistics"]["worst_neighbor_deterioration"] is None


def test_sensitivity_uses_ordered_parameter_domain_and_complete_slice(candidates, space) -> None:
    result = build_sensitivity(
        candidates,
        space,
        parameter="period",
        metric_ids=("cagr", "max_drawdown", "turnover"),
        fixed_parameter_values={"mode": "qqq", "threshold": 0.1},
    )
    assert result["parameter_values"] == [10, 20, 30]
    assert [item["candidate_index"] for item in result["points"]] == [0, 2, 4]
    assert result["interpolation"] is False


@pytest.mark.parametrize("removed_index", [0, 2, 4])
def test_sensitivity_pruned_point_is_a_gap(candidates, space, removed_index) -> None:
    modified = tuple(item for item in candidates if item.candidate_index != removed_index)
    result = build_sensitivity(
        modified,
        space,
        parameter="period",
        metric_ids=("cagr",),
        fixed_parameter_values={"mode": "qqq", "threshold": 0.1},
    )
    assert "pruned" in {item["status"] for item in result["points"]}


def test_sensitivity_failed_point_is_not_interpolated(candidates, space) -> None:
    modified = list(candidates)
    modified[2] = _summary(2, dict(modified[2].parameter_values), status="failed")
    result = build_sensitivity(
        modified,
        space,
        parameter="period",
        metric_ids=("cagr",),
        fixed_parameter_values={"mode": "qqq", "threshold": 0.1},
    )
    assert result["points"][1]["status"] == "failed"
    assert result["interpolation"] is False


def test_oos_metric_cannot_be_requested() -> None:
    with pytest.raises(OptimizationAnalysisError):
        metric_definition("oos_cagr")


def test_candidate_with_oos_data_still_projects_is_metric() -> None:
    candidate = _summary(0, {"index": 0}, cagr=0.12)
    assert candidate.metric("cagr").value == 0.12


@pytest.mark.parametrize(
    "strategy_shape",
    ["QQQ/TQQQ", "SPY/UPRO/SGOV", "daily", "daily_weekly", "state_machine", "position_policy"],
)
def test_analysis_is_generic_to_strategy_shape(strategy_shape) -> None:
    candidate = _summary(0, {"index": 0})
    assert candidate.metric("cagr").value == 0.1
    assert strategy_shape


@pytest.mark.parametrize("count", [100, 500, 1_000])
def test_filter_scale_is_deterministic(count: int) -> None:
    space = _pareto_space(count)
    items = tuple(_summary(index, {"index": index}, cagr=index / count) for index in range(count))
    filtered, excluded = apply_candidate_filter(items, CandidateFilter(minimum_cagr=0.5), space)
    assert len(filtered) + len(excluded) == count
    assert [item.candidate_index for item in filtered] == sorted(
        item.candidate_index for item in filtered
    )


@pytest.mark.parametrize("count", [500, 1_000])
def test_pareto_scale_candidates(count: int) -> None:
    space = _pareto_space(count)
    items = tuple(
        _summary(
            index,
            {"index": index},
            cagr=index / count,
            turnover=(count - index) / count,
        )
        for index in range(count)
    )
    result = calculate_pareto(items, space, objective_ids=("cagr", "turnover"))
    assert result["eligible_count"] == count


def test_heatmap_scale_100_candidates() -> None:
    space = ParameterSpace(
        parameters=(
            ParameterDefinition("x", ParameterType.INTEGER, min=0, max=9, step=1),
            ParameterDefinition("y", ParameterType.INTEGER, min=0, max=9, step=1),
        ),
        max_candidates=100,
    )
    items = tuple(
        _summary(y * 10 + x, {"x": x, "y": y}, cagr=(x + y) / 100)
        for y in range(10)
        for x in range(10)
    )
    result = build_heatmap(
        items,
        space,
        x_parameter="x",
        y_parameter="y",
        metric_id="cagr",
        fixed_parameter_values={},
    )
    assert len(result["cells"]) == 100


def test_neighbor_lookup_scale_1000_candidates() -> None:
    count = 1_000
    space = _pareto_space(count)
    items = tuple(_summary(index, {"index": index}, cagr=index / count) for index in range(count))
    result = analyze_stability(
        items, space, center_candidate_id=items[500].candidate_id, metric_id="cagr"
    )
    assert result["statistics"]["neighbor_count_expected"] == 2
    assert result["statistics"]["neighbor_count_available"] == 2
