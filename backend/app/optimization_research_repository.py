"""One-query lightweight projection for PHASE 11I optimization research."""

from __future__ import annotations

import json
import math
import sqlite3
from pathlib import Path
from typing import Any

from research.candidates import ParameterCandidateSet
from research.canonical import canonical_json
from research.execution import candidate_id_for
from research.experiments import Experiment, ParameterSet, ParameterSpace
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
                   c.candidate_set_hash, p.parameter_space_hash AS origin_space_hash,
                   p.canonical_json AS parameter_set_json,
                   origin_space.schema_version AS origin_space_schema_version,
                   origin_space.canonical_json AS origin_space_json,
                   x.protocol_id AS stored_protocol_id,
                   x.content_hash AS stored_experiment_hash,
                   x.parameter_space_hash AS experiment_space_hash,
                   x.base_strategy_version_id AS stored_base_version_id,
                   x.base_strategy_version_hash AS stored_base_version_hash,
                   candidate_set.parameter_space_hash AS candidate_space_hash,
                   candidate_set.candidate_set_hash AS stored_candidate_set_hash,
                   candidate_set.candidate_count AS stored_candidate_count,
                   candidate_set.theoretical_candidate_count AS theoretical_candidate_count,
                   CASE WHEN c.candidate_index = 0 THEN candidate_set.canonical_json END
                       AS candidate_set_json,
                   e.candidate_id AS execution_candidate_id,
                   e.parameter_set_hash AS execution_parameter_set_hash,
                   e.base_strategy_version_id AS execution_base_version_id,
                   e.base_strategy_version_hash AS execution_base_version_hash,
                   COALESCE(e.status, 'pending') AS execution_status,
                   e.failure_code, e.failure_message,
                   CASE WHEN r.experiment_result_id IS NOT NULL THEN 'completed'
                        WHEN e.status = 'failed' THEN 'failed'
                        ELSE COALESCE(e.status, 'pending') END AS result_status,
                   r.experiment_result_id, r.candidate_id AS result_candidate_id,
                   r.result_hash, r.parameter_set_hash AS result_parameter_set_hash,
                   r.parameter_space_hash AS result_space_hash,
                   r.candidate_set_hash AS result_candidate_set_hash,
                   r.base_strategy_version_id AS result_base_version_id,
                   r.base_strategy_version_hash AS result_base_version_hash,
                   COALESCE(r.derived_strategy_version_id, e.derived_strategy_version_id)
                       AS derived_strategy_version_id,
                   COALESCE(r.derived_strategy_version_hash, e.derived_strategy_version_hash)
                       AS derived_strategy_version_hash,
                   r.backtest_configuration_hash, r.backtest_run_id,
                   r.is_start, r.is_end, r.engine_version, r.analysis_version,
                   {", ".join(columns)}
            FROM experiment_candidates c
            JOIN experiments x ON x.experiment_id = c.experiment_id
            LEFT JOIN experiment_candidate_sets candidate_set
              ON candidate_set.experiment_id = c.experiment_id
            JOIN experiment_parameter_sets p
              ON p.parameter_set_hash = c.parameter_set_hash
            LEFT JOIN experiment_parameter_spaces origin_space
              ON origin_space.parameter_space_hash = p.parameter_space_hash
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
            summaries = tuple(self._decode(row, experiment) for row in rows)
            if rows:
                candidate_set = ParameterCandidateSet(
                    parameter_space=experiment.parameter_space,
                    theoretical_candidate_count=int(rows[0]["theoretical_candidate_count"]),
                    candidates=tuple(
                        ParameterSet(item.parameter_values, experiment.parameter_space)
                        for item in summaries
                    ),
                )
                if (
                    len(rows) != int(rows[0]["stored_candidate_count"])
                    or candidate_set.candidate_set_hash != rows[0]["stored_candidate_set_hash"]
                    or rows[0]["candidate_set_json"]
                    != canonical_json(
                        {
                            **candidate_set.to_dict(),
                            "candidate_set_hash": candidate_set.candidate_set_hash,
                        }
                    )
                ):
                    raise OptimizationResearchPersistenceError(
                        "candidate set content mismatch", code="OPTIMIZATION_INTEGRITY_ERROR"
                    )
            return summaries
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
        if (
            row["experiment_id"] != experiment.experiment_id
            or row["stored_experiment_hash"] != experiment.content_hash
            or row["stored_protocol_id"] != experiment.protocol_id
            or row["experiment_space_hash"] != experiment.parameter_space_hash
            or row["stored_base_version_id"] != experiment.base_strategy_version_id
            or row["stored_base_version_hash"] != experiment.base_strategy_version_hash
            or row["candidate_space_hash"] != experiment.parameter_space_hash
            or row["candidate_set_hash"] != row["stored_candidate_set_hash"]
        ):
            raise OptimizationResearchPersistenceError(
                "candidate binding does not match experiment",
                code="OPTIMIZATION_INTEGRITY_ERROR",
            )
        raw_parameter_set = str(row["parameter_set_json"])
        payload = json.loads(raw_parameter_set, parse_constant=cls._reject_nonfinite)
        if raw_parameter_set != canonical_json(payload):
            raise OptimizationResearchPersistenceError(
                "candidate parameter set is not canonical",
                code="OPTIMIZATION_INTEGRITY_ERROR",
            )
        origin_hash = row["origin_space_hash"]
        raw_origin_space = row["origin_space_json"]
        if (
            origin_hash is None
            or raw_origin_space is None
            or row["origin_space_schema_version"] != 2
        ):
            raise OptimizationResearchPersistenceError(
                "candidate original parameter space is missing",
                code="OPTIMIZATION_INTEGRITY_ERROR",
            )
        origin_payload = json.loads(raw_origin_space, parse_constant=cls._reject_nonfinite)
        if raw_origin_space != canonical_json(origin_payload):
            raise OptimizationResearchPersistenceError(
                "candidate original parameter space is not canonical",
                code="OPTIMIZATION_INTEGRITY_ERROR",
            )
        origin_space = ParameterSpace.from_dict(origin_payload)
        if origin_space.content_hash != origin_hash:
            raise OptimizationResearchPersistenceError(
                "candidate original parameter space hash mismatch",
                code="OPTIMIZATION_INTEGRITY_ERROR",
            )
        ParameterSet.from_dict(payload, origin_space)
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
        if row["execution_candidate_id"] is not None and (
            row["execution_parameter_set_hash"] != row["parameter_set_hash"]
            or row["execution_base_version_id"] != experiment.base_strategy_version_id
            or row["execution_base_version_hash"] != experiment.base_strategy_version_hash
        ):
            raise OptimizationResearchPersistenceError(
                "candidate execution binding mismatch", code="OPTIMIZATION_INTEGRITY_ERROR"
            )
        if row["experiment_result_id"] is not None and (
            row["result_parameter_set_hash"] != row["parameter_set_hash"]
            or row["result_space_hash"] != experiment.parameter_space_hash
            or row["result_candidate_set_hash"] != row["candidate_set_hash"]
            or row["result_base_version_id"] != experiment.base_strategy_version_id
            or row["result_base_version_hash"] != experiment.base_strategy_version_hash
        ):
            raise OptimizationResearchPersistenceError(
                "candidate result binding mismatch", code="OPTIMIZATION_INTEGRITY_ERROR"
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
