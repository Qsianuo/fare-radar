# 10 · Duffel 接入说明（v1.16.0）

> 关联：ISSUE-025｜PR-026｜RELEASE-v1.16.0
> 适配器：`src/sources/duffel.py`｜配置：`src/sources/duffel_config.json`（gitignore，不入库）

## 1. 为什么换成 Duffel

原本留给国际线的第二真源是 **Amadeus Self-Service**（`amadeus.py`，v1.6 起）。2026-10 复核时
发现这条路已经不存在：

| 事实 | 依据 |
|---|---|
| 2026-02 起停止自助平台新注册 | Amadeus 官方公告 + 多家迁移指南一致 |
| **2026-07-17 自助门户下线**，存量自助 Key 同日失效 | 官网横幅原文："Amadeus for Developers self-service portal has been decommissioned on July 17th, this website is for Amadeus Enterprise API Portal only" |
| 现存路径只有 Enterprise | 需 IATA/ARC 资质 + 顾问对接 + 商务谈判，周期以周/月计；多数接口仍是 SOAP |
| 对个人的结论 | **无任何合法免费自助路径**，注册选项作废（原待办第 2 条关闭） |

Duffel 是同类平台里仍开放自助注册的一家：注册即得 token、**搜索不收费**（下单才结算）、
REST + JSON、覆盖 300+ 航司（NDC / GDS / LCC）。`amadeus.py` 的 220 行代码不删——将来若走
企业版可复用 OAuth2 与映射；渠道恒为 `not_available` 并在原因里写明下线日期。

## 2. 接口事实（2026-10-26 按官方文档核对）

```
POST https://api.duffel.com/air/offer_requests?return_offers=true&supplier_timeout=20000
Headers: Authorization: Bearer <token>
         Duffel-Version: v2          ← 必带，缺了直接 4xx
         Content-Type: application/json
         Accept: application/json
Body:    {"data": {"slices": [{"origin": "PVG", "destination": "LHR",
                               "departure_date": "2026-12-20"}],
                   "passengers": [{"type": "adult"}],
                   "cabin_class": "economy"}}
```

- 响应 `data.offers[]`：`total_amount`（字符串）、`total_currency`、`owner{name,iata_code}`、
  `expires_at`、`slices[].duration`、`slices[].segments[]{origin,destination,departing_at,
  arriving_at,marketing_carrier,marketing_carrier_flight_number,operating_carrier}`。
- **`total_amount` 已含税费**，故 `tax_included=True`；机建/燃油不拆分（`fee_breakdown` 置空值 +
  `included_desc` 说明），引擎不再补费。
- **`Duffel-Version` 只有 `v2`（v1 已于 2025-01-23 停用）**，写死在常量 `API_VERSION`。

## 3. 两条硬规则（本适配器的存在理由）

### 3.1 测试模式 = 虚构数据，直接拒绝

Duffel 测试模式（token 前缀 `duffel_test_`）返回的是沙盒航司 **Duffel Airways（IATA `ZZ`）**，
官方文档明写 "you won't see realistic flight schedules or prices"。这与项目「零编造」红线
正面冲突，因此：

- `mode == "test"` → `status = not_available`，`status_reason` 明确说清"虚构数据、不接入"；
- `search()` 在 `availability()` 处即返回空，**不触达网络层**（单测 `test_test_mode_token_never_sends_request`
  用假 opener 断言 `requests == []`）；
- 不给"打水印后展示"的余地：一旦允许出现，用户无法分辨哪行是真的。

### 3.2 非人民币报价不折算

Duffel 请求侧**没有**币种参数（`currency` 不是合法字段），货币由航司报价决定，同一批结果里
可能混着 CNY 与 GBP/USD。适配器口径：

| 情况 | 处理 |
|---|---|
| `total_currency == "CNY"` | 正常出价：`price` / `total_estimate` 有值，参与排序与最低价 |
| 其它币种 | `price=None` + `price_note="上游报价 GBP 512.30，非人民币（不做汇率折算，故不参与比价）"`，行保留 |
| 无 `total_amount` | `price_note="上游未返回可用报价"` |

折算汇率是**会变的第三方数据**，写死等于替用户编价；沿用 v1.14「行保留、价格留空、说明原因」
的既有语义最稳。

## 4. 调用纪律

| 项 | 值 | 理由 |
|---|---|---|
| 结果缓存 | 15 分钟 | Duffel 报价本身约 20 分钟失效，缓存不宜更长 |
| 空结果缓存 | 30 分钟 | 与其余渠道一致，避免瞬时失败锁死半天 |
| 当日自我上限 | 200 次 | 搜索不收费，仅防死循环；**不属于计费护栏** |
| 超时 | 25 秒 | `supplier_timeout=20000` 由 Duffel 侧控上游航司，本机留余量 |
| 429 / 401 / 403 | 立即抛中文原因，不重试 | 重试只会加深限流或撞死 token |

## 5. 测试

`tests/test_duffel.py` **22 例**，全程离线：

- `tests/fixtures/duffel_offer_request_sample.json` 是**按官方文档结构构造的形态样本，不是真实
  响应**（本迭代尚无 live token）——联调后必须按实测回填，文件头已写明这一点；
- 覆盖：闸门四态（无 token / test / 未知形态 / live）、离线开关、请求形态（方法/头/体/查询串）、
  直飞与 1/2 段中转映射、跨天、外币不折算、缺航段丢弃、承运人回退、时长解析、机场名回退、
  缓存命中、日限硬停、空结果说明、HTTP 错误文案、注册表。
- 离线注入按 `docs/03` 第 9 条同步 **6 处**（5 个测试模块 + 守卫 `NETWORK_MODULES`），
  跑全量前后 `src/sources/.juhe_cache/day_counter.json` 保持 `20`，且未生成 `.duffel_cache/`。

## 6. 待办（交回产品侧）

1. 注册 Duffel（https://duffel.com）→ 邮箱验证 + KYC 激活 → 创建 **live** token
   （`duffel_live_`）→ 写入 `src/sources/duffel_config.json`；
2. 拿到 token 后做一次真实联调：`PVG→LHR`、`CAN→SIN`、`PEK→NRT` 三条国际线 + 一条国内线
   （预期国内线多为空报价），核对字段映射与币种分布，再用实测响替换 fixture；
3. 联调若发现国际线 CNY 报价占比过低（大量外币行落到"未披露"），再评估是否调整展示策略
   （不引入汇率折算的前提下）。
