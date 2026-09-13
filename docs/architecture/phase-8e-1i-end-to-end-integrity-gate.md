# PHASE 8E-1I — Experiment Research End-to-End Integrity Gate

## 1. Purpose

本阶段以审计测试验证 PHASE 8E-1A 至 PHASE 8E-1H 的完整闭环：冻结的 Experiment 结果只能通过已持久化的 researcher selection 进入 PHASE 7 SelectionDecision，并最终冻结同一个已持久化的 derived StrategyVersion。

本阶段不新增业务功能，不执行 OOS，不进行排名、推荐、优化或新的回测会计实现。

## 2. Scope

新增范围仅包括：

- `tests/audit/test_phase_8e_1i_end_to_end_integrity.py`
- 本架构审计文档

除非发现 CRITICAL/HIGH 完整性缺陷，不修改生产代码、API、schema 或前端。

## 3. Baseline

审计基线为 PHASE 8E-1H 提交：

- branch: `main`
- HEAD: `c4ce33c` (`feat: add experiment research frontend`)
- working tree: clean

## 4. End-to-End Flow

```text
Frozen Experiment
  -> persisted candidate execution/result
  -> ExperimentResultsReadService
  -> compatibility diagnostics
  -> frozen objective evaluation
  -> immutable ExperimentSelectionDecision
  -> PHASE 7 SelectionDecision
  -> StrategyFreezeRecord
  -> exact persisted derived StrategyVersion
```

审计测试通过现有 repository/service 边界构造本地 SQLite 场景，不调用 Tiingo，也不 mock 核心选择、持久化或 handoff 完整性逻辑。

## 5. Source-of-Truth Map

| 信息 | Source of truth |
|---|---|
| 回测订单、成交、持仓、equity curve | `BacktestRun` |
| Experiment candidate result identity 与 analytics summary | `ExperimentResult` |
| candidate 读取与状态组合 | `ExperimentResultsReadService` |
| researcher 的显式选择 | `ExperimentSelectionDecision` |
| PHASE 7 governance selection | `SelectionDecision` |
| 选中策略冻结 | `StrategyFreezeRecord` |
| derived strategy 内容 | 持久化的 `StrategyVersion` |

`ExperimentResult` 不复制完整回测会计数据；`SelectionDecision` 只引用 IS backtest run 和 provenance。

## 6. Identity Chain

审计验证以下 ID/hash 链保持一致：

```text
ExperimentResult.derived_strategy_version_id
  == ExperimentSelectionDecision.selected_derived_strategy_version_id
  == SelectionDecision.selected_strategy_version_id
  == StrategyFreezeRecord.strategy_version_id
  == persisted StrategyVersion.version_id
```

对应 content hash 也必须一致。PHASE 7 decision 通过 `data_provenance` 回溯 Experiment selection、result、candidate set、parameter space 和 objective identity。

## 7. Selection and Handoff Integrity

Selection 必须是显式 researcher action。API 只接受：

- `selected_candidate_id`
- `selection_method`
- `researcher_rationale`

策略 ID、hash、result ID、日期、配置和 OOS 字段由服务端从冻结数据解析，额外字段被拒绝。Handoff 只接受 `experiment_id`，并重新读取持久化的 official ExperimentSelectionDecision；不能由调用方提供 candidate 或 strategy override。

Experiment selection 和 PHASE 7 selection 都具有冲突拒绝与相同请求幂等重试语义；数据库 uniqueness constraint 保持 one-per-Experiment / one-per-Protocol。

## 8. Lifecycle, Atomicity and Recovery

Experiment selection persistence 会将 selection 与 Experiment `SELECTION_RECORDED` 状态及 lifecycle event 原子提交。PHASE 7 handoff 使用现有 protocol repository 的 atomic selection/freeze transaction，保证 selection、governance event 和 freeze 一起提交或一起回滚。

审计覆盖重启式重开 SQLite repository、重复 handoff、重复 event/freeze 检查。并发 uniqueness 与故障回滚由既有 PHASE 8E-0/8E-1 测试继续覆盖。

## 9. IS/OOS Firewall

8E-1I 只验证 IS 研究链：

- ExperimentResult 的统计边界为 Experiment frozen IS range；
- selection evidence 只保存 IS identity/metrics/provenance；
- handoff provenance 只保存 IS 日期与 IS backtest reference；
- 未创建或读取 OOS evaluation；
- 不进入 `OOS_EVALUATED`。

允许 protocol 本身保存冻结 OOS metadata，但它不能成为本阶段的 performance input、selection evidence 或 candidate observation。

## 10. Warmup and No-Lookahead

Warmup 与 `Signal(T) -> T+1 trading day open` 的执行语义由 PHASE 8D execution/backtest 审计覆盖。本阶段跨层检查只确认 ExperimentResult / BacktestRun 的 performance range 等于 frozen IS range，且结果链没有引入 OOS execution。

## 11. Failure Semantics

FAILED candidate 保留在 read model，不转换为正常结果；`NOT_EVALUABLE` 保持显式状态，不转换成零值、布尔 PASS/FAIL 或可选择结果。Compatibility 只表示公平比较是否可行，不产生 ranking、winner、best 或 recommendation。

## 12. Security

审计保持服务端 provenance resolution、结构化安全错误、canonical JSON finite-number 约束，并禁止将 secret、traceback、filesystem path 或 caller-provided strategy identity 注入 selection。测试中的安全 sentinel 仅用于验证过滤行为，不作为真实凭据。

## 13. Tests

新增集中审计测试：

```text
tests/audit/test_phase_8e_1i_end_to_end_integrity.py
```

它覆盖完整 selection/handoff/freeze 链、exact identity、restart idempotency、BacktestRun source of truth、IS/OOS firewall、API request injection protection，以及 no-ranking/recommendation scope audit。

最终测试数字、静态检查和 Tiingo 网络状态以 PHASE 8E-1I Final Report 为准；本文档不声称未实际执行的结果。

## 14. Known Limitations

- 本阶段不创建完整 dataset versioning 或 market-data snapshot system。
- 本地审计不替代真实 Tiingo network integration；外部 DNS/network 不可用时，真实 Tiingo 测试必须标记 BLOCKED。
- 本阶段不实现 OOS evaluation、candidate selection automation、ranking、recommendation、optimization 或下一阶段功能。

## 15. Final Gate Decision

Final gate 必须同时满足 focused audit、相关回归、frontend、backend non-network、静态检查和 security audit，且无 CRITICAL/HIGH integrity finding。通过后才允许提交 `test: close phase 8e experiment research integrity gate`，并在提交后停止，不进入下一阶段。
