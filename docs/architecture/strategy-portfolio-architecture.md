# Strategy & Portfolio Architecture Proposal

## 1. Architecture Goals

本提案为 PHASE 3 Backtest Engine 之前的架构设计，不实现 Strategy Engine、Portfolio Engine、Backtest Engine 或 Frontend。

架构目标：

- 同时支持单 ETF 策略、多 ETF 组合策略和动态资产配置。
- 将策略定义、策略版本、回测运行和结果明确分离。
- 让策略从市场数据与指标产生目标配置或信号，但不负责订单执行。
- 让回测从目标配置或信号产生订单、成交、持仓、组合权益和交易记录。
- 保证每次回测可通过完整配置快照复现，不依赖未来被修改的策略记录。
- 保留 raw / adjusted price 的统一、可追溯契约。
- 允许未来增加 RSI、MACD、Bollinger、Momentum、Volatility 等条件，而不重写规则核心。
- 为未来的策略版本比较、Optimization、OOS 和 Walk-Forward 预留稳定边界。

本提案不把“最高收益”定义为最佳策略。未来的策略评价应由独立 Performance Analytics 根据多个指标完成。

## 2. Strategy Definition

`StrategyDefinition` 描述“策略是什么”，但不代表一次具体运行。建议包含：

```text
StrategyDefinition
├── strategy_id
├── name
├── strategy_type
├── assets
├── indicators / indicator references
├── rules
├── allocation rules
├── rebalance policy
└── metadata
```

建议字段：

- `strategy_id`：逻辑策略的稳定标识。
- `name`：用户可读名称，例如 `QQQ/TQQQ/SGOV Dynamic Allocation`。
- `strategy_type`：例如 `single_asset_signal`、`multi_asset_allocation`。
- `assets`：策略可使用的资产集合；即使单资产策略也使用集合形式，以避免未来迁移。
- `rules`：条件和规则树，不保存可执行 Python 代码。
- `allocation_rules`：条件到目标仓位的映射。
- `rebalance_policy`：调仓频率、阈值和执行时机。
- `metadata`：描述、标签、创建来源等非计算信息。

策略定义是逻辑对象。修改策略定义不得覆盖历史版本，也不得改变旧回测的解释。

## 3. Strategy Version

`StrategyVersion` 是策略定义在某个时点的不可变快照。每次规则、参数、资产、价格字段或调仓政策发生变化，都创建新版本。

```text
StrategyDefinition
        ├── Version 1
        ├── Version 2
        └── Version 3
```

建议字段：

- `strategy_version_id`：全局唯一标识。
- `strategy_id`：所属逻辑策略。
- `version_number`：同一策略内递增版本号。
- `created_at`：创建时间。
- `definition_snapshot`：完整、序列化、不可变的策略配置。
- `parent_version_id`：可选，用于追踪从哪个版本修改而来。
- `content_hash`：对规范化配置生成的哈希，用于检测内容一致性。
- `status`：例如 `draft`、`active`、`archived`；状态不改变配置内容。

版本号不是配置内容本身。复现必须依赖 `definition_snapshot` 和内容哈希，而不是只依赖 `strategy_id` 或当前最新版本。

## 4. Condition Model

条件必须是结构化数据，而不是前端拼接的 Python 表达式。建议使用可序列化的 AST-like 模型：

```text
Condition
├── left_operand: ValueReference
├── operator: ComparisonOperator
└── right_operand: ValueReference
```

`ValueReference` 统一表示可比较值：

```text
ValueReference
├── kind: price | indicator | constant | portfolio_state
├── asset: optional symbol
├── field: optional price field
├── indicator: optional indicator specification
└── value: optional constant
```

第一阶段至少支持：

- `price > indicator`
- `price < indicator`
- `indicator > indicator`
- `indicator < indicator`
- `cross_above`
- `cross_below`

示例：

```json
{
  "left": {"kind": "price", "asset": "TQQQ", "price_field": "adjusted_close"},
  "operator": "greater_than",
  "right": {
    "kind": "indicator",
    "asset": "TQQQ",
    "indicator": {"type": "ma", "period": 50, "price_field": "adjusted_close"}
  }
}
```

条件求值必须使用与当前日期对应的输入值。尚未形成完整窗口的指标值为 `None`，不得被当作满足条件的数值。

未来新增 RSI、MACD、Bollinger、Volatility、Drawdown、Momentum 或 Relative Strength 时，只需新增可识别的 `indicator` 类型和对应计算器，不改变 `Condition`、`RuleGroup` 或 Allocation 的基本结构。

## 5. Rule Group

条件组合使用显式的递归规则组：

```text
RuleGroup
├── operator: AND | OR
└── children: Condition | RuleGroup
```

示例：

```text
AND
├── TQQQ Price > TQQQ MA50
└── TQQQ MA20 > TQQQ MA50
```

或：

```text
OR
├── QQQ Price > QQQ MA200
└── QQQ MA20 > QQQ MA50
```

建议规则组语义：

- `AND`：所有子项都必须为 true。
- `OR`：至少一个子项为 true。
- 空规则组非法，避免产生含义不清的默认 true 或 false。
- `None`、缺失或尚未可计算的条件值默认不满足条件，并在求值结果中保留原因。
- 前端只提交结构化规则树；后端负责 schema 校验、求值和错误报告。

## 6. Allocation Rule

`AllocationRule` 把条件结果映射为资产目标权重：

```text
AllocationRule
├── rule_id
├── condition: optional Condition | RuleGroup
├── target_asset
├── target_weight
├── minimum_weight
├── maximum_weight
├── priority
└── conflict_policy
```

应支持以下类型：

- **Unconditional allocation**：无条件的基础配置，例如 SGOV 20%。
- **Conditional allocation**：条件满足时设置目标仓位，例如 TQQQ 50%。
- **Remaining allocation**：将校验后的剩余资金分配给指定资产或资产组。
- **Fallback allocation**：没有条件规则满足时使用的明确配置。

推荐求值顺序：

1. 校验资产集合、权重范围和规则结构。
2. 计算所有条件，不产生订单。
3. 按 `priority` 从高到低选择或合并规则。
4. 解析固定权重和条件权重。
5. 计算 `Remaining Allocation`。
6. 应用 minimum / maximum weight。
7. 执行最终权重归一化和精度校验。
8. 生成不可变的 `TargetAllocation`，同时保存命中的规则和求值原因。

默认约束：

- 目标权重范围为 `[0, 1]`，API 展示可使用百分比。
- 最终可投资目标权重总和必须为 `1.0`，除非策略明确声明保留 cash buffer。
- `cash_buffer` 单独建模，不把未解释的剩余权重静默丢失。
- 同一资产的 minimum weight 不得大于 maximum weight。
- `Remaining Allocation` 最多只能有一个明确接收者，或必须指定优先级分配方式。
- 无规则满足时必须使用 fallback；没有 fallback 时返回明确的配置错误，不默认全仓任一资产。

## 7. Target Allocation

`TargetAllocation` 是 Strategy Engine 对 Backtest Engine 的主要组合输出：

```text
TargetAllocation
├── as_of_date
├── strategy_version_id
├── price_field_used
├── weights: AssetTarget[]
├── cash_buffer
├── triggered_rule_ids
└── evaluation_metadata
```

每个 `AssetTarget` 至少包含：

- `symbol`
- `target_weight`
- `minimum_weight`
- `maximum_weight`
- `reason` 或命中的规则 ID

示例：

```text
2026-01-15
QQQ   0.30
TQQQ  0.50
SGOV  0.20
Cash  0.00
```

下一日期可以产生完全不同的目标配置。目标配置不是实际持仓，不能直接当作已成交结果。

## 8. Rebalance Policy

`RebalancePolicy` 描述何时将目标配置转换为调仓请求，当前只设计接口，不实现：

```text
RebalancePolicy
├── frequency: daily | weekly | monthly | on_signal_change
├── threshold: optional weight difference
├── reference: current_weight | target_weight
├── timing: evaluation timestamp / evaluation date
└── minimum_trade_value: optional
```

支持的策略：

- `daily`：每个可交易日评估。
- `weekly`：按明确的周边界评估，例如每周第一个或最后一个交易日。
- `monthly`：按明确的月边界评估。
- `on_signal_change`：只有目标配置或信号状态变化才评估调仓。
- `threshold_based`：只有目标权重与当前权重差异达到阈值才调仓。

阈值示例：

```text
Target QQQ = 40%
Current QQQ = 37%
Difference = 3%
Threshold = 5%
Result = no rebalance
```

必须明确阈值的比较方式。推荐使用绝对权重差：

```text
abs(target_weight - current_weight) >= threshold
```

调仓规则应对每个资产分别计算差异，并在组合层考虑交易成本、现金可用性和最小交易金额。Rebalance Engine 输出调仓意图或订单请求，不负责模拟成交。

## 9. Portfolio Model

组合模型必须能同时表达现金和多个资产：

```text
Portfolio
├── portfolio_id
├── base_currency
├── initial_capital
├── cash
├── positions: Position[]
└── accounting_policy
```

核心恒等式：

```text
Portfolio Equity = Cash + Σ Asset Market Value
Asset Market Value = Quantity × Execution/Valuation Price
```

`Position` 至少包含：

- `symbol`
- `quantity`
- `average_cost`
- `market_value`
- `weight`
- `price_field_used`
- `as_of_date`

组合必须支持：

- 多资产同时持仓。
- 买入、卖出和调仓。
- commission 和 slippage。
- 资金不足、现金缓冲和交易金额约束。
- Fractional Shares 的明确开关，而不是隐式支持。
- 权重舍入规则和剩余现金处理。
- 最小交易金额。

推荐 PHASE 3 初始策略：默认不支持 fractional shares，使用整数股；通过明确的 rounding policy 将无法投资的零头保留为 cash。未来可为每个市场或账户启用 fractional shares。

现金不足时不得扩大杠杆或静默产生负现金。默认应拒绝该订单计划并报告资金不足；若未来支持融资或杠杆，必须作为独立 accounting policy 显式启用。

## 10. Signal Contract

单资产策略可以输出 `Signal`，但 Signal 不是订单：

```text
Signal
├── as_of_date
├── strategy_version_id
├── symbol
├── action: buy | sell | hold
├── strength: optional
├── price_field_used
├── reason / triggered_rule_ids
└── source_data_timestamp
```

Signal Contract 的职责是表达策略判断。它不包含成交数量、成交价格或成交结果。

多资产策略优先输出 `TargetAllocation`；如果需要兼容单资产流程，可以由 Allocation Adapter 将单资产目标仓位转换为 Signal，但不应让单资产 Signal 反向定义组合会计。

## 11. Order Contract

Order 由 Backtest 或 Rebalance 编排层根据目标配置/信号和当前组合生成：

```text
OrderRequest
├── order_id
├── as_of_date
├── symbol
├── side: buy | sell
├── quantity
├── target_weight: optional
├── reason
└── execution_rule
```

```text
OrderFill
├── order_id
├── fill_date
├── quantity
├── fill_price
├── commission
├── slippage
└── total_cash_effect
```

Strategy Engine 不创建成交记录，也不决定最终成交数量。Backtest Engine 根据当前持仓、目标权重、可用现金和 execution rule 生成并执行订单。

## 12. Backtest Contract

Backtest 的输入和输出应明确分离：

```text
BacktestInput
├── strategy_version_snapshot
├── asset data sets
├── indicator configuration
├── initial capital
├── commission policy
├── slippage policy
├── execution rule
├── rebalance policy
├── start date
└── end date
```

```text
BacktestOutput
├── portfolio equity series
├── positions history
├── allocation history
├── orders
├── fills
├── trades
└── run metadata
```

Backtest 必须逐日期推进，确保策略在日期 `T` 只能读取截至 `T` 可用的市场数据和指标值。目标配置生成与订单执行之间必须遵守执行规则，不允许把收盘信号直接当作同一收盘成交，除非 execution rule 明确允许。

## 13. Backtest Run

`BacktestRun` 是一次可复现的执行实例，与 `StrategyVersion` 是多对一关系：

```text
Strategy Version v3
        ├── Backtest Run #102
        └── Backtest Run #118
```

每次运行必须保存：

- `backtest_run_id`
- `strategy_version_id`
- `strategy_configuration_snapshot`
- `assets`
- `indicator_parameters`
- `price_field_used`
- `initial_capital`
- `commission`
- `slippage`
- `execution_rule`
- `rebalance_policy`
- `start_date`
- `end_date`
- `data source / data snapshot references`
- `engine_version`
- `created_at`

不能只保存 Strategy ID。即使策略后来创建 v4，v3 的旧运行也必须能按原始配置和数据引用复现。

## 14. Strategy Engine / Backtest Engine Boundary

### Strategy Engine

```text
Market Data + Indicators + Strategy Version
                    ↓
       Signal / Target Allocation
```

Strategy Engine 负责：

- 在指定日期求值条件和规则。
- 使用明确的 price field 和指标配置。
- 产生 Signal、Target Allocation 和求值解释。
- 不访问或修改 Portfolio 现金、持仓或成交状态。
- 不计算订单数量，不模拟 commission、slippage 或 fill。

### Backtest Engine

```text
Signal / Target Allocation
            ↓
          Orders
            ↓
        Execution
            ↓
         Positions
            ↓
      Portfolio Equity
            ↓
          Trades
```

Backtest Engine 负责：

- 按日期推进回测时钟。
- 管理订单、执行规则、成交价格、commission 和 slippage。
- 根据目标配置计算调仓数量。
- 更新现金、持仓、组合权益和交易记录。

### Portfolio/Rebalance 的两种方案

方案 A：Portfolio/Rebalance 作为 Backtest Engine 内部模块。

- 优点：PHASE 3 初期实现路径短，状态管理集中，避免过早形成跨模块公共 API。
- 缺点：未来实时组合管理或独立调仓服务复用时需要拆分。

方案 B：Portfolio/Rebalance 作为独立领域模块，由 Strategy 和 Backtest 共同调用。

- 优点：目标配置、持仓会计和调仓逻辑边界清晰，未来实时或纸面交易更容易复用。
- 缺点：需要更早定义 Position、Order、Fill 和状态快照契约，初期实现成本更高。

推荐：采用“独立领域边界、分阶段实现”的方案。目录和接口按方案 B 设计，但 PHASE 3 由 Backtest Orchestrator 统一调用 Portfolio Accounting 和 Rebalance Planner。这样不把组合逻辑写进 Strategy，也不在 PHASE 3 初期引入独立服务或复杂基础设施。

## 15. Price Field Contract

继承 PHASE 1 / PHASE 2 的 `PriceField`：

```text
RAW_CLOSE
ADJUSTED_CLOSE
```

统一原则：

- Strategy 的价格条件必须记录 price field。
- Indicator 的 `IndicatorSeries` 已记录 `price_field_used`。
- Backtest Run 必须记录实际使用的 price field。
- Allocation 和 Rebalance 的估值价格必须使用同一口径，或明确记录不同用途的字段。
- 默认不允许 Indicator 使用 adjusted、Execution 却隐式使用 raw 的混合模式。
- 如果未来确实需要“信号使用 adjusted，成交使用 raw”，必须将其建模为显式的 `signal_price_field` 与 `execution_price_field`，并在 Backtest Run 中记录原因；PHASE 3 初期不启用该混合模式。

推荐 PHASE 3 初期对一轮 Backtest 强制单一 `price_field_used`，所有信号、估值和执行都使用相同价格契约。数据集仍保留 raw 与 adjusted 字段，以便未来显式扩展。

## 16. Execution Rule Contract

Execution Rule 独立于 Strategy：

```text
ExecutionRule
├── signal_timing: close | open | other
├── execution_timing: same_close | next_open | next_close
└── missing_price_policy
```

PHASE 3 首选且唯一实现模式：

```text
Signal at T Close -> Execution at T+1 Open
```

未来可以扩展：

- `T Close -> T Close`
- `T Close -> T+1 Close`

但每种模式必须明确可用数据边界、缺失价格处理和交易日历规则。Strategy Engine 只产生截至 T 的输出，不负责选择成交价格。

## 17. Frontend -> Backend 数据流

未来 Strategy Lab 的数据流：

```text
Frontend Form
    ↓ structured JSON configuration
Backend Schema Validation
    ↓
Strategy Definition Draft
    ↓ user confirms save
Strategy Version Snapshot
    ↓
Backtest Request
    ↓
Backtest Run
    ↓
Backtest Result
```

前端提交结构化配置，包括：

- Portfolio 名称。
- Assets：QQQ、TQQQ、SGOV。
- 每个 Asset 的 price field、minimum / maximum weight 和优先级。
- Condition / RuleGroup 树。
- Allocation Rules 和 fallback。
- Rebalance Policy。
- Initial Capital、commission、slippage、执行规则和日期范围。

前端不得拼接 Python、执行策略代码或直接修改持仓。后端必须返回 schema 错误、权重冲突、条件不可用和数据不足等明确错误。

## 18. Strategy -> Allocation -> Rebalance -> Backtest Data Flow

```text
HistoricalDataSet
        +
IndicatorSeries
        +
StrategyVersion Snapshot
        ↓
Condition Evaluation
        ↓
RuleGroup Evaluation
        ↓
TargetAllocation
        ↓
RebalancePolicy Evaluation
        + Current Portfolio State
        ↓
OrderRequest Plan
        ↓
ExecutionRule
        ↓
OrderFill
        ↓
Positions / Cash / Portfolio Equity
        ↓
Allocation History / Trade History
```

每一步都应产生可审计的中间结果，至少包括日期、输入配置版本、price field、触发规则和结果状态。

## 19. Versioning & Reproducibility

可复现性需要同时固定四类内容：

1. **Strategy**：完整 `strategy_configuration_snapshot` 和 content hash。
2. **Backtest parameters**：资金、费用、滑点、执行规则、调仓策略和日期范围。
3. **Data**：数据源、请求范围、schema version、缓存或数据快照引用。
4. **Engine**：计算引擎版本和指标参数。

修改任何影响结果的内容都必须创建新 Strategy Version 或新 Backtest Run。旧版本和旧运行只读，禁止覆盖。

建议所有序列化配置使用稳定字段顺序和规范化格式生成 hash。展示名称、标签等非计算字段的变化可以不影响内容 hash，但必须记录变更历史。

## 20. Future Optimization / OOS / Walk-Forward Extension

未来扩展应建立在不可变 Strategy Version 和 Backtest Run 之上：

- **Optimization**：输入参数空间，输出多个候选 Strategy Version / Backtest Run，不覆盖原策略。
- **In-Sample / Out-of-Sample**：在 Backtest Run 中保存数据区间角色和分割边界。
- **Walk-Forward**：保存每个训练窗口、验证窗口、参数选择结果和滚动生成的版本引用。
- **Strategy Comparison**：比较多个 Backtest Run 的 Total Return、CAGR、Max Drawdown、Sharpe、Sortino、Calmar、Volatility、Win Rate、Trades、Recovery 和 Stability。

“最佳策略”必须是带评价目标的比较结果，不应写成永远选择最高 Total Return 的固定规则。

## 21. Risks

- **Look-ahead bias**：条件求值、指标窗口和 T+1 执行边界必须逐日期测试。
- **Raw / adjusted 混用**：所有策略、指标、估值和运行配置都必须保存 price field。
- **权重冲突**：多条规则同时满足时，如果没有确定的优先级或合并策略，必须拒绝配置。
- **权重总和不一致**：剩余权重、cash buffer 和舍入误差必须显式处理。
- **现金不足**：不得静默产生负现金或隐式杠杆。
- **交易日历和缺失价格**：执行规则需要明确下一交易日和缺失数据行为。
- **分红、拆分和杠杆 ETF**：必须继续依赖 PHASE 1 的 adjusted/raw 数据语义，不把 adjusted price 当作真实成交价的无条件替代。
- **版本漂移**：旧回测必须保存策略快照、参数和数据引用，不能依赖最新策略记录。
- **规则解释性**：每次目标配置应保存触发规则和未满足原因，便于研究和审计。

## 22. Migration Impact

PHASE 1 / PHASE 2 不需要重构：

- `HistoricalDataSet` 已同时保留 raw 与 adjusted OHLCV。
- `PriceField` 已提供显式价格字段枚举。
- `IndicatorSeries` 已保存 `price_field_used`。
- 现有数据验证已能阻止空数据、乱序日期、重复日期和非法值。

后续 PHASE 3 只需新增适配层，将 `HistoricalDataSet` 和 `IndicatorSeries` 传入 Strategy/Portfolio 领域对象。不得让 Strategy 直接访问 Tiingo Client，也不得把策略状态写回 Data Engine。

本阶段不创建 `strategies/`、`portfolio/` 或 `backtest/` 业务实现文件，避免在架构审查阶段提前实现核心功能。

## 23. Recommended Module Structure

推荐采用清晰的领域边界：

```text
data/
    models.py
    service.py
    validation.py
    cache.py

indicators/
    models.py
    moving_average.py
    exponential_moving_average.py

strategies/
    definitions/
        models.py              # StrategyDefinition / AssetSpec
        schemas.py             # 可序列化配置校验
    conditions/
        models.py              # Condition / ValueReference / operators
        evaluator.py           # 条件求值
    rules/
        groups.py              # RuleGroup
        allocation.py          # AllocationRule
        evaluator.py           # 规则优先级和冲突处理
    signals/
        models.py              # Signal / TargetAllocation
    versions/
        models.py              # StrategyVersion / snapshot / hash

portfolio/
    models.py                  # Portfolio / Position / Cash
    allocation/
        models.py              # TargetAllocation / weight constraints
        resolver.py             # 最终权重解析
    rebalance/
        models.py              # RebalancePolicy / RebalancePlan
        planner.py              # 当前持仓到目标配置
    accounting/
        ledger.py               # cash / positions / market value

backtest/
    models.py                  # BacktestInput / Output / Run
    orders/
        models.py
        planner.py
    execution/
        models.py               # ExecutionRule / OrderFill
        engine.py
    portfolio/
        orchestrator.py         # 组合回测编排
    trades/
        models.py
        recorder.py

analytics/
    # PHASE 5：指标、比较、稳定性和风险分析
```

实现顺序建议：

1. 先定义不可变模型和序列化契约。
2. 再实现条件求值和规则组。
3. 再实现目标配置和权重校验。
4. 再实现 Portfolio accounting 与 Rebalance planner。
5. 最后由 Backtest orchestrator 连接执行、持仓和交易记录。

## 24. PHASE 3 Impact

PHASE 3 应优先实现最小但完整的执行闭环：

- `Signal` 和 `TargetAllocation` 的输入契约。
- `OrderRequest`、`OrderFill`、`Position`、`Portfolio` 和 `Trade`。
- `Signal at T Close -> T+1 Open`。
- commission、slippage、整数股、现金不足拒绝和明确的 rounding policy。
- 单资产与多资产都通过组合模型运行。
- 每次 Backtest Run 保存 Strategy Version 快照和完整参数。

PHASE 3 不应实现 Optimization、OOS、Walk-Forward、Frontend 或 Performance Analytics。上述内容保留到后续阶段。
