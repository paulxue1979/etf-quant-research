"""Human decisions use only isolated temporary strategy/proposal databases."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from mcp import Client

from backend.app.operator_change_set_apply_service import (
    ChangeSetDecisionError,
    OperatorChangeSetApplyService,
)
from backend.app.operator_change_set_repository import (
    ChangeSetStorageError,
    OperatorChangeSetRepository,
)
from backend.app.operator_change_set_service import (
    DraftChangeOperation,
    OperationKind,
    OperatorChangeSetService,
)
from backend.app.operator_read_service import OperatorReadService
from backend.app.strategy_repository import StrategyRepository
from backend.operator_mcp import create_server
from data.cache import DiskCache
from tests.unit.test_peak_drawdown import _entry_version


def _fixture(tmp_path: Path):
    strategy_db = tmp_path / "strategy.db"
    store = StrategyRepository(strategy_db)
    definition = _entry_version().configuration
    payload = definition.to_dict()
    payload["transitions"].reverse()
    store.create_strategy(definition.strategy_id, definition.name, definition.description)
    draft = store.save_draft(
        strategy_id=definition.strategy_id,
        expected_revision=0,
        active_mode="regime_state_machine",
        rule_based_draft={"editor_state": {}},
        regime_draft={"editor_state": {}},
        active_strategy_payload=payload,
        source_metadata={},
        name=definition.name,
        description=definition.description,
    )
    proposals = OperatorChangeSetRepository(tmp_path / "proposals.db")
    prepare = OperatorChangeSetService(
        OperatorReadService(strategy_db, DiskCache(tmp_path / "cache")), proposals
    )
    apply = OperatorChangeSetApplyService(store, proposals)
    return store, draft, proposals, prepare, apply


def _proposal(prepare: OperatorChangeSetService, draft):
    return prepare.prepare(
        draft.strategy_id,
        draft.draft_revision,
        [
            DraftChangeOperation(
                operation_type=OperationKind.SET_PEAK_DRAWDOWN_THRESHOLD,
                transition_id="idle-to-value",
                value=-0.25,
            ),
            DraftChangeOperation(
                operation_type=OperationKind.SET_PEAK_DRAWDOWN_THRESHOLD,
                transition_id="value-to-deep",
                value=-0.35,
            ),
        ],
    )


def test_approve_is_atomic_exact_and_idempotent(tmp_path: Path) -> None:
    store, draft, proposals, prepare, apply = _fixture(tmp_path)
    proposal = _proposal(prepare, draft)
    before_versions = len(store.list_version_records(draft.strategy_id))
    approved = apply.approve(draft.strategy_id, proposal["change_set_id"])
    current = store.get_draft(draft.strategy_id)
    assert approved["result"] == "APPLIED"
    assert current.draft_revision == draft.draft_revision + 1
    assert current.draft_hash == proposal["proposed_after_hash"]
    assert len(store.list_version_records(draft.strategy_id)) == before_versions
    assert current.rule_based_draft == draft.rule_based_draft
    assert current.regime_draft == draft.regime_draft
    before_transitions = {
        item["transition_id"]: item for item in draft.active_strategy_payload["transitions"]
    }
    after_transitions = {
        item["transition_id"]: item for item in current.active_strategy_payload["transitions"]
    }
    assert set(before_transitions) == set(after_transitions)
    assert after_transitions["idle-to-value"]["condition"]["children"][1]["right"]["value"] == -0.25
    assert after_transitions["value-to-deep"]["condition"]["children"][1]["right"]["value"] == -0.35
    for transition_id in ("idle-to-value", "value-to-deep"):
        before = before_transitions[transition_id]["condition"]["children"][0]
        after = after_transitions[transition_id]["condition"]["children"][0]
        assert after == before
    assert after_transitions["deep-to-recovery"] == before_transitions["deep-to-recovery"]
    persisted = proposals.get(proposal["change_set_id"])
    assert persisted["status"] == "APPLIED"
    audit = persisted["decision_audit"]
    assert audit["actor_type"] == "HUMAN_APPROVAL"
    assert audit["source"] == "CODEX_MCP"
    assert audit["hash_before"] == draft.draft_hash
    assert audit["hash_after"] == current.draft_hash
    assert audit["draft_revision_before"] == 1
    assert audit["draft_revision_after"] == 2
    assert len(audit["operations"]) == 2
    assert (
        apply.approve(draft.strategy_id, proposal["change_set_id"])["result"] == "ALREADY_APPLIED"
    )
    assert store.get_draft(draft.strategy_id).draft_revision == 2
    assert (
        OperatorChangeSetRepository(proposals.path).get(proposal["change_set_id"])["status"]
        == "APPLIED"
    )


def test_reject_is_persisted_without_draft_write(tmp_path: Path) -> None:
    store, draft, proposals, prepare, apply = _fixture(tmp_path)
    proposal = _proposal(prepare, draft)
    before = store.db_path.read_bytes()
    rejected = apply.reject(draft.strategy_id, proposal["change_set_id"])
    assert rejected["result"] == "REJECTED"
    assert rejected["audit"]["hash_before"] == rejected["audit"]["hash_after"]
    assert store.db_path.read_bytes() == before
    assert proposals.get(proposal["change_set_id"])["status"] == "REJECTED"
    assert (
        apply.reject(draft.strategy_id, proposal["change_set_id"])["result"] == "ALREADY_REJECTED"
    )
    with pytest.raises(ChangeSetDecisionError, match="ALREADY_REJECTED"):
        apply.approve(draft.strategy_id, proposal["change_set_id"])
    assert store.db_path.read_bytes() == before


def test_revision_and_hash_conflicts_do_not_apply(tmp_path: Path) -> None:
    store, draft, proposals, prepare, apply = _fixture(tmp_path)
    proposal = _proposal(prepare, draft)
    store.save_draft(
        strategy_id=draft.strategy_id,
        expected_revision=1,
        active_mode=draft.active_mode,
        rule_based_draft=draft.rule_based_draft,
        regime_draft=draft.regime_draft,
        active_strategy_payload=draft.active_strategy_payload,
        source_metadata=draft.source_metadata,
        name="edited",
        description="",
    )
    current = store.db_path.read_bytes()
    with pytest.raises(ChangeSetDecisionError, match="CHANGESET_STALE"):
        apply.approve(draft.strategy_id, proposal["change_set_id"])
    assert store.db_path.read_bytes() == current
    assert prepare.get_change_set(proposal["change_set_id"])["status"] == "STALE"
    assert proposals.get(proposal["change_set_id"])["status"] == "PROPOSED"

    store2, draft2, proposals2, prepare2, apply2 = _fixture(tmp_path / "other")
    proposal2 = _proposal(prepare2, draft2)
    connection = sqlite3.connect(store2.db_path)
    connection.execute(
        "UPDATE strategy_drafts SET draft_hash = ? WHERE strategy_id = ?",
        ("f" * 64, draft2.strategy_id),
    )
    connection.commit()
    connection.close()
    with pytest.raises(ChangeSetDecisionError, match="CHANGESET_STALE"):
        apply2.approve(draft2.strategy_id, proposal2["change_set_id"])
    assert store2.get_draft(draft2.strategy_id).draft_revision == 1
    assert proposals2.get(proposal2["change_set_id"])["status"] == "PROPOSED"


def test_invalid_second_operation_rolls_back_first(tmp_path: Path) -> None:
    store, draft, proposals, prepare, apply = _fixture(tmp_path)
    proposal = _proposal(prepare, draft)
    connection = sqlite3.connect(proposals.path)
    row = connection.execute(
        "SELECT proposal_json FROM operator_draft_change_sets WHERE change_set_id = ?",
        (proposal["change_set_id"],),
    ).fetchone()
    body = json.loads(row[0])
    body["operations"][1]["transition_id"] = "deep-to-recovery"
    connection.execute(
        "UPDATE operator_draft_change_sets SET proposal_json = ? WHERE change_set_id = ?",
        (json.dumps(body), proposal["change_set_id"]),
    )
    connection.commit()
    connection.close()
    before = store.db_path.read_bytes()
    with pytest.raises(ChangeSetDecisionError, match="CHANGESET_OPERATION_RESOLUTION_FAILED"):
        apply.approve(draft.strategy_id, proposal["change_set_id"])
    assert store.db_path.read_bytes() == before
    failure = proposals.get(proposal["change_set_id"])
    assert failure["status"] == "FAILED"
    assert failure["failure_stage"] == "OPERATION_RESOLUTION"
    assert prepare.get_change_set(proposal["change_set_id"])["status"] == "FAILED"
    assert not proposals.reject(proposal["change_set_id"])
    assert (
        apply.get(draft.strategy_id, proposal["change_set_id"])["approval_gate"]["can_approve"]
        is False
    )
    with pytest.raises(ChangeSetDecisionError, match="CHANGESET_FAILED"):
        apply.approve(draft.strategy_id, proposal["change_set_id"])


def test_displayed_diff_cannot_diverge_from_approved_operations(tmp_path: Path) -> None:
    store, draft, proposals, prepare, apply = _fixture(tmp_path)
    proposal = _proposal(prepare, draft)
    connection = sqlite3.connect(proposals.path)
    row = connection.execute(
        "SELECT proposal_json FROM operator_draft_change_sets WHERE change_set_id = ?",
        (proposal["change_set_id"],),
    ).fetchone()
    body = json.loads(row[0])
    body["human_readable_diff"]["changes"][0]["after"] = "-99%"
    connection.execute(
        "UPDATE operator_draft_change_sets SET proposal_json = ? WHERE change_set_id = ?",
        (json.dumps(body), proposal["change_set_id"]),
    )
    connection.commit()
    connection.close()
    with pytest.raises(ChangeSetDecisionError, match="CHANGESET_VALIDATION_FAILED"):
        apply.approve(draft.strategy_id, proposal["change_set_id"])
    assert store.get_draft(draft.strategy_id).draft_revision == 1
    assert proposals.get(proposal["change_set_id"])["status"] == "FAILED"


def test_concurrent_approval_increments_revision_once(tmp_path: Path) -> None:
    store, draft, proposals, prepare, _ = _fixture(tmp_path)
    proposal = _proposal(prepare, draft)

    def decide() -> str:
        service = OperatorChangeSetApplyService(
            StrategyRepository(store.db_path), OperatorChangeSetRepository(proposals.path)
        )
        return service.approve(draft.strategy_id, proposal["change_set_id"])["result"]

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: decide(), range(2)))
    assert sorted(results) == ["ALREADY_APPLIED", "APPLIED"]
    assert store.get_draft(draft.strategy_id).draft_revision == draft.draft_revision + 1
    connection = sqlite3.connect(proposals.path)
    try:
        count = connection.execute(
            "SELECT count(*) FROM operator_change_set_decisions WHERE change_set_id = ?",
            (proposal["change_set_id"],),
        ).fetchone()[0]
    finally:
        connection.close()
    assert count == 1


def test_validation_is_rechecked_and_wrong_strategy_is_hidden(tmp_path: Path) -> None:
    store, draft, proposals, prepare, apply = _fixture(tmp_path)
    proposal = _proposal(prepare, draft)
    apply._preview._validator.validate = lambda _payload: type(
        "InvalidResult",
        (),
        {"to_dict": lambda self: {"is_valid": False, "errors": [], "warnings": []}},
    )()
    with pytest.raises(ChangeSetDecisionError, match="CHANGESET_VALIDATION_FAILED"):
        apply.approve(draft.strategy_id, proposal["change_set_id"])
    assert store.get_draft(draft.strategy_id).draft_revision == 1
    assert proposals.get(proposal["change_set_id"])["status"] == "FAILED"
    assert proposals.get(proposal["change_set_id"])["failure_stage"] == "VALIDATION"
    reloaded = OperatorChangeSetApplyService(
        StrategyRepository(store.db_path), OperatorChangeSetRepository(proposals.path)
    ).get(draft.strategy_id, proposal["change_set_id"])
    assert reloaded["status"] == "FAILED"
    assert reloaded["failure_code"] == "CHANGESET_VALIDATION_FAILED"
    assert reloaded["failure_audit"]["result"] == "FAILED"
    assert reloaded["failure_audit"]["change_set_id"] == proposal["change_set_id"]
    assert reloaded["approval_gate"] == {"can_approve": False, "reason": "CHANGESET_FAILED"}
    assert store.get_draft(draft.strategy_id).draft_hash == draft.draft_hash
    assert (
        len(
            [
                item
                for item in apply.list_for_strategy(draft.strategy_id)
                if item["status"] == "PROPOSED"
            ]
        )
        == 0
    )
    with pytest.raises(ChangeSetDecisionError, match="CHANGESET_FAILED"):
        apply.approve(draft.strategy_id, proposal["change_set_id"])
    connection = sqlite3.connect(proposals.path)
    assert (
        connection.execute("SELECT count(*) FROM operator_change_set_failures").fetchone()[0] == 1
    )
    connection.close()
    with pytest.raises(ChangeSetDecisionError, match="CHANGESET_NOT_FOUND"):
        apply.get("other-strategy", proposal["change_set_id"])


def test_wal_mode_fails_closed_for_cross_database_atomicity(tmp_path: Path) -> None:
    store, draft, proposals, prepare, apply = _fixture(tmp_path)
    proposal = _proposal(prepare, draft)
    connection = sqlite3.connect(proposals.path)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.close()
    with pytest.raises(ChangeSetDecisionError, match="ATOMICITY_UNAVAILABLE"):
        apply.approve(draft.strategy_id, proposal["change_set_id"])
    assert store.get_draft(draft.strategy_id).draft_revision == 1


def test_transaction_failure_rolls_back_draft_then_persists_failure(tmp_path: Path) -> None:
    store, draft, proposals, prepare, apply = _fixture(tmp_path)
    proposal = _proposal(prepare, draft)
    connection = sqlite3.connect(proposals.path)
    connection.execute(
        "CREATE TABLE operator_change_set_decisions (change_set_id TEXT PRIMARY KEY, "
        "result TEXT NOT NULL, actor_type TEXT NOT NULL, audit_json TEXT NOT NULL, "
        "decided_at TEXT NOT NULL)"
    )
    connection.execute(
        "CREATE TRIGGER fail_approval BEFORE INSERT ON operator_change_set_decisions "
        "BEGIN SELECT RAISE(ABORT, 'test transaction failure'); END"
    )
    connection.commit()
    connection.close()
    before = store.db_path.read_bytes()
    with pytest.raises(ChangeSetDecisionError, match="CHANGESET_STORAGE_ERROR"):
        apply.approve(draft.strategy_id, proposal["change_set_id"])
    assert store.db_path.read_bytes() == before
    assert store.get_draft(draft.strategy_id).draft_revision == draft.draft_revision
    assert store.get_draft(draft.strategy_id).draft_hash == draft.draft_hash
    failure = OperatorChangeSetRepository(proposals.path).get(proposal["change_set_id"])
    assert failure["status"] == "FAILED"
    assert failure["failure_stage"] == "TRANSACTION"
    connection = sqlite3.connect(proposals.path)
    assert (
        connection.execute("SELECT count(*) FROM operator_change_set_decisions").fetchone()[0] == 0
    )
    connection.close()


def test_failure_and_success_evidence_conflict_is_not_guessed(tmp_path: Path) -> None:
    store, draft, proposals, prepare, apply = _fixture(tmp_path)
    proposal = _proposal(prepare, draft)
    assert apply.approve(draft.strategy_id, proposal["change_set_id"])["result"] == "APPLIED"
    connection = sqlite3.connect(proposals.path)
    connection.execute(
        "CREATE TABLE operator_change_set_failures (change_set_id TEXT PRIMARY KEY, "
        "failure_code TEXT, failure_stage TEXT, safe_message TEXT, failed_at TEXT)"
    )
    connection.execute(
        "INSERT INTO operator_change_set_failures VALUES (?, 'X', 'VALIDATION', 'safe', 'now')",
        (proposal["change_set_id"],),
    )
    connection.commit()
    connection.close()
    with pytest.raises(ChangeSetStorageError, match="CHANGE_SET_INTEGRITY_ERROR"):
        proposals.get(proposal["change_set_id"])
    with pytest.raises(ChangeSetDecisionError, match="CHANGESET_INTEGRITY_ERROR"):
        apply.approve(draft.strategy_id, proposal["change_set_id"])


def test_failed_state_is_readable_over_mcp_without_write_tools(tmp_path: Path) -> None:
    store, draft, proposals, prepare, apply = _fixture(tmp_path)
    proposal = _proposal(prepare, draft)
    apply._preview._validator.validate = lambda _payload: type(
        "InvalidResult",
        (),
        {"to_dict": lambda self: {"is_valid": False, "errors": [], "warnings": []}},
    )()
    with pytest.raises(ChangeSetDecisionError, match="CHANGESET_VALIDATION_FAILED"):
        apply.approve(draft.strategy_id, proposal["change_set_id"])

    async def exercise() -> None:
        async with Client(create_server(prepare._read, prepare)) as client:
            tools = {tool.name for tool in (await client.list_tools()).tools}
            assert not any("apply" in name or "approve" in name for name in tools)
            result = await client.call_tool(
                "quant_get_change_set", {"change_set_id": proposal["change_set_id"]}
            )
            assert not result.is_error
            assert result.structured_content["status"] == "FAILED"
            assert result.structured_content["failure_stage"] == "VALIDATION"
            assert "Draft was not changed" in result.structured_content["safe_message"]

    asyncio.run(exercise())
    assert store.get_draft(draft.strategy_id).draft_hash == draft.draft_hash
