# PHASE 13I Strategy Durability and Lifecycle

## Canonical storage

`StrategyRepository` is the single persistence owner for `strategies`,
`strategy_drafts`, and immutable `strategy_versions` (plus derived materialized
versions). The canonical path is the repository default
`data/strategy.db`; tests must inject a temporary SQLite path.

## Durability contract

Application startup and repository initialization are additive and idempotent.
Existing strategy IDs, draft revisions, version IDs, hashes, timestamps, and
payload JSON are not rewritten. Schema migration records a consistent safety
snapshot before upgrading an existing older schema and retains the five newest
managed migration snapshots.

## Destructive-operation safety

`clear()` is permitted only for a database under the system temporary
directory. It rejects the canonical development database with
`DESTRUCTIVE_OPERATION_BLOCKED`. Unit API fixtures use `tmp_path` and never
clear the user database.

## Lifecycle

Strategy identities have independent lifecycle metadata: `ACTIVE` or `TRASHED`,
`is_protected`, and `deleted_at`. Moving to Trash is a soft delete and retains
all drafts, immutable versions, and research evidence. Restore preserves the
original identity and version history. Protected identities cannot be trashed
until explicitly unprotected.

Permanent deletion is allowed only for an unprotected trashed strategy with no
references from Backtest, Experiment, Selection, Freeze, or OOS persistence
tables. Dependency details are returned to the caller; research evidence is
never cascade-deleted.

## API and UI

Strategy Lab exposes lifecycle endpoints for listing Active/Trash/all records,
Trash, Restore, Protect/Unprotect, bulk Trash, dependency inspection, and
permanent delete. Diagnostics expose database path, schema version, and
Strategy/Draft/Version counts. The Strategy Library provides Active and Trash
tabs, selection-based Trash, protection controls, Restore, and dependency-safe
permanent delete messaging.

## Verification

Focused tests cover restart persistence, migration preservation and safety
backup, protected Trash, Restore, draft-only permanent deletion, dependency
blocking, API lifecycle operations, diagnostics, and frontend Trash/Restore
behavior. No release tag or push is part of this phase.

## Frozen HOLD_PREVIOUS contract

Fallback is active iff strategy_mode is RULE_BASED and no_match_behavior is
USE_FALLBACK. Active empty fallback fails with InvalidFallback. Inactive
fallback is never parsed as an allocation, validated, or used to collect assets.
Its raw JSON can remain in historical version provenance without changing hashes.
Starting Allocation is distinct from fallback: the evaluation timeline starts
from Starting Allocation, then retains the latest resolved target on no match.

## Permanent golden gate

Every later Strategy Lab / V1.4 phase must run these guards:

- GOLDEN-1: HOLD_PREVIOUS with empty fallback and valid Starting Allocation passes.
- GOLDEN-2: USE_FALLBACK with empty fallback fails InvalidFallback.
- GOLDEN-3: Regime validation ignores inactive Rule fallback.
- GOLDEN-4: inactive SGOV fallback never enters execution assets or data requests.
- GOLDEN-5: a persisted HOLD_PREVIOUS canary survives repository restart/migration.

Automated commands (temporary test databases, never clear the user database):

```sh
.venv/bin/pytest -q tests/unit/test_strategy_validation.py tests/unit/test_stateful_strategy_domain.py tests/unit/test_strategy_lab_api.py tests/unit/test_strategy_evaluation.py tests/unit/test_backtest_service.py tests/unit/test_strategy_lifecycle.py tests/unit/test_strategy_repository.py tests/unit/test_exact_strategy_persistence.py tests/unit/test_semantic_target_api.py
```

## User-confirmed browser UAT

Evidence source: user report on 2026-09-29, not automated inference.
Canary: PERSISTENCE_CANARY_V14_HOLD.

| User action / observation | Result |
| --- | --- |
| Completely close browser | PASS |
| Stop terminal/backend/frontend | PASS |
| Restart system | PASS |
| Discover Canary in Strategy Library | PASS |
| Draft still exists | PASS |
| Version still exists | PASS |
| HOLD_PREVIOUS with empty fallback reload | PASS |
| Validate | PASS |
| Backtest exact saved StrategyVersion | PASS |

Cold browser reopen = PASS. Backend/frontend restart persistence = PASS.
HOLD_PREVIOUS reload = PASS. Exact version Backtest = PASS.
Remaining manual lifecycle scenarios (Trash/Restore, Protect, bulk operations,
dependency-protected permanent deletion) remain V1.4 Release Gate items, not
blockers to committing these stability fixes.

## PHASE 13J entry baseline

Read-only inspection: strategy_id PERSISTENCE_CANARY_V14_HOLD; draft revision 11.
Latest version: PERSISTENCE_CANARY_V14_HOLD-v5-99a03142359b.
Latest content hash: 16f8936d88aafe6b4343c238e4855720881e92b85b432c8da86beccda2dd34fd.
Latest version retains HOLD_PREVIOUS and Starting QQQ 90%; the earlier user UAT
Starting QQQ 100% version remains v1-d2c1f014828c, hash
8ae09d276b4b175f1b80abe3d846eea5f66e4f508a4c19f980c3f1da968c222f.
Do not overwrite these user versions to recreate a test fixture.
