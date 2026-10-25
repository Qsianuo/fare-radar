# ISSUE-019: 接入飞常准 Aviation MCP——补国内航线真实票价

- 提交人: 产品评审人（"你只调取了谷歌接口调用、国内接口也是免费的"） | 日期: 2026-10-25
- 状态: **Closed（v1.11.0）**｜schema 已于 2026-10-25 校准完成
- 类型: ⚙️数据源治理（覆盖不足）

## 问题

Google Flights（v1.9 起）对国内航线覆盖不足。实测证据（2026-10-25，`.rapidapi_cache/`）：

| 航线 | 上游结果 |
|---|---|
| INC→SZX / INC→SHA | 有 6 / 4 条真实报价 |
| INC→CTU（4 个日期） | 204 无此航线数据 |
| INC→CAN / INC→PEK / INC→WUH | 200 但 0 条可售行程 |

评审人指出应评估国内数据源。

## 候选评估（结论先行）

| 方案 | 票价能力 | 费用 | 合规 | 采纳 |
|---|---|---|---|---|
| **飞常准 Aviation MCP** | 国内**全部航司与舱位**，航班覆盖 99.99% | 1 积分=¥0.01，5–50 积分/次；**送 ¥50**；`initialize`/`tools/list` 免费 | ✅ 官方开放平台 | **采纳** |
| 接口盒子 apihz 低价日历 | 仅"参考最低价"，非可售报价 | 号称永久免费 | ⚠️ 数据来源不透明，疑抓取 | 否决（触"不爬取"红线风险） |
| 飞猪开放平台 | 询价/生单全套 | 接口免费 | ✅ | 不可用（需企业资质+类目授权） |
| toolkk 参考票价 | 参考最低价 | 需 Key，额度不明 | 未核实 | 备选 |
| RollingGo | 实时库存 | 号称免费无上限 | 营销软文来源 | 不采信 |

**否决理由（记录在案）**：本项目红线是"不爬取、不违反平台条款"。我自己不抓，但使用数据来源不明的聚合接口等于间接使用违规数据，且无法在上线披露中自证清白。飞常准是官方授权数据源，可在界面明确标注。

## 已确认的技术事实

- 端点 `https://ai.variflight.com/servers/aviation/mcp`，实测无 Key 返回 `401`，明确要求
  请求头 `X-API-Key` 或 OAuth Bearer；Key 自助创建于 https://ai.variflight.com/keys
- 协议：MCP over HTTP（streamable HTTP，JSON-RPC 2.0），响应可能是 `application/json`
  或 `text/event-stream`，两者均需兼容
- 官方文档页**未公开**工具名与参数 schema，仅有"9 个工具、含最低票价"的概述

## 本期已交付（骨架，默认关闭）

| 文件 | 内容 |
|---|---|
| `src/sources/variflight.py` | JSON-RPC over HTTP 客户端（兼容 JSON/SSE）、401/402/429 明确报错不重试、积分护栏、缓存、`probe_tools()` 自检入口 |
| `src/sources/variflight_config.example.json` | 配置模板（真实 Key 文件已 gitignore） |
| `src/sources/__init__.py` | 注册为配置门控渠道（排在 RapidAPI 之后） |
| `tests/test_variflight.py` | 9 例离线测试（未配 Key / 未校准不联网 / 日与月积分硬停 / 最坏单价预扣 / JSON 与 SSE 解析 / 401 不重试 / 错误透传 / 参数未校准拒绝构造） |
| `.gitignore` | `variflight_config.json`、`.variflight_cache/` |

### 计费纪律（按评审人确认的"硬停"执行）

- 按**最坏单价 50 积分/次**预算：日上限 500 积分（≈¥5）、月上限 4500 积分（≈¥45，对 ¥50 体验额度留 10% 余量）
- 到线抛错硬停，**绝不自动充值**；失败不扣费但仍按最坏值预扣（保守）
- 结果缓存 6 小时 / 空结果 30 分钟，符合 `docs/03` 第 9 节通用约定

### 双闸门设计（防编造）

1. 无 Key → `not_available`，原因"未配置 API Key"
2. 有 Key 但 `SCHEMA_CALIBRATED=False` → 仍 `not_available`，原因"工具 schema 待校准"，
   且 `search()` **不发起任何联网调用**；`_build_args()` 直接抛错，不猜参数名

## 阻塞点：缺 API Key

`tools/list` **免费**（官方口径），是唯一能拿到真实工具名与参数 schema 的合法途径。
需要评审人：

1. 打开 https://ai.variflight.com/keys 注册（自动送 ¥50）
2. 创建 API Key，填入 `src/sources/variflight_config.json`（或直接提供）

拿到后我会：执行 `python -m sources.variflight` 取回 9 个工具的 schema → 补齐
`PRICING_TOOL_NAME` / `PRICING_TOOL_ARGS` / `_to_offers()` 映射 → 置
`SCHEMA_CALIBRATED = True` → 用 1 次真实调用验证 → 出 PR 与 RELEASE（届时补 PR-021 /
RELEASE-v1.11.0，并把 Adapter 纳入离线注入约定）。

## 验收标准（校准后执行）

1. INC→CTU（Google 无数据、国内真实有航班）能返回真实报价
2. 单入口与三入口同源（Web / CLI / MCP 输出一致）
3. 积分实际消耗可核对（day/month 计数器与平台账单一致）
4. 到日/月上限时硬停且提示明确，不发生任何自动充值

## 遗留

- v1.11 校准完成后，需在 `tests/test_fareradar.py` 等使用 `all_sources()` 的模块
  补 variflight 的离线注入（`CONFIG_NAME` 指向不存在文件），否则会真实扣费。

## 校准结果（2026-10-25，Key 到位后执行）

### tools/list 实测（免费）

Aviation MCP 共 9 个工具。票价工具为 **`getFlightPriceByCities(dep_city, arr_city, dep_date)`**，
另有 `searchFlightsByDepArr`（航班动态）、`getFlightTransferInfo`（中转方案）、
`searchFlightItineraries`（自然语言行程摘要）、`getTodayDate`（免费）等。

### 真实调用验证（INC→CTU 2026-11-01，即 Google 侧无数据的那条）

返回 **6 班直飞真实航班**：长龙 GJ8604 ¥350、国航 CA2590 ¥440、东航 MU9802 ¥478、
国航 CA4212 ¥530（双流）、国航 CA2596 ¥700、川航 3U6542 ¥1060。
到手价按全站口径 = 票面 + 机建 50 + 燃油 40（890km > 800km），最低 ¥440。
**这条航线 Google Flights 返回 code 244/空数据，飞常准有 6 班——评审人提出的方向得到验证。**

### 上游数据形态（实录，供后续维护）

- `content[0].text` 是**文本**，形如 `"Flight prices: {python 字面量}"` → 用 `ast.literal_eval` 解析，不能当 JSON
- 航班字段：`flightno`/`flightcompany`/`depaptcname`/`arraptcname`/`flighthterminal`/
  `flightdeptimeplandate`/`stopflag`/`distance`/`cabins[]`
- `cabins[]`：`cabinclass`（Y=经济舱）、`classname`、`cabincode`、`price`、`stprice`、`discount`
- 时间戳是**北京时间的 epoch**（按 UTC 解析得到 02:35，+8h 为 10:35 才合理）→ 显式 +8h，不依赖宿主时区
- `oilfee`/`tax` 字段为空，票价是否含税上游未明示 → 本适配器按**票面价**处理，由引擎统一补费用
- 舱位数据存在上游不一致（同一 `cabincode` 在不同航班上 classname 不同）→ 取 `cabinclass=="Y"` 的最低价，
  取不到再退化为全部舱位最低价

### 显示修正（自查）

初版把未收录的航司二字码塞进 `carrier_name`，被 `flight_display` 拼成 "GJ GJ8604"。
已改为：补充中文名映射（3U 川航 / GJ 长龙 等），**未收录的一律留空**只显示航班号，
不允许二字码冒充中文名。

## Closed

- v1.11.0（tag），PR-021。同批修复子进程测试真实扣费问题，见 ISSUE-020。
