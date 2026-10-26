# ISSUE-025: Amadeus 自助平台已下线 —— 换 Duffel 作为国际线第二真源（含沙盒数据隔离）

- 提交人: 产品侧（"Amadeus" 选项） | 日期: 2026-10-26 | 状态: Closed（v1.16.0）
- 类型: ✨特性（新数据源）+ 🐞缺陷（文档/渠道文案与事实不符）
- 前序: v1.15.0 待办第 2 条"是否注册 Amadeus" → 执行前的准入核验

## 1. 结论先行：Amadeus 这条路已不存在

原待办是"注册 Amadeus 拿免费 Key"。核验后否决，理由不是成本而是**通道关闭**：

| 事实 | 依据 |
|---|---|
| 2026-02 起停止自助平台新注册 | 官方公告与多家迁移指南一致 |
| **2026-07-17 自助门户整体下线**，存量自助 Key 同日失效 | 官网现只显示一句 "Amadeus for Developers self-service portal has been decommissioned on July 17th, this website is for Amadeus Enterprise API Portal only" |
| 现存仅 Enterprise | 100+ 接口，需 IATA/ARC 资质、顾问对接、合同谈判；多数仍是 SOAP |
| 对个人开发者 | **无任何合法免费自助路径** |

我方原建议（"注册即得 Key"）基于 2026-07 之前的事实，已过期——本单同时修正
`amadeus.py`、`sources/__init__.py`、`README.md` 中所有"注册后自配 Key 启用"的过时文案。

## 2. 换成 Duffel 的理由

| 对比项 | Duffel | Amadeus（Enterprise） |
|---|---|---|
| 自助注册 | ✅ 开放，注册即得 token | ❌ 需合同 + 资质 |
| 搜索成本 | **免费**（下单才结算） | 按合同配额 / 按次 |
| 协议 | REST + JSON | 多为 SOAP |
| 覆盖 | 300+ 航司（NDC / GDS / LCC） | 400+ 航司（含私有运价） |
| 适合本项目 | ✅ | ❌ |

## 3. 两条必须写死的规则

### 3.1 沙盒数据不得进比价（新红线条款）

Duffel 测试模式（`duffel_test_`）返回沙盒航司 **Duffel Airways（IATA `ZZ`）** 的虚构航班与
虚构价格，官方原话 "you won't see realistic flight schedules or prices"。
与「零编造」红线冲突，故：`mode == "test"` → `not_available`，**且 `search()` 不触达网络层**。
不是"打水印展示"——一旦允许出现，用户无法分辨哪行是真的，v1.14 的"价格未披露"语义也随之失效。

### 3.2 外币报价不折算

Duffel 请求侧无币种参数，同一批结果可能混 CNY/GBP/USD。折算汇率是会变的第三方数据，
写死等于替用户编价。口径：非 CNY → 行保留、`price=None`、`price_note` 写明外币金额与
"不参与比价"，沿用 v1.14 语义。

## 4. Fix

- 新增 `src/sources/duffel.py`（`DuffelSource`，`source_id="duffel"`）：
  四态判定 `mode`（none/test/unknown/live）、`Duffel-Version: v2`、
  `tax_included=True`（总价已含税，引擎不再补费）、缓存 15 分钟 / 空结果 30 分钟、
  当日 200 次自我上限（搜索不收费，仅防失控）、25 秒超时、429/401/403 给中文可执行原因；
- 新增机场中文名补充表 `_AIRPORT_ZH_EXTRA`（**仅本适配器展示用**；不动 `cities._CITIES`——
  那是 PRD 定义的 50 城唯一事实来源，也是网页下拉与定价引擎的输入域）；
- `sources/carriers.py` 补 ~40 个国际航司中文简称（含中国香港/中国台湾航司）；
- `sources/__init__.py` 注册（位于 juhe 之后），三入口自动生效；
- `amadeus.py` 文案全部改为"2026-07-17 已下线"，代码保留备查。

## 5. Test

- `tests/test_duffel.py` **22 例**（全程离线）：四态闸门、测试 token 零请求、离线开关、
  请求形态（POST/头/体/查询串）、直飞与 1/2 段中转、跨天、外币不折算、缺航段丢弃、
  承运人回退、时长解析、机场回退、缓存命中、日限硬停、空结果说明、HTTP 错误文案、注册表；
- `tests/fixtures/duffel_offer_request_sample.json`：**按官方文档结构构造的形态样本，
  非真实响应**（无 live token，联调后回填），文件头已注明；
- 离线注入按 `docs/03` 第 9 条同步 **6 处**（5 个测试模块 + 守卫清单），
  另更新 `test_amadeus`（原因文案）与 `test_fareradar`（票源家族清单/凭证措辞正则）；
- 全量 **286 例全绿**（原 264 + 22），跑测试前后 `juhe day_counter.json` 均为 `20`，
  未生成 `.duffel_cache/`（零真实请求）。

## 6. 关闭

v1.16.0 合并 main，tag `v1.16.0`。遗留：Duffel live token 需产品侧完成注册与 KYC 后联调；
届时按 `docs/10-duffel-integration.md` §6 复验三条国际线，并用实测响应替换 fixture。

## 7. 产物

| 类型 | 文件 |
|---|---|
| 适配器 | `src/sources/duffel.py`、`src/sources/carriers.py`（国际航司）、`src/sources/amadeus.py`（文案修正） |
| 注册 | `src/sources/__init__.py` |
| 测试 | `tests/test_duffel.py`、`tests/fixtures/duffel_offer_request_sample.json`、`tests/__init__.py`、`tests/test_offline_guard.py`、`tests/test_amadeus.py`、`tests/test_fareradar.py` 及 4 个模块离线注入 |
| 文档 | `docs/10-duffel-integration.md`、`docs/03-tech-design.md`（第 10 条）、`README.md` |
| 配置 | `src/sources/duffel_config.example.json` + `.gitignore` 补两行 |
