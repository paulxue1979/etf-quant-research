"""Proposal tests use temporary strategy and ChangeSet databases only."""

from __future__ import annotations

import asyncio
import copy
from pathlib import Path

import pytest
from mcp import Client
from pydantic import ValidationError

from backend.app.operator_change_set_repository import OperatorChangeSetRepository
from backend.app.operator_change_set_service import (
    ChangeSetPreparationError,
    DraftChangeOperation,
    OperationKind,
    OperatorChangeSetService,
    _draft_envelope,
)
from backend.app.operator_read_service import OperatorReadService
from backend.app.strategy_repository import StrategyRepository
from backend.operator_mcp import create_server
from data.cache import DiskCache
from tests.unit.test_peak_drawdown import _entry_version
from tests.unit.test_value_zones import _strategy


def _seed(tmp_path: Path, *, peak: bool = True, reordered: bool = False, threshold: bool = False):
    db = tmp_path / "strategy.db"
    store = StrategyRepository(db)
    definition = _entry_version().configuration if peak else _strategy()
    payload = definition.to_dict()
    if reordered:
        payload["transitions"].reverse()
        payload["regimes"].reverse()
    if threshold:
        transition = next(
            item for item in payload["transitions"] if item["transition_id"] == "full-recovery"
        )
        transition["condition"]["threshold"] = {"type": "relative", "value": 0.1}
    store.create_strategy(definition.strategy_id, definition.name, definition.description)
    draft = store.save_draft(
        strategy_id=definition.strategy_id,
        expected_revision=0,
        active_mode="regime_state_machine",
        rule_based_draft={},
        regime_draft={},
        active_strategy_payload=payload,
        source_metadata={},
        name=definition.name,
        description=definition.description,
    )
    read = OperatorReadService(db_path=db, cache=DiskCache(tmp_path / "cache"))
    proposals = OperatorChangeSetRepository(tmp_path / "proposals.db")
    service = OperatorChangeSetService(read, proposals)
    return store, draft, read, proposals, service, db


def _op(kind: OperationKind, value, **selectors) -> DraftChangeOperation:
    return DraftChangeOperation(operation_type=kind, value=value, **selectors)


def test_two_peak_changes_persist_exact_diff_and_leave_draft_unchanged(tmp_path: Path) -> None:
    store, draft, read, proposals, service, db = _seed(tmp_path)
    before = db.read_bytes()
    result = service.prepare(
        draft.strategy_id,
        1,
        [
            _op(OperationKind.SET_PEAK_DRAWDOWN_THRESHOLD, -0.25, transition_id="idle-to-value"),
            _op(OperationKind.SET_PEAK_DRAWDOWN_THRESHOLD, -0.35, transition_id="value-to-deep"),
        ],
    )
    assert result["status"] == "PROPOSED"
    assert result["validation_status"] == "PASS"
    assert result["source"] == "CODEX_MCP"
    assert len(result["operations"]) == 2
    assert [
        (item["before"], item["after"]) for item in result["human_readable_diff"]["changes"]
    ] == [("-20%", "-25%"), ("-30%", "-35%")]
    assert result["before_hash"] == draft.draft_hash
    assert result["proposed_after_hash"] != draft.draft_hash
    assert "Exit logic" in result["human_readable_diff"]["unchanged"]
    assert db.read_bytes() == before
    assert store.get_draft(draft.strategy_id).draft_revision == 1
    assert store.list_version_records(draft.strategy_id) == ()
    assert (
        OperatorChangeSetService(read, OperatorChangeSetRepository(proposals.path)).get_change_set(
            result["change_set_id"]
        )["status"]
        == "PROPOSED"
    )


def test_semantic_ids_resolve_stored_draft_order_not_canonical_indices(tmp_path: Path) -> None:
    store, draft, _, _, service, db = _seed(tmp_path, reordered=True)
    before = db.read_bytes()
    proposal = service.prepare(
        draft.strategy_id,
        1,
        [
            _op(OperationKind.SET_PEAK_DRAWDOWN_THRESHOLD, -0.25, transition_id="idle-to-value"),
            _op(OperationKind.SET_PEAK_DRAWDOWN_THRESHOLD, -0.35, transition_id="value-to-deep"),
        ],
    )
    assert proposal["validation_status"] == "PASS"
    assert [
        (row["before"], row["after"]) for row in proposal["human_readable_diff"]["changes"]
    ] == [("-20%", "-25%"), ("-30%", "-35%")]
    expected = copy.deepcopy(draft.active_strategy_payload)
    for transition in expected["transitions"]:
        if transition["transition_id"] == "idle-to-value":
            transition["condition"]["children"][1]["right"]["value"] = -0.25
        if transition["transition_id"] == "value-to-deep":
            transition["condition"]["children"][1]["right"]["value"] = -0.35
    assert proposal["proposed_after_hash"] == StrategyRepository._hash_json(
        _draft_envelope(draft, expected)
    )
    assert db.read_bytes() == before
    assert store.get_draft(draft.strategy_id).draft_hash == draft.draft_hash


def test_one_period_allocation_timeframe_and_initial_allocation(tmp_path: Path) -> None:
    _, draft, _, _, service, db = _seed(tmp_path, peak=False)
    before = db.read_bytes()
    proposals = [
        _op(OperationKind.SET_INDICATOR_PERIOD, 100, transition_id="full-recovery"),
        _op(OperationKind.SET_STATE_ALLOCATION, 0.8, state_id="full_risk", symbol="TQQQ"),
        _op(OperationKind.SET_TIMEFRAME, "weekly", transition_id="full-recovery"),
        _op(OperationKind.SET_INITIAL_ALLOCATION, {"QQQ": 1.0}),
    ]
    for operation in proposals:
        result = service.prepare(draft.strategy_id, 1, [operation])
        assert result["status"] == "PROPOSED"
        assert len(result["human_readable_diff"]["changes"]) == 1
    assert db.read_bytes() == before


def test_relative_transition_threshold_is_typed_and_semantic(tmp_path: Path) -> None:
    _, draft, _, _, service, db = _seed(tmp_path, peak=False, threshold=True)
    before = db.read_bytes()
    result = service.prepare(
        draft.strategy_id,
        1,
        [_op(OperationKind.SET_TRANSITION_THRESHOLD, 0.15, transition_id="full-recovery")],
    )
    assert result["validation_status"] == "PASS"
    assert result["human_readable_diff"]["changes"][0]["before"] == "10%"
    assert result["human_readable_diff"]["changes"][0]["after"] == "15%"
    assert db.read_bytes() == before


def test_invalid_targets_exit_peak_and_raw_paths_never_persist(tmp_path: Path) -> None:
    _, draft, _, proposals, service, _ = _seed(tmp_path)
    with pytest.raises(ChangeSetPreparationError, match="SEMANTIC_TARGET_NOT_UNIQUE"):
        service.prepare(
            draft.strategy_id,
            1,
            [_op(OperationKind.SET_INDICATOR_PERIOD, 40, transition_id="missing")],
        )
    with pytest.raises(ChangeSetPreparationError, match="PEAK_DRAWDOWN_ENTRY_ONLY"):
        service.prepare(
            draft.strategy_id,
            1,
            [
                _op(
                    OperationKind.SET_PEAK_DRAWDOWN_THRESHOLD,
                    -0.4,
                    transition_id="deep-to-recovery",
                )
            ],
        )
    with pytest.raises(ValidationError):
        DraftChangeOperation.model_validate(
            {
                "operation_type": "SET_PEAK_DRAWDOWN_THRESHOLD",
                "value": -0.2,
                "target_path": "transitions[3].conditions[0]",
            }
        )
    assert not proposals.path.exists()


def test_no_op_is_rejected_but_small_real_change_is_preserved(tmp_path: Path) -> None:
    _, draft, _, proposals, service, _ = _seed(tmp_path)
    with pytest.raises(ChangeSetPreparationError, match="NO_CHANGE"):
        service.prepare(
            draft.strategy_id,
            1,
            [_op(OperationKind.SET_PEAK_DRAWDOWN_THRESHOLD, -0.2, transition_id="idle-to-value")],
        )
    assert not proposals.path.exists()
    result = service.prepare(
        draft.strategy_id,
        1,
        [
            _op(
                OperationKind.SET_PEAK_DRAWDOWN_THRESHOLD,
                -0.200000001,
                transition_id="idle-to-value",
            )
        ],
    )
    assert result["status"] == "PROPOSED"
    assert result["human_readable_diff"]["changes"][0]["after"] != "-20%"


def test_validation_failure_is_proposed_and_stale_is_read_only(tmp_path: Path) -> None:
    store, draft, read, proposals, service, db = _seed(tmp_path)
    before = db.read_bytes()
    result = service.prepare(
        draft.strategy_id,
        1,
        [_op(OperationKind.SET_INITIAL_ALLOCATION, {"SPY": 1.0, "UPRO": 1.0})],
    )
    assert result["validation_status"] == "FAIL"
    assert result["validation_issues"]
    assert result["status"] == "PROPOSED"
    assert db.read_bytes() == before
    store.save_draft(
        strategy_id=draft.strategy_id,
        expected_revision=1,
        active_mode=draft.active_mode,
        rule_based_draft=draft.rule_based_draft,
        regime_draft=draft.regime_draft,
        active_strategy_payload=draft.active_strategy_payload,
        source_metadata=draft.source_metadata,
        name="updated",
        description="",
    )
    current = db.read_bytes()
    assert service.get_change_set(result["change_set_id"])["status"] == "STALE"
    assert db.read_bytes() == current
    assert (
        OperatorChangeSetService(read, OperatorChangeSetRepository(proposals.path)).get_change_set(
            result["change_set_id"]
        )["status"]
        == "STALE"
    )


def test_revision_conflict_and_backend_only_reject(tmp_path: Path) -> None:
    _, draft, _, proposals, service, _ = _seed(tmp_path)
    operation = _op(OperationKind.SET_PEAK_DRAWDOWN_THRESHOLD, -0.25, transition_id="idle-to-value")
    with pytest.raises(ChangeSetPreparationError, match="DRAFT_REVISION_CONFLICT"):
        service.prepare(draft.strategy_id, 2, [operation])
    assert not proposals.path.exists()
    result = service.prepare(draft.strategy_id, 1, [operation])
    assert proposals.reject(result["change_set_id"])
    assert service.get_change_set(result["change_set_id"])["status"] == "REJECTED"


def test_mcp_prepare_and_read_contract(tmp_path: Path) -> None:
    _, draft, read, proposals, service, db = _seed(tmp_path)
    before = db.read_bytes()

    async def exercise() -> None:
        async with Client(create_server(read, service)) as client:
            catalog = await client.list_tools()
            tools = {item.name: item for item in catalog.tools}
            assert "quant_prepare_draft_changes" in tools
            assert "quant_get_change_set" in tools
            assert not tools["quant_prepare_draft_changes"].annotations.read_only_hint
            assert tools["quant_get_change_set"].annotations.read_only_hint
            assert not any("apply" in name or "approve" in name or "save" in name for name in tools)
            prepared = await client.call_tool(
                "quant_prepare_draft_changes",
                {
                    "strategy_id": draft.strategy_id,
                    "expected_draft_revision": 1,
                    "operations": [
                        {
                            "operation_type": "SET_PEAK_DRAWDOWN_THRESHOLD",
                            "transition_id": "idle-to-value",
                            "value": -0.25,
                        }
                    ],
                },
            )
            assert not prepared.is_error, prepared.content
            change_set_id = prepared.structured_content["change_set_id"]
            loaded = await client.call_tool(
                "quant_get_change_set", {"change_set_id": change_set_id}
            )
            assert (
                loaded.structured_content["operations"] == prepared.structured_content["operations"]
            )
            invalid = await client.call_tool(
                "quant_prepare_draft_changes",
                {
                    "strategy_id": draft.strategy_id,
                    "expected_draft_revision": 1,
                    "operations": [
                        {
                            "operation_type": "SET_PEAK_DRAWDOWN_THRESHOLD",
                            "target_path": "transitions[0]",
                            "value": -0.25,
                        }
                    ],
                },
            )
            assert invalid.is_error
            rejected = await client.call_tool(
                "quant_prepare_draft_changes",
                {
                    "strategy_id": draft.strategy_id,
                    "expected_draft_revision": 1,
                    "operations": [
                        {
                            "operation_type": "SET_PEAK_DRAWDOWN_THRESHOLD",
                            "transition_id": "deep-to-recovery",
                            "value": -0.25,
                        }
                    ],
                },
            )
            assert not rejected.is_error
            assert rejected.structured_content == {
                "status": "REJECTED",
                "reason": "PEAK_DRAWDOWN_ENTRY_ONLY",
                "change_set_id": None,
            }

    asyncio.run(exercise())
    assert proposals.path.exists()
    assert db.read_bytes() == before
