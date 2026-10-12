"""Human-only decisions over persisted Codex proposals."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError

from backend.app.operator_change_set_repository import OperatorChangeSetRepository
from backend.app.operator_change_set_service import (
    ChangeSetPreparationError,
    DraftChangeOperation,
    OperatorChangeSetService,
)
from backend.app.operator_read_service import OperatorReadService
from backend.app.strategy_repository import StrategyRepository
from research.canonical import canonical_json


class ChangeSetDecisionError(ValueError):
    """Stable public failure code, without database or strategy internals."""


class OperatorChangeSetApplyService:
    """Use one rollback-journal transaction across Draft and proposal files."""

    def __init__(
        self,
        strategies: StrategyRepository | None = None,
        proposals: OperatorChangeSetRepository | None = None,
    ) -> None:
        self._strategies = strategies or StrategyRepository()
        self._proposals = proposals or OperatorChangeSetRepository()
        self._preview = OperatorChangeSetService(
            OperatorReadService(db_path=self._strategies.db_path), self._proposals
        )

    def get(self, strategy_id: str, change_set_id: str) -> dict[str, Any]:
        try:
            proposal = self._preview.get_change_set(change_set_id)
        except ChangeSetPreparationError as exc:
            raise ChangeSetDecisionError("CHANGESET_NOT_FOUND") from exc
        if proposal["strategy_id"] != strategy_id:
            raise ChangeSetDecisionError("CHANGESET_NOT_FOUND")
        return {**proposal, "approval_gate": self._approval_gate(proposal)}

    def list_for_strategy(self, strategy_id: str) -> list[dict[str, Any]]:
        return [
            self.get(strategy_id, item["change_set_id"])
            for item in self._proposals.list_for_strategy(strategy_id)
        ]

    def _approval_gate(self, proposal: dict[str, Any]) -> dict[str, Any]:
        if proposal["status"] != "PROPOSED":
            return {
                "can_approve": False,
                "reason": "CHANGESET_STALE"
                if proposal["status"] == "STALE"
                else proposal["status"],
            }
        if proposal["validation_status"] != "PASS":
            return {"can_approve": False, "reason": "CHANGESET_VALIDATION_FAILED"}
        try:
            strategy = self._strategies.get_strategy(proposal["strategy_id"])
            draft = self._strategies.get_draft(proposal["strategy_id"])
            if strategy is None or strategy.lifecycle_status != "ACTIVE":
                return {"can_approve": False, "reason": "DRAFT_NOT_EDITABLE"}
            if draft is None:
                return {"can_approve": False, "reason": "DRAFT_NOT_FOUND"}
            if (
                draft.draft_revision != proposal["expected_draft_revision"]
                or draft.draft_hash != proposal["before_hash"]
            ):
                return {"can_approve": False, "reason": "CHANGESET_STALE"}
            operations = [
                DraftChangeOperation.model_validate(item) for item in proposal["operations"]
            ]
            _, details = self._preview.preview(draft, operations)
            if (
                details["validation_status"] != "PASS"
                or details["proposed_after_hash"] != proposal["proposed_after_hash"]
                or details["human_readable_diff"] != proposal["human_readable_diff"]
            ):
                return {"can_approve": False, "reason": "CHANGESET_VALIDATION_FAILED"}
        except (ChangeSetPreparationError, ValidationError, KeyError, TypeError, ValueError):
            return {"can_approve": False, "reason": "CHANGESET_VALIDATION_FAILED"}
        return {"can_approve": True, "reason": None}

    def approve(self, strategy_id: str, change_set_id: str) -> dict[str, Any]:
        return self._decide(strategy_id, change_set_id, approve=True)

    def reject(self, strategy_id: str, change_set_id: str) -> dict[str, Any]:
        return self._decide(strategy_id, change_set_id, approve=False)

    def _decide(self, strategy_id: str, change_set_id: str, *, approve: bool) -> dict[str, Any]:
        if not self._proposals.path.exists():
            raise ChangeSetDecisionError("CHANGESET_NOT_FOUND")
        connection = self._strategies._connect()
        try:
            connection.execute("ATTACH DATABASE ? AS operator", (str(self._proposals.path),))
            if any(
                connection.execute(f"PRAGMA {database}.journal_mode").fetchone()[0].lower()
                not in {"delete", "truncate", "persist"}
                for database in ("main", "operator")
            ):
                raise ChangeSetDecisionError("ATOMICITY_UNAVAILABLE")
            connection.execute("BEGIN IMMEDIATE")
            proposal_row = connection.execute(
                "SELECT proposal_json, status, expected_draft_revision, before_hash "
                "FROM operator.operator_draft_change_sets "
                "WHERE change_set_id = ? AND strategy_id = ?",
                (change_set_id, strategy_id),
            ).fetchone()
            if proposal_row is None:
                raise ChangeSetDecisionError("CHANGESET_NOT_FOUND")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS operator.operator_change_set_decisions ("
                "change_set_id TEXT PRIMARY KEY, result TEXT NOT NULL "
                "CHECK(result IN ('APPLIED','REJECTED')), "
                "actor_type TEXT NOT NULL, audit_json TEXT NOT NULL, decided_at TEXT NOT NULL)"
            )
            prior = connection.execute(
                "SELECT result, audit_json FROM operator.operator_change_set_decisions "
                "WHERE change_set_id = ?",
                (change_set_id,),
            ).fetchone()
            if prior is not None:
                result = "ALREADY_APPLIED" if prior["result"] == "APPLIED" else "ALREADY_REJECTED"
                connection.execute("ROLLBACK")
                if (approve and prior["result"] == "REJECTED") or (
                    not approve and prior["result"] == "APPLIED"
                ):
                    raise ChangeSetDecisionError(result)
                return {"result": result, "audit": json.loads(prior["audit_json"])}
            if proposal_row["status"] != "PROPOSED":
                if approve:
                    raise ChangeSetDecisionError("ALREADY_REJECTED")
                connection.execute("ROLLBACK")
                return {"result": "ALREADY_REJECTED"}
            proposal = json.loads(proposal_row["proposal_json"])
            if (
                proposal.get("change_set_id") != change_set_id
                or proposal.get("strategy_id") != strategy_id
                or proposal.get("expected_draft_revision")
                != proposal_row["expected_draft_revision"]
                or proposal.get("before_hash") != proposal_row["before_hash"]
            ):
                raise ChangeSetDecisionError("CHANGESET_VALIDATION_FAILED")
            strategy = connection.execute(
                "SELECT lifecycle_status FROM strategies WHERE strategy_id = ?",
                (strategy_id,),
            ).fetchone()
            if strategy is None or strategy["lifecycle_status"] != "ACTIVE":
                raise ChangeSetDecisionError("DRAFT_NOT_EDITABLE")
            row = connection.execute(
                "SELECT * FROM strategy_drafts WHERE strategy_id = ?",
                (strategy_id,),
            ).fetchone()
            if row is None:
                raise ChangeSetDecisionError("DRAFT_NOT_FOUND")
            draft = StrategyRepository._row_to_draft(row)
            if (
                draft.draft_revision != proposal["expected_draft_revision"]
                or draft.draft_hash != proposal["before_hash"]
            ):
                raise ChangeSetDecisionError("CHANGESET_STALE")
            when = datetime.now(UTC).isoformat()
            if approve:
                if proposal["validation_status"] != "PASS":
                    raise ChangeSetDecisionError("CHANGESET_VALIDATION_FAILED")
                try:
                    operations = [
                        DraftChangeOperation.model_validate(item) for item in proposal["operations"]
                    ]
                    after_payload, details = self._preview.preview(draft, operations)
                except (ChangeSetPreparationError, ValidationError, KeyError, TypeError) as exc:
                    raise ChangeSetDecisionError("CHANGESET_VALIDATION_FAILED") from exc
                if (
                    details["validation_status"] != "PASS"
                    or details["proposed_after_hash"] != proposal["proposed_after_hash"]
                    or details["human_readable_diff"] != proposal["human_readable_diff"]
                ):
                    raise ChangeSetDecisionError("CHANGESET_VALIDATION_FAILED")
                updated = connection.execute(
                    "UPDATE strategy_drafts SET draft_revision = ?, "
                    "active_strategy_payload_json = ?, draft_hash = ?, updated_at = ? "
                    "WHERE strategy_id = ? AND draft_revision = ? AND draft_hash = ?",
                    (
                        draft.draft_revision + 1,
                        StrategyRepository._safe_json(after_payload),
                        details["proposed_after_hash"],
                        when,
                        strategy_id,
                        draft.draft_revision,
                        draft.draft_hash,
                    ),
                ).rowcount
                if updated != 1:
                    raise ChangeSetDecisionError("CHANGESET_STALE")
                connection.execute(
                    "UPDATE strategies SET updated_at = ? WHERE strategy_id = ?",
                    (when, strategy_id),
                )
                result = "APPLIED"
                after_revision = draft.draft_revision + 1
                after_hash = details["proposed_after_hash"]
            else:
                result = "REJECTED"
                after_revision = draft.draft_revision
                after_hash = draft.draft_hash
            audit = {
                "actor_type": "HUMAN_APPROVAL",
                "source": "CODEX_MCP",
                "change_set_id": change_set_id,
                "strategy_id": strategy_id,
                "draft_revision_before": draft.draft_revision,
                "draft_revision_after": after_revision,
                "hash_before": draft.draft_hash,
                "hash_after": after_hash,
                "operations": proposal["operations"],
                "decided_at": when,
                "result": result,
            }
            connection.execute(
                "INSERT INTO operator.operator_change_set_decisions "
                "(change_set_id, result, actor_type, audit_json, decided_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (change_set_id, result, "HUMAN_APPROVAL", canonical_json(audit), when),
            )
            connection.execute("COMMIT")
            return {"result": result, "audit": audit}
        except ChangeSetDecisionError:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        except (sqlite3.Error, ValueError, TypeError, KeyError) as exc:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise ChangeSetDecisionError("CHANGESET_STORAGE_ERROR") from exc
        finally:
            connection.close()
