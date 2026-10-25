# 低价票雷达 FareRadar —— 技术方案 v1.0

| 项 | 内容 |
|---|---|
| 作者 | Dev |
| 日期 | 2026-10-24 |
| 依据 | 02-PRD-v1.0.md、handoff-pm-to-dev.md、04-douyin-groupbuy-research.md |
| 硬约束 | 仅 Python 3.10 标准库；总代码量 ≤500 行；单机 CLI，零网络请求 |

## 1. 总体架构

三层结构，依赖单向向下，CLI 层不认识任何具体票源：

```
src/fareradar.py          CLI 层：参数解析/校验、合并排序、格式化输出、提醒判断、alerts.json 读写
src/sources/__init__.py   注册表：all_sources() / source_by_id()（可插拔的唯一改动点）
src/sources/base.py       统一 Source 抽象（接口契约）
src/sources/simulated_airline.py   模拟航司源（比价行）
src/sources/douyin_groupbuy.py     模拟抖音团购源（团购卡 + 比价行）
```

入口形态：单文件 `python src/fareradar.py <命令>`。脚本运行时 Python 自动将 `src/`
置于 `sys.path[0]`，故 `import sources` 零配置可用；不提供 `-m` 入口（见 Q1）。

## 2. 票源适配器接口（统一 Source 抽象）

```python
class Source:                      # base.py
    source_id = ""                 # 机器名，用于 --source 过滤，如 "douyin_groupbuy"
    display_name = ""              # 展示名，如 "抖音团购源(模拟)"
    def search(self, origin, destination, date) -> list[dict]   # 比价行
    def deals(self, origin=None, destination=None) -> list[dict] # 团购卡，非团购源返回 []
```

- **比价行**（search 返回）统一字段：`source_id / source_name / product / price /
  tax_included / is_deal / note`。航司行 product=航班号；团购行 product=套餐名，
  note 含"团购 ▶ 需抖音App下单"，满足 PRD AC3。
- **团购卡**（deals 返回）严格执行 04 文档字段契约：`deal_id / source_name / route /
  price+currency+含税口径 / valid_from / valid_until / stock / purchase_entry（必含
  "需在抖音 App 内下单"文案）/ refund_change_rules / updated_at(可选)`。
  缺失字段由 CLI 层统一兜底显示"未披露"，适配器不做展示逻辑。
- **可插拔验收口径**：新增模拟票源 = 新增一个模块实现 Source 并在
  `sources/__init__.py` 的 `all_sources()` 登记，search/deals/watch 逻辑零改动
  （PRD 4.5）。模拟团购数据内置一条退改规则缺失的记录（DY-005），用于验证兜底红线。

## 3. 数据流

```
CLI 参数 → 中文校验（三字码/日期/票价） → collect()：逐源调用 search，
单源异常捕获降级 → 合并 → sort_offers() 价格升序 → 最低价标注 + alerts 命中判断
→ 全中文格式化输出（CJK 等宽对齐）
```

模拟数据设计：航司源 5 条航线（SHA→PEK、SHA→CTU、SHA→KIX、PEK→SHA、CTU→SHA），
票价 = 基础价 + 按星期浮动系数（确定性，同日期结果可复现、可测试）；团购源 5 张
套餐，价格明显低于直连但带有效期/预约/退改限制。全部内置常量，无随机、无网络。

## 4. 错误处理策略

| 场景 | 行为 |
|---|---|
| 参数缺失/非法（含日期格式、日期早于今天、三字码不合法、--source 未知） | 中文错误说明 + 用法示例，退出码 2 |
| 单票源适配器抛异常 | 捕获，输出"[提示] XX 源暂不可用"，其余票源照常展示（PRD AC2） |
| 搜索结果为空 | 输出友好提示 + 已接入票源清单，退出码 0（非用户错误） |
| alerts.json 缺失/损坏 | 按"无关注"处理，下次 watch 设置时自动重建 |
| Windows GBK 控制台 | 启动时 stdout/stderr reconfigure 为 UTF-8，防中文乱码（交接坑提示 1） |

诚实披露红线的技术保障：`disclose()` 统一兜底函数——字段为空/缺失一律输出
"未披露，下单前请在购买页确认"；含价格输出的第一行永远是模拟数据声明。

## 5. 测试策略

- **单元测试**：标准库 `unittest`（tests/test_fareradar.py），聚焦核心纯逻辑：
  多源合并与升序排序、最低价标记、票源失败降级（注入故障源）、缺失字段兜底、
  日期/三字码校验、提醒阈值命中、alerts 读写、参数错误退出码。
- **手工自测**：search/deals/watch 三命令真实运行，输出留档交接 QA。
- 不引入 pytest 等第三方框架，与产品同守"零依赖"约束。

## 6. 对 PM 三个待确认问题的技术决定

| 问题 | 决定 | 理由 |
|---|---|---|
| Q1 入口形态 | `python src/fareradar.py` 单文件入口，不支持 `-m` | 零配置最友好；`-m` 需额外 `__main__.py` 且无用户收益；README 写清即 |
| Q2 提醒存储 | 仓库根 `alerts.json`，UTF-8 JSON 列表 `{origin, destination, threshold, created_at}` | 总协调者已拍板 alerts.json（覆盖 PM 倾向的 fare-radar-watch.json，差异已记录）；标准库 json 单文件够用 |
| Q3 参数语法 | `--from/--to/--date` 英文 flag + **三字码值**（内部 `from_code` 避让关键字） | 总协调者拍板三字码（覆盖 PM 示例中的中文城市值，差异已记录）；flag 解析稳妥，输出层用中文城市名（如"上海(SHA)"）保持友好 |

## 7. 与 PRD/交接的差异记录（以 PRD 为准的裁决）

1. alerts 文件名：交接倾向 `fare-radar-watch.json` → 按协调者决定改 `alerts.json`。
   PRD 4.4 仅要求"本地持久化"，不冲突。
2. 城市/机场参数值：交接示例用中文值 → 按协调者决定用三字码；PRD 4.2 明示
   "CLI 具体形态由技术方案定"，语义（出发地/目的地/日期）不变，不违反 PRD。
3. `watch` 裸命令行为：PRD 未定义"仅运行 watch"的含义 → 技术方案补充为
   "检查全部关注航线的当前（明日）最低价并输出命中情况"，属增强不越界。

## 8. v1.4 增量（ISSUE-011，依据 docs/02-PRD-v1.4.md 第 4~9 节）

- **城市注册表 v2**（`cities.py`）：50 纯国内城市，条目扩为 (码, 名, 机场全称, 纬度, 经度)；
  `airport_name()/all_cities()` 供航班字段与 /api/cities；HKG/KIX/NRT/ICN/BKK 进 `RETIRED_CITIES`
  仅作存量 alerts 兼容展示（显式"城市已下线"，不参与比价与提醒，数据结构不变）。
- **航班生成器**（`airlines.py`，新增）：6 航司名册+枢纽集；`route_flights()` 每航线每日 3 班
  （早/午/晚窗口各 1 班、时段与槽位一一对应）、时刻 md5 派生 5 分钟粒度、时长=大圆距离÷780km/h+35min
  向上取整 5 分钟、到达=起飞+时长且钳制不跨日；航班号=二字码+4 位（同航线内确定性去重）。
- **税费引擎**（`fees.py`，新增）：机建 50；燃油按距离 ≤800km 20 / >800km 40（边界含 800）；
  `fee_breakdown/total_estimate` 由 `collect()` 统一附加——CLI 与网页同函数（规则唯一来源）。
- **到手价口径**：`total_price()` 取代 `offer["price"]` 成为 rank_offers/lowest_eligible/watch 判定
  唯一口径（修订 ISSUE-001）；`eligible_for_lowest` 移除"不含税退出竞争"分支（税费另计标记退役）。
- **去重**（`dedup_offers`）：键=出发日期+航班号、仅跨渠道合并、保留到手最低价、标 channel_count/
  channel_names；无航班号行（券/卡）与 `no_dedup` 行（特价票）不参与。OTA 代理票经
  `route_flights` 取同航班号形成真实跨渠道重复。
- **两类产品**：`douyin_miniapp` = 21 条固定特价票（逐字 fixture + 仅固定日期可售 + 时段按价格表
  顺序早/中/晚循环 + `special_fares()` 目录）∪ 基地航线会员价常规航班（无座机制保留）；
  `douyin_groupbuy` = 6 张卡卡池（`cards_for()` 命中规则/`deals()` 全量；search 恒空、availability
  恒报"卡类渠道"，覆盖模型不变式保持可解释）；`base.Source` 扩 `cards_for()/special_fares()` 默认空实现。
- **API 契约**：/api/cities 50 城+airport；/api/search 行级航班 6 要素+fee_breakdown+total_estimate+
  channel_count+顶层 card_hints；/api/deals = special_fares+cards 两类；/api/trend 切到手价；
  watch 三接口到手价判定+retired 标注。详见 docs/handoffs/handoff-dev-api-v14.md。
- **与 PRD 文本的既定取舍**：直播间闪购按券无航班字段；特价票有航班号但 no_dedup；卡面价固定无
  分钟波动——三项均记录于 handoff-dev-api-v14.md 第 8 节与 ISSUE-011 Fix 节。

## 9. 网络票源适配器通用约定（v1.10.2，ISSUE-017/018 定案）

新增或改动任何**联网票源适配器**时，以下五条为强制约定，缺一条即视为不完整实现：

1. **必须校验响应体 `status`，不能只看 HTTP 状态码**。上游常以 HTTP 200 + body `status` 表达业务状态；
   只看 HTTP 会把配额/限速错误静默当成"无数据"（ISSUE-017 实测：`status: 204` = provider 无数据）。
   建议映射：`200` 正常；`204` 无数据（不抛错，记录后可读展示）；`429` 抛配额/限速错误；
   其它非 200 抛错并携带上游 `description`。
2. **空结果与正常结果的缓存 TTL 必须分开**。空结果用短 TTL（现为 `EMPTY_CACHE_TTL = 30 分钟`），
   避免一次瞬时失败把用户锁死数小时；正常结果仍按各渠道策略（RapidAPI 为 6 小时）。
3. **空结果必须给出可区分的原因**：至少区分「上游无此航线数据（204）」与「查询成功但无可售行程
   （200 + 0 条）」，再区分「配额受限」「请求失败」。不得只显示笼统的"暂无可用报价"。
4. **原因说明只读缓存，不得回源**。展示层取原因不能额外消耗配额；无缓存时返回 `None`（不猜）。
5. **不编造补充**：字段缺失即 `None`；未知枚举值原样保留、不在渲染层猜中文；脏数据点丢弃。

实现参考：`sources/rapidapi_sky.py` 的 `_fetch`（status 校验）、`EMPTY_CACHE_TTL`、
`empty_note`；引擎侧 `fareradar.empty_notes()` 为三入口共用的原因词表来源。
