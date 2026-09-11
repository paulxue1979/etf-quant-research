from __future__ import annotations

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from backend.app.research_protocol import (
    ResearchPersistenceError,
    ResearchProtocolError,
    ResearchProtocolRepository,
)
from tests.unit.test_research_protocol import (
    _advance_to_is,
    _is_run,
    _selection,
    _version,
)


def _unique_index_columns(db_path, index_name: str) -> tuple[str, ...]:
    with sqlite3.connect(db_path) as connection:
        indexes = connection.execute(
            "PRAGMA index_list('research_selection_decisions')"
        ).fetchall()
        assert any(row[1] == index_name and row[2] == 1 for row in indexes)
        return tuple(
            row[2]
            for row in connection.execute(f'PRAGMA index_info("{index_name}")').fetchall()
        )


def test_selection_protocol_unique_index_exists_and_oos_unique_index_is_preserved(tmp_path) -> None:
    db_path = tmp_path / "research.db"
    ResearchProtocolRepository(db_path)

    with sqlite3.connect(db_path) as connection:
        metadata = connection.execute(
            "SELECT schema_version FROM schema_metadata WHERE schema_key = ?",
            (ResearchProtocolRepository._SCHEMA_KEY,),
        ).fetchone()
        selection_indexes = connection.execute(
            "PRAGMA index_list('research_selection_decisions')"
        ).fetchall()
        oos_indexes = connection.execute(
            "PRAGMA index_list('research_oos_evaluations')"
        ).fetchall()

    assert metadata == (3,)
    assert any(
        row[1] == "uq_research_selection_protocol" and row[2] == 1
        for row in selection_indexes
    )
    assert any(row[1] == "uq_research_oos_protocol" and row[2] == 1 for row in oos_indexes)
    assert _unique_index_columns(db_path, "uq_research_selection_protocol") == ("protocol_id",)


def test_exact_selection_retry_is_idempotent_and_conflicting_selection_is_rejected(
    tmp_path,
) -> None:
    db_path = tmp_path / "research.db"
    repository = ResearchProtocolRepository(db_path)
    version = _version()
    candidate_set = _advance_to_is(repository, version)
    decision = _selection()

    first = repository.create_selection(
        decision, candidate_set=candidate_set, runs=(_is_run(version),), version=version
    )
    retry = repository.create_selection(
        decision, candidate_set=candidate_set, runs=(_is_run(version),), version=version
    )
    assert retry == first

    conflicting = replace(decision, selected_metrics={"cagr": 0.99, "max_drawdown": -0.2})
    with pytest.raises(ResearchProtocolError) as error:
        repository.create_selection(
            conflicting,
            candidate_set=candidate_set,
            runs=(_is_run(version),),
            version=version,
        )
    assert error.value.code == "SELECTION_CONFLICT"

    with sqlite3.connect(db_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM research_selection_decisions WHERE protocol_id = ?",
            (decision.protocol_id,),
        ).fetchone()[0] == 1


def test_database_unique_constraint_rejects_direct_duplicate_insert(tmp_path) -> None:
    db_path = tmp_path / "research.db"
    repository = ResearchProtocolRepository(db_path)
    version = _version()
    candidate_set = _advance_to_is(repository, version)
    decision = _selection()
    repository.create_selection(
        decision, candidate_set=candidate_set, runs=(_is_run(version),), version=version
    )

    duplicate = replace(decision, decision_id="selection-2")
    with sqlite3.connect(db_path) as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO research_selection_decisions VALUES (?, ?, ?, ?, ?)",
                (
                    duplicate.decision_id,
                    duplicate.protocol_id,
                    duplicate.candidate_set_id,
                    duplicate.created_at.isoformat(),
                    json.dumps(duplicate.to_dict(), sort_keys=True),
                ),
            )


def test_concurrent_different_selections_only_one_is_accepted(tmp_path) -> None:
    db_path = tmp_path / "research.db"
    repository = ResearchProtocolRepository(db_path)
    version = _version()
    candidate_set = _advance_to_is(repository, version)
    decision_a = _selection()
    decision_b = replace(decision_a, decision_id="selection-2", rationale="Different selection")

    def submit(decision):
        try:
            stored = ResearchProtocolRepository(db_path).create_selection(
                decision,
                candidate_set=candidate_set,
                runs=(_is_run(version),),
                version=version,
            )
            return "success", stored.decision_id
        except ResearchProtocolError as error:
            return error.code, None

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(submit, (decision_a, decision_b)))

    assert sorted(outcome[0] for outcome in outcomes) == ["SELECTION_CONFLICT", "success"]
    assert repository.get_selection("selection-1") is not None or repository.get_selection(
        "selection-2"
    ) is not None


def test_concurrent_exact_retries_persist_one_row(tmp_path) -> None:
    db_path = tmp_path / "research.db"
    repository = ResearchProtocolRepository(db_path)
    version = _version()
    candidate_set = _advance_to_is(repository, version)
    decision = _selection()

    def submit(_):
        return ResearchProtocolRepository(db_path).create_selection(
            decision,
            candidate_set=candidate_set,
            runs=(_is_run(version),),
            version=version,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(submit, (1, 2)))

    assert outcomes[0] == outcomes[1] == decision
    with sqlite3.connect(db_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM research_selection_decisions WHERE protocol_id = ?",
            (decision.protocol_id,),
        ).fetchone()[0] == 1


def test_legacy_schema_migration_creates_selection_unique_index_and_preserves_data(
    tmp_path,
) -> None:
    db_path = tmp_path / "research.db"
    repository = ResearchProtocolRepository(db_path)
    version = _version()
    candidate_set = _advance_to_is(repository, version)
    decision = repository.create_selection(
        _selection(), candidate_set=candidate_set, runs=(_is_run(version),), version=version
    )

    with sqlite3.connect(db_path) as connection:
        connection.execute("DROP INDEX uq_research_selection_protocol")
        connection.execute("DROP INDEX uq_research_oos_protocol")
        connection.execute(
            "UPDATE schema_metadata SET schema_version = 1 WHERE schema_key = ?",
            (ResearchProtocolRepository._SCHEMA_KEY,),
        )

    reopened = ResearchProtocolRepository(db_path)
    assert reopened.get_selection(decision.decision_id) == decision
    assert _unique_index_columns(db_path, "uq_research_selection_protocol") == ("protocol_id",)
    with sqlite3.connect(db_path) as connection:
        assert connection.execute(
            "SELECT schema_version FROM schema_metadata WHERE schema_key = ?",
            (ResearchProtocolRepository._SCHEMA_KEY,),
        ).fetchone()[0] == 3

    ResearchProtocolRepository(db_path)
    assert reopened.get_selection(decision.decision_id) == decision


def test_legacy_duplicate_selection_migration_rolls_back_without_deleting_or_choosing_winner(
    tmp_path,
) -> None:
    db_path = tmp_path / "research.db"
    repository = ResearchProtocolRepository(db_path)
    version = _version()
    candidate_set = _advance_to_is(repository, version)
    decision_a = _selection()
    repository.create_selection(
        decision_a, candidate_set=candidate_set, runs=(_is_run(version),), version=version
    )
    decision_b = replace(decision_a, decision_id="selection-2")
    sentinel = "SECRET_SENTINEL_8E0D"
    payload_b = {**decision_b.to_dict(), "data_provenance": {"unexpected": sentinel}}

    with sqlite3.connect(db_path) as connection:
        connection.execute("DROP INDEX uq_research_selection_protocol")
        connection.execute("DROP INDEX uq_research_oos_protocol")
        connection.execute(
            "INSERT INTO research_selection_decisions VALUES (?, ?, ?, ?, ?)",
            (
                decision_b.decision_id,
                decision_b.protocol_id,
                decision_b.candidate_set_id,
                decision_b.created_at.isoformat(),
                json.dumps(payload_b, sort_keys=True),
            ),
        )
        connection.execute(
            "UPDATE schema_metadata SET schema_version = 1 WHERE schema_key = ?",
            (ResearchProtocolRepository._SCHEMA_KEY,),
        )

    with pytest.raises(ResearchPersistenceError) as error:
        ResearchProtocolRepository(db_path)
    assert error.value.code == "MIGRATION_INTEGRITY_CONFLICT"
    assert sentinel not in str(error.value)
    assert sentinel not in str(error.value.details)

    with sqlite3.connect(db_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM research_selection_decisions WHERE protocol_id = ?",
            (decision_a.protocol_id,),
        ).fetchone()[0] == 2
        assert connection.execute(
            "SELECT schema_version FROM schema_metadata WHERE schema_key = ?",
            (ResearchProtocolRepository._SCHEMA_KEY,),
        ).fetchone()[0] == 1
        index_names = {
            row[1]
            for row in connection.execute(
                "PRAGMA index_list('research_selection_decisions')"
            ).fetchall()
        }
        oos_index_names = {
            row[1]
            for row in connection.execute(
                "PRAGMA index_list('research_oos_evaluations')"
            ).fetchall()
        }
    assert "uq_research_selection_protocol" not in index_names
    assert "uq_research_oos_protocol" not in oos_index_names
