from __future__ import annotations

import sqlite3
from dataclasses import replace
from hashlib import sha256
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
from research.candidates import generate_candidates
from research.canonical import canonical_json
from research.enums import ParameterType
from research.experiments import ParameterDefinition, ParameterSpace
from research.optimization_analysis import CandidateFilter
from tests.unit.test_experiment_execution_service import _setup as _execution_setup
from tests.unit.test_grid_search import _service_setup


def _completed_grid(tmp_path):
    service, experiments, _, _, _, _, experiment, definition = _service_setup(tmp_path)
    service.prepare(experiment.experiment_id, definition)
    service.execute(experiment.experiment_id)
    return experiments, experiments.get(experiment.experiment_id)


def _cross_space_completed_grid(tmp_path):
    service, experiments, _, results, _, _, experiment, definition = _service_setup(tmp_path)
    historical_space = ParameterSpace(
        parameters=(ParameterDefinition("period", ParameterType.INTEGER, min=2, max=2, step=1),),
        max_candidates=1,
    )
    historical = replace(
        experiment,
        experiment_id="historical-experiment",
        parameter_space=historical_space,
        provenance=replace(
            experiment.provenance, parameter_space_hash=historical_space.content_hash
        ),
    )
    experiments.create(historical)
    experiments.attach_candidate_set(
        historical.experiment_id,
        generate_candidates(historical),
        transition_key="historical-candidates",
    )
    service.prepare(experiment.experiment_id, definition)
    service.execute(experiment.experiment_id)
    return experiments, experiments.get(experiment.experiment_id), historical, results


def test_repository_reads_completed_grid_reusing_parameter_set_from_other_space(tmp_path) -> None:
    experiments, experiment, historical, results = _cross_space_completed_grid(tmp_path)
    repository = OptimizationResearchRepository(experiments.db_path)
    with sqlite3.connect(experiments.db_path) as connection:
        origin_hash = connection.execute(
            "SELECT parameter_space_hash FROM experiment_parameter_sets "
            "WHERE parameter_set_hash = (SELECT parameter_set_hash FROM experiment_candidates "
            "WHERE experiment_id = ? AND candidate_index = 0)",
            (experiment.experiment_id,),
        ).fetchone()[0]
    assert origin_hash == historical.parameter_space_hash
    assert origin_hash != experiment.parameter_space_hash

    summaries = repository.list_candidate_summaries(experiment)

    assert len(summaries) == 2
    assert [item.parameter_values for item in summaries] == [{"period": 2}, {"period": 3}]
    assert {item.candidate_id for item in summaries} == {
        item.candidate_id for item in results.list(experiment.experiment_id)
    }
    assert {item.experiment_result_id for item in summaries} == {
        item.experiment_result_id for item in results.list(experiment.experiment_id)
    }
    assert {item.backtest_run_id for item in summaries} == {
        item.backtest_run_id for item in results.list(experiment.experiment_id)
    }
    assert {item.candidate_set_hash for item in summaries} == {
        experiments.get_candidate_set(experiment.experiment_id).candidate_set_hash
    }
    reopened = OptimizationResearchRepository(experiments.db_path)
    assert summaries == reopened.list_candidate_summaries(experiments.get(experiment.experiment_id))


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE experiment_parameter_sets SET canonical_json = "
        '\'{"values":{"period":3}}\' WHERE parameter_set_hash = '
        "(SELECT parameter_set_hash FROM experiment_candidates "
        "WHERE experiment_id = 'experiment-8d3' AND candidate_index = 0)",
        "UPDATE experiment_parameter_sets SET canonical_json = "
        '\'{ "values": {"period": 2}}\' WHERE parameter_set_hash = '
        "(SELECT parameter_set_hash FROM experiment_candidates "
        "WHERE experiment_id = 'experiment-8d3' AND candidate_index = 0)",
        "UPDATE experiment_candidate_sets SET parameter_space_hash = "
        "(SELECT parameter_space_hash FROM experiment_candidate_sets "
        "WHERE experiment_id = 'historical-experiment') "
        "WHERE experiment_id = 'experiment-8d3'",
        "UPDATE experiment_candidate_sets SET canonical_json = '{}' "
        "WHERE experiment_id = 'experiment-8d3'",
        "UPDATE experiment_candidates SET candidate_set_hash = 'bad' "
        "WHERE experiment_id = 'experiment-8d3' AND candidate_index = 0",
        "UPDATE experiments SET protocol_id = 'wrong-protocol' "
        "WHERE experiment_id = 'experiment-8d3'",
        "UPDATE research_experiment_results SET parameter_set_hash = 'bad' "
        "WHERE experiment_id = 'experiment-8d3' AND candidate_index = 0",
        "UPDATE research_experiment_results SET parameter_space_hash = 'bad' "
        "WHERE experiment_id = 'experiment-8d3' AND candidate_index = 0",
        "UPDATE experiment_parameter_spaces SET canonical_json = '{}' "
        "WHERE parameter_space_hash = (SELECT parameter_space_hash "
        "FROM experiment_parameter_sets WHERE parameter_set_hash = "
        "(SELECT parameter_set_hash FROM experiment_candidates "
        "WHERE experiment_id = 'experiment-8d3' AND candidate_index = 0))",
    ],
)
def test_cross_space_projection_still_rejects_corrupt_bindings(tmp_path, statement) -> None:
    experiments, experiment, _, _ = _cross_space_completed_grid(tmp_path)
    with sqlite3.connect(experiments.db_path) as connection:
        connection.execute(statement)

    with pytest.raises(OptimizationResearchPersistenceError) as error:
        OptimizationResearchRepository(experiments.db_path).list_candidate_summaries(experiment)
    assert error.value.code == "OPTIMIZATION_INTEGRITY_ERROR"


def test_cross_space_projection_rejects_incompatible_original_space(tmp_path) -> None:
    experiments, experiment, _, _ = _cross_space_completed_grid(tmp_path)
    incompatible = ParameterSpace(
        parameters=(ParameterDefinition("period", ParameterType.INTEGER, min=3, max=3, step=1),),
        max_candidates=1,
    )
    with sqlite3.connect(experiments.db_path) as connection:
        connection.execute(
            "INSERT INTO experiment_parameter_spaces VALUES (?, 2, ?)",
            (incompatible.content_hash, canonical_json(incompatible.to_dict())),
        )
        connection.execute(
            "UPDATE experiment_parameter_sets SET parameter_space_hash = ? "
            "WHERE parameter_set_hash = (SELECT parameter_set_hash FROM experiment_candidates "
            "WHERE experiment_id = ? AND candidate_index = 0)",
            (incompatible.content_hash, experiment.experiment_id),
        )

    with pytest.raises(OptimizationResearchPersistenceError) as error:
        OptimizationResearchRepository(experiments.db_path).list_candidate_summaries(experiment)
    assert error.value.code == "OPTIMIZATION_INTEGRITY_ERROR"


def test_cross_space_projection_does_not_write_research_data(tmp_path) -> None:
    experiments, experiment, _, _ = _cross_space_completed_grid(tmp_path)
    before = sha256(experiments.db_path.read_bytes()).hexdigest()

    summaries = OptimizationResearchRepository(experiments.db_path).list_candidate_summaries(
        experiment
    )
    assert len(summaries) == 2

    assert sha256(experiments.db_path.read_bytes()).hexdigest() == before


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
