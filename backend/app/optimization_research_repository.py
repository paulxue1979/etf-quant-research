"""One-query lightweight projection for PHASE 11I optimization research."""

from __future__ import annotations

import json
import math
import sqlite3
from pathlib import Path
from typing import Any

from research.canonical import canonical_json
from research.execution import candidate_id_for
from research.experiments import Experiment, ParameterSet
from research.optimization_analysis import OptimizationCandidateSummary


class OptimizationResearchPersistenceError(RuntimeError):
    def __init__(self, message: str, *, code: str = "OPTIMIZATION_RESULTS_UNAVAILABLE") -> None:
        super().__init__(message)
        self.code = code


_METRIC_OBJECTS = (
    "cagr",
    "total_return",
    "max_drawdown",
    "sharpe_ratio",
    "sortino_ratio",
    "calmar_ratio",
    "annualized_volatility",
    "turnover",
    "xirr",
)


class OptimizationResearchRepository:
    """Read candidate coordinates and canonical metric scalars without full run artifacts."""

    MAX_CANDIDATES = 1_000

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=30.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA query_only = ON")
        return connection

    def list_candidate_summaries(
        self, experiment: Experiment
    ) -> tuple[OptimizationCandidateSummary, ...]:
        columns: list[str] = []
        for name in _METRIC_OBJECTS:
            columns.extend(
                (
                    f"json_extract(r.performance_summary_json, '$.{name}.value') AS {name}_value",
                    f"json_extract(r.performance_summary_json, '$.{name}.status') AS {name}_status",
                    f"json_extract(r.performance_summary_json, '$.{name}.reason') AS {name}_reason",
                )
            )
        columns.extend(
            (
                self._json_extract("trade_metrics.number_of_closed_trades", "trade_count_value"),
                self._json_extract(
                    "trade_metrics.average_holding_period.value",
                    "average_holding_period_value",
                ),
                self._json_extract(
                    "trade_metrics.average_holding_period.status",
                    "average_holding_period_status",
                ),
                self._json_extract(
                    "trade_metrics.average_holding_period.reason",
                    "average_holding_period_reason",
                ),
                self._json_extract(
                    "exposure_summary.average_gross_exposure",
                    "average_gross_exposure_value",
                ),
                self._json_extract(
                    "exposure_summary.time_invested_fraction",
                    "time_invested_fraction_value",
                ),
                self._json_extract("final_equity", "final_equity_value"),
            )
        )
        query = f"""
            SELECT c.experiment_id, c.candidate_index, c.parameter_set_hash,
                   c.candidate_set_hash, p.parameter_space_hash,
                   p.canonical_json AS parameter_set_json,
                   e.candidate_id AS execution_candidate_id,
                   COALESCE(e.status, 'pending') AS execution_status,
                   e.failure_code, e.failure_message,
                   CASE WHEN r.experiment_result_id IS NOT NULL THEN 'completed'
                        WHEN e.status = 'failed' THEN 'failed'
                        ELSE COALESCE(e.status, 'pending') END AS result_status,
                   r.experiment_result_id, r.candidate_id AS result_candidate_id,
                   r.result_hash,
                   COALESCE(r.derived_strategy_version_id, e.derived_strategy_version_id)
                       AS derived_strategy_version_id,
                   COALESCE(r.derived_strategy_version_hash, e.derived_strategy_version_hash)
                       AS derived_strategy_version_hash,
                   r.backtest_configuration_hash, r.backtest_run_id,
                   r.is_start, r.is_end, r.engine_version, r.analysis_version,
                   {", ".join(columns)}
            FROM experiment_candidates c
            JOIN experiment_parameter_sets p
              ON p.parameter_set_hash = c.parameter_set_hash
            LEFT JOIN candidate_executions e
              ON e.experiment_id = c.experiment_id
             AND e.candidate_index = c.candidate_index
            LEFT JOIN research_experiment_results r
              ON r.experiment_id = c.experiment_id
             AND r.candidate_index = c.candidate_index
            WHERE c.experiment_id = ?
            ORDER BY c.candidate_index ASC
        """
        connection = self._connect()
        try:
            rows = connection.execute(query, (experiment.experiment_id,)).fetchall()
            if len(rows) > self.MAX_CANDIDATES:
                raise OptimizationResearchPersistenceError(
                    "optimization research candidate limit exceeded",
                    code="OPTIMIZATION_CANDIDATE_LIMIT_EXCEEDED",
                )
            if any(int(row["candidate_index"]) != index for index, row in enumerate(rows)):
                raise OptimizationResearchPersistenceError(
                    "candidate indexes are not contiguous", code="OPTIMIZATION_INTEGRITY_ERROR"
                )
            return tuple(self._decode(row, experiment) for row in rows)
        except OptimizationResearchPersistenceError:
            raise
        except (sqlite3.Error, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise OptimizationResearchPersistenceError(
                "optimization result projection failed integrity checks",
                code="OPTIMIZATION_INTEGRITY_ERROR",
            ) from exc
        finally:
            connection.close()

    @classmethod
    def _decode(cls, row: sqlite3.Row, experiment: Experiment) -> OptimizationCandidateSummary:
        if row["parameter_space_hash"] != experiment.parameter_space_hash:
            raise OptimizationResearchPersistenceError(
                "candidate parameter space does not match experiment",
                code="OPTIMIZATION_INTEGRITY_ERROR",
            )
        raw_parameter_set = str(row["parameter_set_json"])
        payload = json.loads(raw_parameter_set, parse_constant=cls._reject_nonfinite)
        if raw_parameter_set != canonical_json(payload):
            raise OptimizationResearchPersistenceError(
                "candidate parameter set is not canonical",
                code="OPTIMIZATION_INTEGRITY_ERROR",
            )
        parameter_set = ParameterSet.from_dict(payload, experiment.parameter_space)
        if parameter_set.content_hash != row["parameter_set_hash"]:
            raise OptimizationResearchPersistenceError(
                "candidate parameter set hash mismatch", code="OPTIMIZATION_INTEGRITY_ERROR"
            )
        expected_candidate_id = candidate_id_for(
            experiment.experiment_id,
            int(row["candidate_index"]),
            str(row["parameter_set_hash"]),
        )
        stored_ids = tuple(
            value
            for value in (row["execution_candidate_id"], row["result_candidate_id"])
            if value is not None
        )
        if any(value != expected_candidate_id for value in stored_ids):
            raise OptimizationResearchPersistenceError(
                "candidate identity mismatch", code="OPTIMIZATION_INTEGRITY_ERROR"
            )
        if row["experiment_result_id"] is not None and (
            row["is_start"] != experiment.is_start_date.isoformat()
            or row["is_end"] != experiment.is_end_date.isoformat()
            or row["engine_version"] != experiment.engine_version
            or row["analysis_version"] != experiment.analysis_version
        ):
            raise OptimizationResearchPersistenceError(
                "candidate IS provenance mismatch", code="OPTIMIZATION_IS_INTEGRITY_ERROR"
            )
        summary: dict[str, Any] = {}
        for name in _METRIC_OBJECTS:
            cls._add_metric_object(summary, row, name)
        trade_metrics: dict[str, Any] = {}
        if row["trade_count_value"] is not None:
            trade_metrics["number_of_closed_trades"] = int(row["trade_count_value"])
        cls._add_metric_object(trade_metrics, row, "average_holding_period")
        if trade_metrics:
            summary["trade_metrics"] = trade_metrics
        exposure_summary = {
            name: cls._finite_or_none(row[f"{name}_value"])
            for name in ("average_gross_exposure", "time_invested_fraction")
            if row[f"{name}_value"] is not None
        }
        if exposure_summary:
            summary["exposure_summary"] = exposure_summary
        if row["final_equity_value"] is not None:
            summary["final_equity"] = cls._finite_or_none(row["final_equity_value"])
        return OptimizationCandidateSummary(
            experiment_id=str(row["experiment_id"]),
            candidate_id=expected_candidate_id,
            candidate_index=int(row["candidate_index"]),
            parameter_set_hash=str(row["parameter_set_hash"]),
            candidate_set_hash=str(row["candidate_set_hash"]),
            parameter_values=parameter_set.values,
            execution_status=str(row["execution_status"]),
            result_status=str(row["result_status"]),
            experiment_result_id=cls._optional(row["experiment_result_id"]),
            result_hash=cls._optional(row["result_hash"]),
            derived_strategy_version_id=cls._optional(row["derived_strategy_version_id"]),
            derived_strategy_version_hash=cls._optional(row["derived_strategy_version_hash"]),
            backtest_configuration_hash=cls._optional(row["backtest_configuration_hash"]),
            backtest_run_id=cls._optional(row["backtest_run_id"]),
            is_start=cls._optional(row["is_start"]),
            is_end=cls._optional(row["is_end"]),
            engine_version=cls._optional(row["engine_version"]),
            analysis_version=cls._optional(row["analysis_version"]),
            performance_summary=summary,
            failure_code=cls._optional(row["failure_code"]),
            failure_summary=cls._optional(row["failure_message"]),
        )

    @classmethod
    def _add_metric_object(cls, target: dict[str, Any], row: sqlite3.Row, name: str) -> None:
        status = row[f"{name}_status"]
        value = row[f"{name}_value"]
        reason = row[f"{name}_reason"]
        if status is None and value is None and reason is None:
            return
        target[name] = {
            "value": cls._finite_or_none(value),
            "status": str(status) if status is not None else None,
            "reason": str(reason) if reason is not None else None,
        }

    @staticmethod
    def _finite_or_none(value: object) -> float | None:
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise OptimizationResearchPersistenceError(
                "metric value is not numeric", code="OPTIMIZATION_INTEGRITY_ERROR"
            )
        numeric = float(value)
        if not math.isfinite(numeric):
            raise OptimizationResearchPersistenceError(
                "metric value is not finite", code="OPTIMIZATION_INTEGRITY_ERROR"
            )
        return numeric

    @staticmethod
    def _optional(value: object) -> str | None:
        return str(value) if value is not None else None

    @staticmethod
    def _json_extract(path: str, alias: str) -> str:
        return f"json_extract(r.performance_summary_json, '$.{path}') AS {alias}"

    @staticmethod
    def _reject_nonfinite(value: str) -> None:
        raise ValueError(f"non-finite JSON constant is not allowed: {value}")


__all__ = ["OptimizationResearchPersistenceError", "OptimizationResearchRepository"]
