# Ozon 接口清单（待确认）

> **来源说明**：开发环境的网络策略屏蔽了 docs.ozon.ru，无法直接查阅官方文档。
> 下表根据开源客户端 diphantxm/ozon-api-client（最近提交 2025-10-20）的源码整理，
> 并经网络搜索交叉核对。**请在官方文档中逐项核对后再进入阶段 3。**

| # | 能力 | 方法与路径 | 关键参数 / 说明 |
|---|---|---|---|
| 1 | FBS 订单列表 | `POST /v3/posting/fbs/list` | `filter.since` / `filter.to`（创建时间，最长 1 年）、`filter.last_changed_status_date.from/to`（状态变更时间）、`limit`（最大 50）、`offset`；响应 `result.has_next` 表示还有下一页 |
| 2 | 单个订单详情 | `POST /v3/posting/fbs/get` | `posting_number`，可选 `with` 附加字段 |
| 3 | 备货 / 打包确认 | `POST /v4/posting/fbs/ship` | `posting_number` + `packages[].products[]`（`product_id` 即 SKU、`quantity`）+ `with.additional_data`；返回 `result[]` 为拆包后的货件号 |
| 4 | 获取面单 PDF | `POST /v2/posting/fbs/package-label` | `posting_number[]`（最多 20 个），返回 PDF（兼容 JSON+base64 格式）；建议备货后 45–60 秒再获取，未就绪返回 not ready。批量时有异步版：`/v2/posting/fbs/package-label/create` + `/v1/posting/fbs/package-label/get` |
| 5 | 设置物流单号 | `POST /v2/fbs/posting/tracking-number/set` | `tracking_numbers[]`：`posting_number` + `tracking_number`。**本项目使用 Ozon 物流，不调用此接口**；单号从订单数据的 `tracking_number` 读取 |
| 6 | 商品列表 | `POST /v3/product/list` + `POST /v3/product/info/list` | 前者分页拿 `product_id`/`offer_id`（`last_id` 游标），后者按 ID 批量取 `sku`、名称 |
| 7 | 更新库存 | `POST /v2/products/stocks` | `stocks[]`：`offer_id` / `product_id`、`stock`、**`warehouse_id`（必填）**；仓库 ID 来自 `POST /v1/warehouse/list`。旧接口 `/v1/product/import/stocks` 已弃用。每次最多 100 条；同一商品同一仓库每 2 分钟只能更新一次 |
| 8 | 仓库列表 | `POST /v1/warehouse/list` | 无参数；返回 `result[]`：`warehouse_id`、`name`、`status`。也用于“测试连接” |
| 9 | 读取 FBS 仓库库存 | `POST /v1/product/info/stocks-by-warehouse/fbs` | `sku[]`；返回 `result[]`：`sku`、`warehouse_id`、`present`（总数）、`reserved`（预留） |
