# PHASE 2 - Indicator Engine

## 范围

本阶段仅提供 Simple Moving Average（MA）和 Exponential Moving Average（EMA）。不包含交易信号、策略、回测、绩效分析或前端展示。

## 输入与价格字段契约

`moving_average()` 和 `exponential_moving_average()` 都必须接收 `HistoricalDataSet`、正整数 `period` 和显式的 `PriceField`。`price_field` 没有默认值，调用方必须传入 `PriceField.RAW_CLOSE` 或 `PriceField.ADJUSTED_CLOSE`。

每个 `IndicatorSeries` 保存 `price_field_used`，因此后续模块可追溯每个指标的价格口径。指标层绝不根据数据集、标的或调用位置隐式选择 raw 或 adjusted close。

## 数据完整性与日期对齐

计算前会调用 PHASE 1 的 `validate_historical_data()`。因此空数据、无效数值、非升序日期和重复日期都会被拒绝。计算结果保持原始日期顺序和长度，不会修改输入市场数据。

## MA

MA 在日期 `t` 仅使用包含 `t` 的最近完整 `period` 个价格。完整窗口形成前返回 `None`，不产生部分窗口值，也不读取未来价格。

## EMA

EMA 同样在完整窗口形成前返回 `None`。第一个有效值是前 `period` 个价格的 SMA；之后使用：

`EMA_t = alpha * price_t + (1 - alpha) * EMA_(t - 1)`

其中 `alpha = 2 / (period + 1)`。该规则明确、可复现，且每个日期只使用当前及历史价格。
