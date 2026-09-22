from __future__ import annotations

import sqlite3
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import backend.app.optimization_research_api as api
from backend.app.experiment_result_repository import ExperimentResultRepository
from backend.app.optimization_research_repository import (
    OptimizationResearchPersistenceError,
    OptimizationResearchRepository,
)
from backend.app.optimization_research_service import OptimizationResearchService
from research.optimization_analysis import CandidateFilter
from tests.unit.test_experiment_execution_service import _setup as _execution_setup
from tests.unit.test_grid_search import _service_setup


def _completed_grid(tmp_path):
    service, experiments, _, _, _, _, experiment, definition = _service_setup(tmp_path)
    service.prepare(experiment.experiment_id, definition)
    service.execute(experiment.experiment_id)
    return experiments, experiments.get(experiment.experiment_id)


def test_repository_projects_parameters_and_canonical_metrics_without_full_run(tmp_path) -> None:
    experiments, experiment = _completed_grid(tmp_path)
    repository = OptimizationResearchRepository(experiments.db_path)

    summaries = repository.list_candidate_summaries(experiment)

    assert len(summaries) == 2
    assert summaries[0].parameter_values == {"period": 2}
    assert summaries[0].metric("cagr").value is not None
    assert summaries[0].metric("turnover").status.value in {
        "available",
        "not_applicable",
    }
    assert "drawdown_curve" not in summaries[0].performance_summary
    assert "twr_wealth_curve" not in summaries[0].performance_summary


def test_repository_executes_one_summary_select_and_never_selects_result_json(
    tmp_path, monkeypatch
) -> None:
    experiments, experiment = _completed_grid(tmp_path)
    repository = OptimizationResearchRepository(experiments.db_path)
    statements: list[str] = []
    original_connect = repository._connect

    def traced_connect():
        connection = original_connect()
        connection.set_trace_callback(statements.append)
        return connection

    monkeypatch.setattr(repository, "_connect", traced_connect)
    repository.list_candidate_summaries(experiment)

    selects = [
        statement for statement in statements if statement.lstrip().upper().startswith("SELECT")
    ]
    assert len(selects) == 1
    assert "result_json" not in selects[0]
    assert "backtest_runs" not in selects[0]


def test_repository_rejects_is_boundary_tampering(tmp_path) -> None:
    experiments, experiment = _completed_grid(tmp_path)
    connection = sqlite3.connect(experiments.db_path)
    connection.execute(
        "UPDATE research_experiment_results SET is_end = '2099-01-01' WHERE experiment_id = ?",
        (experiment.experiment_id,),
    )
    connection.commit()
    connection.close()

    with pytest.raises(OptimizationResearchPersistenceError) as error:
        OptimizationResearchRepository(experiments.db_path).list_candidate_summaries(experiment)
    assert error.value.code == "OPTIMIZATION_IS_INTEGRITY_ERROR"


def test_repository_projects_1000_candidate_summaries_in_one_query(tmp_path, monkeypatch) -> None:
    _, experiments, _, _, _, experiment = _execution_setup(tmp_path, parameter_max=1_001)
    ExperimentResultRepository(experiments.db_path)
    repository = OptimizationResearchRepository(experiments.db_path)
    statements: list[str] = []
    original_connect = repository._connect

    def traced_connect():
        connection = original_connect()
        connection.set_trace_callback(statements.append)
        return connection

    monkeypatch.setattr(repository, "_connect", traced_connect)
    summaries = repository.list_candidate_summaries(experiment)

    selects = [
        statement for statement in statements if statement.lstrip().upper().startswith("SELECT")
    ]
    assert len(summaries) == 1_000
    assert len(selects) == 1
    assert all(item.result_status == "pending" for item in summaries)


def test_service_returns_is_only_lightweight_results(tmp_path) -> None:
    experiments, experiment = _completed_grid(tmp_path)
    service = OptimizationResearchService(
        experiments, OptimizationResearchRepository(experiments.db_path)
    )

    result = service.results(
        experiment.protocol_id,
        experiment.experiment_id,
        CandidateFilter(minimum_trade_count=0),
        ("cagr", "max_drawdown", "turnover"),
    )

    assert result["is_only"] is True
    assert result["source_count"] == 2
    assert len(result["candidates"]) == 2
    assert set(result["candidates"][0]["metrics"]) == {
        "cagr",
        "max_drawdown",
        "turnover",
    }
    assert "performance_summary" not in result["candidates"][0]


def test_service_rejects_protocol_mismatch(tmp_path) -> None:
    experiments, experiment = _completed_grid(tmp_path)
    service = OptimizationResearchService(
        experiments, OptimizationResearchRepository(experiments.db_path)
    )

    with pytest.raises(Exception) as error:
        service.results("wrong-protocol", experiment.experiment_id, CandidateFilter())
    assert error.value.code == "EXPERIMENT_PROTOCOL_MISMATCH"


@pytest.fixture
def client(monkeypatch) -> TestClient:
    fake = SimpleNamespace(
        metric_registry=lambda: {"schema_version": "phase-11i.1", "metrics": []},
        results=lambda *args: {"analysis": "results", "is_only": True},
        heatmap=lambda *args, **kwargs: {"analysis": "heatmap", "aggregation": None},
        pareto=lambda *args, **kwargs: {"analysis": "pareto", "frontier": []},
        stability=lambda *args, **kwargs: {
            "analysis": "stability",
            "topology_uses_filtered_universe": False,
        },
        sensitivity=lambda *args, **kwargs: {
            "analysis": "sensitivity",
            "interpolation": False,
        },
    )
    monkeypatch.setattr(api, "optimization_service", fake)
    app = FastAPI()
    app.include_router(api.router)
    return TestClient(app)


@pytest.mark.parametrize(
    ("suffix", "body", "analysis"),
    [
        ("results", {}, "results"),
        (
            "heatmap",
            {
                "x_parameter": "period",
                "y_parameter": "threshold",
                "metric_id": "cagr",
                "fixed_parameter_values": {},
            },
            "heatmap",
        ),
        ("pareto", {"objective_ids": ["cagr", "turnover"]}, "pareto"),
        (
            "stability",
            {"center_candidate_id": "candidate-1", "metric_id": "cagr"},
            "stability",
        ),
        (
            "sensitivity",
            {"parameter": "period", "metric_ids": ["cagr"]},
            "sensitivity",
        ),
    ],
)
def test_api_exposes_read_only_analysis_endpoints(client, suffix, body, analysis) -> None:
    response = client.post(
        f"/research/protocols/protocol/experiments/experiment/optimization/{suffix}",
        json=body,
    )
    assert response.status_code == 200
    assert response.json()["analysis"] == analysis


def test_api_rejects_extra_fields(client) -> None:
    response = client.post(
        "/research/protocols/protocol/experiments/experiment/optimization/pareto",
        json={"objective_ids": ["cagr", "turnover"], "formula": "eval(cagr)"},
    )
    assert response.status_code == 422


def test_api_metric_registry_is_public_and_read_only(client) -> None:
    response = client.get("/research/optimization/metrics")
    assert response.status_code == 200
    assert response.json()["schema_version"] == "phase-11i.1"
