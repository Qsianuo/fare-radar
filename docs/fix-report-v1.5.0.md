# FareRadar 修复交付报告 v1.5.0（ISSUE-012）

- 执行依据：评审人《修复执行提示词》（极限版）+ 总协调者修复裁定 + ISSUE-012 证据
- 目标航线/日期：银川(INC) → 广州白云(CAN)，2026-11-01（周日）｜基准价格源：航旅纵横｜可接受价差：¥50（同口径）
- 技术栈：Python 3.10 纯标准库（CLI: argparse + http.server；测试 unittest）
- 分支：feature/v1.5（未合并）｜测试基线：v1.4.0 全绿 117 例 → 修复后全绿 144 例（改写 39 例、新增 24 例、净增 27 例）

---

## 1. 变更文件清单

| 文件路径 | 改动说明 | 改动原因 |
|---|---|---|
| src/sources/snapshot_pack.py | **新增**：实测快照数据层（5 直飞 + 3 中转 + 临近日期最低价条，逐行标注"航旅纵横（评审人 2026-10-25 实测截图）"） | 问题 2/3 根治：编造行退场后，比价行唯一合规来源为人工核验实测快照 |
| src/sources/base.py | Source 基类新增 `status`/`status_reason` 属性与渠道状态契约说明 | 问题 4：未接入渠道需显式声明，不假装接入 |
| src/sources/__init__.py | 注册 SnapshotPackSource；source_catalog() 输出 `status`/`reason` | 问题 2/4：快照渠道入册；/api/sources 携带接入状态 |
| src/sources/airline.py | 引擎"6 航司正班"行退场，改为 `status="not_available"` + 原因"暂无合规公开接口，未接入" | 问题 3：航班号/时刻为哈希编造值（与真实计划零重合） |
| src/sources/ota.py | "特惠代理票/低价引流票"行退场，同上声明未接入 | 问题 1/3：引流票行无航班号；代理票复用编造正班 |
| src/sources/douyin_live.py | 引擎定价的轮换场次/闪购卡退场，同上声明未接入 | 问题 3：券价与场次为模拟数据 |
| src/sources/douyin_miniapp.py | 仅保留 21 条固定特价票（真实商品）；删除"会员价常规航班"与"当日无座"机制；特价票不再携带编造航班号/时刻（诚实"未披露"） | 问题 3：真实商品保留（评审人提供），编造字段清除 |
| src/fareradar.py | 新增 `flight_display()`；CLI 航班列改航班号一等展示；未接入/无销售渠道分组提示；临近日期最低价条；未收录航线显式提示；DATA_NOTICE/DEALS_NOTICE 文案；collect() 不覆盖快照自带含税价；unavailable 携带 status | 问题 1/2/3/4 渲染与治理主逻辑 |
| src/webapp.py | /api/trend（演示走势）退役 → /api/nearby（临近日期最低价，快照数据）；/api/search 增加 `sources_status`、`cross_day`、`is_snapshot`；notice 更新 | 问题 2/4：演示波动全面移除，渠道状态透出 |
| src/webapp.html | flightCell 去重拼接（"南航 CZ · CZ9782"→"南航 CZ9782"）；移除"演示波动"标签与走势/自动刷新；新增临近日期最低价条与渠道灰化 chip（.chip-na）；票源下拉禁用未接入项；徽标改"实测快照数据"；页脚 v1.5.0 | 问题 1 渲染缺陷 + 演示波动移除 + 渠道状态灰化 |
| tests/test_fareradar.py | 改写 14 例（快照口径/存根源/未收录航线/静态确定性）；删除引擎波动与闪购场次用例 | 受影响用例随数据层裁决改写（清单见附录 A） |
| tests/test_v14_features.py | 改写 16 例（去重单元化/特价票不编航班号/费用引擎作用于特价票/卡池 6 张/ retired 渠道零行） | 同上 |
| tests/test_webapp.py | 改写 9 例 + 新增 1 例（INC→CAN 契约/sources_status/nearby/stub watch 流程） | 同上 |
| tests/test_v15_snapshot.py | **新增 24 例**：快照逐字断言（5 直飞/3 中转/临近日期条）、0 编造航班号（含 CZ9782/CZ8645/CZ7836 反向断言）、渲染去重拼接、未收录航线提示、sources_status 契约 | 评审人验证用例的程序化落地 |
| docs/fix-report-v1.5.0.md | **新增**：本报告 | 交付物 |
| issues/ISSUE-012.md | 回填 Fix（Dev 填）段 | 项目流程 |

---

## 2. 问题 1 修复：搜索结果「航班号」字段缺失 / 显示为「南航 CZ · CZ9782」

### 根因
渲染层把承运人二字码与航班号重复拼接：webapp.html `flightCell` 执行 `[carrier_name, carrier_code].join(" ") + " · " + flight_no`，carrier_code="CZ" 且 flight_no="CZ9782" 已含二字码，产出"南航 CZ · CZ9782"。同时"低价引流票"行与卡类行无 flight_no 字段，航班/机场显示"—"（评审人原话第 3 点）；CLI 的"航班/套餐"列展示 product 而非一等航班号字段。

### 修复方案（关键 diff）
航班号作为一等字段贯穿"数据模型 → 票源 → API → 渲染"：快照行/中转行携带 `flight_no`（自带二字码，如 `CZ3270`、`CA4270|CA4349`）；展示规则统一为"承运人中文名 + 航班号"，二字码不再单独拼接。

新增共享展示函数（src/fareradar.py，CLI 与测试共用，规则唯一来源）：

<file>
def flight_display(offer):
    """航班号一等字段的展示名（ISSUE-012 问题 1/3）：统一"南航 CZ9782"格式。

    航班号本身已含承运人二字码（如 CZ9782 / CA4270|CA4349），展示时只拼
    承运人中文名——禁止再现"南航 CZ · CZ9782"式二字码重复拼接；无航班号的
    行（特价票卡面未展示/卡类）返回空串，由调用方按"未披露"兜底。
    """
    flight_no = offer.get("flight_no")
    if not flight_no:
        return ""
    carrier_name = (offer.get("carrier_name") or "").strip()
    return f"{carrier_name} {flight_no}" if carrier_name else flight_no
</file>

CLI 航班列（src/fareradar.py）：

<file>
def _flight_text(offer):
    """航班/套餐列：有航班号的行展示"厦航 MF8753"，否则展示产品名。"""
    return flight_display(offer) or offer["product"]
</file>

Web 渲染（src/webapp.html flightCell，旧拼接整体移除）：

<file>
   if (o.flight_no) {
-    const line = el("div", "flight-line");
-    const carrier = [o.carrier_name, o.carrier_code].filter(Boolean).join(" ");
-    line.textContent = carrier ? carrier + " · " + o.flight_no : o.flight_no;
+    // 航班号一等字段：flight_no 自带承运人二字码，只拼承运人中文名——
+    // 禁止"南航 CZ · CZ9782"式重复拼接（ISSUE-012 渲染缺陷）
+    const line = el("div", "flight-line");
+    const carrier = (o.carrier_name || "").trim();
+    line.textContent = carrier ? carrier + " " + o.flight_no : o.flight_no;
</file>

特价票卡面（webapp.html specialFareCard）：卡面未展示航班号 → 诚实渲染"未披露（以下单页为准）"，禁止生成值填充。

取舍：不在 flight_no 字段里存"南航 CZ9782"整串（字段保持纯航班号，展示层负责文案），保证去重键（出发日期+航班号）与未来真实源接入不受展示格式影响。

### 验证步骤
1. `python -m unittest discover tests`（RenderDedupTest 3 例：flight_display 规则、HTML 旧模式 `carrier + " · " + o.flight_no` 不存在、CLI 无" · "拼接）。
2. 浏览器打开 http://127.0.0.1:8765/，读 DOM 快照核对表格航班列。

### 验证结果
- 144/144 全绿；RenderDedupTest 通过（HTML 中旧拼接模式与"演示波动"标签均不存在，页脚 v1.5.0）。
- 浏览器实测渲染：`国航 CA4270|CA4349`、`厦航 MF8753`、`长龙航 GJ8258`、`深航 ZH8942`、`九元 AQ1275`、`南航 CZ3270`、`华夏·川航 G54338|3U8119`、`春秋 9C6202|9C8680`——全部为"中文名 + 航班号"，零重复拼接；跨日到达显示"+1"角标。

---

## 3. 问题 2 修复：价格比航旅纵横高约 ¥600

### 根因
三层：① 工具价格为引擎模拟值（距离线性定价 + 分钟级演示波动），与真实行情无关；② 口径不一（工具标"已含机建+燃油"实为引擎定价，基准为裸价/含税双口径）；③ 航司覆盖 100% 南航（引擎按枢纽分配），缺低成本航司（厦航/九元等）低价票。评审人实测：真实最低 MF8753 裸价 ¥500（含税 ¥640），工具最低 ¥978（自报"已含"）→ 含税口径差 ¥338，叠加口径混淆呈现约 ¥600 体感差。

### 修复方案（关键 diff）
引擎编造行全部退场；新增实测快照数据层为唯一航班比价来源；价格逐字采用快照（裸价/含税双口径），含税价直接作为到手价，引擎费用模型不覆盖快照行；演示波动在比价行全面移除（静态数据）。

快照行自带实测含税价，引擎不覆盖（src/fareradar.py collect）：

<file>
             for offer in source.search(origin, destination, date):
                 offer["entry_url"] = source.entry_url
                 offer["entry_note"] = source.entry_note
-                if km is not None:
+                # 实测快照行自带实测含税价（fee_breakdown 已标注来源），引擎不覆盖
+                if offer.get("total_estimate") is None and km is not None:
                     offer["fee_breakdown"] = fees.fee_breakdown(
                         offer["price"], offer.get("tax_included", True), km,
                         offer.get("included_desc"))
</file>

快照费用说明不拆分机建/燃油（拆分即编造，src/sources/snapshot_pack.py）：

<file>
def _snapshot_fee(price, total, tax_included):
    """快照行的费用说明：只给实测两个口径值，不拆分机建/燃油（拆分即编造）。"""
    if tax_included:
        desc = f"实测快照：¥{total}（中转组合价，含税口径）"
    else:
        desc = f"实测快照：含税 ¥{total}（含机建+燃油）"
    return {"airport_fee": None, "fuel_fee": None,
            "tax_included": tax_included, "included_desc": desc}
</file>

演示波动退役：/api/trend → /api/nearby（临近日期最低价条，快照数据）；网页自动刷新/走势/仪表区间随演示波动机制一并移除；CLI 增加临近日期条输出：

<file>
     nearby = snapshot_pack.nearby_lowest(origin, destination)
     if nearby:
         bar = "｜".join(f"{point['label']} ¥{point['price']}" for point in nearby)
         print(f"临近日期最低价（裸价，来源：{snapshot_pack.DATA_SOURCE}）：{bar}")
</file>

### 多源价格对照表（同口径：含税 = 裸价 + 机建+燃油；抓取时刻均为 2026-10-25）

| 来源 | 航班 | 价格（裸价/含税） | 舱位 | 税费 | 抓取时刻 | 与基准差异 |
|---|---|---|---|---|---|---|
| 航旅纵横（基准，评审人实测截图） | MF8753 | ¥500 / ¥640 | 未披露（当日最低价） | 已含于含税价 | 2026-10-25 | — |
| 航旅纵横（基准） | GJ8258 | ¥980 / ¥1120 | 未披露 | 同上 | 2026-10-25 | — |
| 航旅纵横（基准） | ZH8942 | ¥1000 / ¥1140 | 未披露 | 同上 | 2026-10-25 | — |
| 航旅纵横（基准） | AQ1275 | ¥1049 / ¥1189 | 未披露 | 同上 | 2026-10-25 | — |
| 航旅纵横（基准） | CZ3270 | ¥1170 / ¥1310 | 未披露 | 同上 | 2026-10-25 | — |
| 航旅纵横（基准） | CA4270\|CA4349（中转） | — / ¥550 | 未披露 | 同上 | 2026-10-25 | — |
| 航旅纵横（基准） | G54338\|3U8119（中转） | — / ¥710 | 未披露 | 同上 | 2026-10-25 | — |
| 航旅纵横（基准） | 9C6202\|9C8680（中转） | — / ¥780 | 未披露 | 同上 | 2026-10-25 | — |
| **本工具修复后（实测快照源）** | 同上 8 行 | 逐字同基准 | 未披露（展示机型/餐食） | 快照口径（差额 ¥140，不拆分） | 2026-10-25 | **¥0（≤ ¥50）** |
| 本工具修复前（引擎模拟，已退场） | CZ9782/CZ8645/CZ7836 | ¥978/¥999/¥1029（自报已含税） | 引擎虚拟舱位 | 引擎口径 | 运行时 | 含税口径 +¥338 起 |

注：舱位/税费明细快照未披露，本工具如实标"未披露"并以实测含税价为到手价，不做拆分推断。同程旅行未接入（无合规公开接口），无同口径抓取数据可比——如实声明，不编数。

### 验证步骤
1. `python src/fareradar.py search --from INC --to CAN --date 2026-11-01`，逐行对照 ISSUE-012 快照证据。
2. `python -m unittest discover tests`（SnapshotVerbatimTest/SearchVerbatimTest/NearbyBarTest/StaticSnapshotTest）。

### 验证结果
- CLI 输出 8 行与快照逐字一致：最低到手价 ¥550（中转，与基准"中转 ¥550 起"一致）、直飞含税最低 ¥640（与基准一致）、裸价最低 ¥500——**同口径价差 ¥0 ≤ ¥50**。
- 临近日期条逐字：10-30 ¥450｜10-31 ¥450｜11-01 ¥500｜11-02 ¥490。
- StaticSnapshotTest：不同查询时刻输出逐字节一致（无波动）；全库 144 例全绿。

---

## 4. 问题 3 修复：部分航班号疑似虚假、无法实际查询到

### 根因
`airlines._flight_no` 以 md5 哈希生成"CZ9782/CZ8645/CZ7836"式航班号、`build_flight` 派生时刻——与真实航班计划零重合（评审人交叉验证：航班号零重合、时刻全不符，判定为虚构）；引擎正班/OTA 代理/小程序常规票/直播闪购全部使用该生成器；21 条特价票（真实商品）也被生成了 9C 编造航班号。

### 修复方案（关键 diff）
编造行退场 + 航班号只允许来自实测快照白名单；真实商品（特价票）去掉编造字段，诚实"未披露"。

以航司直连为例（ota/douyin_live 同模式）：

<file>
 class AirlineSource(Source):
-    """航司直连：display_name 为 PRD 裁定的用户语言，"演示"由输出层声明承担。"""
-
     source_id = "airline"
     display_name = "航司直连"
-    tagline = "国航/东航/南航/海航/春秋/吉祥官网直飞正班，退改按航司规则"
+    tagline = "各航司官网/官方小程序直飞正班（暂无合规公开接口，未接入）"
     entry_url = ""  # 多家航司无单一入口；前端对空链接不渲染
     entry_note = "各航司官网/官方小程序"
+    status = "not_available"
+    status_reason = "暂无合规公开接口，未接入"
+
+    def availability(self, origin, destination, date):
+        return False, self.status_reason
 
     def search(self, origin, destination, date):
-        flights = airlines.route_flights(origin, destination)
-        if flights is None:  # 城市码不在注册表或同城
-            return []
-        offers = []
-        for flight in flights:
-            carrier = airlines.AIRLINES[flight["carrier_code"]]
-            price = pricing.quote(origin, destination, date,
-                                  key=f"airline|{flight['flight_no']}",
-                                  tier=carrier["tier"])
-            offers.append({
-                "source_id": self.source_id,
-                "source_name": self.display_name,
-                "product": flight["flight_no"],
-                "price": price,
-                "tax_included": carrier["tax_included"],
-                "is_deal": False,
-                "note": f"{flight['carrier_name']}{flight['carrier_code']} 直飞正班",
-                **{k: flight[k] for k in ("depart_time", "arrive_time", "duration_text",
-                                          "dep_airport", "arr_airport",
-                                          "carrier_code", "carrier_name", "flight_no")},
-            })
-        return offers
+        return []  # 未接入：不出任何比价行（真实直飞行情见实测快照渠道）
</file>

特价票不再编造航班号（src/sources/douyin_miniapp.py，核心裁剪）：

<file>
             return {
                 "source_id": self.source_id,
                 "source_name": self.display_name,
                 "product": f"特价票 上海→{_dest_display(dest)}",
                 "price": price,
                 "tax_included": False,       # "不含机建燃油费"：票面不含税费
                 "is_deal": False,
                 "is_special_fare": True,
                 "no_dedup": True,            # 特价票不参与跨渠道去重（渠道独家固定产品）
                 "included_desc": "不含机建燃油费",
                 "carrier_code": "9C",
                 "carrier_name": "春秋",
                 "flight_no": None,           # 卡面未展示航班号——禁止编造填充
                 "note": f"特价票·固定航班日期·不含机建燃油费｜{_SAVE_TAG}"
                         f"｜限 {fixed.isoformat()} 出发｜航班号与时刻未披露（以下单页为准）"
                         f"｜需抖音 App 跳转小程序下单",
             }
</file>

未收录航线显式提示（src/fareradar.py，webapp empty_hint 同规则）：

<file>
         elif snapshot_selected and not snapshot_pack.is_covered(origin, destination, date):
             print(f"该航线暂无真实数据快照；当前收录范围：{snapshot_pack.coverage_text()}。")
             print("可到航旅纵横/航司官网等官方渠道查询实时报价。")
</file>

### 航班号可查性校验抽样

- **样本数：11**（8 条比价行、11 个航班号段：MF8753、GJ8258、ZH8942、AQ1275、CZ3270、CA4270、CA4349、G54338、3U8119、9C6202、9C8680）
- **命中数：11**（每个航班号均可溯源至评审人航旅纵横 2026-10-25 实测截图的原文记录，可查性=通过）
- **命中率：100%**
- 反向断言：v1.4 编造航班号 CZ9782/CZ8645/CZ7836 在 CLI 输出与 Web 载荷中出现次数为 **0**（test_v15_snapshot.ZeroFabricatedFlightNoTest 白名单 + 反向断言锁定）。
- 特价票 21 条：flight_no/depart_time 恒为 None（编造通道已封死，单测锁定）。

### 验证步骤
1. `python -m unittest discover tests`（ZeroFabricatedFlightNoTest 4 例、SourceFamilyTest.test_retired_channels_produce_no_rows、test_v14_features.SpecialFareTest）。
2. CLI/Web 双入口输出比对 ISSUE-012 原文。

### 验证结果
- 比价结果航班号白名单命中 11/11（100%），0 编造；三个退场渠道对任意航线/日期 search() 恒返回 []、deals() 恒返回 []。
- 未收录航线（SHA→PEK）返回空 + "该航线暂无真实数据快照；当前收录范围：银川(INC) → 广州(CAN) 2026-11-01"。
- 全库 144 例全绿。

---

## 5. 问题 4 修复：已接入数据源清单（来源｜接入方式｜合规性说明）

| 来源 | 接入方式 | 合规性说明 |
|---|---|---|
| 实测快照（snapshot_pack，display_name"实测快照"） | 人工核验静态快照：评审人 2026-10-25 航旅纵横实测截图（INC→CAN 2026-11-01 全量航班/价格/临近日期条），结构化入库、逐行标注来源与抓取时刻；**非自动接口** | 数据由评审人人工获取并授权入库，不涉及程序抓取；可溯源、可人工扩充 |
| 抖音小程序·春秋航空（douyin_miniapp，active） | 21 条固定特价票真实商品信息（评审人提供的抖音渠道商品截图，固定日期/价/标签逐字）+ 渠道外链 `https://v.douyin.com/USSyX47SP7s/`；**非自动接口** | 无自动抓取；购买跳转抖音官方渠道；航班号/时刻卡面未披露的如实标"未披露" |
| 抖音团购（douyin_groupbuy，active） | 6 张次卡/省钱卡真实商品信息（含 4 张"退改规则未披露"资产、1 张确定性售罄 DY-103）+ 渠道外链；**非自动接口** | 无自动抓取；卡面文案照抄（含"潮汕"等注册表外城市）；不卖具体航班，不计入比价 |
| 航司直连（airline）、OTA 比价（ota）、抖音直播间（douyin_live） | **未接入**：status="not_available"，reason="暂无合规公开接口，未接入"（/api/sources、CLI、网页灰化 chip 三处显式呈现） | 航旅纵横、同程旅行、携程及各航司官网均无面向第三方的公开 API；春秋航空 https://www.ch.com/ 《法律声明》明文禁止程序自动获取官网数据——按评审人约束不批量抓取、不假装接入；引擎模拟行已全部退场 |

结论：**航旅纵横、同程旅行、各航司官网均未以自动接口形式接入**（如实声明"暂无合规公开接口"）；当前全部数据行来自人工核验的实测快照与真实商品两类合规来源。

### 验证步骤
`python -m unittest discover tests`（SourcesStatusContractTest、SourceFamilyTest、test_webapp.test_sources_carry_new_location_link）；浏览器读取 /api/sources 与页面灰化 chip。

### 验证结果
- 契约：airline/ota/douyin_live → status="not_available" + reason="暂无合规公开接口，未接入"；snapshot_pack/douyin_miniapp/douyin_groupbuy → "active"。
- 页面实测：票源下拉"航司直连（未接入）/OTA 比价（未接入）/抖音直播间（未接入）"灰化禁选；结果区渠道 chip 灰化展示并附原因提示。
- 全库 144 例全绿。

---

## 6. 未解决项与风险（如实）

1. **真实数据无法自动化获取**：航班价格/座位实时变化，本工具无合规自动接口，数据时效完全依赖人工更新快照；当前快照抓取时刻为 2026-10-25，届时价格可能已变化。已在每行输出标注来源与抓取时间缓解误导风险。
2. **快照覆盖面极小**：仅收录 INC→CAN 2026-11-01 一条航线一个日期（5 直飞 + 3 中转 + 临近日期条）；其余航线/日期一律显式"暂无真实数据快照"，不提供任何价格。
3. **税费明细未拆分**：快照含税差额 ¥140 来自两个实测值相减，机建/燃油拆分为编造风险，故费用列展示实测总口径（单测锁定 airport_fee/fuel_fee=None）。
4. **特价票/卡池为商品信息快照**：库存、下架状态、退改细则无法实时校验，购买以抖音购买页为准（输出已声明）。
5. **休眠代码**：pricing/airlines 引擎定价与航班生成模块保留（未参与任何输出），供未来接入合规真实源时复用；其对应生成逻辑已有单元测试锁定确定性，但不再进入比价链路。
6. **待确认项**：同程旅行是否存在可申请的商务 API（未验证，显式声明"待确认"）；航旅纵横有无企业级数据合作通道（未验证）。

---

## 交付前自检（评审人 output_format）

- [x] 每个问题四段齐全（根因/修复方案含 diff/验证步骤/验证结果）
- [x] 所有航班号均来自真实源（实测快照白名单，抽样命中率 100%；编造号反向断言为 0）
- [x] 未违反任何航司条款（无程序抓取；未接入渠道显式声明"暂无合规公开接口，未接入"）
- [x] 价格同口径比对（含税对含税、同抓取时刻），INC→CAN 2026-11-01 价差 ¥0 ≤ ¥50
- [x] 未省略任何一节；未用"同上/略"替代验证结果；验证不达标项无（144 例全绿后交付）

## 附录 A：受影响测试改写清单（39 例逐条）

**tests/test_fareradar.py（14 例改写）**：test_collect_merges_flight_sources→test_collect_merges_snapshot_rows；test_rank_offers_by_total_price（改快照航线）；test_search_output_contract（首标签改"实测快照"）；test_all_engine_rows_have_fee_breakdown_and_total→test_engine_fee_rows_have_breakdown_and_total；test_search_marks_lowest_by_total（INC→CAN）；test_tax_retired_flag_absent→test_fee_descs_verbatim；test_live_sold_out_row_disclosed_when_hit→test_live_channel_retired_but_soldout_asset_kept；test_not_covered_live_coupon_excluded→test_not_covered_coupon_excluded（单元级）；test_no_alert_triggered_by_uncovered_coupon_only（存根券源）；test_broken_source_degrades / test_search_reports_unavailable_source（INC→CAN）；test_watch_check_hit_by_total_price / test_watch_check_miss / test_list_and_check_display_unthresholded_collection（存根源）。

**tests/test_v14_features.py（16 例改写）**：test_airline_source_rows_match_schedule→test_airline_source_retired_no_rows；test_engine_applies_distance_tiers（特价票航线）；test_ota_agent_row_carries_schedule_flight_no→test_ota_channel_retired；test_cross_channel_merge_keeps_lowest_total / test_same_channel_flights_never_merged / test_rows_without_flight_no_never_deduped（单元级+快照集成）；test_special_fare_exempt_from_dedup（flight_no=None）；test_fixed_times_deterministic_and_period_cycles→test_flight_fields_not_fabricated_and_deterministic；test_catalog_has_21_and_carries_full_fields→test_catalog_has_21_and_verbatim_fields；test_cli_card_hints_block_before_rows（INC→CAN）；test_deals_shows_special_fares_and_cards（卡池 6 张/未披露）；test_watch_check_skips_retired_without_query / test_web_watch_list_carries_retired_flag（无数据显式披露）；test_cgo_xiy_has_no_miniapp_quote→test_non_special_route_has_no_miniapp_quote；test_cli_reports_unavailable_sources / test_base_route_no_seat_day_deterministic→test_special_date_sellable_and_others_not；test_offers_carry_channel_entry（INC→CAN）。

**tests/test_webapp.py（9 例改写 + 1 例新增）**：test_search_payload_contract / test_search_rows_carry_flight_six_elements→test_search_rows_carry_flight_fields / test_search_channel_count_and_note→test_search_note_carries_snapshot_disclosure / test_search_retired_city_hint / test_card_hints_block / test_watch_roundtrip / test_search_reports_unavailable_and_card_channels / test_sources_carry_new_location_link / test_deals_payload_two_sections / test_trend_uses_total_price_basis 与 test_trend_rejects_bad_input→test_nearby_bar_uses_snapshot_verbatim、test_nearby_uncovered_route_empty_with_hint、test_nearby_rejects_bad_input / test_search_deterministic_same_minute→test_search_deterministic_same_input；新增 test_watch_check_hit_and_wait_with_stub_source。

**tests/test_v15_snapshot.py（新增 24 例）**：SnapshotVerbatimTest×6、ZeroFabricatedFlightNoTest×4、SearchVerbatimTest×3、NearbyBarTest×3、RenderDedupTest×3、UncoveredRouteDisclosureTest×2、SourcesStatusContractTest×3。

**类更名**：DemoFluctuationTest→StaticSnapshotTest（3 例）、FiveSourceFamilyTest→SourceFamilyTest（5 例）、AnyRoutePricingTest→UncoveredRouteTest（4 例）。
