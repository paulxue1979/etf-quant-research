# PHASE 8F Controlled OOS Evaluation Preflight

## 1. Purpose

PHASE 8F 只定义冻结研究协议上的一次受控 Out-of-Sample（OOS）观察架构。本阶段不运行 OOS、不新增 production code、不改变 PHASE 7 selection/freeze、不进行重新选择，也不提供任何“最佳策略”或推荐结论。

核心原则是：OOS 是在候选选择和策略冻结完成之后打开的一次性 holdout observation。OOS 结果只能描述事实，不能反向改变选择、参数、协议或策略版本。

## 2. Baseline

只读基线核验结果：

- workspace：`/Users/gaozhiyong/Projects/etf-quant-research`
- branch：`main`
- HEAD：`8a167d00d62b1fd671aac5a53f15d42db7743a7a`
- HEAD message：`test: close phase 8e experiment research integrity gate`
- `git show --check HEAD`：PASS
- working tree：clean

本次 Architecture Review 仅新增本文件，不修改 production code、测试、schema、migration、API 或 frontend。

## 3. Current Architecture Inventory

### 3.1 Research Protocol

`backend/app/research_protocol.py` 已提供不可变 `ResearchProtocol`、严格 IS/OOS 日期分离、evaluation configuration snapshot、协议状态与 append-only lifecycle event。当前状态链为：

```text
DRAFT → FROZEN → IS_EVALUATED → SELECTION_RECORDED → OOS_EVALUATED → CLOSED
```

协议构造时已强制 IS 与 OOS 不重叠，并应用 `gap_days` / `embargo_days` 分隔规则。

### 3.2 Selection and Freeze

`SelectionDecision` 只接受 IS BacktestRun，PHASE 7 protocol-level selection 由数据库 `UNIQUE(protocol_id)` 约束为每个 protocol 至多一条。`StrategyFreezeRecord` 绑定 selection decision、精确 `strategy_version_id` 与 content hash。

### 3.3 Exact Materialized StrategyVersion

`StrategyRepository.persist_exact_strategy_version()` 与 `get_any_version()` 支持 PHASE 8D-1 的精确 derived StrategyVersion 持久化。`materialization_provenance`、derived id、content hash、base version id/hash 和 parameter set hash 可被重新验证。

### 3.4 Existing experiment integrity

PHASE 8D/8E 已提供 candidate execution、immutable ExperimentResult、atomic result finalization、selection handoff、read model、compatibility/objective diagnostics 与 PHASE 8E-1I end-to-end integrity audit。ExperimentResult 通过 `backtest_run_id` 引用详细回测事实。

### 3.5 Strategy Evaluation / Signal / Backtest / Analytics

现有调用边界为：

```text
StrategyEvaluation
    → StrategySignal
    → StrategyBacktestResult
    → BacktestEngine
    → PerformanceAnalytics
```

`backtest/integration.py` 的 `run_strategy_backtest()` 负责连接 PHASE 4G strategy evaluation 与 PHASE 3 backtest；`BacktestEngine` 只接受 target allocations，不重复执行策略条件。

`BacktestEngine` 当前强制：

```text
Signal(T) close
    → next trading day open execution
```

`BacktestConfig` 会固化 price field、日期、initial capital、成本、slippage、execution rule、rebalance policy、engine version 与 data reference。

### 3.6 Data service and cache

`HistoricalDataService` 通过 `DiskCache` 或 Tiingo 提供已验证的 `HistoricalDataSet`。Disk Cache 使用 request-specific key 与 atomic JSON replace；当前 cache identity 包括 symbol、日期范围、frequency、price field 和 schema version，并记录 source/request/rows，但不是完整 immutable dataset snapshot system。

## 4. Reusable Components

PHASE 8F 应复用：

1. `ResearchProtocol`、`ProtocolStatus`、`ResearchEvaluationConfig`。
2. `SelectionDecision` 与 `StrategyFreezeRecord`。
3. `StrategyRepository.get_any_version()` 的 exact persisted derived StrategyVersion 验证。
4. `BacktestConfig` 的冻结配置语义。
5. 现有 Indicator Engine、Strategy Evaluation、Signal Engine。
6. `run_strategy_backtest()` 与 PHASE 3 `BacktestEngine`。
7. PHASE 4I Performance Analytics。
8. `BacktestRepository` 作为 BacktestRun 持久化边界。
9. 现有 research repository 的 SQLite transaction、append-only event 与 unique-index 模式。
10. `HistoricalDataService` / `DiskCache` 的请求验证与数据质量验证。
11. 现有 structured exception / API error mapping conventions。

## 5. Existing Components Must Not Be Reused Incorrectly

以下现有能力不能直接作为完整 OOS finalization：

- `transition_protocol()` 目前是通用状态转换，虽能阻止无 observation 的 `OOS_EVALUATED`，但不会把 OOS result、observation、状态和 lifecycle event 放在同一最终化事务中。
- `create_oos_evaluation()` 能校验 freeze、strategy、日期与 frozen config，并利用 `UNIQUE(protocol_id)` 防重复，但目前先单独写 observation，再由 caller 另行推进 protocol 状态；它不是完整的 `finalize_oos_observation()`。
- PHASE 8D `ExperimentResultFinalizationService` 的 IS candidate result 语义不能直接冒充 official OOS one-shot observation。
- 普通 strategy/version create 或 rematerialization 不能替代 freeze 指向的 exact persisted version。
- BacktestEngine 的 accounting、order、fill、portfolio 与 analytics 不得在 OOS service 中复制。
- 现有 cache 的 request identity 不能被误称为 dataset version/checksum framework。

## 6. Missing Components and Integrity Gaps

未来实现需要最小新增：

1. OOS-specific domain/provenance model。
2. protocol-scoped one-shot observation / result persistence。
3. OOS execution claim 或等价 lease，避免昂贵计算重复运行。
4. atomic finalization operation。
5. server-side frozen input resolution，禁止 caller override。
6. partial-result suppression 与 structured failure handling。
7. OOS-specific API；frontend 在后续 subphase 实现。
8. 针对并发、重启、冲突、数据边界和 no-lookahead 的测试矩阵。

当前最高风险 gap 是 OOS finalization 尚未实现为一个原子 operation；现有 OOS repository checks 是必要防线，但不足以证明端到端的一次性观察语义。

## 7. OOS Preconditions

未来 `start_oos_evaluation(protocol_id)` 必须在任何 OOS 数据请求前，以 server-side read-only validation 检查全部条件：

| 条件 | 必须满足的事实 |
|---|---|
| A | protocol 存在 |
| B | protocol.status = `SELECTION_RECORDED` |
| C | 存在唯一 official PHASE 7 `SelectionDecision` |
| D | decision.protocol_id 与 protocol 相同 |
| E | 存在唯一 `StrategyFreezeRecord` |
| F | freeze.strategy_version_id = decision.selected_strategy_version_id |
| G | freeze.strategy_version_content_hash = selected strategy hash |
| H | freeze 指向的 exact persisted StrategyVersion 可按 id 读取 |
| I | StrategyVersion content hash、materialization provenance 与 derived id 可重算且一致 |
| J | protocol 已冻结 OOS range |
| K | OOS start <= OOS end |
| L | IS end < OOS start，并满足 gap/embargo 规则 |
| M | 当前 protocol 没有 OOS observation/result/consumed marker |
| N | frozen evaluation config 存在且完整 |
| O | OOS contract 允许的 config 与 protocol snapshot 一致 |

任何一项失败都必须在下载数据之前返回结构化错误；不得改变 protocol status、创建 BacktestRun 或写 OOS result。

## 8. One-Shot Observation Principle

一个 protocol 只能拥有一次 official OOS observation。OOS 一旦形成可读取的 performance result，即被视为 consumed/observed，不得恢复为 untouched。

禁止：

- 删除、替换或覆盖 OOS result；
- 修改 OOS 日期、策略、参数、成本、price field、execution rule 或 rebalance policy；
- 用另一 candidate 覆盖结果；
- 看到 OOS 后重新选择、修改 PHASE 7 decision 或重新冻结；
- 将同一个 protocol 重新打开为未观察状态。

如果研究设计或策略需要改变，必须新建 protocol 和 research lineage。

技术失败只有在没有向研究者暴露任何 performance evidence、且没有 official observation/result 的前提下，才允许 retry。

## 9. Frozen Strategy Identity

OOS 必须读取并使用 freeze 指向的 exact persisted version，不得从 mutable StrategyDefinition 重建、rematerialize、复制为新 id 或 fallback 到 latest version。

必须验证完整 identity chain：

```text
SelectionDecision.strategy_version_id
  == StrategyFreezeRecord.strategy_version_id
  == persisted derived StrategyVersion.version_id
  == OOS evaluation.strategy_version_id
  == BacktestRun.strategy_version_id
```

同时验证：

```text
freeze.strategy_version_content_hash
  == persisted version.content_hash
  == BacktestRun.strategy_version_content_hash
  == OOS result strategy_content_hash
```

任何不一致都返回 `OOS_STRATEGY_IDENTITY_MISMATCH`，停止执行，不创建 official result。

## 10. Frozen OOS Range

日期只从 protocol frozen definition 解析，caller 不得覆盖。未来 OOS evaluate request body 应为空，或最多包含不影响执行的 explicit confirmation metadata；不得接受 `oos_start` / `oos_end` 作为执行参数。

日期语义采用当前 BacktestConfig 的 inclusive `start_date` / `end_date`。因此：

```text
IS end < OOS start <= OOS end
```

若 OOS start 是交易日，OOS start 日收盘可以参与当日策略 evaluation，但最早交易执行仍为下一交易日 open。OOS end 日产生的 signal 若无区间内下一交易日，不应在区间外执行；应保持现有 integration 的“无 next trading day 则不提交”语义。

## 11. OOS Data Access Firewall

OOS evaluation 只允许请求：

```text
必要的 pre-OOS warmup
+ frozen OOS [oos_start, oos_end]
```

不得请求 OOS end 之后、第二个 OOS range、selection 后扩大的日期范围，或由 caller 提供的替代 range。service 必须根据 strategy indicators、assets、frequency 和 price field 计算请求边界，并再次核对返回 dataset 的 request identity。

OOS performance、trades、positions、equity 和 analytics 的统计起点必须是 frozen OOS start；warmup rows 不能进入统计区间。

## 12. Warmup Design

MA/EMA 等指标所需的历史数据可以早于 OOS start。建议 service 显式记录：

```text
warmup_start
warmup_end = day before oos_start (or actual last pre-OOS row)
evaluation_start = oos_start
evaluation_end = oos_end
```

Warmup 只进入 indicator history，不进入 OOS BacktestConfig 的 performance range，不改变 OOS initial capital，也不能生成 selection evidence。

OOS strategy evaluation 应在 OOS boundary 重新初始化：不继承 IS 的 signal、pending order、position 或 portfolio ledger。这样避免一个 IS close signal 在 OOS start open 偷渡执行。OOS start 前产生的 signal 不得提交为 OOS execution；OOS 内 signal 继续遵循 `Signal(T) → T+1 trading day open`。

## 13. Portfolio Initialization Decision

现有 PHASE 3 `BacktestEngine.run()` 每次以 `BacktestConfig.initial_capital` 创建新的 cash ledger，并没有 carry-forward IS portfolio 接口。因此 V1 正式采用：

```text
Fresh Capital OOS Evaluation
```

OOS 从 frozen initial capital、空持仓、全现金开始。理由：

- 隔离 OOS strategy performance；
- 不依赖 IS 的 path-dependent terminal positions；
- 与现有 BacktestEngine 语义一致；
- 更容易重试和确定性复现。

这不是修改 PHASE 3 accounting，而是对 OOS 调用现有 engine 的输入语义作明确约束。Carry-forward portfolio 若未来需要，必须另建研究协议字段和独立设计，不在 PHASE 8F 临时实现。

## 14. No-Lookahead Boundary

严格保持：

```text
Signal(T close) → T+1 trading-day Open
```

禁止使用 OOS start 日 close 在同一日 open 执行，也禁止让 IS/pre-OOS signal 在 OOS start open 隐式执行。OOS 的 performance run 应从 OOS range 内产生的 signal 开始。

由于 PHASE 3 engine 需要至少两个 trading dates 才能处理 next-open execution，OOS 数据必须包含至少两个共同交易日；若不足，应标记 execution failure/NOT_EVALUABLE，不扩展统计区间到 OOS 之外。

## 15. Frozen Evaluation Configuration

OOS 只能使用 protocol frozen `ResearchEvaluationConfig`，并转换为现有 `BacktestConfig`。至少锁定：

- `price_field_used`
- `initial_capital`
- commission rate 与 per-order commission
- slippage
- `execution_rule = next_trading_day_open`
- `fractional_shares = false`
- rebalance frequency 与 threshold
- `engine_version`
- analytics version（OOS result provenance 中记录）

caller 不得覆盖任一 result-affecting field。转换后的 BacktestConfig 的 strategy version、dates、data reference 由 server 补全；不能把 caller payload 当作 frozen identity source。

## 16. OOS Domain Model Proposal

建议将执行事实与观察事实分开：

### OosEvaluationRun

表示一次技术执行尝试，建议字段：

```text
execution_id
protocol_id
selection_decision_id
strategy_freeze_id
strategy_version_id
status: PENDING | RUNNING | COMPLETED | FAILED | BLOCKED
attempt_count
lease_owner / lease_expires_at (如果启用 claim)
frozen_input_hash
failure_code (nullable)
created_at / started_at / completed_at
```

该对象可以记录失败与重试，但不得保存 partial performance evidence。

### OosObservationRecord / OosEvaluationResult

表示唯一 official 观察，建议字段：

```text
oos_result_id
protocol_id                 UNIQUE
selection_decision_id
strategy_freeze_id
strategy_version_id
strategy_content_hash
backtest_run_id             UNIQUE reference
is_start / is_end (if echoed)
oos_start / oos_end
warmup_start / warmup_end
price_field_used
configuration_hash
engine_version
analytics_version
data_provenance
performance_summary
result_hash
created_at
```

为避免重复概念，V1 可将 ObservationRecord 与 Result 放入同一 immutable row，同时用 execution row 表示失败/retry。关键是 protocol-level unique official result 和不可变的 consumed marker。

## 17. Persistence Proposal

最小数据库扩展：

1. `oos_evaluation_runs`：可记录技术执行状态、attempt、lease 和失败代码；允许多个失败尝试，但不产生 official observation。
2. `oos_evaluation_results`：只允许每 protocol 一条 official result，使用 `UNIQUE(protocol_id)`；`result_hash` 与 canonical payload 共同保护内容。
3. `research_protocol_events`：沿用 append-only lifecycle events。

所有 result/provenance JSON 必须使用 canonical JSON：sorted keys、固定 separators、`allow_nan=False`，仅允许 finite numbers。结果 hash 不包含 `created_at`、lease、worker id、runtime trace 等 transient fields。

不得删除或更新已持久化的 official result；不同 payload 的同 protocol 重试必须返回 `OOS_OBSERVATION_CONFLICT`。相同 immutable identity 的 retry 应返回既有 `oos_result_id`、`backtest_run_id` 与 `result_hash`。

## 18. BacktestRun Source of Truth

BacktestRun 是详细 OOS 会计事实源，OOS result 只保存 identity、provenance、summary 与 reference。不得在 OOS result 中复制：

- full equity curve；
- orders / fills；
- trades；
- positions；
- portfolio snapshots；
- 另一套 accounting fields。

OOS detail 查询必须通过 `backtest_run_id` 进入既有 BacktestRepository / read model。

## 19. Execution / Claim Model

OOS 计算和网络/指标/回测/analytics 不得放入长 SQLite transaction。建议三阶段：

```text
A. short transaction: validate + claim execution
B. outside transaction: data fetch, warmup, evaluation, signal, backtest, analytics
C. short transaction: immutable finalization
```

因为 OOS 计算昂贵且一次性，V1 建议 explicit claim/lease。若同一 protocol 已有未过期 RUNNING claim，返回 `OOS_EXECUTION_IN_PROGRESS`；过期 claim 可由 restart recovery worker 标记为可重试，但不能自动打开 OOS 或产生结果。

即使 claim 出现 race，最终仍必须依赖 DB unique(protocol_id)；lease 不是唯一正确性防线。

## 20. Atomic Finalization

未来 `finalize_oos_observation()` 的最终化事务应在同一个 SQLite transaction 内完成：

1. 重新读取 protocol、selection、freeze 与 exact strategy identity；
2. 检查 protocol 仍为 `SELECTION_RECORDED`；
3. 检查 OOS range、warmup metadata、config、data provenance 与 result hash；
4. 确认 BacktestRun 已持久化且 identity 一致；
5. 插入 immutable OOS result；
6. 写 consumed/observed marker（可由 result row 充当）；
7. 将 protocol transition 到 `OOS_EVALUATED`；
8. append `OOS_RESULT_RECORDED` / `OOS_EVALUATED` 最小 lifecycle event；
9. commit。

理想顺序是 result、marker、state、event 同一 commit。不得先把 protocol 置为 OOS_EVALUATED 再写 result，也不得把 result 写成 official 后长期不更新 protocol state。

BacktestRun 的重型写入可以在 finalization 之前独立提交，因为计算不能长时间占用 SQLite。为处理 crash：finalizer 必须按 deterministic run identity 检查并复用已存在的 BacktestRun；若 BacktestRun 存在但其 identity/config/hash 不匹配，则停止并报告 integrity conflict，而不是覆盖。

## 21. Failure Semantics

必须区分：

- `Execution Failure`：DNS、timeout、数据缺失、process crash、invalid dataset、backtest error 等技术失败；protocol 保持 `SELECTION_RECORDED`，没有 official observation。
- `Observed OOS Failure`：只有在产生可读的 official performance result 后才可能发生，此时 observation 已 consumed，不能 retry 为另一结果。
- `BLOCKED`：环境或数据条件阻止执行且没有任何 performance evidence；不等于 OOS 已被观察。

任何 failure response 只返回 code、message、execution status、attempt metadata，不返回 partial return、Sharpe、drawdown、trades 或 equity。

## 22. Partial Result Leakage

在 finalization 前不得通过 API、streaming response、logs、progress events 或 frontend state 暴露：

- partial return；
- partial equity curve；
- Sharpe / Sortino / drawdown；
- partial trades、fills 或 positions。

可显示的最小状态是 `PENDING`、`RUNNING`、`FAILED`、`BLOCKED`。日志也不得包含 credentials、authorization header、完整 result payload 或 sensitive filesystem details。

## 23. Retry Semantics

技术失败可 retry，但必须同时满足：

- 没有 official OOS result；
- 没有 consumed/observed marker；
- protocol 仍是 `SELECTION_RECORDED`；
- server 重新解析同一个 freeze、OOS range 与 frozen config；
- caller 不能改变 strategy、参数、日期、费用或 price field；
- 未向研究者暴露 partial performance evidence。

同一成功结果 retry 是 idempotent；不同 result identity 在同一 protocol 下是 conflict。OOS 一旦进入 `OOS_EVALUATED`，任何再执行请求均应为 already observed/closed，而不是新 attempt。

## 24. Idempotency

建议同时使用：

- execution identity：protocol + frozen input hash；
- caller/request idempotency key（只用于复用执行状态，不作为 research truth）；
- DB `UNIQUE(protocol_id)` official result；
- result canonical hash。

同一成功 retry 返回原 result references，不重复 BacktestRun、result 或 lifecycle event。不同 provenance、config、strategy 或日期即使 caller 使用相同 key，也必须拒绝，不能以 key 覆盖 truth。

## 25. Concurrency

必须覆盖：

A. 两个 caller 同时 start：短 claim transaction 只允许一个 RUNNING lease，或一个获得 claim、另一个返回 in-progress；
B. 两个 finalizer 同时写：SQLite write lock + unique(protocol_id) 只允许一个成功；另一方读取 winner 并验证 hash；
C. crash 后 retry：旧 lease 过期可恢复，不改变 protocol 为 observed；
D. stale writer：重新读取 protocol/freeze，已存在 result 时只能返回同一 immutable truth 或 conflict；
E. same request retry：返回同一 references；
F. different provenance retry：`OOS_OBSERVATION_CONFLICT`。

目标是 exactly one official OOS result，而不是保证永远只有一次技术 attempt。

## 26. Restart Recovery

| Crash 点 | Restart 行为 |
|---|---|
| claim 后 | lease 到期后可重新 claim；protocol 仍 `SELECTION_RECORDED` |
| 数据 fetched 后 | 丢弃或按同一 frozen request 重取；不得暴露数据衍生指标 |
| backtest/analytics 后 | 若无 BacktestRun 可重新计算；若已有 run，按 deterministic identity 验证/复用 |
| BacktestRun persisted 后 | finalizer 查询既有 run；匹配则继续，不匹配则 integrity conflict |
| result insert 前 | protocol 未 observed，可安全 retry |
| result insert 后、事务未 commit | SQLite rollback；下次 retry 重新执行，unique constraint 保证最终单一 truth |
| result 与 state/event 同 transaction 已 commit 后 | 读取 existing result，返回 idempotent success；不重复 event |
| state/event 单独提交的旧实现 | 不作为 V1 official path；迁移/修复必须避免把不完整状态当作成功观察 |

所有恢复路径都必须以数据库中的 immutable records 为准，不能以进程内缓存或请求 payload 为准。

## 27. Data Quality

在 finalization 前必须验证：

- 所有 strategy assets 存在；
- common trading dates 按现有 multi-asset policy 对齐；
- 日期无重复、顺序有效；
- required open/close 与 selected price field 存在；
- OHLC/adjusted values 为 finite；
- 无违反现有 `validate_historical_data()` 的缺失或异常；
- OOS 数据 request identity 与 frozen price field/date 相符。

质量失败应为 execution failure/blocked，不得写 OOS_EVALUATED。

## 28. Multi-Asset OOS

OOS 必须支持当前 strategy assets 的 multi-asset dynamic allocation，并完全复用现有：

- common date intersection；
- target allocation；
- rebalance policy；
- SELL-first；
- commission/slippage；
- integer shares；
- cash buffer；
- remaining allocation。

PHASE 8F 不改变这些 semantics，不引入 OOS 专属 accounting。

## 29. Analytics

OOS analytics 复用 PHASE 4I，使用现有 metric semantics，包括 Total Return、CAGR、Volatility、Sharpe、Sortino、Max Drawdown、Calmar 与 trade metrics。`NOT_EVALUABLE` 必须保留。

8F 只产生观察事实，不生成 PASS/FAIL、winner/loser、best strategy、recommendation 或统计显著性结论。p-value、multiple-testing correction、deflated Sharpe、bootstrap 与 Monte Carlo 均留给未来独立阶段。

## 30. Protocol Lifecycle

推荐保持：

```text
SELECTION_RECORDED → OOS_EVALUATED → CLOSED
```

OOS finalization 只推进到 `OOS_EVALUATED`，不自动 `CLOSED`。后续显式 close 允许审计、导出和报告，但不允许修改已观察结果或研究结论。

普通 `transition_protocol()` 不应被 future OOS API 直接用于进入 `OOS_EVALUATED`；必须由专用 finalization operation 执行完整 precondition 与 atomic write。

## 31. No OOS-Based Reselection

OOS result 是 observation，不是 selection evidence。进入 `OOS_EVALUATED` 后必须拒绝：

- 修改 ExperimentSelectionDecision；
- 修改 PHASE 7 SelectionDecision；
- 修改 StrategyFreezeRecord；
- 重开 IS selection；
- 用不同 candidate 或参数重跑同一 protocol；
- 修改 objective 或 parameter space。

研究者要研究另一方案时必须新建 protocol/lineage。8F 不实现 selection、ranking 或 recommendation。

## 32. API Proposal

只设计，不实现：

```text
POST /research/protocols/{protocol_id}/oos/evaluate
GET  /research/protocols/{protocol_id}/oos
GET  /research/protocols/{protocol_id}/oos/result
```

POST body 应为空，或只允许显式确认 metadata。server 必须从 protocol、selection、freeze 和 persisted version 解析所有执行输入。禁止 body 包含或覆盖：

- strategy id/version/hash；
- candidate/parameter set；
- oos start/end；
- price field；
- initial capital、commission、slippage；
- rebalance、execution rule、engine/analytics version。

不提供 `PUT`、`PATCH`、`DELETE`。API 错误使用结构化 code/message，不暴露 traceback、SQLite error、filesystem path、Tiingo credential 或 partial metrics。

## 33. Frontend Proposal

本阶段不修改 frontend。后续 UI 只能在 protocol 已冻结且有 StrategyFreezeRecord 时展示：

- frozen strategy/version/hash；
- frozen OOS period；
- frozen evaluation config；
- 一次性确认文案；
- PENDING/RUNNING/FAILED/BLOCKED/OBSERVED 状态。

确认文案应明确：“该操作将正式打开并评估冻结的样本外区间。完成后该 Protocol 的 OOS 将被视为已观察，不能恢复为未观察状态。”

结果页面区分 IS/OOS，但只格式化和展示 backend facts，不在 frontend 重新计算 metrics，不自动显示 good/bad、winner 或 recommendation。IS/OOS comparative interpretation 留给后续 report phase。

## 34. Security

未来 OOS flow 必须防止：

- caller strategy/date/config override；
- result hash/provenance spoofing；
- path injection；
- NaN/Infinity payload；
- arbitrary Python expression、`eval`、`exec`、dynamic import；
- secret/API key/traceback 泄露；
- 直接绕 API 修改 SQLite 后被当作可信输入。

所有 identity 与 result hash 必须由 server 从持久化对象重建；只接受安全 identifier 与 finite canonical JSON。数据库 integrity check、unique constraints、append-only events 与 repository re-read 是必须的 defense-in-depth。

## 35. Data Provenance

最小 OOS provenance：

```text
protocol_id
selection_decision_id
strategy_freeze_id
strategy_version_id
strategy_content_hash
oos_start / oos_end
warmup_start / warmup_end
price_field_used
configuration_hash
engine_version
analytics_version
data source
request/cache identity
data_snapshot_reference
evaluation timestamp
result_hash
backtest_run_id
```

不得虚构 dataset versioning。若数据来自 Tiingo 或 local cache，应记录实际 source、request identity、rows 和现有 data reference；数据 snapshot capability 的限制须进入 audit。

## 36. Historical Data Reproducibility

### 当前能力

A. atomic JSON cache 能防止 partial write，并通过 request identity 验证请求匹配；
B. cache 可区分 API fresh 与 local cache source；
C. restart 后同 request 可读取同一 cache 文件，前提是文件未被外部修改；
D. BacktestRun configuration snapshot 能保存 request identity 与 data reference。

### 当前限制

- 当前没有完整 dataset content hash / immutable snapshot id；
- Tiingo 后续历史修订可能使 cache miss 后的重取数据不同；
- cache 的 row count 不是内容证明；
- 没有 row-level checksum 或 source response timestamp contract。

### V1.0 判定

这是 **MEDIUM** reproducibility limitation，不阻止本 Architecture Preflight READY。8F V1 最低要求是冻结 request/data provenance、禁止范围扩大、记录 source/cache identity，并保证同一现有 cache 输入下结果 deterministic。若发布要求是跨数据修订的 bit-for-bit official OOS replay，则需要先增加 dataset snapshot/hash layer，此需求升级为 HIGH blocker。

### V1.1 建议

引入独立 Historical Market Data Snapshot / Dataset Versioning phase，包含 immutable content hash、snapshot id、获取时间与可验证的 canonical rows。该阶段不在 8F 实现。

## 37. Tiingo / Local Cache Assessment

本阶段不运行真实 OOS。未来 Tiingo DNS/network failure 必须是 execution `BLOCKED` / technical failure，而不是 consumed OOS；不得暴露 partial metrics。命中已验证的 local Disk Cache 可以作为 data source，但 provenance 必须区分 cache 与 fresh API。

建议 future execution service 在 claim 之前先执行“数据可用性检查”，但该检查不能向研究者返回 price-derived research evidence。预取与 observation 分离：数据可用性可以阻止 execution，只有 final performance result 才能 consume OOS。

## 38. Test Plan

未来 implementation 必须至少覆盖：

1. 所有 OOS preconditions 与 fail-before-download；
2. exact freeze/version id/hash chain；
3. frozen dates 与 IS/OOS non-overlap；
4. frozen config lock 与 caller override rejection；
5. warmup boundary 与 performance start boundary；
6. fresh-capital initialization；
7. Signal(T) → T+1 open no-lookahead；
8. successful single OOS run；
9. immutable result / no overwrite；
10. UNIQUE(protocol_id)；
11. same retry idempotency；
12. conflicting retry；
13. technical failure retry；
14. process crash/restart recovery；
15. concurrent claim/finalizer；
16. no partial metric leakage；
17. no OOS-based reselection；
18. OOS_EVALUATED / CLOSED lifecycle；
19. security / finite JSON / structured errors；
20. multi-asset dynamic allocation；
21. `NOT_EVALUABLE` metrics；
22. data provenance and cache/API failure。

PHASE 8F preflight itself只运行相关既有 tests，不新增 test code。

## 39. Recommended Implementation Subphases

根据当前代码，建议最小拆分：

### 8F-0A — OOS Domain Model & Preconditions

定义 OOS execution/result contracts、frozen identity checks、range/config validation 与 structured errors。

### 8F-0B — OOS Persistence & One-Shot Observation

新增最小 result/observation persistence、`UNIQUE(protocol_id)`、canonical hash、immutable read/duplicate conflict semantics。

### 8F-0C — OOS Claim & Recovery

实现 short claim transaction、execution state、lease/expiry、retry 与 restart recovery；不暴露 partial metrics。

### 8F-0D — OOS Boundary / Warmup Adapter

连接现有 data service、indicator warmup、fresh-capital boundary 与 no-lookahead date filtering。

### 8F-0E — Backtest / Analytics Adapter

只做 orchestration adapter，复用 Strategy Evaluation、Signal、`run_strategy_backtest()`、BacktestEngine 与 PHASE 4I analytics。

### 8F-0F — Atomic OOS Finalization

实现 result + consumed marker + protocol state + lifecycle event 的单事务 finalization，并复用/验证既有 BacktestRun。

### 8F-0G — OOS API

提供 evaluate/read endpoints，所有执行输入 server-resolved，结构化错误，无 PUT/PATCH/DELETE。

### 8F-0H — OOS Frontend

只渲染 backend state/result 与确认交互，不重算指标、不选择候选。

### 8F-0I — End-to-End Integrity Audit

覆盖 one-shot、identity、no-lookahead、no leakage、recovery、concurrency、provenance 和 no-reselection。

Selection、ranking、statistical interpretation、dataset versioning 不属于这些 subphases。

## 40. Risk Register

### CRITICAL

当前未发现已实现代码中的 CRITICAL finding。8F implementation 若违反 one-shot、冻结 identity 或泄露 partial OOS metrics，应立即升级为 CRITICAL 并阻断发布。

### HIGH

1. **Atomic finalization 尚不存在**：若直接复用当前 `create_oos_evaluation()` + 普通 `transition_protocol()`，可能出现 result/state/event 不一致。8F-0F 必须关闭。
2. **Caller override / OOS range injection**：未来 API 若接受 strategy、dates 或 config body，将污染 holdout。8F-0G 必须 server-resolve。
3. **OOS-based reselection**：任何 OOS result 被 selection service 读取或修改 selection/freeze 都是完整性阻断。
4. **Partial result leakage**：在 finalization 前返回任何 metric/equity/trade evidence 都是完整性阻断。
5. **Exact identity bypass**：若不读取 persisted frozen derived version，或允许 latest/rematerialized fallback，是完整性阻断。
6. **Duplicate official observation**：DB uniqueness、immutable conflict 与 concurrency testing 缺一不可。

当前 historical data 缺少完整 dataset hash 属于 MEDIUM；若产品宣称跨数据修订的 release-grade replay，则升级为 HIGH。

### MEDIUM

1. Tiingo/历史数据修订可能改变 cache miss 后输入；
2. 当前 cache provenance 没有 row-level content checksum；
3. future OOS claim/lease 尚未存在；
4. OOS comparative report 与统计解释边界尚未实现；
5. OOS data prefetch 可靠性与 cache eviction policy 尚未定义。

### LOW

1. lifecycle event 的最终事件命名仍可在 8F-0F 实现时最小化；
2. 未来 report UI 的展示格式与导出格式尚未确定；
3. lease owner 的具体 opaque identifier 方案可在实现阶段确定。

## 41. Blocking Prerequisites for Implementation

在开始 8F production implementation 前必须保留以下硬约束：

1. exact persisted frozen StrategyVersion 可重新验证；
2. protocol-level official OOS result 必须有 DB uniqueness；
3. OOS result、consumed marker、protocol state 与 lifecycle event 必须由专用 atomic finalizer 一起完成；
4. API/service 必须拒绝 caller strategy/date/config override；
5. finalization 前不得暴露任何 performance evidence；
6. technical failure 不得 consume OOS；
7. 所有 retry/concurrency/restart 路径必须只产生一个 official result；
8. OOS 不得进入 selection/recommendation/re-ranking。

这些是后续实现的 acceptance gates，不是本次文档阶段的代码缺陷修复范围。

## 42. Out of Scope

本阶段不实现：

- OOS production code；
- database schema/migration；
- OOS API；
- frontend；
- actual OOS backtest / market data download；
- new OOS result；
- ranking、winner、recommendation、selection；
- parameter optimization、grid/random/Bayesian/genetic search；
- walk-forward、Monte Carlo、benchmark、buy-and-hold；
- AI/ML、live trading；
- PostgreSQL、Redis、Kafka、Celery、Docker、Kubernetes、microservices；
- dataset versioning / Parquet / historical data warehouse；
- statistical significance testing。

## 43. Validation

本次 preflight 只需验证当前基线相关既有测试：

```text
tests/unit/test_research_protocol.py
tests/unit/test_research_protocol_api.py
tests/unit/test_exact_strategy_persistence.py
tests/unit/test_phase_8e_0d_selection_uniqueness.py
tests/unit/test_experiment_selection_handoff.py
tests/audit/test_phase_8e_1i_end_to_end_integrity.py
```

不运行 OOS，不运行 batch backtest，不新增测试代码。

## 44. Preflight Decision

Architecture Review 已完成：

- 现有 freeze、selection、exact materialization、BacktestRun、PHASE 3 engine、analytics 与 research persistence 可复用；
- 现有 OOS repository checks 不能替代未来 atomic finalization；
- 数据 snapshot 完整性是已知 MEDIUM limitation，但已定义 V1 最低 provenance 边界；
- 所有 HIGH integrity requirements 已被转化为 implementation acceptance gates；
- 没有发现需要修改现有 PHASE 7/8E 代码才能继续进行本次架构 preflight 的问题。

# PHASE 8F PREFLIGHT READY

Waiting for approval to start PHASE 8F implementation.
