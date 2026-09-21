# PHASE 11B Strategy Contract Foundation

## Contract

Strategy schema `1.0` is the legacy implicit schema. A payload without
`strategy_schema_version` is read as `1.0`, and its declared assets retain the
legacy `BOTH` capability semantics. Legacy serialization omits all new fields,
so existing content hashes and immutable references remain unchanged.

Schema `2.0` explicitly serializes `strategy_schema_version`, each asset's
`role`, and each operand's `timeframe`. The generic roles are
`signal_source`, `execution_asset`, and `both`. Cash remains an implicit ledger
remainder; `SGOV` is an ordinary declared execution asset.

`Timeframe` currently supports `daily` and `weekly`. Missing legacy timeframe
values deterministically resolve to `daily`. Weekly is a contract value only in
PHASE 11B. Existing strategy, backtest, experiment, and OOS execution paths
reject weekly indicator requirements with a controlled unsupported-timeframe
error before daily indicator calculation.

## Identity and Persistence

Schema 2 roles, timeframes, and schema version participate in canonical
`StrategyDefinition` serialization and therefore in `StrategyVersion.content_hash`.
Materialization copies the canonical configuration, so roles and timeframes are
retained in derived versions. Freeze and OOS identity continue to use the
resulting immutable strategy version/content hash; no historical record is
rewritten.

The Strategy Lab emits schema 2 with `BOTH` roles for its existing editor
surface and explicit daily operands. It does not add a role or timeframe editor
in this phase. Older API payloads remain accepted through the legacy read path.

## Deliberately Deferred

PHASE 11B does not aggregate weekly bars, compute weekly indicators, define
completed-week/as-of semantics, or change signal, backtest, analytics, freeze,
OOS, or financial calculation semantics. Those concerns remain PHASE 11C
scope.
