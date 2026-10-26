# 10 · Duffel 接入说明（v1.16.2）

> 关联：ISSUE-025 / ISSUE-026 / **ISSUE-027**｜PR-026 / PR-027 / **PR-028**
> ｜RELEASE-v1.16.0 / v1.16.1 / **v1.16.2**
> 适配器：`src/sources/duffel.py`｜配置：`src/sources/duffel_config.json`（gitignore，不入库）

## 0. 结论：中国大陆个人无法激活 live（2026-10-27 实测，本渠道实际不可用）

按「Go live」真实走了一遍，KYC 卡在账户国家：

| 关卡 | 实测结果 |
|---|---|
| 入口 | `dashboard.duffel.com/{team}/test` →「Go live」→ `/activation` → `connect.stripe.com/setup/c/acct_1UUq4K…` |
| 第 1 步 商家类型 | 选 `Individual`（无公司文件时的正确选项）→ 通过 |
| 第 2 步 个人详情 | 姓名/邮箱/出生日期/家庭住址/电话/**SSN 后四位** |
| **国家/地区下拉** | **只有一个选项「美国」，且为灰色不可选** |
| 电话 | 输入框固定 `+1`，页面原文「您的电话号码必须与账户所在国家/地区一致」 |
| 地址 | 强制「州」下拉（Alabama/Alaska/…）+ 5 位邮编 |
| SSN | 必填，说明为「履行监管义务」 |
| 结论 | 要的是**美国身份**（美国住址 + 州 + +1 电话 + SSN）。Stripe 支持的国家/地区列表**不含中国大陆**，该账户也无法改成内地 → 大陆个人无合法路径，编造即身份欺诈 |

想走通只剩三条：美国主体（EIN + 美国地址 + SSN/ITIN）、香港主体、新加坡主体。
三者都要当地身份与银行账户，不是填表技巧问题。

**因此本渠道在代码里保持 `not_available`**，`status_reason` 已写明上述原因；`entry_note`
也改成同一口径，避免以后再有人（包括我自己）按「注册即得 Key」的老说法白跑一遍。

另有一条能力层面的坏消息（与 KYC 无关）：实测三条航线报价**全为 USD**（含国内线
PVG→PEK 69.71–799.60、CAN→CTU 71.88–1818.80），按「外币不折算」红线这些行不参与比价，
即便激活也撑不起「低于底价」判定。详见 §5。

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

### 2.1 实测校准（2026-10-26，test token 打三条航线）

用测试令牌实际调用了 `PVG→LHR`、`PVG→PEK`、`CAN→CTU`（2026-11-15），确认以下形态，
与「照文档构造」的假设有出入的地方已回写到 fixture 与解析逻辑：

| 项 | 实测结果 | 影响 |
|---|---|---|
| 响应码 | `201 Created`（不是 200） | 现有代码只判成功不判具体码，无需改 |
| `departing_at` | `2026-11-15T10:50:00`，**无时区后缀**，为机场当地时间 | `_hhmm` 直接取 `[11:16]`；不能假设带 `+08:00` |
| `marketing_carrier_flight_number` | **整数** `1274` | `str()` 转换后拼 `AA1274`，已验证 |
| `aircraft` | 可能为 **`null`** | 适配器未依赖该字段，安全 |
| `stops` | 空数组 `[]`，**不是中转次数** | 中转段数仍按 `len(segments)-1` 计 |
| 税费 | `base 297.05 + tax 53.47 = total 350.52`（USD） | 证实 `total_amount` 含税，`tax_included=True` 口径正确 |
| 币种 | 三条航线**全部 `USD`**（含国内线 PVG→PEK / CAN→CTU） | 见下方 3.2 的结论 |
| `live_mode` | 顶层与每个 offer 都有布尔字段，test 模式为 `false` | 新增响应级闸门 3.3 |
| 结果规模 | PVG→LHR 82 条、CAN→CTU 44 条、PVG→PEK 13 条 | 必须排序后再截取（3.4） |

## 3. 两条硬规则（本适配器的存在理由）

### 3.1 测试模式 = 虚构数据，直接拒绝

Duffel 测试模式（token 前缀 `duffel_test_`）官方文档明写 "you won't see realistic flight
schedules or prices"。**实测（2026-10-26）发现结果集并非只有沙盒航司**：`PVG→PEK` 13 条里混着
`CA`/`MU`，`CAN→CTU` 44 条里混着 `3U`/`BA`/`BR`/`CA`/`CZ`/`HR`/`HX`/`IB`/`MU`，只有 `ZZ`
（Duffel Airways）是纯虚构航司。

**"出现真实航司"不能当作放行理由**——这些行的价格同样是沙盒值、不可订。与项目「零编造」
红线正面冲突，因此：

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

**实测补充（对价值判断很关键）**：三条航线（含两条国内线）返回的 `total_currency` **全部是
`USD`**，没有一条人民币报价。含义是——**即使拿到 live token，Duffel 也基本提供不了可直接
比价的人民币票价**，它对中国航线的价值落在「航班时刻/航司/舱位参考 + 外币标价」层面。
若要让它的价格进入最低价与"低于底价"判定，必须先做汇率折算，那是另一个产品决策
（需要显式开关 + 全链路标注 + 不参与排名的隔离），不属于本适配器的默认行为。

### 3.3 响应级闸门：`live_mode=false` 一律不采用（v1.16.1）

token 前缀闸门只能挡住 `duffel_test_` 开头的凭据。若上游改前缀规则、或账号在 live 模式下
被降级，光看 token 形状会漏。实测确认响应**顶层带 `live_mode` 布尔字段**，因此加第二道校验：

```python
if isinstance(data, dict) and data.get("live_mode") is False:
    # 缓存一条"上游自称测试环境"的空结果，empty_note 据此给出可执行原因
    return []
```

这样做的好处是不依赖任何本地约定，完全以上游自述为准。

### 3.4 先排序再截取（v1.16.1）

实测 `PVG→LHR` 一次返回 **82 条** offer，而适配器只取前 12 条再截到 6 条展示。上游**未承诺**
返回顺序按价格升序（本次实测近似升序纯属巧合）。若不排序，一旦乱序就会把真正便宜的报价
截掉，等于给出错误的最低价。现在按「人民币优先 → 同币种总价升序 → 无法解析的排最后」排序
后再截取，单测 `test_cheapest_offer_survives_truncation` 用 15 条乱序数据锁住该行为。

## 4. 调用纪律

| 项 | 值 | 理由 |
|---|---|---|
| 结果缓存 | 15 分钟 | Duffel 报价本身约 20 分钟失效，缓存不宜更长 |
| 空结果缓存 | 30 分钟 | 与其余渠道一致，避免瞬时失败锁死半天 |
| 当日自我上限 | 200 次 | 搜索不收费，仅防死循环；**不属于计费护栏** |
| 超时 | 25 秒 | `supplier_timeout=20000` 由 Duffel 侧控上游航司，本机留余量 |
| 429 / 401 / 403 | 立即抛中文原因，不重试 | 重试只会加深限流或撞死 token |

## 5. 测试

`tests/test_duffel.py` **25 例**，全程离线：

- `tests/fixtures/duffel_offer_request_sample.json` 的**字段形态取自 2026-10-26 实测响应**
  （三条航线），航线/航班号/价格为人工示意值，不是任何真实报价；文件头 `_说明` 记录了实测
  确认的 6 条形态要点，改 fixture 前先读它；
- 覆盖：闸门四态（无 token / test / 未知形态 / live）、离线开关、请求形态（方法/头/体/查询串）、
  直飞与 1/2 段中转映射、跨天、外币不折算、缺航段丢弃、承运人回退、时长解析、机场名回退、
  缓存命中、日限硬停、空结果说明、HTTP 错误文案、注册表，
  以及 v1.16.1 新增的 **响应级 `live_mode` 闸门**与**排序后截取**；
- 离线注入按 `docs/03` 第 9 条同步 **6 处**（5 个测试模块 + 守卫 `NETWORK_MODULES`），
  跑全量前后 `src/sources/.juhe_cache/day_counter.json` 保持 `20`，且未生成 `.duffel_cache/`
  （v1.16.1 实测：全量 289 例，计数器 20→20）。

## 6. 待办（交回产品侧）

1. **Go live 的真实路径**（2026-10-26 实测）：Dashboard → `Go live` → `/activation` 实际跳到
   **Stripe Connect 商家入驻**（`connect.stripe.com/setup/c/acct_…`）。第 1 步选商家类型
   （无公司注册文件选 `Individual`），随后要填法定名称、地址、出生日期、银行账户、税号，
   并过 hCaptcha。**这必须由账号持有人本人填**（涉及真实身份与金融信息），提交后 Stripe
   自动审核，通过后才能在 live 模式下创建 `duffel_live_` token。
2. 前置：邮箱必须已验证（未验证时点 `Go live` 会先跳 `/email` 等确认链接，24 小时有效，
   可重发）。
3. 拿到 live token 后联调 `PVG→LHR`、`CAN→SIN`、`PEK→NRT` 与一条国内线，**预期货币为 USD**
   （见 3.2），不要指望人民币行；核对字段映射与币种分布后同步更新 fixture。
4. 若要让 Duffel 价格参与比价与"低于底价"判定，需先做汇率折算的独立决策
   （显式开关 + 全链路标注 + 不参与排名），默认不做。
