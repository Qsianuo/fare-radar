# 交接说明：Dev → UI 工程师（v1.4.0 后端 API 契约）

- 交接人: Dev | 接收人: UI 工程师（经总协调者中转）| 日期: 2026-10-25
- 依据: docs/02-PRD-v1.4.md（第 4~9 节为口径唯一依据）、docs/handoffs/handoff-pm-to-dev-v14.md、ISSUE-011
- 后端已实现并全绿（`python -m unittest discover tests`，117 例）；本文为 `/api/*` 的**完整契约增量**与前端注意事项。`src/webapp.html` 本版未动，等你按本文重写。

## 0. 全局约定（不变项）

- 启动 `python src/webapp.py [--port 8080]`，仅绑定 127.0.0.1；JSON 均含 `ok` 布尔；错误 `{ok:false, error:"中文"}`。
- 页顶常驻演示声明：`notice` 字段逐字渲染，不要自己写文案；禁止出现"实时/秒级/行情"字样。
- 纯 HTML/CSS/JS，无 CDN/框架/图标库/字体。
- **v1.4 核心口径**：`price_basis` 字段（"到手价（票面+机建+燃油）"）——所有排序、最低价、提醒均为**到手价**（`total_estimate` = `price + 未含税费`）。大字号第一视觉给到手价，票面价小字。

## 1. GET /api/cities —— 50 城下拉（含机场全称）

```json
{"ok": true, "count": 50,
 "cities": [
   {"code": "PEK", "name": "北京", "airport": "北京首都国际机场"},
   {"code": "SHA", "name": "上海", "airport": "上海虹桥国际机场"},
   {"code": "JIQ", "name": "黔江", "airport": "黔江武陵山机场"}]}
```

- 50 项、零国际城市（HKG/KIX/NRT/ICN/BKK 已下线，不再返回）。下拉可展示"上海（上海虹桥国际机场）"样式。
- 顺序即注册表顺序，直接按数组顺序渲染。

## 2. GET /api/search —— 结果页（真实样例，SHA→JJN 2026-10-27）

```json
{"ok": true,
 "route": "上海(SHA) → 泉州(JJN)",
 "date": "2026-10-27", "weekday": "周二",
 "price_basis": "到手价（票面+机建+燃油）",
 "notice": "[演示数据] 以下价格来自内置演示票源，非真实报价；到手价=票面价+机建+燃油（价格含分钟级演示波动），不能用于实际购票。",
 "failed": [],
 "unavailable_sources": [
   {"source_id": "douyin_groupbuy", "name": "抖音团购",
    "reason": "卡类渠道：卖次卡/省钱卡不卖具体航班（命中卡见结果页卡提示）"},
   {"source_id": "douyin_live", "name": "抖音直播间", "reason": "今日闪购场次未含该航线"}],
 "card_hints": [
   {"card_id": "DY-101", "name": "省钱卡单人版365天", "price": 79,
    "validity_text": "365 天（自购买日起）",
    "scope": "每张机票同日同航班优惠 40 元起，支持国内(含港澳台)/国际机票",
    "stock": "充足", "refund_disclosed": false,
    "refund": "退改规则未披露（商家未公布退改规则，…截图留证）",
    "purchase": "需在抖音 App 内下单，本工具不提供购买",
    "note": "下单后立减，具体以下单页为准", "updated": "2026-10-25",
    "entry_url": "https://v.douyin.com/USSyX47SP7s/",
    "entry_note": "春秋航空·次卡与省钱卡（抖音地点页）"},
   {"card_id": "DY-103", "name": "199 秋季机票单次卡·石家庄/兰州/大连/重庆/潮汕/上海/西安",
    "price": 199, "validity_text": "2026-10-25 ~ 2026-11-30", "stock": "售罄",
    "refund_disclosed": true, "refund": "过期退/随时退（照抄卡面，细则以抖音购买页为准）", "...": "..."}],
 "offers": [
   {"price": 179, "total_estimate": 249,
    "source_id": "douyin_miniapp", "source_name": "抖音小程序·春秋航空",
    "source_desc": "春秋自营抖音小程序：固定特价票+会员价常规航班，不含行李额",
    "entry_url": "https://v.douyin.com/USSyX47SP7s/",
    "entry_note": "春秋航空·特价票与固定航线机票（抖音地点页）",
    "product": "特价票 上海→泉州",
    "depart_time": "12:45", "arrive_time": "14:20", "duration_text": "1小时35分",
    "dep_airport": "上海虹桥国际机场", "arr_airport": "泉州晋江国际机场",
    "carrier_code": "9C", "carrier_name": "春秋", "flight_no": "9C6714",
    "fee_breakdown": {"airport_fee": 50, "fuel_fee": 20, "tax_included": false,
                      "included_desc": "不含机建燃油费"},
    "channel_count": 1, "channel_names": ["抖音小程序·春秋航空"],
    "is_special_fare": true, "note": "特价票·固定航班日期·不含机建燃油费｜省 ¥41｜限 2026-10-27 出发｜需抖音 App 跳转小程序下单",
    "risk_flags": [], "is_deal": false, "is_lowest": true},
   {"price": 475, "total_estimate": 475, "source_id": "douyin_miniapp", "product": "春秋会员价直飞",
    "depart_time": "17:20", "arrive_time": "18:55", "...": "...", "is_special_fare": false},
   {"price": 479, "total_estimate": 479, "source_id": "airline", "product": "HO6200",
    "channel_count": 2, "channel_names": ["OTA 比价", "航司直连"],
    "note": "吉祥HO 直飞正班｜另有 1 个渠道有售", "...": "..."}],
 "alert_hit": null, "warning": null, "empty_hint": null}
```

### offers 行字段一览（UI 用途标注）

| 字段 | 含义 | 前端注意 |
|---|---|---|
| `price` / `total_estimate` | 票面价 / **到手价** | 到手价大字号；票面价小字并排 |
| `depart_time` / `arrive_time` / `duration_text` | 起飞/到达 HH:MM、航程"1小时35分" | **券/引流票为 null**（同行的 `flight_no` 也为 null）→ 显示"—"，不要渲染空串 |
| `dep_airport` / `arr_airport` | 机场**全称**（注册表唯一展示值） | 如"上海虹桥国际机场 → 泉州晋江国际机场"；可截断加 title |
| `carrier_code` / `carrier_name` / `flight_no` | 承运人码/中文名/航班号 | 行内显示"春秋 9C · 9C6714"风格；6 家：CA 国航、MU 东航、CZ 南航、HU 海航、9C 春秋、HO 吉祥 |
| `fee_breakdown` | `{airport_fee:50, fuel_fee:20|40, tax_included, included_desc}` | `included_desc` 直接展示（"已含机建+燃油" / "另加机建 ¥50 + 燃油 ¥20" / "不含机建燃油费"），不要自己拼税费文案 |
| `channel_count` / `channel_names` / note | 跨渠道去重 | `channel_count>1` 时渲染徽标"另有 N 个渠道有售"（note 中也有同文案）；=1 不渲染 |
| `is_special_fare` | 固定特价票行 | 独立区块/截图式标签（"省 ¥41""不含机建燃油费"），特价票有自己区块，勿与常规行混排 |
| `is_lowest` / `risk_flags` / `is_deal` | 最低价标记 / 风险词数组 / 券类 | `risk_flags` 逐条渲染（已售罄/退改不明/有效期不覆盖） |
| `entry_url` / `entry_note` | 渠道入口 | 空串（airline）不渲染链接 |

- **card_hints**：命中才非空（全航线卡恒命中，单次卡按航线+日期）。渲染为结果页顶部独立"省钱卡提示"卡：卡名/价格/有效期/适用范围/库存（"售罄"要显眼）/退改（`refund_disclosed=false` 时 `refund` 以"退改规则未披露"开头，必须显著）/`purchase` 与 `entry_url`（"前往渠道 ↗"）。**card_hints 不是 offers，不参与最低价**。
- **排序**：offers 已按（准入、到手价）升序；`offers[0].is_lowest=true`。不要在前端重排。
- **unavailable_sources**：逐条渲染渠道名+原因（含"卡类渠道"说明）。
- 参数：`from`/`to`/`date` 必填，`source` 可选（同 CLI）。400 载荷 `{ok:false,error}`；`empty_hint` 为空结果中文提示（含"城市已下线"标注）。

## 3. GET /api/deals —— 特价票清单 + 卡池（两类结构，旧 DY 券字段废止）

```json
{"ok": true,
 "notice": "[演示数据] 以下特价票与卡类商品来自内置演示票源，非真实商品，不能直接购买。",
 "special_fares": [
   {"deal_id": "SP-02", "origin": "SHA", "destination": "JJN", "destination_display": "泉州",
    "route_text": "上海→泉州", "date": "2026-10-27", "price": 179,
    "depart_time": "12:45", "arrive_time": "14:20", "flight_no": "9C6714",
    "carrier_code": "9C", "carrier_name": "春秋",
    "dep_airport": "上海虹桥国际机场", "arr_airport": "泉州晋江国际机场",
    "label": "特价票·不含机建燃油费｜省 ¥41",
    "note": "特价票·固定航班日期·不含机建燃油费",
    "entry_url": "https://v.douyin.com/USSyX47SP7s/", "entry_note": "…"}],
 "cards": [
   {"card_id": "DY-101", "name": "省钱卡单人版365天", "price": 79,
    "validity_text": "365 天（自购买日起）", "scope": "每张机票同日同航班优惠 40 元起，…",
    "route_text": "每张机票同日同航班优惠 40 元起，…", "stock": "充足",
    "refund_disclosed": false, "refund": "退改规则未披露（…）", "purchase": "需在抖音 App 内下单，…",
    "note": "下单后立减，具体以下单页为准", "updated": "2026-10-25",
    "entry_url": "https://v.douyin.com/USSyX47SP7s/", "entry_note": "…"}]}
```

- `special_fares` 恒 21 条（不传 from/to）；`route_text` 中"黔江(重庆)"为照抄截图口径，逐字展示。
- `cards` = 6 张团购卡 + 当日 4 张直播间闪购卡；闪购卡有 `route_text`（"上海 → 成都"样式）且 `card_id` 形如 `LIVE-xxx`。`refund_disclosed=false` 的卡渲染"退改规则未披露"徽标；`stock="售罄"` 显著置灰。
- `from`/`to` 可选过滤（三字码）。

## 4. GET /api/trend —— 24 小时曲线（已切到手价口径）

```json
{"ok": true, "route": "上海(SHA) → 成都(CTU)", "date": "2026-11-15",
 "price_basis": "到手价（票面+机建+燃油）",
 "current": 830, "day_low": 782, "day_high": 886,
 "points": [{"hours_ago": 23, "time": "10-25 14:05", "price": 801}, {"hours_ago": 0, "time": "10-25 13:05", "price": 830}],
 "notice": "演示波动：近 24 小时到手价（票面+机建+燃油）为确定性生成的重放，非真实报价。"}
```

- `points[].price` 语义已变为**到手价**（v1.3 为含税票面）；轴标签建议用 `price_basis`。
- 个别点可能缺失（当时无可比最低价）→ 按现有 points 连线即可；无点时 `current/day_low/day_high` 为 null。

## 5. GET /api/sources —— 渠道清单（entry_url 已更新）

`douyin_miniapp` 与 `douyin_groupbuy` 的 `entry_url` 均为 `https://v.douyin.com/USSyX47SP7s/`（春秋航空地点页短链；"前往渠道 ↗"可点击，但页面需注明"需在抖音 App 内下单/打开"）。`airline` 的 `entry_url` 为空串 → 不渲染链接。

## 6. /api/watch/* —— 收藏（判定已切到手价；已下线城市显式标注）

- `GET /api/watch/list` 的 items：
```json
[{"origin": "SHA", "destination": "HKG", "threshold": 800, "created_at": "…",
  "check_date": "2026-10-26", "price_basis": "到手价（票面+机建+燃油）",
  "retired": true, "status_note": "城市已下线（v1.4 城市全集收缩），不再参与比价与提醒：香港",
  "current_lowest": null, "below_threshold": false},
 {"origin": "SHA", "destination": "CTU", "threshold": 700, "retired": false,
  "status_note": null, "current_lowest": 830, "below_threshold": false}]
```
- `retired=true` 的行：渲染"城市已下线"状态（禁用/灰化查询入口），数据仍展示（不静默删除，由用户自行删除）。
- `current_lowest` 为到手价；`below_threshold` 为到手价判定。
- `GET /api/watch/check`：`lines[].status ∈ hit/wait/info/none/retired`，`text` 为现成中文文案；`retired` 行按上面文案渲染。
- `POST /api/watch/set|remove`：参数与 v1.3 一致（`from`/`to`/`price` 可空）；损坏文件返回 409 + `error`（弹窗展示即可，不要重试覆盖）。

## 7. 前端注意事项（红线）

1. `notice`/`refund`/`included_desc`/`empty_hint` 等中文文案**逐字渲染**，不要改写或"补全"（未披露/售罄/退改未披露是测试资产）。
2. 特价票行（`is_special_fare=true`）建议独立区块置顶风格；其 `price` 即真实截图票价，`fee_breakdown.included_desc="不含机建燃油费"` 必须显示。
3. `channel_count>1` 的"另有 N 个渠道有售"徽标：N = `channel_count - 1`。
4. 时间字段 null 的行（券/引流票）显示"—"；不要为它们伪造航班字段。
5. 所有价格展示用 `total_estimate` 做主数字；`price` 是票面价小字。
6. 城市下拉不要写死城市清单，从 `/api/cities` 拉取（增删城市只改注册表一处）。

## 8. PRD 与任务口径的既定裁定（实现时发生的取舍，PM 已按任务书裁定，UI 知悉即可）

- **直播间闪购行无航班字段**：PRD 第 5 节把"直播间闪购"列入 6 要素清单，但任务书裁定"券无航班号、不参与去重"，且闪购卖的是 3 日券而非具体航班——按券处理（时间/机场为 null）。已在下方 Issue 回填记录。
- **特价票展示航班号但不参与去重**（`no_dedup` 内部标记）：PRD 第 5 节要求 6 要素，任务书第 5 点裁定特价票不参与去重——两者同时满足。
- **团购渠道出现在 `unavailable_sources`**：原因文案已说明"卡类渠道"，属覆盖模型不变式（每个渠道要么有报价要么有原因），不是 bug。
- **卡面价固定无波动**（真实团购"固定价"），比价行价格仍有分钟级演示波动。
