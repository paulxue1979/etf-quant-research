"""Atomic publication of a calculated PHASE 8F OOS outcome."""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from backend.app.backtest_models import BacktestRun
from backend.app.backtest_repository import BacktestRepository
from backend.app.oos_execution_repository import OosExecutionRepository
from backend.app.oos_result_repository import OosResultRepository
from backend.app.research_protocol import (
    OOSObservationStatus,
    ProtocolStatus,
    ResearchProtocolError,
    ResearchProtocolRepository,
    _dump,
    _freeze_from_dict,
    _load,
    _selection_from_dict,
)
from research.canonical import sha256_hash
from research.exceptions import (
    OosAlreadyObservedError,
    OosFinalizationConflictError,
    OosFinalizationPersistenceError,
    OosFinalizationStaleWriterError,
    OosOfficialResultExistsError,
    OosPreconditionError,
    OosResultIntegrityError,
)
from research.oos import (
    OosEvaluationConfig,
    OosEvaluationResult,
    OosPerformanceSummary,
    validate_one_shot_result,
    validate_oos_configuration,
    validate_oos_preconditions,
    validate_oos_range,
)
from research.oos_execution import OosExecution, OosExecutionStatus
from research.oos_execution_outcome import OosExecutionOutcome


class OosFinalizationService:
    """Publish one already-calculated outcome without doing any heavy work."""

    def __init__(
        self,
        *,
        protocol_repository: ResearchProtocolRepository,
        execution_repository: OosExecutionRepository,
        result_repository: OosResultRepository,
        backtest_repository: BacktestRepository,
        strategy_repository: Any,
        clock: Any | None = None,
    ) -> None:
        self._protocols = protocol_repository
        self._executions = execution_repository
        self._results = result_repository
        self._backtests = backtest_repository
        self._strategies = strategy_repository
        self._clock = clock or (lambda: datetime.now(UTC))

    def finalize_oos_observation(
        self,
        *,
        protocol_id: str,
        execution_id: str,
        lease_token: str,
        outcome: OosExecutionOutcome,
    ) -> OosEvaluationResult:
        """Atomically publish the official result or return an exact retry."""
        if not isinstance(outcome, OosExecutionOutcome):
            raise OosResultIntegrityError("OOS outcome is invalid")
        now = self._clock()
        connection = self._protocols._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._ensure_schemas(connection)
            execution = self._executions._load_execution(connection, execution_id)
            protocol = self._protocols._get_protocol_in_connection(connection, protocol_id)
            existing = OosResultRepository.get_by_protocol_in_transaction(connection, protocol_id)

            if existing is not None:
                candidate = self._build_result(
                    outcome,
                    execution=execution,
                    protocol=protocol,
                    now=now,
                    backtest_run_id=self._backtest_run_id(outcome),
                )
                validate_one_shot_result(existing, candidate)
                if existing.execution_id != execution_id:
                    raise OosFinalizationConflictError(
                        "official OOS result belongs to another execution"
                    )
                connection.execute("COMMIT")
                return existing

            self._validate_inputs(
                protocol_id=protocol_id,
                execution_id=execution_id,
                lease_token=lease_token,
                outcome=outcome,
                execution=execution,
                protocol=protocol,
                connection=connection,
                now=now,
            )
            strategy = self._strategies.get_any_version(execution.strategy_version_id)
            if strategy is None:
                raise OosPreconditionError("persisted OOS strategy version was not found")
            run_id = self._backtest_run_id(outcome)
            result = self._build_result(
                outcome, execution=execution, protocol=protocol, now=now, backtest_run_id=run_id
            )
            run = self._build_backtest_run(outcome, strategy_id=strategy.strategy_id, result=result)
            self._validate_run_binding(run, result, protocol)

            BacktestRepository.persist_in_transaction(connection, run)
            OosResultRepository.persist_in_transaction(connection, result)
            completed = self._executions.complete_in_transaction(
                connection, execution, lease_token=lease_token, now=now
            )
            self._insert_official_observation(
                connection,
                result=result,
                execution=completed,
                freeze_id=execution.strategy_freeze_id,
            )
            self._transition_protocol(connection, protocol, result, now=now)
            connection.execute("COMMIT")
            return result
        except (OosFinalizationConflictError, OosFinalizationStaleWriterError,
                OosOfficialResultExistsError, OosPreconditionError, OosResultIntegrityError,
                OosAlreadyObservedError, ResearchProtocolError,
                OosFinalizationPersistenceError):
            self._rollback(connection)
            raise
        except (sqlite3.Error, ValueError, TypeError, KeyError, OverflowError) as exc:
            self._rollback(connection)
            raise OosFinalizationPersistenceError(
                "could not atomically finalize OOS observation"
            ) from exc
        except Exception as exc:
            self._rollback(connection)
            raise OosFinalizationPersistenceError(
                "could not atomically finalize OOS observation"
            ) from exc
        finally:
            connection.close()

    def _validate_inputs(
        self,
        *,
        protocol_id: str,
        execution_id: str,
        lease_token: str,
        outcome: OosExecutionOutcome,
        execution: OosExecution,
        protocol: Any,
        connection: sqlite3.Connection,
        now: datetime,
    ) -> None:
        if protocol is None or protocol.protocol_id != protocol_id:
            raise OosPreconditionError("research protocol was not found")
        if protocol.status != ProtocolStatus.SELECTION_RECORDED:
            raise OosPreconditionError("OOS finalization requires selection_recorded protocol")
        if execution.protocol_id != protocol_id or execution.execution_id != execution_id:
            raise OosPreconditionError("OOS execution identity does not match request")
        if execution.status is not OosExecutionStatus.RUNNING:
            raise OosPreconditionError("OOS execution is not RUNNING")
        if execution.lease_token != lease_token:
            raise OosFinalizationStaleWriterError("OOS execution lease token does not match")
        if execution.lease_expires_at is None or execution.lease_expires_at <= now:
            raise OosFinalizationStaleWriterError("OOS execution lease has expired")
        if outcome.execution_id != execution_id or outcome.protocol_id != protocol_id:
            raise OosPreconditionError("OOS outcome identity does not match execution")
        if outcome.strategy_version_id != execution.strategy_version_id:
            raise OosPreconditionError("OOS outcome strategy version does not match execution")
        if outcome.strategy_content_hash != execution.strategy_content_hash:
            raise OosPreconditionError("OOS outcome strategy hash does not match execution")
        if outcome.oos_spec_hash != execution.oos_spec_hash:
            raise OosPreconditionError("OOS outcome spec hash does not match execution")
        if outcome.engine_version != self._engine_version(connection, protocol_id):
            raise OosPreconditionError("OOS outcome engine version does not match protocol")
        if outcome.oos_start != protocol.oos_start_date or outcome.oos_end != protocol.oos_end_date:
            raise OosPreconditionError("OOS outcome range does not match protocol")
        validate_oos_range(
            protocol.is_start_date,
            protocol.is_end_date,
            outcome.oos_start,
            outcome.oos_end,
            warmup_start=outcome.warmup_request_start,
        )
        selection_row = connection.execute(
            "SELECT payload_json FROM research_selection_decisions WHERE decision_id = ?",
            (execution.selection_decision_id,),
        ).fetchone()
        freeze_row = connection.execute(
            "SELECT payload_json FROM research_strategy_freezes WHERE freeze_id = ?",
            (execution.strategy_freeze_id,),
        ).fetchone()
        if selection_row is None or freeze_row is None:
            raise OosPreconditionError("frozen OOS selection records are incomplete")
        selection = _selection_from_dict(_load(selection_row["payload_json"]))
        freeze = _freeze_from_dict(_load(freeze_row["payload_json"]))
        strategy = self._strategies.get_any_version(execution.strategy_version_id)
        identity = validate_oos_preconditions(
            protocol,
            selection,
            freeze,
            strategy,
            requested_oos_start=outcome.oos_start,
            requested_oos_end=outcome.oos_end,
        )
        if identity.strategy_content_hash != outcome.strategy_content_hash:
            raise OosPreconditionError("OOS outcome is not bound to the frozen strategy")
        frozen = OosEvaluationConfig.from_research_evaluation_config(
            protocol.evaluation_config, analytics_version=outcome.analytics_version
        )
        actual_payload = dict(outcome.backtest_result.configuration_snapshot)
        actual_payload["analytics_version"] = outcome.analytics_version
        actual = OosEvaluationConfig.from_dict(actual_payload)
        validate_oos_configuration(frozen, actual)
        if outcome.oos_spec_hash != execution.oos_spec_hash:
            raise OosPreconditionError("OOS spec identity changed")
        if self._official_observation_exists(connection, protocol_id):
            raise OosOfficialResultExistsError("official OOS observation already exists")

    @staticmethod
    def _engine_version(connection: sqlite3.Connection, protocol_id: str) -> str:
        row = connection.execute(
            "SELECT payload_json FROM research_protocols WHERE protocol_id = ?", (protocol_id,)
        ).fetchone()
        if row is None:
            return ""
        payload = _load(row["payload_json"])
        config = payload.get("evaluation_config")
        return str(config.get("engine_version", "")) if isinstance(config, Mapping) else ""

    @staticmethod
    def _official_observation_exists(connection: sqlite3.Connection, protocol_id: str) -> bool:
        row = connection.execute(
            "SELECT payload_json FROM research_oos_evaluations WHERE protocol_id = ?",
            (protocol_id,),
        ).fetchone()
        if row is None:
            return False
        return _load(row["payload_json"]).get("status") in (
            OOSObservationStatus.OBSERVED,
            OOSObservationStatus.SEALED,
        )

    @staticmethod
    def _build_result(
        outcome: OosExecutionOutcome, *, execution: OosExecution, protocol: Any, now: datetime,
        backtest_run_id: str | None = None,
    ) -> OosEvaluationResult:
        resolved_backtest_run_id = backtest_run_id or OosFinalizationService._backtest_run_id(
            outcome
        )
        result_id = "oos-result-" + sha256_hash(
            {"protocol_id": protocol.protocol_id, "execution_id": execution.execution_id,
             "outcome_hash": outcome.outcome_hash}
        )
        return OosEvaluationResult(
            oos_result_id=result_id,
            execution_id=execution.execution_id,
            protocol_id=protocol.protocol_id,
            selection_decision_id=execution.selection_decision_id,
            strategy_freeze_id=execution.strategy_freeze_id,
            strategy_version_id=outcome.strategy_version_id,
            strategy_content_hash=outcome.strategy_content_hash,
            backtest_run_id=resolved_backtest_run_id,
            oos_start=outcome.oos_start,
            oos_end=outcome.oos_end,
            warmup_start=outcome.warmup_request_start,
            price_field_used=outcome.data_provenance.price_field,
            configuration_hash=execution.configuration_hash,
            engine_version=outcome.engine_version,
            analytics_version=outcome.analytics_version,
            data_provenance=outcome.data_provenance,
            performance_summary=OosPerformanceSummary.from_analysis(outcome.performance_analysis),
            created_at=now,
        )

    @staticmethod
    def _build_backtest_run(
        outcome: OosExecutionOutcome, *, strategy_id: str, result: OosEvaluationResult
    ) -> BacktestRun:
        run_id = "backtest-oos-" + sha256_hash(
            {"execution_id": outcome.execution_id, "outcome_hash": outcome.outcome_hash}
        )
        analysis = outcome.performance_analysis
        analysis = type(analysis)(
            **{**analysis.__dict__, "backtest_run_id": run_id, "strategy_id": strategy_id}
        )
        provenance = {
            "source": "phase-8f-3-oos-execution",
            "protocol_id": outcome.protocol_id,
            "execution_id": outcome.execution_id,
            "oos_start": outcome.oos_start.isoformat(),
            "oos_end": outcome.oos_end.isoformat(),
            "warmup_start": outcome.warmup_request_start.isoformat(),
            "data_provenance": outcome.data_provenance.to_dict(),
        }
        return BacktestRun(
            backtest_run_id=run_id,
            strategy_id=strategy_id,
            strategy_version_id=outcome.strategy_version_id,
            created_at=result.created_at,
            strategy_version_content_hash=outcome.strategy_content_hash,
            backtest_result=outcome.backtest_result,
            performance_analysis=analysis,
            provenance=provenance,
            strategy_provenance=(
                outcome.strategy_provenance
                if outcome.strategy_provenance is not None
                else None
            ),
        )

    @staticmethod
    def _backtest_run_id(outcome: OosExecutionOutcome) -> str:
        return "backtest-oos-" + sha256_hash(
            {"execution_id": outcome.execution_id, "outcome_hash": outcome.outcome_hash}
        )

    @staticmethod
    def _validate_run_binding(run: BacktestRun, result: OosEvaluationResult, protocol: Any) -> None:
        if run.backtest_run_id != result.backtest_run_id:
            raise OosResultIntegrityError("official backtest identity mismatch")
        if run.strategy_version_id != result.strategy_version_id:
            raise OosResultIntegrityError("official backtest strategy identity mismatch")
        if (
            run.backtest_result.start_date != result.oos_start
            or run.backtest_result.end_date != result.oos_end
        ):
            raise OosResultIntegrityError("official backtest range mismatch")
        if (
            run.backtest_result.configuration_snapshot.get("price_field_used")
            != result.price_field_used.value
        ):
            raise OosResultIntegrityError("official backtest price field mismatch")

    def _insert_official_observation(
        self, connection: sqlite3.Connection, *, result: OosEvaluationResult,
        execution: OosExecution, freeze_id: str
    ) -> None:
        payload = {
            "evaluation_id": result.oos_result_id,
            "protocol_id": result.protocol_id,
            "freeze_id": freeze_id,
            "strategy_version_id": result.strategy_version_id,
            "backtest_run_id": result.backtest_run_id,
            "status": OOSObservationStatus.OBSERVED,
            "observed_at": result.created_at.isoformat(),
            "created_at": result.created_at.isoformat(),
            "provenance": {
                "execution_id": execution.execution_id,
                "result_hash": result.result_hash,
                "oos_result_id": result.oos_result_id,
                "backtest_run_id": result.backtest_run_id,
            },
            "untouched_oos": False,
        }
        connection.execute(
            "INSERT INTO research_oos_evaluations VALUES (?, ?, ?, ?)",
            (
                result.oos_result_id,
                result.protocol_id,
                result.created_at.isoformat(),
                _dump(payload),
            ),
        )

    def _transition_protocol(
        self,
        connection: sqlite3.Connection,
        protocol: Any,
        result: OosEvaluationResult,
        *,
        now: datetime,
    ) -> None:
        if protocol.status != ProtocolStatus.SELECTION_RECORDED:
            raise ResearchProtocolError("OOS finalization requires selection_recorded protocol")
        updated = protocol.with_status(ProtocolStatus.OOS_EVALUATED)
        del updated
        event_time = OosFinalizationService._next_protocol_event_time(
            connection, result.protocol_id, now
        )
        payload = {
            "status": ProtocolStatus.OOS_EVALUATED,
            "finalization": "oos_observation",
            "protocol_id": result.protocol_id,
            "execution_id": result.execution_id,
            "oos_result_id": result.oos_result_id,
            "backtest_run_id": result.backtest_run_id,
            "strategy_version_id": result.strategy_version_id,
            "result_hash": result.result_hash,
        }
        connection.execute(
            "INSERT INTO research_protocol_events VALUES (?, ?, ?, ?, ?)",
            (f"event-oos-finalized-{result.result_hash}", result.protocol_id,
             ProtocolStatus.OOS_EVALUATED, event_time.isoformat(), _dump(payload)),
        )
        connection.execute(
            "INSERT INTO research_protocol_events VALUES (?, ?, ?, ?, ?)",
            (f"event-oos-observed-{result.result_hash}", result.protocol_id,
             "oos_observation_finalized", (event_time + timedelta(microseconds=1)).isoformat(),
             _dump(payload)),
        )

    @staticmethod
    def _next_protocol_event_time(
        connection: sqlite3.Connection, protocol_id: str, now: datetime
    ) -> datetime:
        """Keep event replay monotonic when a deterministic test clock is used."""
        rows = connection.execute(
            "SELECT created_at FROM research_protocol_events WHERE protocol_id = ?",
            (protocol_id,),
        ).fetchall()
        latest: datetime | None = None
        for row in rows:
            try:
                timestamp = datetime.fromisoformat(str(row["created_at"]))
            except (TypeError, ValueError):
                continue
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=UTC)
            if latest is None or timestamp > latest:
                latest = timestamp
        candidate = now if now.tzinfo is not None else now.replace(tzinfo=UTC)
        if latest is not None and candidate <= latest:
            return latest + timedelta(microseconds=1)
        return candidate

    @staticmethod
    def _ensure_schemas(connection: sqlite3.Connection) -> None:
        from backend.app.oos_result_repository import OosResultRepository

        OosResultRepository.ensure_schema(connection)
        BacktestRepository.ensure_schema(connection)

    @staticmethod
    def _rollback(connection: sqlite3.Connection) -> None:
        try:
            connection.execute("ROLLBACK")
        except sqlite3.Error:
            pass


__all__ = ["OosFinalizationService"]
