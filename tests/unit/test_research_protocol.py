from __future__ import annotations

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, date, datetime

import pytest

from backend.app.research_protocol import (
    CandidateSet,
    CandidateSetStatus,
    OOSEvaluationRecord,
    OOSObservationStatus,
    ProtocolStatus,
    ResearchEvaluationConfig,
    ResearchPersistenceError,
    ResearchProtocol,
    ResearchProtocolError,
    ResearchProtocolRepository,
    SelectionDecision,
    StrategyFreezeRecord,
)
from strategies import StrategyVersion
from tests.unit.test_backtest_repository import _run
from tests.unit.test_strategy_repository import _definition


def _protocol() -> ResearchProtocol:
    return ResearchProtocol(
        protocol_id="holdout-2026",
        protocol_version=1,
        created_at=datetime(2026, 9, 8, tzinfo=UTC),
        is_start_date=date(2026, 1, 2),
        is_end_date=date(2026, 1, 4),
        oos_start_date=date(2026, 1, 6),
        oos_end_date=date(2026, 1, 8),
        selection_rules=("human review of IS metrics",),
        allowed_metrics=("cagr", "max_drawdown"),
        forbidden_actions=("oos_back_selection", "oos_parameter_tuning"),
        data_policy={"snapshot_note": "request provenance only"},
        execution_policy={"execution_rule": "next_trading_day_open"},
        evaluation_policy={"oos_selection_allowed": False},
        evaluation_config=ResearchEvaluationConfig(
            price_field_used="adjusted_close",
            initial_capital=10_000.0,
            commission={"rate": 0.0, "per_order": 0.0},
            slippage=0.0,
            execution_rule="next_trading_day_open",
            fractional_shares=False,
            rebalance_policy={"frequency": "daily", "threshold": None},
            engine_version="phase-3.0",
        ),
        provenance={"source": "unit-test"},
    )


def _version() -> StrategyVersion:
    definition = _definition("repo")
    return StrategyVersion(
        strategy_id="repo",
        version_id="repo-v1",
        version_number=1,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        configuration=definition,
    )


def _is_run(version: StrategyVersion):
    return _run_for_version(version)


def _run_for_version(
    version: StrategyVersion,
    run_id: str = "repo-run",
    start_date: date | None = None,
    end_date: date | None = None,
):
    source = _run()
    result = replace(
        source.backtest_result,
        strategy_version_id=version.version_id,
        start_date=start_date or source.backtest_result.start_date,
        end_date=end_date or source.backtest_result.end_date,
    )
    analysis = replace(
        source.performance_analysis,
        backtest_run_id=run_id,
        strategy_version_id=version.version_id,
        start_date=result.start_date,
        end_date=result.end_date,
    )
    return replace(
        source,
        backtest_run_id=run_id,
        strategy_version_id=version.version_id,
        strategy_version_content_hash=version.content_hash,
        backtest_result=result,
        performance_analysis=analysis,
    )


def _oos_run(version: StrategyVersion):
    return _run_for_version(
        version,
        start_date=date(2026, 1, 6),
        end_date=date(2026, 1, 8),
    )


def _oos_run_with_snapshot_changes(version: StrategyVersion, **changes: object):
    run = _oos_run(version)
    snapshot = dict(run.backtest_result.configuration_snapshot)
    for field, value in changes.items():
        if field in {"commission", "rebalance_policy"}:
            snapshot[field] = {**dict(snapshot[field]), **dict(value)}
        else:
            snapshot[field] = value
    return replace(
        run,
        backtest_result=replace(run.backtest_result, configuration_snapshot=snapshot),
    )


def _candidate_set(version: StrategyVersion | None = None) -> CandidateSet:
    version = version or _version()
    return CandidateSet(
        candidate_set_id="candidate-set-1",
        protocol_id="holdout-2026",
        strategy_version_ids=("repo-v1",),
        created_at=datetime(2026, 9, 8, tzinfo=UTC),
        strategy_version_content_hashes={"repo-v1": version.content_hash or ""},
    )


def _version_two() -> StrategyVersion:
    return replace(_version(), version_id="repo-v2", version_number=2)


def _advance_to_is(
    repository: ResearchProtocolRepository, version: StrategyVersion | None = None
) -> CandidateSet:
    version = version or _version()
    repository.create_protocol(_protocol())
    repository.create_candidate_set(_candidate_set(version))
    locked = repository.lock_candidate_set("candidate-set-1")
    repository.transition_protocol("holdout-2026", ProtocolStatus.FROZEN)
    repository.transition_protocol("holdout-2026", ProtocolStatus.IS_EVALUATED)
    return locked


def _selection() -> SelectionDecision:
    return SelectionDecision(
        decision_id="selection-1",
        protocol_id="holdout-2026",
        candidate_set_id="candidate-set-1",
        selected_strategy_version_id="repo-v1",
        is_backtest_run_ids=("repo-run",),
        selected_metrics={"cagr": 0.12, "max_drawdown": -0.2},
        rationale="Human researcher selected the candidate using IS evidence only.",
        created_at=datetime(2026, 9, 8, tzinfo=UTC),
        data_provenance={"scope": "in_sample"},
    )


def _freeze(version: StrategyVersion) -> StrategyFreezeRecord:
    return StrategyFreezeRecord(
        freeze_id="freeze-1",
        protocol_id="holdout-2026",
        strategy_version_id="repo-v1",
        strategy_version_content_hash=version.content_hash or "",
        selection_decision_id="selection-1",
        frozen_at=datetime(2026, 9, 8, tzinfo=UTC),
        reason="Selected candidate is frozen before OOS observation.",
    )


def _prepare_frozen_oos(
    repository: ResearchProtocolRepository, version: StrategyVersion | None = None
) -> tuple[StrategyVersion, StrategyFreezeRecord]:
    version = version or _version()
    candidate_set = _advance_to_is(repository, version)
    decision = repository.create_selection(
        _selection(), candidate_set=candidate_set, runs=(_is_run(version),), version=version
    )
    repository.transition_protocol("holdout-2026", ProtocolStatus.SELECTION_RECORDED)
    return version, repository.create_freeze(_freeze(version), decision=decision, version=version)


def test_protocol_rejects_overlapping_or_insufficiently_separated_split() -> None:
    with pytest.raises(ResearchProtocolError, match="overlap"):
        replace(_protocol(), oos_start_date=date(2026, 1, 4))

    with pytest.raises(ResearchProtocolError, match="required separation"):
        replace(_protocol(), gap_days=3)


def test_protocol_status_transition_is_one_way() -> None:
    frozen = _protocol().with_status(ProtocolStatus.FROZEN)

    assert frozen.status == ProtocolStatus.FROZEN
    with pytest.raises(ResearchProtocolError, match="invalid protocol transition"):
        frozen.with_status(ProtocolStatus.DRAFT)


def test_repository_persists_append_only_protocol_state(tmp_path) -> None:
    repository = ResearchProtocolRepository(tmp_path / "research.db")
    repository.create_protocol(_protocol())
    repository.create_candidate_set(_candidate_set())
    assert repository.get_candidate_set("candidate-set-1").status == CandidateSetStatus.OPEN

    locked = repository.lock_candidate_set("candidate-set-1")
    assert locked.status == CandidateSetStatus.LOCKED
    assert repository.get_protocol("holdout-2026").status == ProtocolStatus.DRAFT

    frozen = repository.transition_protocol("holdout-2026", ProtocolStatus.FROZEN)
    assert frozen.status == ProtocolStatus.FROZEN
    assert (
        repository.get_frozen_evaluation_config("holdout-2026").canonical_json()
        == _protocol().evaluation_config.canonical_json()
    )
    assert (
        ResearchProtocolRepository(tmp_path / "research.db").get_protocol("holdout-2026").status
        == ProtocolStatus.FROZEN
    )
    assert (
        ResearchProtocolRepository(tmp_path / "research.db")
        .get_frozen_evaluation_config("holdout-2026")
        .canonical_json()
        == _protocol().evaluation_config.canonical_json()
    )


def test_protocol_cannot_freeze_without_one_locked_candidate_set(tmp_path) -> None:
    repository = ResearchProtocolRepository(tmp_path / "research.db")
    repository.create_protocol(_protocol())

    with pytest.raises(ResearchProtocolError, match="locked candidate set"):
        repository.transition_protocol("holdout-2026", ProtocolStatus.FROZEN)


def test_selection_accepts_is_runs_only_and_requires_locked_candidate_set(tmp_path) -> None:
    repository = ResearchProtocolRepository(tmp_path / "research.db")
    version = _version()
    candidate_set = _advance_to_is(repository, version)

    with pytest.raises(ResearchProtocolError, match="IS backtest runs only"):
        repository.create_selection(
            _selection(),
            candidate_set=candidate_set,
            runs=(_oos_run(version),),
            version=version,
        )

    decision = repository.create_selection(
        _selection(), candidate_set=candidate_set, runs=(_is_run(version),), version=version
    )
    assert decision.selected_strategy_version_id == "repo-v1"
    assert repository.get_selection("selection-1") == decision

    with pytest.raises(ResearchProtocolError, match="already has a selection decision"):
        repository.create_selection(
            replace(_selection(), decision_id="selection-2"),
            candidate_set=candidate_set,
            runs=(_is_run(version),),
            version=version,
        )


def test_freeze_requires_selection_and_hash_match(tmp_path) -> None:
    repository = ResearchProtocolRepository(tmp_path / "research.db")
    version = _version()
    candidate_set = _advance_to_is(repository, version)
    decision = repository.create_selection(
        _selection(), candidate_set=candidate_set, runs=(_is_run(version),), version=version
    )
    repository.transition_protocol("holdout-2026", ProtocolStatus.SELECTION_RECORDED)

    freeze = repository.create_freeze(_freeze(version), decision=decision, version=version)
    assert repository.get_freeze("freeze-1") == freeze

    with pytest.raises(ResearchProtocolError, match="content hash"):
        repository.create_freeze(
            replace(_freeze(version), freeze_id="freeze-2", strategy_version_content_hash="bad"),
            decision=decision,
            version=version,
        )


def test_observed_oos_is_single_use_and_must_follow_freeze(tmp_path) -> None:
    repository = ResearchProtocolRepository(tmp_path / "research.db")
    version = _version()
    candidate_set = _advance_to_is(repository, version)
    decision = repository.create_selection(
        _selection(), candidate_set=candidate_set, runs=(_is_run(version),), version=version
    )
    repository.transition_protocol("holdout-2026", ProtocolStatus.SELECTION_RECORDED)
    freeze = repository.create_freeze(_freeze(version), decision=decision, version=version)
    evaluation = OOSEvaluationRecord(
        evaluation_id="oos-1",
        protocol_id="holdout-2026",
        freeze_id="freeze-1",
        strategy_version_id="repo-v1",
        backtest_run_id="repo-run",
        status=OOSObservationStatus.OBSERVED,
        observed_at=datetime(2026, 9, 8, tzinfo=UTC),
        created_at=datetime(2026, 9, 8, tzinfo=UTC),
        provenance={"selection_allowed": False},
    )

    repository.create_oos_evaluation(evaluation, freeze=freeze, run=_oos_run(version))
    assert repository.list_oos_evaluations("holdout-2026")[0].untouched_oos is False
    assert repository.transition_protocol("holdout-2026", ProtocolStatus.OOS_EVALUATED).status == (
        ProtocolStatus.OOS_EVALUATED
    )

    with pytest.raises(ResearchProtocolError, match="already been observed"):
        repository.create_oos_evaluation(
            replace(evaluation, evaluation_id="oos-2"), freeze=freeze, run=_oos_run(version)
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("price_field_used", "raw_close"),
        ("initial_capital", 20_000.0),
        ("commission", {"rate": 0.01}),
        ("slippage", 0.01),
        ("execution_rule", "unsupported_execution_rule"),
        ("fractional_shares", True),
        ("rebalance_policy", {"frequency": "weekly"}),
        ("engine_version", "phase-3.1"),
    ],
)
def test_oos_rejects_each_evaluation_config_mismatch_without_persisting(
    tmp_path, field: str, value: object
) -> None:
    repository = ResearchProtocolRepository(tmp_path / "research.db")
    version, freeze = _prepare_frozen_oos(repository)
    evaluation = OOSEvaluationRecord(
        evaluation_id=f"oos-mismatch-{field}",
        protocol_id="holdout-2026",
        freeze_id=freeze.freeze_id,
        strategy_version_id=version.version_id,
        backtest_run_id="repo-run",
        status=OOSObservationStatus.OBSERVED,
        observed_at=datetime(2026, 9, 8, tzinfo=UTC),
        created_at=datetime(2026, 9, 8, tzinfo=UTC),
    )

    with pytest.raises(ResearchProtocolError) as error:
        repository.create_oos_evaluation(
            evaluation,
            freeze=freeze,
            run=_oos_run_with_snapshot_changes(version, **{field: value}),
        )

    assert error.value.code == "OOS_CONFIGURATION_MISMATCH"
    assert repository.list_oos_evaluations("holdout-2026") == ()


def test_oos_accepts_exact_frozen_evaluation_config(tmp_path) -> None:
    repository = ResearchProtocolRepository(tmp_path / "research.db")
    version, freeze = _prepare_frozen_oos(repository)
    evaluation = OOSEvaluationRecord(
        evaluation_id="oos-exact-config",
        protocol_id="holdout-2026",
        freeze_id=freeze.freeze_id,
        strategy_version_id=version.version_id,
        backtest_run_id="repo-run",
        status=OOSObservationStatus.OBSERVED,
        observed_at=datetime(2026, 9, 8, tzinfo=UTC),
        created_at=datetime(2026, 9, 8, tzinfo=UTC),
    )

    assert repository.create_oos_evaluation(
        evaluation, freeze=freeze, run=_oos_run(version)
    ) == evaluation


def test_candidate_hash_mismatch_is_rejected_before_selection(tmp_path) -> None:
    repository = ResearchProtocolRepository(tmp_path / "research.db")
    version = _version()
    repository.create_protocol(_protocol())
    candidate = replace(
        _candidate_set(version),
        strategy_version_content_hashes={version.version_id: "wrong-hash"},
    )
    repository.create_candidate_set(candidate)
    locked = repository.lock_candidate_set(candidate.candidate_set_id)
    repository.transition_protocol("holdout-2026", ProtocolStatus.FROZEN)
    repository.transition_protocol("holdout-2026", ProtocolStatus.IS_EVALUATED)

    with pytest.raises(ResearchProtocolError) as error:
        repository.create_selection(
            _selection(), candidate_set=locked, runs=(_is_run(version),), version=version
        )

    assert error.value.code == "CANDIDATE_CONTENT_HASH_MISMATCH"
    assert repository.list_selections("holdout-2026") == ()


def test_selection_requires_an_is_run_for_the_selected_version(tmp_path) -> None:
    repository = ResearchProtocolRepository(tmp_path / "research.db")
    selected = _version()
    other = _version_two()
    repository.create_protocol(_protocol())
    candidate = replace(
        _candidate_set(selected),
        strategy_version_ids=(selected.version_id, other.version_id),
        strategy_version_content_hashes={
            selected.version_id: selected.content_hash or "",
            other.version_id: other.content_hash or "",
        },
    )
    repository.create_candidate_set(candidate)
    locked = repository.lock_candidate_set(candidate.candidate_set_id)
    repository.transition_protocol("holdout-2026", ProtocolStatus.FROZEN)
    repository.transition_protocol("holdout-2026", ProtocolStatus.IS_EVALUATED)
    decision = _selection()
    other_run = _run_for_version(other)

    with pytest.raises(ResearchProtocolError) as error:
        repository.create_selection(
            decision,
            candidate_set=locked,
            runs=(other_run,),
            version=selected,
        )

    assert error.value.code == "SELECTION_MISSING_SELECTED_VERSION_IS_RUN"
    assert repository.list_selections("holdout-2026") == ()


def test_candidate_set_cannot_change_after_protocol_freezes(tmp_path) -> None:
    repository = ResearchProtocolRepository(tmp_path / "research.db")
    repository.create_protocol(_protocol())
    repository.create_candidate_set(_candidate_set())
    repository.lock_candidate_set("candidate-set-1")
    repository.transition_protocol("holdout-2026", ProtocolStatus.FROZEN)

    with pytest.raises(ResearchProtocolError, match="must be draft"):
        repository.create_candidate_set(
            replace(_candidate_set(), candidate_set_id="candidate-set-2")
        )


def test_selection_run_ids_must_match_and_freeze_uses_persisted_decision(tmp_path) -> None:
    repository = ResearchProtocolRepository(tmp_path / "research.db")
    version = _version()
    candidate_set = _advance_to_is(repository, version)

    with pytest.raises(ResearchProtocolError, match="do not match"):
        repository.create_selection(
            replace(_selection(), is_backtest_run_ids=("missing",)),
            candidate_set=candidate_set,
            runs=(_is_run(version),),
            version=version,
        )

    decision = repository.create_selection(
        _selection(), candidate_set=candidate_set, runs=(_is_run(version),), version=version
    )
    repository.transition_protocol("holdout-2026", ProtocolStatus.SELECTION_RECORDED)
    with pytest.raises(ResearchProtocolError, match="recorded selection"):
        repository.create_freeze(
            replace(_freeze(version), selection_decision_id="missing"),
            decision=decision,
            version=version,
        )


def test_oos_range_and_transition_require_observed_record(tmp_path) -> None:
    repository = ResearchProtocolRepository(tmp_path / "research.db")
    version = _version()
    candidate_set = _advance_to_is(repository, version)
    decision = repository.create_selection(
        _selection(), candidate_set=candidate_set, runs=(_is_run(version),), version=version
    )
    repository.transition_protocol("holdout-2026", ProtocolStatus.SELECTION_RECORDED)
    freeze = repository.create_freeze(_freeze(version), decision=decision, version=version)

    with pytest.raises(ResearchProtocolError, match="requires an observed OOS record"):
        repository.transition_protocol("holdout-2026", ProtocolStatus.OOS_EVALUATED)

    evaluation = OOSEvaluationRecord(
        evaluation_id="oos-invalid-range",
        protocol_id="holdout-2026",
        freeze_id=freeze.freeze_id,
        strategy_version_id=version.version_id,
        backtest_run_id="repo-run",
        status=OOSObservationStatus.OBSERVED,
        observed_at=datetime(2026, 9, 8, tzinfo=UTC),
        created_at=datetime(2026, 9, 8, tzinfo=UTC),
    )
    with pytest.raises(ResearchProtocolError, match="exactly match"):
        repository.create_oos_evaluation(
            evaluation,
            freeze=freeze,
            run=replace(
                _oos_run(version),
                backtest_result=replace(
                    _oos_run(version).backtest_result,
                    end_date=date(2026, 1, 7),
                ),
            ),
        )


def test_corrupted_persisted_protocol_is_rejected(tmp_path) -> None:
    db_path = tmp_path / "research.db"
    repository = ResearchProtocolRepository(db_path)
    repository.create_protocol(_protocol())
    connection = sqlite3.connect(db_path)
    try:
        connection.execute(
            "UPDATE research_protocols SET payload_json = ? WHERE protocol_id = ?",
            ('{"protocol_id":"holdout-2026","created_at":"not-a-date"}', "holdout-2026"),
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(ResearchPersistenceError, match="integrity checks"):
        repository.get_protocol("holdout-2026")


def test_schema_migration_refuses_duplicate_oos_records_without_deleting_them(tmp_path) -> None:
    db_path = tmp_path / "research.db"
    repository = ResearchProtocolRepository(db_path)
    version, freeze = _prepare_frozen_oos(repository)
    payload = OOSEvaluationRecord(
        evaluation_id="legacy-oos-1",
        protocol_id="holdout-2026",
        freeze_id=freeze.freeze_id,
        strategy_version_id=version.version_id,
        backtest_run_id="repo-run",
        status=OOSObservationStatus.OBSERVED,
        observed_at=datetime(2026, 9, 8, tzinfo=UTC),
        created_at=datetime(2026, 9, 8, tzinfo=UTC),
    ).to_dict()
    payload_two = {**payload, "evaluation_id": "legacy-oos-2"}
    with sqlite3.connect(db_path) as connection:
        connection.execute("DROP INDEX uq_research_oos_protocol")
        connection.execute(
            "INSERT INTO research_oos_evaluations VALUES (?, ?, ?, ?)",
            ("legacy-oos-1", "holdout-2026", "2026-09-08T00:00:00+00:00", json.dumps(payload)),
        )
        connection.execute(
            "INSERT INTO research_oos_evaluations VALUES (?, ?, ?, ?)",
            ("legacy-oos-2", "holdout-2026", "2026-09-08T00:00:01+00:00", json.dumps(payload_two)),
        )
        connection.execute(
            "UPDATE schema_metadata SET schema_version = 1 WHERE schema_key = ?",
            (ResearchProtocolRepository._SCHEMA_KEY,),
        )

    with pytest.raises(ResearchPersistenceError, match="duplicate OOS observations"):
        ResearchProtocolRepository(db_path)

    with sqlite3.connect(db_path) as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM research_oos_evaluations WHERE protocol_id = ?",
            ("holdout-2026",),
        ).fetchone()[0]
        version_row = connection.execute(
            "SELECT schema_version FROM schema_metadata WHERE schema_key = ?",
            (ResearchProtocolRepository._SCHEMA_KEY,),
        ).fetchone()[0]
    assert count == 2
    assert version_row == 1


def test_concurrent_oos_observations_are_serialized_to_one_record(tmp_path) -> None:
    db_path = tmp_path / "research.db"
    repository = ResearchProtocolRepository(db_path)
    version, freeze = _prepare_frozen_oos(repository)

    def record(evaluation_id: str) -> str:
        evaluation = OOSEvaluationRecord(
            evaluation_id=evaluation_id,
            protocol_id="holdout-2026",
            freeze_id=freeze.freeze_id,
            strategy_version_id=version.version_id,
            backtest_run_id="repo-run",
            status=OOSObservationStatus.OBSERVED,
            observed_at=datetime(2026, 9, 8, tzinfo=UTC),
            created_at=datetime(2026, 9, 8, tzinfo=UTC),
        )
        try:
            ResearchProtocolRepository(db_path).create_oos_evaluation(
                evaluation, freeze=freeze, run=_oos_run(version)
            )
        except ResearchProtocolError as error:
            return error.code or "missing-code"
        return "success"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(record, ("oos-concurrent-1", "oos-concurrent-2")))

    assert sorted(outcomes) == ["OOS_OBSERVATION_ALREADY_RECORDED", "success"]
    assert len(repository.list_oos_evaluations("holdout-2026")) == 1
