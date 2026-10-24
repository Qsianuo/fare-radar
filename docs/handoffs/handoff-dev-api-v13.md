# 交接说明：Dev → UI 工程师（v1.3.0 前端重做，API 契约）

- 交接人: Dev（后端/数据层第一棒）| 接收人: UI 工程师（第二棒）| 日期: 2026-10-25
- 分支: `feature/v1.3`；后端已全部就绪、60 例单测全绿。**你只改 `src/webapp.html`，不碰任何 Python 文件**（`webapp.py` 会原样提供下列接口）。
- 口径依据: docs/02-PRD-v1.3.md 第 8 节（仪表/收藏）、第 9 节（UI 设计原则，验收=原则符合性）、第 7 节（演示波动）。

## 一、现有 webapp.html 结构（改造起点，292 行）

- 三页签：`tab-search`（搜索比价）/ `tab-deals`（团购专区）/ `tab-watch`（低价提醒→改收藏航线）；页签按钮 `data-tab` 切换，激活面板 class `panel active`。
- 表单与控件 id：`search-form`（`#from`/`#to`/`#date`/`#source`/`#search-status`/`#search-result`）、`deals-form`（`#deal-from`/`#deal-to`/`#deals-result`）、`watch-form`（`#watch-from`/`#watch-to`/`#watch-price`/`#watch-list`/`#watch-result`）。
- JS 工具：`$(id)` 取元素、`el(tag,cls,text)` 建节点、`api(path, params, method)` 请求封装（自动拼 query、`!data.ok` 时 throw `data.error`）、`notice(node,kind,text)`、`localDate(offsetDays)`。
- 需替换点：`<datalist id="cities">`（5 城按值过滤，缺陷根源）→ 换 `/api/cities` 渲染的完整中文 `<select>`；watch 页签整块重做为收藏航线组件。

## 二、API 契约（全部返回 `{"ok": true/false, ...}`；错误 `{"ok": false, "error": 中文说明}`，HTTP 400=参数错、404=无此接口、409=alerts.json 损坏）

### GET /api/cities —— 城市全集（AC1 城市下拉的数据源）

```json
{"ok": true, "count": 25,
 "cities": [{"code": "PEK", "name": "北京"}, {"code": "SHA", "name": "上海"}, ...]}
```
**前端用法**：出发/到达各渲染一个 `<select>`，`option value=code`、文本=中文名，25 项全量、无需输入过滤；默认出发 SHA、到达 CTU。

### GET /api/sources —— 票源清单（结果表图例/说明用）

```json
{"ok": true, "sources": [
  {"id": "airline", "name": "航司直连", "description": "航司官网/小程序直飞正班，退改按航司规则"},
  {"id": "ota", "name": "OTA 比价", "description": "多平台聚合比价，代理票退改更严、可能收服务费"},
  {"id": "douyin_groupbuy", "name": "抖音团购", "description": "精选套餐券，有效期＝可出行窗口，需抖音 App 下单"},
  {"id": "douyin_miniapp", "name": "抖音小程序·春秋航空", "description": "航司自营抖音小程序特价，不含行李额、时段偏早晚、退改较严"},
  {"id": "douyin_live", "name": "抖音直播间", "description": "直播间限量闪购：价低但库存少、易售罄、有效期短，需进直播间下单"}]}
```

### GET /api/trend?from=SHA&to=CTU&date=2026-11-20 —— 首页仪表数据（AC6）

```json
{"ok": true, "route": "上海(SHA) → 成都(CTU)", "date": "2026-11-20",
 "current": 459, "day_low": 443, "day_high": 475,
 "points": [{"hours_ago": 23, "time": "10-25 02:20", "price": 467}, ..., {"hours_ago": 0, "time": "10-25 01:20", "price": 459}],
 "notice": "演示波动：近 24 小时价格为确定性生成的重放，非真实报价。"}
```
**前端用法**：`current` 大字号呈现；`day_low`~`day_high` 画区间条/弧形表盘并标注 current 位置；`points`（时间升序 24 点）画折线；`notice` 必须随图展示。曲线为确定性重放，**不得标注"实时/秒级/行情"**。

### GET /api/search?from&to&date[&source] —— 比价（语义不变，载荷补字段）

```json
{"ok": true, "route": "上海(SHA) → 成都(CTU)", "date": "2026-11-20", "weekday": "周五",
 "notice": "[演示数据] 以下价格来自内置演示票源，非真实报价；价格含分钟级演示波动，不能用于实际购票。",
 "failed": [], "alert_hit": null, "warning": null, "empty_hint": null,
 "offers": [{
   "price": 459, "source_id": "douyin_groupbuy", "source_name": "抖音团购",
   "source_desc": "精选套餐券，有效期＝可出行窗口，需抖音 App 下单",
   "product": "川航联营 上海-成都 双城券", "tax_label": "含税",
   "note": "团购 ▶ 需抖音App下单｜有效期 2026-11-15~2026-12-10｜退改不明",
   "risk_flags": ["退改不明"], "is_deal": true, "is_lowest": true}, ...]}
```
**字段说明**：`offers` 已按准入排序（可竞争最低价的在前、按价格升序）；`is_lowest` 全表至多一个 true；`risk_flags`（结构化，逐行渲染成标记，勿再解析 note）：`税费另计，到手价高于票面` / `已售罄，暂时抢不到` / `退改不明` / `有效期不覆盖 <日期> 出发` / `有效期无法确认…`；`source_desc` 悬浮/次要行展示；`empty_hint` 非 null 时整块展示（不渲染空表）；`warning` 非 null 以醒目但非错误样式展示；`alert_hit` 为命中底价的行动建议文案。错误场景同 v1.2（400 + error）。

### GET /api/deals[?from&to] —— 团购专区（卡片补票源字段）

```json
{"ok": true, "notice": "[演示数据] 以下团购套餐来自内置演示票源，非真实商品，不能直接购买。",
 "deals": [{"deal_id": "DY-001", "source_name": "抖音团购", "name": "春秋航空 上海-成都 3日有效券",
   "price": 482, "tax_label": "含税", "stock": "紧张", "route_text": "上海(SHA) → 成都(CTU)",
   "validity": "2026-11-01 ~ 2026-11-30", "refund": "未使用可退，收 30% 手续费；过期作废；不可签转",
   "purchase": "需在抖音 App 内下单，本工具不提供购买", "note": "限指定日期航班，需提前 3 天在抖音预约",
   "updated": "2026-10-23"}, ...]}
```
`source_name` 可为"抖音团购"或"抖音直播间"（卡片需能区分票源）；`stock` 可能是"售罄"（灰色/标记处理）；`refund`/`updated` 等已做"未披露"兜底，直接展示。

### GET /api/watch/list / GET /api/watch/check / POST /api/watch/set / POST /api/watch/remove —— 收藏航线（原"低价提醒"，AC5）

```json
// list（新增 current_lowest / below_threshold，供"一眼看到当前最低价+绿色标记"）
{"ok": true, "date": "2026-10-26",
 "alerts": [{"origin": "SHA", "destination": "CTU", "threshold": null, "created_at": "2026-10-25 10:00",
             "check_date": "2026-10-26", "current_lowest": 470, "below_threshold": false}, ...]}
// set：from/to 必填，price 可省略（省略=未设底价收藏，threshold 存 null）；price 给定须为正整数
{"ok": true, "message": "已收藏航线：上海(SHA) → 成都(CTU)（未设底价）。"}
// check（逐条状态）：{"ok": true, "date": "...", "notice": "...", "lines": [
//   {"route": "...", "status": "hit|wait|info|none", "text": "..."}]}
// remove：{"ok": true, "message": "已删除收藏：上海(SHA) → 成都(CTU)。"}
```
**前端用法**：收藏列表 = origin/destination（中文渲染用 /api/cities 映射）+ `current_lowest`（大数字）+ `threshold`（null 时显示"未设底价"）+ `below_threshold=true` 时加**绿色**标记（绿色全场唯一语义）+ 删除按钮（POST remove）。`threshold` 为 null 是 v1.3 授权演进，勿按 0 处理。

## 三、设计原则与红线（验收口径）

1. PRD 第 9 节 5 条逐条落实：价格第一视觉、声明常驻不喧宾、与 CLI 同词表（票源名/标记全部来自 API，不要自造文案）、纯 CSS 无 CDN/框架/字体、无动效堆砌。
2. 页面任何位置不得出现"实时/秒级/行情"；演示数据声明常驻首屏。
3. `webapp.py`/Python 一律不改——缺字段或契约疑问写回本文件备注找 Dev，不要前端硬造数据。

## 四、Dev 记录的技术决定与 PRD 差异（需评审知悉，前端无需处理）

1. **演示波动统一作用于全部报价**（含固定价精选券，按 deal_id 相位 ±3.5%）：PRD §7 授权技术方案定幅度/周期；若只波动引擎票源，精选券为最低价时曲线全平、"会呼吸"落空。既有 459/499 等锚价为基准价，显示价=基准价×波动因子。
2. **任务书测试示例 SHA→URC 不可查**：URC 不在 PRD 第 4 节 25 城清单（清单即边界）。单测改用 HRB→SYX、KMG→HKG 等清单内偏远航线；若产品坚持 URC，在 `cities.py` 的 `_CITIES` 加一行即可（唯一改动点）。
3. 直播间闪购"3 日券"有效期＝自查询日起 3 天（可出行窗口口径），远期搜索会如实标注"有效期不覆盖"；闪购场次按天确定性轮换（每日 4 条航线）。
4. `--source` 的票源 id 由 `simulated_airline` 更名为 `airline`，并新增 ota/douyin_miniapp/douyin_live（CLI 参数语义与退出码约定未变）。
