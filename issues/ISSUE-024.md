# ISSUE-024: 接入聚合数据「航班机票查询」，补 RapidAPI 在国内线的覆盖面与缺价

- 提交人: 产品侧（"这是补充md文件" → 5 源分级方案；随后提供 AppKey） | 日期: 2026-10-26 | 状态: Closed（v1.15.0）
- 类型: ✨特性（新数据源）+ 🐞缺陷（测试离线注入失效，红线第 5 次复发）
- 前序: v1.14.0 已把"有航班无价"改成"保留展示 + 价格未披露"，但**没有价就是没有价**——
  本单是给国内线补一个真能出价的源

## 1. 需求

产品侧提供了 5 套数据源的分级方案（RapidAPI / 聚合数据 Juhe / 飞猪 / 民航局时刻表 / 飞常准），
要求按优先级接入。经核对，其中两条不成立，一条不具备条件：

| 文档主张 | 核对结论 |
|---|---|
| 聚合数据「每日 100 次免费」 | ❌ 实际是**试用共 3 次**，正价 ¥99/450 次 |
| 民航局"公共开放航班时刻表 API" | ❌ **不存在**；官方数据走「民航行业数据共享与服务平台」，主体限定为航司/机场/空管等运行单位，个人无法接入（网上能搜到的是台湾 PTX 平台） |
| 飞猪开放平台 | ⚠️ 需 ISV 入驻 + 企业实名 + 机票类 API 权限组，个人账号在"可申请"列表里看不到该权限 |
| 文档的降级策略：price 空即**本地 Mock 票价** | ❌ 与「零编造」红线直接冲突，**未采纳**（见 §6） |

## 2. 定位与证据

接聚合数据的直接理由是补 RapidAPI 在国内线的两个缺口：

| 缺口 | 证据（实测） |
|---|---|
| 国内覆盖差 | `INC→CTU`、`INC→SYX` 返回 `status 204`（上游无此航线数据） |
| 有航班但无价 | `INC→SZX 2026-10-27`：`other_flights` 1 条真实行程（BK3037 11:30→15:00），`price: null` |

聚合数据对同一条 **BK3037** 给出 **¥918**；`INC→SZX 2026-10-28` 一次返回 **33 条、33 条全有价**
（¥918–¥3160）。结论：这是能出价的国内源，不是又一个"只给航班不给钱"的渠道。

## 3. 接口事实（实测校准）

| 项 | 文档写法 | 实测 |
|---|---|---|
| 路径 | `/jipiao/query` | `/flight/query`（前者 404 + `error_code 10022`） |
| 参数 | `start` / `end` / `date` | `departure` / `arrival` / `departureDate` |
| 成功判据 | `resultcode` | **无该字段**，用 `error_code == 0` |

两个必须写死的坑：

1. **`transferNum` 是航段数不是中转次数**：33 条样本里 `transferNum=1` 的 7 条全直飞，
   `=2` 的 26 条 `flightNo` 均形如 `G54338 | PN6229`。按"中转次数"理解 → 每条直飞被标"中转 1 次"。
2. **`airlineName` 是机翻**：BK → `Okay航空公司`（奥凯）、G5 → `中国快运航空公司`（华夏，
   China **Express** 直译）。中文名一律走新建的 `sources/carriers.py`，**不动** `airlines.AIRLINES`
   （后者是演示生成器的 6 家名册，改了会连带改变确定性航班号）。

## 4. Fix

- 新增 `src/sources/juhe.py`（`JuheFlightSource`，`source_id="juhe_flight"`）：
  双闸门（无 Key / `FARE_RADAR_OFFLINE=1` 均 `not_available` 且零请求）、
  日 20 / 月 60 双硬停（按次计费，0.22 元/次，月上限 ≈13 元，**绝不自动充值**）、
  缓存 6 小时 / 空结果 30 分钟、超时 60 秒（实测偶发 25s+ 无响应）；
- 新增 `src/sources/carriers.py`：真实航司中文简称表 + 机翻清洗（与 `airlines` 名册严格分离）；
- 注册到 `all_sources()`（位于 variflight 之后），三入口自动生效；
- `tax_included=False`：上游未明示含税，按票面价处理、由引擎补机建燃油并逐行标注
  （与飞常准同口径；若当成含税会**低估**到手价）。

## 5. Test

- `tests/test_juhe.py` **21 例**：字段映射、机翻不可见、`transferNum` 语义、跨天、
  缺时刻丢弃、坏价保留标未披露、时长方解析、错误码、双硬停、缓存命中、空结果说明、注册表；
- `tests/fixtures/juhe_flight_query_sample.json`：真实响应脱敏裁剪（5 条代表性记录，
  orderid 已脱敏）——测试不联网；
- `tests/test_offline_guard.py` **3 例**（新增守卫，见 §7）；
- 全量 **264 例全绿**（原 240 + 21 + 3），跑测试前后 `day_counter.json` 数值一致（零真实请求）。

## 6. 关于「Mock 票价兜底」——不采纳

产品文档要求"所有数据源 price 为空 → 本地 Mock 票价（按航线距离生成演示价）"。
该条与项目「零编造」红线直接冲突：**用户看到 ¥680 会当成真价去下单**，
而且它恰好会把 v1.14.0 刚完成的"价格未披露"语义抹平。

已向产品侧提出替代方案（如确需演示能力）：仅 `--demo` 显式开关触发、全链路水印
（JSON `data_kind:"demo"` / 网页通栏 / CLI 与 MCP 逐行前缀）、不参与最低价与提醒、
不写缓存。**在该方案获得明确授权前，默认拒绝**，现行口径仍为"价格未披露"。

## 7. 事故：测试真实联网（红线第 5 次复发）

首跑全量测试即发现测试**真实联网并按次计费**，我方计数器当日直接打到上限 20。
根因不是"忘改某个文件"，而是**修法本身在 discover 下无效**：

- `python -m unittest discover tests` 的 `top_level_dir` 默认就是 `tests`，
  测试模块按 `test_xxx` **顶层导入**，`tests/__init__.py` **不会被执行**；
- 因此"只在 `tests/__init__.py` 里重定向 CONFIG_NAME"完全不起作用；
- 既有 4 个源之所以安全，是因为 5 个测试模块**各自手写**了 patch——新源没有同步。

修复：5 个模块各补一行注入 + 新增守卫测试（断言"所有联网源已重定向""测试进程内无 active
联网源""注册表里没有未登记的新源"）+ 测试前后比对计数器。约定写入
`docs/03-tech-design.md` 第 9 条，明确"新增联网源必须同步 6 处"。

## 8. 关闭

v1.15.0 合并 main，tag `v1.15.0`。遗留：聚合数据试用额度已耗尽，真实联调需
购买 ¥99/450 次档后复验；届时只需重启服务即可，无需改代码。

## 9. 产物

| 类型 | 文件 |
|---|---|
| 适配器 | `src/sources/juhe.py`、`src/sources/carriers.py` |
| 注册 | `src/sources/__init__.py` |
| 测试 | `tests/test_juhe.py`、`tests/test_offline_guard.py`、`tests/fixtures/juhe_flight_query_sample.json` |
| 文档 | `docs/09-juhe-integration.md`、`docs/03-tech-design.md`（约定第 9 条）、`README.md` |
| 配置 | `src/sources/juhe_config.json`（gitignore）+ `.gitignore` 补两行 |
