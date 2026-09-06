# Strategy Engine V1.0 Architecture Specification

**Status:** Architecture specification only
**Date:** 2026-09-06
**Implementation status:** Not implemented in this phase

## 1. Purpose

Strategy Engine V1.0 defines a data-driven, serializable and versioned strategy
language for the ETF research system. It transforms validated market data and
PHASE 2 indicator series into explainable `Signal` objects and
`TargetAllocation` objects. It does not place orders or perform portfolio
accounting.

The initial asset universe includes QQQ, TQQQ and SGOV as supported examples,
but the schema treats an asset as an extensible ticker symbol and does not
hard-code this list.

## 2. Design Principles

- Strategy definitions are structured data, never Python expressions.
- Every result is deterministic: identical version, inputs and date produce
  identical output.
- Strategy evaluation is point-in-time and cannot read future market data.
- Raw versus adjusted pricing is explicit and traceable.
- Invalid user configuration fails validation; it is never silently repaired.
- Strategy output is separate from orders, fills, trades and portfolio state.
- Version snapshots are immutable and sufficient to reproduce evaluation.
- Explanations are structured engine output, not frontend-side recomputation.

## 3. Responsibilities

The Strategy Engine owns:

```text
HistoricalDataSet + IndicatorSeries + StrategyVersion
        -> Condition Evaluation
        -> Rule Evaluation
        -> Signal
        -> TargetAllocation
```

It does not own order quantity, execution price, commission, slippage, cash,
positions, trades or equity. Those remain PHASE 3 Backtest responsibilities.

## 4. Architecture

The future implementation should use these domain boundaries:

```text
strategies/
    definitions/      StrategyDefinition, AssetSpec, serializable schemas
    conditions/       Operand, Condition, threshold semantics, evaluator
    rules/            RuleGroup, AllocationRule, rule evaluator
    allocation/       weight resolution and constraint validation
    signals/          Signal and evaluation explanation models
    versions/         immutable snapshots, canonical serialization and hashes
    derived/          future Deviation series and other derived values
```

The engine receives indicator series through an asset-qualified registry, for
example `("QQQ", "ma_50") -> IndicatorSeries`. This is required because the
current PHASE 2 `IndicatorSeries` records kind, period, price field and dates,
but does not itself carry an asset symbol.

## 5. Strategy Definition

`StrategyDefinition` is the logical strategy template:

```text
StrategyDefinition
├── strategy_id
├── name
├── description / metadata
├── assets: AssetSpec[]
├── price_field: raw_close | adjusted_close
├── rules: AllocationRule[]
├── fallback: AllocationRule
└── rebalance_policy: RebalancePolicy
```

The definition contains no executable code and no mutable runtime portfolio
state. The global `price_field` is the V1.0 default contract for all price and
indicator references. An operand may repeat the field for display or
validation, but a conflicting field is invalid rather than implicitly mixed.

## 6. Asset Model

```text
AssetSpec
├── symbol: string
├── enabled: boolean
└── metadata: object
```

Symbols are normalized to uppercase and validated as market identifiers. The
definition may list QQQ, TQQQ and SGOV, but no domain code may branch on those
three names. Data availability is checked separately at evaluation time.

## 7. Operand Model

`ValueReference` is a discriminated union:

```text
ValueReference
├── kind: price
│   ├── asset: Symbol
│   └── price_field: PriceField
└── kind: indicator
    ├── asset: Symbol
    └── indicator:
        ├── type: ma | ema
        ├── period: positive integer
        └── price_field: PriceField
```

V1.0 supports `Price`, `MA(period)` and `EMA(period)`. The shape is open for
future RSI, MACD, Bollinger, Momentum, Volatility and other indicator types.
Constants and portfolio-state operands are reserved for a later version and
are not silently accepted by V1.0.

## 8. Operator Model

V1.0 supports:

```text
greater_than       >
greater_or_equal   >=
less_than          <
less_or_equal      <=
equal              ==
```

Operators compare the evaluated left and right operands on the same evaluation
date. `==` has no hidden tolerance in the schema; an explicit tolerance policy
would be a future extension.

## 9. Condition Model

```text
Condition
├── left: ValueReference
├── operator: ComparisonOperator
├── right: ValueReference
└── threshold: optional Threshold
```

Both operands may be price or indicator references. Therefore the same model
expresses:

```text
Price  > MA50
Price  > MA50 + 4%
MA20   > MA50 + 2%
EMA20  < MA50 - 3%
```

An omitted threshold means zero deviation for the comparison. The engine must
retain the normalized condition and its evaluated operands in the explanation.

## 10. Relative Threshold

V1.0 defines the generic relative threshold as:

```text
Deviation(A, B) = (A - B) / B
```

The serialized threshold is signed and expressed as a decimal fraction:

```json
{"type": "relative", "value": 0.04}
{"type": "relative", "value": -0.03}
```

The comparison is normalized as:

```text
A >  B + X%  -> Deviation(A, B) >  X
A >= B + X%  -> Deviation(A, B) >= X
A <  B + X%  -> Deviation(A, B) <  X
A <= B + X%  -> Deviation(A, B) <= X
```

Thus `Price < MA50 - 3%` is represented by `value = -0.03` and means
`Deviation(Price, MA50) < -0.03`. The sign is part of the value; a bare
`threshold = 3` is invalid.

If the right operand is zero, deviation is not computable. The evaluator must
return a structured `division_by_zero` / `not_evaluable` result or raise the
corresponding domain error. It must never emit or propagate `NaN` or
`Infinity`, and it must not silently treat the condition as true or false.

`Threshold.type` is intentionally extensible:

```text
Threshold
├── type: relative | absolute
└── value: signed numeric value
```

Only `relative` is enabled in V1.0. `absolute` is reserved for a future
expression such as `Price > MA50 + $5`.

## 11. Indicator vs Indicator

Indicator references are asset-qualified and can be compared directly:

```text
MA20(QQQ) > MA50(QQQ) + 2%
EMA20(TQQQ) < MA50(TQQQ) - 1%
```

Cross-asset comparisons are structurally possible, but V1.0 should reject
them unless a future strategy version explicitly declares the comparison
semantics and synchronized data contract.

## 12. RuleGroup

Conditions compose recursively:

```text
RuleGroup
├── operator: AND | OR
└── children: Condition | RuleGroup[]
```

`AND` requires every child to pass; `OR` requires at least one child to pass.
Groups cannot be empty. Nested groups are allowed, so this is representable:

```text
(Price > MA50 + 4% AND MA20 > MA50 + 2%)
OR
(Price > MA200 + 8%)
```

Unavailable, missing and not-yet-formed indicator values are structured
non-satisfied / non-evaluable results. A rule group must preserve child
statuses and reasons in its explanation; it cannot bypass validation because
another branch passed.

## 13. Allocation Rule

An `AllocationRule` maps one condition or rule group to a complete target
allocation:

```text
AllocationRule
├── rule_id: stable string
├── priority: integer
├── when: Condition | RuleGroup | unconditional
├── allocations: AssetAllocation[]
├── remaining: optional RemainingAllocation
├── regime: optional string
└── explanation: optional metadata
```

One matched rule may set multiple assets at once, for example:

```text
QQQ  = 0.30
TQQQ = 0.50
SGOV = 0.20
```

The rule output is a target configuration, not an order.

## 14. Multi-Asset Allocation

Multi-asset allocation is native to the model. Risk-on, neutral and risk-off
states can be represented as separate complete allocations:

```text
Risk-on:  QQQ 0.30, TQQQ 0.50, SGOV 0.20
Neutral:  QQQ 0.50, TQQQ 0.20, SGOV 0.30
Risk-off: QQQ 0.00, TQQQ 0.00, SGOV 1.00
```

Symbols omitted from a resolved target allocation have weight zero. The
allocation resolver must emit a deterministic ordering for serialization.

## 15. Weight Constraints

```text
AssetAllocation
├── symbol
├── target_weight
├── minimum_weight: optional
└── maximum_weight: optional
```

For every asset:

```text
0 <= minimum_weight <= target_weight <= maximum_weight <= 1
sum(target_weight) <= 1
```

If only one bound is supplied, the other bound defaults to `0` or `1` for
validation purposes. Bounds are constraints, not auto-clamping instructions.
Any violation fails evaluation with a domain error. Remaining weight is cash
unless a valid `RemainingAllocation` explicitly assigns it.

## 16. Remaining Allocation

The remaining amount is:

```text
remaining = 1.0 - sum(explicit target weights)
```

V1.0 permits at most one explicit remaining recipient, for example:

```text
QQQ 0.40
TQQQ 0.40
remaining -> SGOV
```

The recipient must be declared in the asset set and must satisfy its maximum
weight. If no recipient is declared, the remainder is an explicit cash
buffer, not silently discarded. Multiple fallback recipients or proportional
distribution are not supported in V1.0 and must be rejected as invalid
configuration.

## 17. Priority

Rules use a deterministic first-match policy. Higher priority is evaluated
first. `priority` must be unique within one strategy definition; equal
priorities are invalid rather than resolved by input order. A matched rule
stops rule selection and supplies the target allocation.

## 18. Fallback

Every V1.0 strategy must define one explicit fallback allocation rule. It is
used when no conditional rule matches and guarantees a legal output, such as
`SGOV = 1.0`. The fallback must itself pass all weight and asset validation.

If a malformed strategy has no fallback, validation fails before evaluation;
the engine does not invent a default asset or default to all cash.

## 19. Signal Contract

The evaluation result is an immutable `Signal`:

```text
Signal
├── date
├── strategy_version_id
├── matched_rule_id
├── regime / state
├── price_field_used
├── target_allocation: TargetAllocation
├── condition_results: ConditionResult[]
├── explanation: Explanation
└── source_data_reference
```

`TargetAllocation` passed to PHASE 3 is the resolved projection of this
signal. The full signal remains the auditable strategy result. Strategy Engine
does not create `Order`, `Fill` or `Trade` objects.

## 20. Explainability

`Explanation` is structured data, not presentation text only:

```text
ConditionResult
├── condition_id / path
├── status: passed | failed | not_evaluable
├── left_value
├── right_value
├── deviation: optional numeric value
├── threshold: optional signed threshold
└── reason
```

The result must identify the matched rule, regime, every evaluated condition,
the actual deviation where applicable, the selected price field and the final
target weights. Frontend code renders this data and must not recalculate
strategy logic independently.

## 21. Rebalance Contract

Strategy Engine emits a target allocation on each evaluation date. The
rebalance policy is part of the versioned strategy definition and supports:

```text
daily | weekly | monthly | on_signal_change
```

An optional `rebalance_threshold` is an absolute weight difference:

```text
abs(target_weight - current_weight) >= rebalance_threshold
```

The comparison with current portfolio weights and the creation of orders stay
in PHASE 3 Portfolio/Rebalance logic. Strategy Engine must not duplicate
commission, slippage, execution timing or order planning. For
`on_signal_change`, the execution layer compares successive resolved signal
or allocation states; Strategy evaluation itself remains independent of
portfolio holdings.

## 22. Price Field Contract

V1.0 supports only:

```text
PriceField.RAW_CLOSE       -> raw OHLC values
PriceField.ADJUSTED_CLOSE  -> adjusted OHLC values
```

The strategy definition, every indicator reference, every signal and the
backtest configuration must agree on the selected field. No component may
guess raw versus adjusted pricing. A conflicting operand or dataset field is
an `InvalidPriceField` error.

## 23. Look-Ahead Bias Policy

`evaluate(strategy_version, market_data, indicators, date=T)` may read only
values dated `<= T`. It may use the market bar and indicator value at T, but
never T+1 open, close, high, low, volume or any indicator derived from them.

Strategy time and execution time are separate:

```text
Strategy signal at T close
        -> PHASE 3 execution at next trading-day open
```

The Strategy Engine has no dependency on the T+1 bar. Missing or incomplete
indicator windows at T produce `unavailable` / `not_evaluable`, never a
forward-filled value.

## 24. Strategy Versioning

`StrategyVersion` is immutable:

```text
StrategyVersion
├── strategy_id
├── version_id
├── version_number
├── created_at: RFC3339 timestamp
├── configuration: canonical StrategyDefinition snapshot
├── parent_version_id: optional
├── content_hash
└── status: draft | active | archived
```

Changing a rule, threshold, asset, price field, indicator parameter or
rebalance policy creates a new version. The old snapshot is never overwritten.
The content hash is computed from canonical JSON with stable field ordering;
display-only metadata may be excluded only if that policy is explicit and
documented.

## 25. Strategy Definition Schema

The following JSON is the V1.0 wire shape. It is a schema contract, not an
implementation:

```json
{
  "strategy_id": "qqq-tqqq-sgov",
  "name": "QQQ/TQQQ/SGOV Dynamic Allocation",
  "description": "Example versioned allocation strategy",
  "assets": [
    {"symbol": "QQQ", "enabled": true},
    {"symbol": "TQQQ", "enabled": true},
    {"symbol": "SGOV", "enabled": true}
  ],
  "price_field": "adjusted_close",
  "rules": [
    {
      "rule_id": "risk-on",
      "priority": 100,
      "when": {
        "type": "group",
        "operator": "and",
        "children": [
          {
            "type": "condition",
            "left": {"kind": "price", "asset": "TQQQ", "price_field": "adjusted_close"},
            "operator": "greater_than",
            "right": {"kind": "indicator", "asset": "TQQQ", "indicator": {"type": "ma", "period": 50, "price_field": "adjusted_close"}},
            "threshold": {"type": "relative", "value": 0.04}
          },
          {
            "type": "condition",
            "left": {"kind": "indicator", "asset": "TQQQ", "indicator": {"type": "ma", "period": 20, "price_field": "adjusted_close"}},
            "operator": "greater_than",
            "right": {"kind": "indicator", "asset": "TQQQ", "indicator": {"type": "ma", "period": 50, "price_field": "adjusted_close"}},
            "threshold": {"type": "relative", "value": 0.02}
          }
        ]
      },
      "allocations": [
        {"symbol": "QQQ", "target_weight": 0.30},
        {"symbol": "TQQQ", "target_weight": 0.50, "minimum_weight": 0.00, "maximum_weight": 0.50},
        {"symbol": "SGOV", "target_weight": 0.20}
      ],
      "regime": "RISK_ON"
    }
  ],
  "fallback": {
    "rule_id": "fallback-risk-off",
    "priority": 0,
    "when": "fallback",
    "allocations": [{"symbol": "SGOV", "target_weight": 1.0}],
    "regime": "RISK_OFF"
  },
  "rebalance_policy": {
    "frequency": "on_signal_change",
    "threshold": 0.05
  }
}
```

## 26. Evaluation Contract

```text
evaluate(
    strategy_version,
    market_data: Mapping[Symbol, HistoricalDataSet],
    indicators: Mapping[(Symbol, IndicatorName), IndicatorSeries],
    evaluation_date: date,
) -> Signal
```

Before evaluating rules, the implementation validates the complete version,
checks that the requested date is available, verifies asset-qualified data and
price-field consistency, and confirms indicator dates align to the evaluation
date. It then evaluates rules in priority order, resolves one allocation,
creates structured explanations and returns an immutable signal.

The function has no random input, wall-clock dependency or portfolio mutation.

## 27. Validation

Validation occurs before execution and before evaluation:

```text
assets -> price field -> operands -> indicators -> conditions
       -> rule groups -> allocations -> priorities -> fallback
       -> rebalance policy
```

Validation must reject unknown assets, invalid symbols, unsupported indicator
types, non-positive or non-integer periods, mixed price fields, empty groups,
duplicate rule IDs, duplicate priorities, invalid threshold signs/types,
invalid bounds, duplicate allocation symbols, weight sums over 1 and missing
fallbacks.

## 28. Error Model

The future domain error taxonomy should include at least:

```text
InvalidStrategy
InvalidCondition
InvalidOperand
InvalidOperator
InvalidThreshold
InvalidWeight
AllocationExceeds100Percent
InvalidAllocationConstraint
MissingFallback
InvalidRule
PriorityConflict
MissingIndicator
UnavailableIndicator
DivisionByZero
InvalidPriceField
InvalidDate
NoRuleMatched
```

Errors should identify the strategy version, rule/condition path and safe
diagnostic context. They must not contain credentials, raw API responses or
unbounded data dumps.

## 29. Frontend Strategy Lab Contract

The future frontend submits structured fields rather than formulas or code:

```text
Asset -> Left (Price / MA / EMA) -> Operator -> Right -> Period
      -> signed relative threshold -> AND / OR -> allocation
      -> min/max -> priority -> fallback -> rebalance
```

For `Price < MA200 - 3%`, the UI may display “Less Than / 3% below”, but it
submits `{"type":"relative","value":-0.03}`. The backend remains the source
of truth for schema validation, evaluation and explanation. Users never enter
Python and the frontend never edits orders or portfolio state.

## 30. Future API Contract

These are planned contracts only:

```text
POST /strategies
GET  /strategies
GET  /strategies/{strategy_id}
POST /strategies/{strategy_id}/versions
GET  /strategies/{strategy_id}/versions
GET  /strategies/{strategy_id}/versions/{version_id}
POST /strategies/{strategy_id}/evaluate
```

The create/version endpoints accept structured JSON and return validation
errors without persistence side effects when invalid. The evaluate endpoint
accepts a version reference, date and data snapshot reference and returns a
Signal; it does not run a backtest or create orders.

## 31. Testing Strategy

This architecture phase defines tests only. Future implementation tests must
cover:

- all five comparison operators;
- signed relative thresholds for Price vs MA, MA vs MA, EMA vs EMA and mixed
  indicator references;
- zero denominator, missing indicator, unavailable window, NaN and Infinity;
- nested AND/OR groups and preservation of child explanations;
- multi-asset QQQ/TQQQ/SGOV allocations;
- minimum/maximum bounds, negative weights and sums above 100%;
- remaining allocation, explicit cash buffer and missing recipient;
- priority ordering, duplicate priorities and fallback behavior;
- complete Signal explainability and target-allocation projection;
- raw/adjusted price-field consistency;
- evaluation at T rejecting any T+1 dependency;
- immutable version snapshots, canonical hashes and reproducibility;
- compatibility projection into PHASE 3 `TargetAllocation` without producing
  Order, Fill or Trade objects.

Synthetic deterministic series are appropriate for mathematical tests. Real
Tiingo integration remains a PHASE 1 data concern and must not be replaced by
fake API responses in Strategy tests.

## 32. PHASE 1-3 Compatibility

### Compatible contracts

- PHASE 1 `HistoricalDataSet` preserves raw and adjusted OHLCV and validates
  dates and values.
- PHASE 2 `PriceField` and `IndicatorSeries.price_field_used` support explicit
  price semantics.
- PHASE 3 accepts date-ordered `TargetAllocation` objects and owns execution,
  order generation, cash, fills, positions, trades and equity.
- PHASE 3 already carries `strategy_version_id`, execution configuration and
  data snapshot references in its result.

### Non-blocking integration gaps

1. **Indicator asset identity**
   `IndicatorSeries` does not contain a symbol. Strategy evaluation must receive
   it through an asset-qualified registry or future adapter; PHASE 2 need not
   be changed for this.

2. **Signal metadata versus backtest projection**
   PHASE 3 `TargetAllocation` contains date and weights, while Strategy V1.0
   `Signal` additionally contains matched rule, condition results, regime and
   explanation. A projection adapter must pass weights into PHASE 3 while
   retaining the full signal in the strategy/backtest run metadata.

3. **Rebalance ownership**
   PHASE 3 already implements rebalance scheduling and threshold behavior.
   Strategy V1.0 stores the policy as versioned configuration but must not
   implement a second order planner.

These are adapter responsibilities, not blocking architecture conflicts. No
PHASE 1-3 business code should be modified during this specification phase.

## 33. Future Optimization Extension

Immutable versions allow future parameter search without mutating history:

```text
Strategy V1 (MA50 + 3%)
Strategy V2 (MA50 + 4%)
Strategy V3 (MA50 + 5%)
        -> separate Backtest Runs
        -> later comparison by Analytics
```

Optimization, parameter search, out-of-sample evaluation and walk-forward
execution are not V1.0 responsibilities. They must reference complete version
snapshots and create new run records rather than altering old results.

## 34. Risks

- look-ahead through indicator alignment or an accidental execution-data
  dependency;
- raw/adjusted price mixing between strategy and backtest;
- ambiguous priority or fallback behavior;
- silently clamped weights or silently distributed remaining cash;
- missing asset identity on indicator series;
- explanations that differ from the actual evaluator;
- floating-point comparison ambiguity for equality and threshold boundaries;
- version hashes that omit a result-affecting field;
- cross-asset data dates that are not synchronized.

## 35. Implementation Plan For PHASE 4

The following is a future implementation sequence only:

```text
Phase 4A  Domain Models
Phase 4B  Canonical JSON schema and validation
Phase 4C  Operand and Condition Evaluator
Phase 4D  Relative Threshold and Deviation support
Phase 4E  Recursive RuleGroup Evaluator
Phase 4F  Allocation Resolver and weight constraints
Phase 4G  Signal and Explainability Engine
Phase 4H  Strategy Versioning and content hashes
Phase 4I  TargetAllocation projection and PHASE 3 integration
Phase 4J  Planned API contracts
Phase 4K  Strategy Lab frontend
```

No item in this plan is implemented by this document-only phase.
