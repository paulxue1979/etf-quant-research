"""MCP contract tests use only an isolated temporary strategy store."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from mcp import Client

from backend.app.operator_read_service import OperatorReadService
from backend.app.strategy_repository import StrategyRepository
from backend.operator_mcp import create_server
from data.cache import DiskCache
from tests.unit.test_value_zones import _strategy

TOOL_NAMES = {
    "quant_health",
    "quant_list_strategies",
    "quant_get_strategy",
    "quant_get_draft",
    "quant_get_strategy_structure",
    "quant_get_semantic_targets",
    "quant_validate_draft",
    "quant_get_data_availability",
    "quant_prepare_draft_changes",
    "quant_get_change_set",
    "quant_get_strategy_version",
    "quant_prepare_backtest_configuration",
    "quant_get_backtest_configuration",
    "quant_list_pending_backtest_configurations",
    "quant_get_backtest_result",
    "quant_prepare_experiment_configuration",
    "quant_get_experiment_configuration",
    "quant_get_experiment_result",
    "quant_prepare_strategy_version_creation",
    "quant_get_strategy_version_creation_request",
}


def _seed(tmp_path: Path) -> tuple[OperatorReadService, str, str, Path]:
    db = tmp_path / "strategy.db"
    repository = StrategyRepository(db)
    definition = _strategy()
    repository.create_strategy(definition.strategy_id, definition.name, definition.description)
    repository.save_draft(
        strategy_id=definition.strategy_id,
        expected_revision=0,
        active_mode="regime_state_machine",
        rule_based_draft={"inactive_note": "SENTINEL_SECRET_VALUE"},
        regime_draft={"editor_state": {}},
        active_strategy_payload=definition.to_dict(),
        source_metadata={"private_note": "SENTINEL_SECRET_VALUE"},
        name=definition.name,
        description=definition.description,
    )
    version = repository.create_version_from_draft(
        strategy_id=definition.strategy_id, expected_revision=1, definition=definition
    )
    return (
        OperatorReadService(db_path=db, cache=DiskCache(tmp_path / "cache")),
        definition.strategy_id,
        version.version_id,
        db,
    )


def test_operator_mcp_read_only_contract_and_isolation(tmp_path: Path) -> None:
    service, strategy_id, version_id, db = _seed(tmp_path)
    before = db.read_bytes()

    async def exercise() -> None:
        async with Client(create_server(service)) as client:
            catalog = await client.list_tools()
            assert {item.name for item in catalog.tools} == TOOL_NAMES
            assert all(
                item.annotations.read_only_hint
                == (
                    item.name
                    not in {
                        "quant_prepare_draft_changes",
                        "quant_prepare_backtest_configuration",
                        "quant_prepare_experiment_configuration",
                        "quant_prepare_strategy_version_creation",
                    }
                )
                for item in catalog.tools
            )
            assert all(not item.annotations.destructive_hint for item in catalog.tools)

            async def call(name: str, arguments: dict | None = None) -> dict:
                result = await client.call_tool(name, arguments or {})
                assert not result.is_error, (name, result.content)
                return result.structured_content

            health = await call("quant_health")
            assert health == {
                "status": "READY",
                "backend_version": "1.4.7",
                "bridge_version": "0.2.0",
            }
            listing = await call("quant_list_strategies")
            assert listing["total"] == 1
            assert listing["strategies"][0]["strategy_id"] == strategy_id
            identity = await call("quant_get_strategy", {"strategy_id": strategy_id})
            assert identity["version_count"] == 1
            draft = await call("quant_get_draft", {"strategy_id": strategy_id})
            assert draft["revision"] == 1
            assert draft["mode"] == "regime_state_machine"
            structure = await call("quant_get_strategy_structure", {"strategy_id": strategy_id})
            assert structure["states"] and structure["transitions"]
            assert structure["transitions"][0]["condition"]
            targets = await call(
                "quant_get_semantic_targets",
                {"strategy_id": strategy_id, "version_id": version_id},
            )
            assert targets["targets"]
            assert (await call("quant_validate_draft", {"strategy_id": strategy_id}))[
                "status"
            ] == "PASS"
            availability = await call(
                "quant_get_data_availability",
                {
                    "strategy_id": strategy_id,
                    "version_id": version_id,
                    "requested_start": "2026-01-02",
                    "requested_end": "2026-03-31",
                },
            )
            assert availability["cache_only"] is True
            assert availability["status"] == "BLOCKED"
            assert "QQQ" in availability["cache_miss_symbols"]
            assert availability["cache_miss_is_not_market_unavailability"] is True
            assert availability["assets"]
            for name, args in (
                ("quant_delete_strategy", {"strategy_id": strategy_id}),
                ("quant_get_strategy", {"strategy_id": "../../.ssh/id_rsa"}),
                (
                    "quant_get_data_availability",
                    {
                        "strategy_id": strategy_id,
                        "version_id": version_id,
                        "requested_start": "yesterday",
                        "requested_end": "2026-03-31",
                    },
                ),
            ):
                result = await client.call_tool(name, args)
                assert result.is_error
            all_results = json.dumps(
                [listing, identity, draft, structure, targets, availability], default=str
            ).lower()
            assert "api_key" not in all_results
            assert "strategy.db" not in all_results
            assert "secret" not in all_results
            assert "SENTINEL_SECRET_VALUE" not in all_results

    asyncio.run(exercise())
    assert db.read_bytes() == before


def test_missing_store_does_not_create_database(tmp_path: Path) -> None:
    db = tmp_path / "missing.db"
    service = OperatorReadService(db_path=db, cache=DiskCache(tmp_path / "cache"))
    assert service.health()["status"] == "UNAVAILABLE"
    assert not db.exists()


def test_invalid_active_draft_remains_readable_and_fails_validation(tmp_path: Path) -> None:
    service, strategy_id, _, db = _seed(tmp_path)
    repository = StrategyRepository(db)
    repository.save_draft(
        strategy_id=strategy_id,
        expected_revision=1,
        active_mode="regime_state_machine",
        rule_based_draft={},
        regime_draft={},
        active_strategy_payload={"strategy_id": strategy_id, "fallback": "invalid"},
        source_metadata={},
        name="Incomplete",
        description="",
    )
    before = db.read_bytes()

    async def exercise() -> None:
        async with Client(create_server(service)) as client:
            draft = await client.call_tool("quant_get_draft", {"strategy_id": strategy_id})
            assert not draft.is_error
            assert draft.structured_content["revision"] == 2
            assert draft.structured_content["summary"]["active_payload_parseable"] is False
            validation = await client.call_tool(
                "quant_validate_draft", {"strategy_id": strategy_id}
            )
            assert validation.structured_content["status"] == "FAIL"
            assert validation.structured_content["blocking_issues"]

    asyncio.run(exercise())
    assert db.read_bytes() == before
