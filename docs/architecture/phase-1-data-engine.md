# PHASE 1 - Tiingo Data Engine

## 范围

本阶段只负责 Tiingo EOD 历史日线数据的获取、标准化、质量验证与本地磁盘持久化缓存。

## 数据流

`TiingoClient -> normalize_tiingo_eod_response -> validate_historical_data -> DiskCache`

`HistoricalDataService` 先读取缓存；缓存缺失、损坏或验证失败时重新请求 Tiingo。成功的 API 数据必须先通过验证，才会原子写入缓存。

## 数据契约

内部每根日线均保留 raw 与 adjusted OHLCV：

- Raw：`open`、`high`、`low`、`close`、`volume`
- Adjusted：`adj_open`、`adj_high`、`adj_low`、`adj_close`、`adj_volume`
- Corporate actions：`div_cash`、`split_factor`

每个 `HistoricalDataRequest` 明确保存 `price_field_used`，当前支持 `raw_close` 与 `adjusted_close`；默认 `adjusted_close`。数据来源明确为 `api_fresh` 或 `cache`。

## 缓存

`DiskCache` 使用版本化 JSON 格式，缓存键包括 symbol、开始/结束日期、频率、选定价格字段和数据 schema 版本。文件按原子替换方式写入；读取到格式错误、版本不匹配或请求身份不匹配时会拒绝该项并触发重新获取，绝不静默返回不可信数据。

缓存根目录为 `data/cache/`，由 `.gitignore` 排除。

## 可靠性与安全

Tiingo 认证通过 `Authorization` header 传递，绝不放入 URL、日志或异常消息。客户端设置 timeout、有限次数指数退避重试，并对 HTTP 401/403、429、5xx、网络与超时分别抛出明确的异常类型。
