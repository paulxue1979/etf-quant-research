from __future__ import annotations

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from backend.app.strategy_repository import StrategyPersistenceError, StrategyRepository
from strategies import StrategyDefinition


def _definition(strategy_id: str = "qqq-tqqq") -> StrategyDefinition:
    return StrategyDefinition.from_dict(
        {
            "strategy_id": strategy_id,
            "name": "Momentum allocation",
            "description": "Repository test strategy",
            "assets": [{"symbol": "QQQ"}, {"symbol": "TQQQ"}],
            "price_field": "adjusted_close",
            "rules": [],
            "fallback": {
                "name": "all QQQ",
                "allocations": [{"symbol": "QQQ", "target_weight": 1}],
            },
            "rebalance_policy": {"frequency": "weekly", "threshold": 0.05},
        }
    )


def test_persistence_round_trip_and_cross_instance_read(tmp_path) -> None:
    repository_a = StrategyRepository(tmp_path / "strategy.db")
    created = repository_a.create(_definition())

    repository_b = StrategyRepository(tmp_path / "strategy.db")
    restored = repository_b.get(created.strategy_id, created.version_id)

    assert restored == created
    assert restored is not created
    assert repository_b.list(created.strategy_id) == (created,)


def test_versions_are_immutable_and_duplicate_configuration_is_allowed(tmp_path) -> None:
    repository = StrategyRepository(tmp_path / "strategy.db")
    first = repository.create(_definition())
    second = repository.create(_definition())

    assert first.version_number == 1
    assert second.version_number == 2
    assert first.version_id != second.version_id
    assert first.content_hash == second.content_hash
    assert repository.get(first.strategy_id, first.version_id) == first

    changed = replace(_definition(), name="Changed name")
    third = repository.create(changed)
    assert third.version_number == 3
    assert repository.get(first.strategy_id, first.version_id).configuration.name == (
        "Momentum allocation"
    )


def test_version_numbers_are_isolated_per_strategy(tmp_path) -> None:
    repository = StrategyRepository(tmp_path / "strategy.db")

    first_a = repository.create(_definition("strategy-a"))
    second_a = repository.create(_definition("strategy-a"))
    first_b = repository.create(_definition("strategy-b"))

    assert [version.version_number for version in repository.list("strategy-a")] == [1, 2]
    assert first_a.version_number == 1
    assert second_a.version_number == 2
    assert first_b.version_number == 1
    assert repository.list("strategy-a") != repository.list("strategy-b")


def test_concurrent_version_creation_is_atomic(tmp_path) -> None:
    db_path = tmp_path / "strategy.db"
    repositories = [StrategyRepository(db_path) for _ in range(8)]

    with ThreadPoolExecutor(max_workers=8) as executor:
        versions = list(
            executor.map(
                lambda repository: repository.create(_definition("concurrent")), repositories
            )
        )

    assert sorted(version.version_number for version in versions) == list(range(1, 9))
    assert len({version.version_id for version in versions}) == 8
    assert [version.version_number for version in repositories[0].list("concurrent")] == list(
        range(1, 9)
    )


def test_content_hash_is_deterministic_for_different_input_key_order(tmp_path) -> None:
    repository = StrategyRepository(tmp_path / "strategy.db")
    first = repository.create(_definition())

    reordered = StrategyDefinition.from_dict(
        json.loads(
            json.dumps(
                {
                    "rebalance_policy": {"threshold": 0.05, "frequency": "weekly"},
                    "fallback": {
                        "allocations": [{"target_weight": 1, "symbol": "QQQ"}],
                        "name": "all QQQ",
                    },
                    "rules": [],
                    "price_field": "adjusted_close",
                    "assets": [{"symbol": "QQQ"}, {"symbol": "TQQQ"}],
                    "description": "Repository test strategy",
                    "name": "Momentum allocation",
                    "strategy_id": "qqq-tqqq",
                }
            )
        )
    )
    second = repository.create(reordered)

    assert first.content_hash == second.content_hash


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("configuration_json", "not-json"),
        ("configuration_json", json.dumps({"strategy_id": "qqq-tqqq"})),
        ("content_hash", "invalid-hash"),
    ],
)
def test_corrupted_persisted_data_fails_explicitly(tmp_path, column: str, value: str) -> None:
    db_path = tmp_path / "strategy.db"
    repository = StrategyRepository(db_path)
    version = repository.create(_definition())

    with sqlite3.connect(db_path) as connection:
        connection.execute(
            f"UPDATE strategy_versions SET {column} = ? WHERE version_id = ?",
            (value, version.version_id),
        )

    with pytest.raises(StrategyPersistenceError, match="integrity checks"):
        repository.get(version.strategy_id, version.version_id)


def test_invalid_definition_is_not_persisted(tmp_path) -> None:
    repository = StrategyRepository(tmp_path / "strategy.db")

    with pytest.raises(StrategyPersistenceError, match="strategy definition is invalid"):
        repository.create(object())  # type: ignore[arg-type]

    assert repository.list("not-persisted") == ()
