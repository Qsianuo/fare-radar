# ISSUE-023: 上游「有航班、无报价」被整条丢弃，用户误以为查不到航班

- 提交人: 产品侧（"RapidAPI 这个 api 能使用，但是有些航班调取不到，是请求出错了么，请用银川到深圳 10 月 27 日测试"） | 日期: 2026-10-26 | 状态: Closed（v1.14.0）
- 类型: 🐞缺陷（数据完整性 + 语义失真），影响三入口
- 前序: v1.10.1/v1.10.2 已修"空结果说不清"（响应体 status、200+0 条 ≠ 204）；本单是同类问题的**第四态**

## 复现（实测，非推测）

```
GET /api/search?from=INC&to=SZX&date=2026-10-27
→ offers: []，empty_hint: "上游查询成功但未返回可售行程"
```

直连上游取原始响应（`RapidapiFlightSource._fetch`）：

| 观测点 | 实测值 |
|---|---|
| HTTP 状态 | `200 OK` |
| 响应体 `status` / `description` | `200` / `"OK"` |
| `best_flights` / `other_flights` | `0` / **`1`** |
| 该行程内容 | 奥凯航空 `BK3037`，INC 11:30 → SZX 15:00，直飞 210 分钟，Boeing 737 |
| 该行程 `price` | **`null`** |

同航线对照：`INC→SZX` 2026-10-28 有 5 条带价行程，2026-11-01 有 6 条 → **航线本身支持**；
`INC→CTU` 等返回 `status: 204` 才是"上游无此航线数据"，与本例语义不同。

## 根因

`sources/rapidapi_sky.py::_to_offer` 里有一条零编造红线：`price` 非数字即 `return None`。
红线本身正确（不许编价），但**"不编价"被实现成了"不展示"**——于是真实存在的航班被静默吞掉，
`empty_hint` 还给出"未返回可售行程"的错误解释，把用户引向"接口坏了/航线不支持"。

同类实现见 `sources/variflight.py::_to_offer`（无舱位价格即丢弃，仅返回一句"均无可用舱位价格"）。

## 裁定

**航班字段是真实数据，价格缺失是价格的事，两者必须解耦。**

| 项 | 旧口径 | 新口径（v1.14.0） |
|---|---|---|
| 有航段、`price=null` | 整条丢弃 | **保留行程**；`price`/`total_estimate` = `null`，`price_note` 说明 |
| 无航段（连航班号/时刻都没有） | 丢弃 | 仍丢弃（连"哪一班"都说不清） |
| 排序位置 | — | 恒排在所有有价行之后 |
| 「◀ 最低价」/ 低价提醒 | — | 不参与（`eligible_for_lowest` 已含此判定） |
| 跨渠道去重 | — | 有价行胜出，不得让无价行顶掉真实报价 |
| 展示 | — | 金额位显示「价格未披露」，**绝不出现 `¥None` / `¥0`** |

零编造红线未松动：只是从"没价就不给看"变成"没价就明说没价"。

## 验收标准（全部已由测试守住）

1. 有航段无价的行程，三入口都能看到航班号/时刻/机场/承运人；
2. 金额位为「价格未披露」，`price`、`total_estimate` 均为 `null`（JSON 里是 `null` 不是 `"None"`）；
3. 无价行不占据排序首位、不被标最低价、不触发低价提醒；
4. 同航班跨渠道时，有价行胜出并标注 `channel_count`；
5. 全表无价时 CLI/MCP 给出「找到 N 个航班但上游均未报价」的说明，而不是"查不到"；
6. 上游连行程都没有时，说明里带**条数**（`raw_itineraries`），不再含糊。

## 关联

- 代码: `sources/base.py`（`NO_PRICE_NOTE` 单一措辞）、`sources/rapidapi_sky.py`、`sources/variflight.py`、
  `fareradar.py`（`total_price`/`_price_sort_key`/`price_text`/`collect`/`dedup_offers`/`rank_offers`）、
  `webapp.py`（`price_note`/`no_price_count`）、`webapp.html`（`price-na` 渲染）、`mcp_server.py`
- 测试: `tests/test_v114_no_price.py`（新增 17 例）+ 两个适配器用例口径改写
- 文档: `docs/03-tech-design.md` 第 9 节新增第 8 条；README「无报价不等于没航班」
- 发布: `RELEASE-v1.14.0.md`｜PR-024
