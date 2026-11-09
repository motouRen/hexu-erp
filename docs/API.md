# v0.1 API 约定

同源 JSON API，通过 `/login/` 登录获取 Session；写入需 CSRF Cookie 对应的 `X-CSRFToken` 和一个 UUID 格式的 `Idempotency-Key`。每个新业务意图使用新键；网络失败后相同内容重试保留原键。API 不提供密码登录 JSON 接口，也不提供第三方令牌认证。

成功写入返回 200 与 `{ "message": "操作结果", ... }`。金额输出为十进制字符串，输入建议使用字符串。错误为 `{ "error": "中文说明", "code": "错误编码" }`；未登录 401、无权限 403、不存在 404、输入错误 400、业务状态/缺货/重复数据冲突 409。CSRF 和超出 Django 上传限制的错误可能返回框架 HTML 错误页。

## 接口表

| 方法与路径 | 参数 / 内容 | 权限与效果 |
| --- | --- | --- |
| GET /health/ | 无 | 公共数据库连通检查 |
| GET /api/state/ | 可选 `?store=门店ID` | 已登录，工作台数据；渠道和商品档案全局，业务记录按门店筛选；管理员额外获得全局审计 |
| POST /api/products/ | sku, name, category, price, reorder_level | manage_catalog，创建商品及各门店零库存 |
| POST /api/products/{id}/update/ | name, category, price, reorder_level | manage_catalog，编辑档案，不修改 SKU 与订单快照 |
| POST /api/stores/ | code, name, location | manage_catalog，创建门店及各商品零库存 |
| POST /api/channels/ | code, name, kind=online/retail | manage_catalog，创建渠道档案 |
| POST /api/stock/receive/ | product_id, store_id, quantity, note | operate_erp，追加到货及库存流水 |
| POST /api/orders/ | 见下例 | operate_erp，整单创建和占用 |
| POST /api/orders/{id}/pay/ | reference | operate_erp，登记全额收款 |
| POST /api/orders/{id}/ship/ | 空对象 | operate_erp，已收款整单发货 |
| POST /api/orders/{id}/cancel/ | 空对象 | operate_erp，未收款订单取消 |
| POST /api/orders/{id}/refund/ | reference, 可选 items | operate_erp，未发货全退或已发货按行退款 |
| POST /api/orders/import/ | csv: UTF-8 CSV 全文字符串 | operate_erp，批量订单原子导入 |
| GET /api/export/{resource}/ | resource=orders/inventory/payments/template | 已登录，全部门店数据的 UTF-8 BOM CSV，或仅列名模板 |

### 创建订单

```json
{
  "channel_id": 1,
  "store_id": 1,
  "customer": "零售顾客",
  "external_id": "PLATFORM-20261106-001",
  "items": [
    { "product_id": 1, "quantity": 2, "unit_price": "89.00" }
  ]
}
```

`unit_price` 省略时取商品参考售价。同商品不能重复，客户不能为空，数量整数。成功返回 `id` 与 `number`，即使金额为零，也需单独登记收款后才可发货。

### 已发货退货退款

```json
{
  "reference": "实际退款交易流水号",
  "items": [
    { "line_id": 12, "quantity": 1, "restock": true }
  ]
}
```

`line_id` 是订单详情返回的订单行 ID，不是商品 ID；`restock` 必须是真正的 JSON 布尔值，省略默认 true。未发货已收款订单只需 `reference`，服务按全额退款并取消，忽略行选择。

### CSV 格式

```csv
external_id,channel_code,store_code,customer,sku,quantity,unit_price
EXT-001,TMALL,SH-01,示例顾客,HX-001,1,89.00
EXT-001,TMALL,SH-01,示例顾客,HX-002,2,68.00
```

同单客户和门店须一致；代码与 SKU 必须存在。总请求上限 2 MiB；CSV 文本另限制 500,000 字符。界面上传限制 500 KB。导出对可能触发电子表格公式的字符串添加前导单引号，导出的业务报表不等同于可直接重新导入的模板。

## 工作台数据上限

`state` 是 MVP 聚合接口，不是无限数据仓库：订单/资金/库存流水分别最近 200 条，审计最近 100 条；商品、门店、渠道、库存余额全量。统计指标从数据库完整记录计算，不受列表截断影响。前端列表搜索仅搜索已加载数据。未来拆成分页资源端点，同时保留概览聚合接口。

## 一致性和重试

幂等键复用不同请求返回 `idempotency_conflict`；重复渠道订单返回 `duplicate_external_order`；缺货返回 `insufficient_stock`；非法状态操作返回 `invalid_state`；超量退货返回 `return_exceeded`。数据库唯一约束的并发竞争可能返回通用 `conflict`。失败请求回滚业务与幂等记录，可修正后以新键提交。
