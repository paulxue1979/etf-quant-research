# PHASE 13T Controlled StrategyVersion Creation

## 1. 业务目标

Codex 只能为已有 Strategy 的已保存 Draft 准备版本创建申请。真正创建 immutable StrategyVersion 必须由用户在 Strategy Lab 中批准。准备申请不修改 Draft、历史 Version 或回测/研究数据。

## 2. 范围与非目标

本阶段增加申请持久化、MCP prepare/read、浏览器列表/审批/拒绝和审计事件。不自动创建策略、修改 Draft、执行回测或启动 Experiment、Selection、Freeze、OOS。原有手工创建版本入口继续可用。

## 3. Architecture

`quant_prepare_strategy_version_creation` -> `ControlledVersionService` -> `ControlledVersionRepository` -> SQLite `strategy.db`。浏览器通过 `/strategy-operator/version-requests` 读取申请，并只在用户点击后调用 approve/reject。approve 使用 `StrategyRepository._create_version_from_draft_in_transaction`，与手工入口共用版本编号、ID、content hash 和 provenance 规则。

## 4. Proposal 数据结构

`pending_strategy_version_creations` 保存 request ID、source=`CODEX_MCP`、精确 strategy ID、Draft revision/hash、最新 Version ID/hash 基线、canonical Draft snapshot、申请 hash、状态、时间戳、创建后的 Version ID/hash 与错误码。显式 `client_request_key` 保存在受 request hash 保护的申请 JSON 中；原有行无需迁移。`strategy_version_creation_events` 记录 PROPOSED、REJECTED、STALE、FAILED、CREATED 转换。新增表通过 `CREATE TABLE IF NOT EXISTS` 向前兼容创建，不改已有策略或版本表。

## 5. Draft Identity Contract

Prepare 输入必须携带 strategy ID、expected Draft revision/hash、最新 Version ID/hash（无历史版本时两者均为空）。服务按 strategy ID 重新读取 ACTIVE Strategy、已保存 Draft 和版本基线，校验 domain validator 后保存 canonical snapshot。不按名称选策略，不接受外部 StrategyVersion payload。Draft hash 与新 Version content hash 是不同对象。

## 6. Stale Protection

读取申请时计算当前审批门禁；审批时在写事务内再次检查 lifecycle、revision/hash、snapshot 和最新 Version 基线。变化时旧申请转为 STALE；Draft 无效时转为 FAILED。旧申请不会自动绑定新 Draft。重新准备必须使用新的有效 identity。

## 7. 状态机

`PROPOSED -> REJECTED | CREATED | STALE | FAILED`。终态不可再次执行；对 CREATED 的重复 approve 只返回同一 Version。动态 approval gate 不会在仅仅读取时写库，因此检测到 stale 但未点击审批时，持久状态可能仍为 PROPOSED，`approval_gate.reason` 会显示具体阻塞原因。

## 8. Transaction / Atomicity

Approve 通过 SQLite `BEGIN IMMEDIATE` 锁定写入，检查申请状态、Draft 与版本基线、调用 canonical 创建函数、更新 request 为 CREATED 并写审计事件，然后一次 COMMIT。事务失败时全部回滚，避免 Version 已创建而申请仍未完成。版本编号由同一个写事务分配。

## 9. Idempotency

未提供 `client_request_key` 的旧 MCP 调用保持原语义：request ID 由 strategy ID、Draft revision/hash 和版本基线确定，重试永远返回原申请及其真实状态。显式提供 key 时，request ID 由版本创建命名空间与 key 确定；同 key、同 payload 的重试返回原申请（包括 REJECTED/CREATED），同 key、不同 payload 报 `IDEMPOTENCY_KEY_CONFLICT`。旧申请 REJECTED 后，用户明确授权的新意图可使用一次性的新 key，在相同 Draft 上创建新的 PROPOSED，旧申请仍为 REJECTED。相同 Draft/基线已有 PROPOSED 时，不允许另一个 key 建立并行申请，报 `ACTIVE_VERSION_REQUEST_EXISTS`。`BEGIN IMMEDIATE` 串行化并发 Prepare。重复或并发 approve 同一申请不会创建第二个 Version。既有手工创建同内容版本的策略不在 13T 中改变。

## 10. MCP Tool Contract

新增 `quant_prepare_strategy_version_creation` 与 `quant_get_strategy_version_creation_request`。前者只持久化申请，后者只读。未注册 MCP approve/create 工具；MCP 无法通过该 surface 直接创建 Version。输入为受约束的 ID、revision、hash、可空的基线 ID/hash，以及可选 `client_request_key`。一次新意图应先生成并保留一个新 key；网络超时或响应丢失后只能复用该 key 重试，不能自动生成第二个 key。省略 key 是兼容旧调用，不代表重新申请。

## 11. Browser Human Approval

Strategy Lab 按 canonical strategy ID 列出申请，显示 Draft revision/hash、版本基线、新版本创建前的结构快照和不可逆警告。用户可 Reject 或 Approve & Create Version。前端禁用无效申请，但后端重新验证才是正确性边界。审批成功后从 canonical backend 获取新 Version ID/hash 并刷新 Version History。

## 12. Failure Recovery

响应丢失后，重新读取申请或重复 approve 会返回 CREATED 和同一 Version。STALE、FAILED、REJECTED 保留供审计。数据库/事务异常回滚，不标记虚假的 CREATED。读取 CREATED 申请时核对所引用 Version 的存在与保存的 hash。

## 13. Persistence

申请、事件和 immutable Version 位于同一策略 SQLite store。Cold Restart 应重新读取 CREATED 状态、Version ID/hash 及 Version History；不能依赖前端内存。测试使用独立 `tmp_path` 数据库，不使用 canonical 用户库。

## 14. Security Boundary

审批 endpoint 仅接受本地浏览器 Origin、request ID 路径且无请求体；这与现有本地可信部署假设一致。Origin 不是用户身份认证，不能据此声明公网级 Human Approval 安全。MCP 不含执行工具，不允许任意 SQL、路径或版本 payload。

## 15. UAT

自动化通过后停在 `PHASE 13T PRE-UAT READY`。用户在专用 `VERSION_CREATION_UAT_13T` Strategy 上依次验证 Prepare+Reject、Draft stale、Approve+Create、重复审批幂等、MCP re-read、Cold Restart 和无意外写入。UAT A 的旧 REJECTED 申请保留；UAT B 在同一 Draft 上明确发起新申请时提供新的 `client_request_key`，并记录该 key 供同一次申请重试复用。Codex 不代替用户点击 Reject 或 Approve，不修改正式策略或 Canary。

## 16. Golden Regression

覆盖手工版本创建、Strategy persistence、生命周期、Draft validation、MCP read tools、Controlled Backtest、Experiment/Grid、Selection/Freeze/OOS、日期与数据边界。真实 UAT 之后再跑最终完整回归，并比对 canonical 表计数和 immutable hash 摘要；真实 UAT 授权写入须单独记录。

## 17. Known Limitations

本阶段不提供跨网络身份认证、不自动比较完整结构差异、不自动准备 Backtest。准备申请时的 validation PASS 不替代审批瞬间重检。无效或 stale 的申请须由用户重新发起；相同 Draft/基线的 REJECTED 申请不会因旧调用重试复活，只有明确的新 key 才能建立新的独立申请。key 是幂等标识，不是密码或授权令牌。
