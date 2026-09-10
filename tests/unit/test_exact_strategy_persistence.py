from __future__ import annotations

import json
import sqlite3
from dataclasses import replace

import pytest

from backend.app.strategy_repository import StrategyPersistenceError, StrategyRepository
from research import (
    ParameterDefinition,
    ParameterSet,
    ParameterType,
    derived_strategy_version_hash,
    derived_strategy_version_id,
    materialize_strategy_version,
)
from strategies import StrategyVersion
from tests.unit.test_materialization import _period_binding, _space, _version


def _derived(base: StrategyVersion | None = None) -> StrategyVersion:
    return materialize_strategy_version(
        base or _version(),
        ParameterSet(
            {"period": 20},
            _space(ParameterDefinition("period", ParameterType.INTEGER, min=1, max=200, step=1)),
        ),
        (_period_binding(),),
    )


def test_exact_derived_version_persists_and_reloads_across_instances(tmp_path) -> None:
    db_path = tmp_path / "strategy.db"
    repository = StrategyRepository(db_path)
    base = repository.create(_version().configuration)
    derived = _derived(base)

    persisted = repository.persist_exact_strategy_version(derived)
    restored = StrategyRepository(db_path).get_any_version(derived.version_id)

    assert persisted == derived
    assert restored == derived
    assert restored is not derived
    assert restored.materialization_provenance == derived.materialization_provenance


def test_exact_persistence_keeps_configuration_hash_and_all_provenance(tmp_path) -> None:
    repository = StrategyRepository(tmp_path / "strategy.db")
    base = repository.create(_version().configuration)
    derived = _derived(base)

    repository.persist_exact_strategy_version(derived)
    restored = repository.get_any_version(derived.version_id)
    assert restored is not None
    assert restored.configuration.to_json() == derived.configuration.to_json()
    assert restored.content_hash == derived.content_hash
    assert restored.materialization_provenance == derived.materialization_provenance
    assert restored.materialization_provenance is not None
    assert restored.materialization_provenance.base_strategy_version_id == (
        derived.materialization_provenance.base_strategy_version_id
    )
    assert restored.materialization_provenance.base_strategy_version_hash == (
        derived.materialization_provenance.base_strategy_version_hash
    )
    assert restored.materialization_provenance.parameter_set_hash == (
        derived.materialization_provenance.parameter_set_hash
    )
    assert restored.materialization_provenance.binding_hash == (
        derived.materialization_provenance.binding_hash
    )
    assert restored.materialization_provenance.materialization_spec_hash == (
        derived.materialization_provenance.materialization_spec_hash
    )
    assert restored.materialization_provenance.derived_strategy_version_hash == (
        derived.materialization_provenance.derived_strategy_version_hash
    )


def test_exact_duplicate_write_is_idempotent(tmp_path) -> None:
    repository = StrategyRepository(tmp_path / "strategy.db")
    base = repository.create(_version().configuration)
    derived = _derived(base)

    assert repository.persist_exact_strategy_version(derived) == derived
    assert repository.persist_exact_strategy_version(derived) == derived

    with sqlite3.connect(repository.db_path) as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM materialized_strategy_versions WHERE version_id = ?",
            (derived.version_id,),
        ).fetchone()[0]
    assert count == 1


def test_exact_persistence_requires_the_base_version_to_exist(tmp_path) -> None:
    repository = StrategyRepository(tmp_path / "strategy.db")

    with pytest.raises(StrategyPersistenceError) as error:
        repository.persist_exact_strategy_version(_derived())

    assert error.value.code == "BASE_STRATEGY_VERSION_INTEGRITY_ERROR"


def test_exact_persistence_requires_the_base_hash_to_match_provenance(tmp_path) -> None:
    repository = StrategyRepository(tmp_path / "strategy.db")
    base = repository.create(_version().configuration)
    derived = _derived(base)
    assert base.version_id == derived.materialization_provenance.base_strategy_version_id
    provenance = derived.materialization_provenance
    assert provenance is not None
    wrong_base_hash = "f" * 64
    wrong_derived_hash = derived_strategy_version_hash(
        derived.configuration,
        base_strategy_version_id=provenance.base_strategy_version_id,
        base_strategy_version_hash=wrong_base_hash,
        parameter_set_hash=provenance.parameter_set_hash,
        binding_hash=provenance.binding_hash,
        materialization_spec_hash=provenance.materialization_spec_hash,
    )
    tampered = replace(
        derived,
        version_id=derived_strategy_version_id(
            provenance.base_strategy_version_id, wrong_derived_hash
        ),
        materialization_provenance=replace(
            provenance,
            base_strategy_version_hash=wrong_base_hash,
            derived_strategy_version_hash=wrong_derived_hash,
        ),
    )

    with pytest.raises(StrategyPersistenceError) as error:
        repository.persist_exact_strategy_version(tampered)

    assert error.value.code == "BASE_STRATEGY_VERSION_INTEGRITY_ERROR"


@pytest.mark.parametrize(
    ("attribute", "replacement"),
    [
        ("content_hash", "f" * 64),
        ("version_id", "unrelated-derived-version-id"),
    ],
)
def test_same_derived_identity_with_tampered_content_or_id_is_rejected(
    tmp_path, attribute: str, replacement: str
) -> None:
    repository = StrategyRepository(tmp_path / "strategy.db")
    base = repository.create(_version().configuration)
    derived = _derived(base)
    repository.persist_exact_strategy_version(derived)

    tampered = _derived(base)
    object.__setattr__(tampered, attribute, replacement)

    with pytest.raises(StrategyPersistenceError) as error:
        repository.persist_exact_strategy_version(tampered)

    assert error.value.code == "STRATEGY_VERSION_INTEGRITY_ERROR"


def test_same_derived_identity_with_changed_provenance_is_rejected(tmp_path) -> None:
    repository = StrategyRepository(tmp_path / "strategy.db")
    base = repository.create(_version().configuration)
    derived = _derived(base)
    repository.persist_exact_strategy_version(derived)

    tampered = _derived(base)
    provenance = tampered.materialization_provenance
    assert provenance is not None
    object.__setattr__(
        tampered,
        "materialization_provenance",
        replace(provenance, parameter_set_hash="a" * 64),
    )

    with pytest.raises(StrategyPersistenceError) as error:
        repository.persist_exact_strategy_version(tampered)

    assert error.value.code == "STRATEGY_VERSION_INTEGRITY_ERROR"


def test_ordinary_versions_are_not_polluted_by_derived_versions(tmp_path) -> None:
    repository = StrategyRepository(tmp_path / "strategy.db")
    ordinary = repository.create(_version().configuration)
    derived = _derived(ordinary)
    repository.persist_exact_strategy_version(derived)

    assert repository.list(ordinary.strategy_id) == (ordinary,)
    assert repository.catalog() == (
        {
            "strategy_id": ordinary.strategy_id,
            "name": ordinary.configuration.name,
            "version_count": 1,
            "latest_version": 1,
        },
    )
    assert repository.get(ordinary.strategy_id, derived.version_id) == derived


def test_ordinary_and_derived_identity_collision_is_rejected(tmp_path) -> None:
    repository = StrategyRepository(tmp_path / "strategy.db")
    base = repository.create(_version().configuration)
    derived = _derived(base)
    repository.persist_exact_strategy_version(derived)

    with sqlite3.connect(repository.db_path) as connection:
        connection.execute(
            "INSERT INTO strategy_versions("
            "strategy_id, version_id, version_number, created_at, configuration_json, "
            "content_hash, status"
            ") VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                base.strategy_id,
                derived.version_id,
                999,
                base.created_at.isoformat(),
                base.configuration.to_json(),
                base.content_hash,
                base.status.value,
            ),
        )

    with pytest.raises(StrategyPersistenceError) as error:
        repository.get_any_version(derived.version_id)
    assert error.value.code == "STRATEGY_VERSION_IDENTITY_COLLISION"


def test_repository_startup_rejects_cross_table_identity_corruption(tmp_path) -> None:
    db_path = tmp_path / "strategy.db"
    repository = StrategyRepository(db_path)
    base = repository.create(_version().configuration)
    derived = _derived(base)
    repository.persist_exact_strategy_version(derived)

    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "INSERT INTO strategy_versions("
            "strategy_id, version_id, version_number, created_at, configuration_json, "
            "content_hash, status"
            ") VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                base.strategy_id,
                derived.version_id,
                999,
                base.created_at.isoformat(),
                base.configuration.to_json(),
                base.content_hash,
                base.status.value,
            ),
        )

    with pytest.raises(StrategyPersistenceError) as error:
        StrategyRepository(db_path)

    assert error.value.code == "STRATEGY_VERSION_IDENTITY_COLLISION"


def test_materialized_row_corruption_is_rejected(tmp_path) -> None:
    repository = StrategyRepository(tmp_path / "strategy.db")
    base = repository.create(_version().configuration)
    derived = _derived(base)
    repository.persist_exact_strategy_version(derived)

    with sqlite3.connect(repository.db_path) as connection:
        connection.execute(
            "UPDATE materialized_strategy_versions SET content_hash = ? WHERE version_id = ?",
            ("0" * 64, derived.version_id),
        )

    with pytest.raises(StrategyPersistenceError, match="integrity"):
        repository.get_any_version(derived.version_id)


def test_materialized_nonfinite_json_is_rejected(tmp_path) -> None:
    repository = StrategyRepository(tmp_path / "strategy.db")
    base = repository.create(_version().configuration)
    derived = _derived(base)
    repository.persist_exact_strategy_version(derived)

    with sqlite3.connect(repository.db_path) as connection:
        version_json = connection.execute(
            "SELECT version_json FROM materialized_strategy_versions WHERE version_id = ?",
            (derived.version_id,),
        ).fetchone()[0]
        assert "1.0" in version_json
        connection.execute(
            "UPDATE materialized_strategy_versions SET version_json = ? WHERE version_id = ?",
            (version_json.replace("1.0", "NaN", 1), derived.version_id),
        )

    with pytest.raises(StrategyPersistenceError, match="integrity"):
        repository.get_any_version(derived.version_id)


def test_v1_schema_is_upgraded_without_losing_ordinary_versions(tmp_path) -> None:
    db_path = tmp_path / "strategy.db"
    ordinary = _version()
    with sqlite3.connect(db_path) as connection:
        connection.executescript(
            """
            CREATE TABLE schema_metadata (
                schema_key TEXT PRIMARY KEY,
                schema_version INTEGER NOT NULL
            );
            CREATE TABLE strategies (
                strategy_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT NOT NULL,
                created_at TEXT NOT NULL,
                latest_version_number INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE strategy_versions (
                strategy_id TEXT NOT NULL,
                version_id TEXT NOT NULL,
                version_number INTEGER NOT NULL CHECK (version_number > 0),
                created_at TEXT NOT NULL,
                configuration_json TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                status TEXT NOT NULL,
                PRIMARY KEY (strategy_id, version_id),
                UNIQUE (strategy_id, version_number),
                FOREIGN KEY (strategy_id) REFERENCES strategies(strategy_id)
            );
            INSERT INTO schema_metadata(schema_key, schema_version)
            VALUES ('strategy_repository', 1);
            """
        )
        connection.execute(
            """
            INSERT INTO strategies(
                strategy_id, name, description, created_at, latest_version_number
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                ordinary.strategy_id,
                ordinary.configuration.name,
                ordinary.configuration.description,
                ordinary.created_at.isoformat(),
                ordinary.version_number,
            ),
        )
        connection.execute(
            """
            INSERT INTO strategy_versions(
                strategy_id, version_id, version_number, created_at,
                configuration_json, content_hash, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ordinary.strategy_id,
                ordinary.version_id,
                ordinary.version_number,
                ordinary.created_at.isoformat(),
                ordinary.configuration.to_json(),
                ordinary.content_hash,
                ordinary.status.value,
            ),
        )

    repository = StrategyRepository(db_path)
    assert repository.get(ordinary.strategy_id, ordinary.version_id) == ordinary
    with sqlite3.connect(db_path) as connection:
        assert connection.execute(
            "SELECT schema_version FROM schema_metadata WHERE schema_key = ?",
            ("strategy_repository",),
        ).fetchone()[0] == 2
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
            ("materialized_strategy_versions",),
        ).fetchone()[0] == "materialized_strategy_versions"


def test_clear_removes_ordinary_and_derived_versions(tmp_path) -> None:
    repository = StrategyRepository(tmp_path / "strategy.db")
    ordinary = repository.create(_version().configuration)
    derived = _derived(ordinary)
    repository.persist_exact_strategy_version(derived)

    repository.clear()

    assert repository.get(ordinary.strategy_id, ordinary.version_id) is None
    assert repository.get_any_version(derived.version_id) is None


def test_exact_persistence_does_not_store_secret_sentinels(tmp_path) -> None:
    repository = StrategyRepository(tmp_path / "strategy.db")
    base = repository.create(_version().configuration)
    derived = _derived(base)
    repository.persist_exact_strategy_version(derived)

    database_bytes = repository.db_path.read_bytes()
    assert b"TIINGO_API_KEY" not in database_bytes
    assert b"SECRET_SENTINEL_8E0A" not in database_bytes
    assert b"Bearer SECRET_SENTINEL_8E0A" not in database_bytes


def test_derived_strategy_json_round_trip_preserves_exact_identity() -> None:
    derived = _derived()
    restored = StrategyVersion.from_json(json.dumps(derived.to_dict(), sort_keys=True))

    assert restored.version_id == derived.version_id
    assert restored.content_hash == derived.content_hash
    assert restored.materialization_provenance == derived.materialization_provenance
