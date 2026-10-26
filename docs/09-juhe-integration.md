# 09 · 聚合数据「航班机票查询」接入说明（v1.15.0）

> 关联：ISSUE-024｜PR-025｜RELEASE-v1.15.0
> 适配器：`src/sources/juhe.py`｜配置：`src/sources/juhe_config.json`（gitignore，不入库）

## 1. 为什么接它

RapidAPI·Google Flights 在国内线有两个硬缺口，实测证据：

| 现象 | 证据 |
|---|---|
| 国内覆盖差 | `INC→CTU`、`INC→SYX` 等返回 `status 204`（上游无此航线数据） |
| 有航班但无价 | `INC→SZX 2026-10-27`：`other_flights` 有 1 条真实行程（奥凯 BK3037 11:30→15:00），`price: null` |

聚合数据补上的正是第二类：同一条 **BK3037**，Google Flights 无价，聚合数据给 **¥918**；
`INC→SZX 2026-10-28` 一次返回 **33 条、33 条全带价**（¥918–¥3160）。

## 2. 接口事实（2026-10-26 实测校准，不照抄文档）

```
GET https://apis.juhe.cn/flight/query
    ?departure=INC&arrival=SZX&departureDate=2026-10-28&key=<AppKey>
```

**参数名与用户提供的文档不一致，以实测为准**：

| 来源 | 路径 | 参数 | 实测 |
|---|---|---|---|
| 原始文档 | `/jipiao/query` | `start` / `end` / `date` | ❌ 404，`error_code 10022`（接口地址不存在） |
| 阿里云商品页 / 实测 | `/flight/query` | `departure` / `arrival` / `departureDate` | ✅ 200，`error_code 0` |

返回结构：

```json
{"error_code": 0, "reason": "成功",
 "result": {"orderid": "JH...", "flightInfo": [
   {"airline": "BK", "airlineName": "Okay航空公司", "flightNo": "BK3037",
    "isCodeShare": false, "equipment": "738",
    "departure": "INC", "departureName": "河东机场",
    "departureDate": "2026-10-28", "departureTime": "11:30",
    "arrival": "SZX", "arrivalName": "宝安国际机场",
    "arrivalDate": "2026-10-28", "arrivalTime": "15:00",
    "duration": "3h30m", "transferNum": 1, "ticketPrice": 918, "segments": []}]}}
```

注意：**没有 `resultcode` 字段**，成功判据是 `error_code == 0`。

错误码（`_ERROR_TEXT`）：

| code | 含义 | 处置 |
|---|---|---|
| 0 | 成功 | — |
| 10001 | Key 无效（错误的请求 KEY） | 抛中文原因，渠道降级 |
| 10012 | 调用次数已用尽（未实测，来自官方文档） | 同上 |
| 10022 | 接口地址不存在 | 同上 |

## 3. 两个必须写死的坑

### 3.1 `transferNum` 是**航段数**，不是中转次数

33 条样本里：`transferNum=1` 的 7 条全部直飞（`flightNo` 无分隔），`transferNum=2` 的 26 条
`flightNo` 均形如 `G54338 | PN6229`。若按"中转次数"理解，**每一条直飞都会被标成"中转 1 次"**。

映射规则：`legs == 1 → 直飞`；`legs >= 2 → "{legs} 段联程（中转城市未披露）"`。
上游不提供中转城市，所以**不能**渲染成"经 XX 中转"——那是编造。

### 3.2 `airlineName` 是机器翻译，不能直接用

| 代码 | 上游返回 | 正确名称 |
|---|---|---|
| BK | `Okay航空公司` | 奥凯航空（Okay Airways 直译） |
| G5 | `中国快运航空公司` | 华夏航空（China **Express** 直译） |

中文名一律走 `sources.carriers.cn_name()`（独立映射表，**不扩充** `airlines.AIRLINES`——
后者是演示生成器的 6 家名册，改了会连带改变确定性航班号与承运人分配）。
上游名仅在本地表缺失时兜底，且经"去后缀 + 含 ASCII 字母即判机翻弃用"清洗。

## 4. 计费与护栏

- **试用额度只有 3 次**（不是"每日 100 次"）；正价档 ¥99 / 450 次 ≈ **0.22 元/次**；
- 自设硬停：**当日 20 次 / 当月 60 次**（月上限 ≈ 13 元），触顶即抛中文原因、渠道降级、
  缓存仍可用，**绝不自动充值**（计费型接口纪律，ISSUE-019）；
- 缓存：同 (航线, 日期) **6 小时**；空结果只缓存 **30 分钟**；
- 超时 60 秒：实测正常 3.2 秒，但该接口**偶发 25 秒以上无响应**（实测踩到一次读超时），
  超时设短会把"慢"误判成"失败"。

## 5. 价格口径

`ticketPrice` 是否含机建/燃油**上游未明示**。处理方式与飞常准一致：

- `tax_included = False`（按票面价处理）；
- 费用明细与到手价由引擎 `fees.fee_breakdown()` / `fees.total_estimate()` 统一补；
- `note` 里写明"票面是否含税上游未明示，到手价按未含机建燃油估算"。

不选"当成含税"的理由：含税判断错会**低估**到手价，用户按票面下单时才发现要多付机建燃油。

## 6. 测试与一次真实事故（必须记录）

`tests/test_juhe.py` 21 例 + `tests/test_offline_guard.py` 3 例，全部基于
`tests/fixtures/juhe_flight_query_sample.json`（真实响应脱敏裁剪 5 条），**零真实请求**。

**事故（2026-10-26，红线第 5 次复发）**：加本适配器后首跑全量测试，发现测试**真实联网**
并按次计费消耗额度。根因：

- `python -m unittest discover tests` 的 `top_level_dir` 默认就是 `tests`，
  模块按 `test_xxx` **顶层导入**，`tests/__init__.py` **不会被执行**；
- 因此"只在 `tests/__init__.py` 里重定向 CONFIG_NAME"在 discover 下完全无效；
- 各测试模块原有的 4 个适配器 patch 是**逐模块手写**的，正是因为这个原因——
  而新加的第 5 个源没有同步。

修复：

1. 5 个使用 `all_sources()` 的测试模块各补一行 `_ju.CONFIG_NAME = "_test_offline_absent.json"`；
2. 新增 `tests/test_offline_guard.py`：在模块级重定向全部联网源，并断言
   "所有联网源的 CONFIG_NAME 已指向不存在文件""测试进程内没有 active 的联网源"
   "注册表里没有未登记的新源"——新增源若忘记注入，这个测试会**指名报错**；
3. 跑测试前后比对 `src/sources/.juhe_cache/day_counter.json`，作为人工复核手段。

**结论（写进纪律）**：新增联网适配器必须同步修改 **6 处**——5 个测试模块 + 守卫测试清单，
并在 `docs/03-tech-design.md` 适配器约定第 9 条留档。

## 7. 启用方法

1. 注册 https://www.juhe.cn/ → 申请「航班机票查询」→ 实名/审核通过；
2. 「我的 API」复制 **AppKey**；
3. 写入 `src/sources/juhe_config.json`：

```json
{"api_key": "<你的 AppKey>", "endpoint": "", "proxy": ""}
```

4. 重启 `python src/webapp.py`，渠道「聚合数据·航班机票」即 `active`；
   未配置或 `FARE_RADAR_OFFLINE=1` 时为 `not_available`，**不发起任何请求**（双闸门）。

## 8. 已知限制

- 票价为**缓存价**，非秒级实时，热门航线可能过时；
- 无舱位类型、无余票、无机建燃油拆分（`segments` 实测恒为空数组）；
- 跨天联程（如 21:40→次日 15:30）到手价可能很低但耗时很长，排序只看价格，
  由用户自行权衡；
- 中转城市、经停信息上游未提供，一律标"未披露"。
