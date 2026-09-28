from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from backend.app.strategy_repository import StrategyPersistenceError, StrategyRepository
from strategies import NoMatchBehavior, StrategyDefinition, validate_strategy
from tests.unit.test_strategy_repository import _definition


def test_strategy_draft_and_version_survive_repository_restart(tmp_path) -> None:
    db_path = tmp_path / "strategy.db"
    repository = StrategyRepository(db_path)
    repository.create_strategy("durable", "Durable Strategy", "Restart fixture")
    draft = repository.save_draft(
        strategy_id="durable",
        expected_revision=0,
        active_mode="rule_based",
        rule_based_draft={"assets": ["QQQ"], "editor_state": {}},
        regime_draft={"assets": [], "editor_state": {}},
        active_strategy_payload=None,
        source_metadata={},
        name="Durable Strategy",
        description="Restart fixture",
    )
    version = StrategyRepository(db_path).create(_definition("durable"))

    reopened = StrategyRepository(db_path)
    assert reopened.get_strategy("durable").lifecycle_status == "ACTIVE"
    assert reopened.get_draft("durable").draft_hash == draft.draft_hash
    restored = reopened.get("durable", version.version_id)
    assert restored.content_hash == version.content_hash
    assert restored.configuration.to_json() == version.configuration.to_json()


@pytest.mark.parametrize("inactive", [{"allocations": []}, {"allocations": "malformed"}])
def test_hold_previous_malformed_inactive_fallback_reloads_as_valid_version(
    tmp_path, inactive
) -> None:
    db_path = tmp_path / "strategy.db"
    payload = {
        "strategy_id": "hold-reload",
        "name": "Hold Reload",
        "description": "Inactive fallback reload fixture",
        "assets": [{"symbol": "QQQ"}],
        "price_field": "adjusted_close",
        "rules": [],
        "fallback": inactive,
        "rebalance_policy": {"frequency": "daily", "threshold": None},
        "no_match_behavior": NoMatchBehavior.HOLD_PREVIOUS_ALLOCATION.value,
        "initial_allocation": {"allocations": [{"symbol": "QQQ", "target_weight": 1.0}]},
    }
    definition = StrategyDefinition.from_dict(payload)
    assert definition.fallback is None
    assert validate_strategy(definition).is_valid

    repository = StrategyRepository(db_path)
    created = repository.create(definition)
    draft = repository.save_draft(
        strategy_id="hold-reload",
        expected_revision=0,
        active_mode="rule_based",
        rule_based_draft={"assets": ["QQQ"], "editor_state": {"fallback": inactive}},
        regime_draft={"assets": [], "editor_state": {}},
        active_strategy_payload=payload,
        source_metadata={},
        name="Hold Reload",
        description="Inactive fallback reload fixture",
    )
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE schema_metadata SET schema_version = 3 WHERE schema_key = 'strategy_repository'"
        )
    reopened = StrategyRepository(db_path)
    restored = reopened.get("hold-reload", created.version_id)

    assert restored is not None
    assert restored.configuration.fallback is None
    assert validate_strategy(restored.configuration).is_valid
    assert restored.content_hash == created.content_hash
    assert restored.configuration.to_dict()["fallback"] == inactive
    assert reopened.get_draft("hold-reload").draft_hash == draft.draft_hash
    assert validate_strategy(reopened.get_draft("hold-reload").active_strategy_payload).is_valid
    assert len(list((tmp_path / "backups").glob("strategy-before-schema-v4-*.db"))) == 1


def test_trash_restore_and_protection_preserve_identity(tmp_path) -> None:
    repository = StrategyRepository(tmp_path / "strategy.db")
    version = repository.create(_definition("lifecycle"))

    assert repository.trash("lifecycle") == "trashed"
    trashed = repository.get_strategy("lifecycle")
    assert trashed.lifecycle_status == "TRASHED"
    assert repository.list_strategies() == ()
    assert repository.list_strategies("TRASHED")[0].strategy_id == "lifecycle"
    assert repository.set_protected("lifecycle", True) == "updated"
    assert repository.trash("lifecycle") == "protected"
    assert repository.restore("lifecycle") == "restored"
    assert repository.get("lifecycle", version.version_id).content_hash == version.content_hash


def test_permanent_delete_allows_only_unreferenced_draft_only_strategy(tmp_path) -> None:
    repository = StrategyRepository(tmp_path / "strategy.db")
    repository.create_strategy("draft-only", "Draft Only", "No version")
    assert repository.trash("draft-only") == "trashed"
    assert repository.permanently_delete("draft-only") == "deleted"
    assert repository.get_strategy("draft-only") is None


def test_permanent_delete_blocks_backtest_dependency(tmp_path) -> None:
    db_path = tmp_path / "strategy.db"
    repository = StrategyRepository(db_path)
    version = repository.create(_definition("referenced"))
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE backtest_runs (
                backtest_run_id TEXT PRIMARY KEY,
                strategy_id TEXT NOT NULL,
                strategy_version_id TEXT NOT NULL
            )
            """
        )
        connection.execute(
            "INSERT INTO backtest_runs VALUES (?, ?, ?)",
            ("run-1", "referenced", version.version_id),
        )

    repository.trash("referenced")
    dependencies = repository.permanently_delete("referenced")
    assert isinstance(dependencies, tuple)
    assert dependencies[0].kind == "backtest_runs"
    assert repository.get("referenced", version.version_id) is not None


def test_destructive_clear_is_blocked_for_canonical_database(tmp_path) -> None:
    repository = StrategyRepository(tmp_path / "strategy.db")
    repository.db_path = Path(__file__).resolve().parents[2] / "data" / "strategy.db"

    with pytest.raises(StrategyPersistenceError) as error:
        repository.clear()

    assert error.value.code == StrategyRepository.DESTRUCTIVE_OPERATION_BLOCKED


def test_old_schema_migration_preserves_records_and_creates_backup(tmp_path) -> None:
    db_path = tmp_path / "strategy.db"
    repository = StrategyRepository(db_path)
    version = repository.create(_definition("migration"))
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE schema_metadata SET schema_version = 3 WHERE schema_key = ?",
            ("strategy_repository",),
        )

    reopened = StrategyRepository(db_path)
    assert reopened.get("migration", version.version_id).content_hash == version.content_hash
    backups = list((tmp_path / "backups").glob("strategy-before-schema-v4-*.db"))
    assert len(backups) == 1
