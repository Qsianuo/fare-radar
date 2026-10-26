# RELEASE v1.16.1 · Duffel 实测校准

**发布日期**：2026-10-26
**类型**：patch（实测收口 + 防御性加固，无新增数据源）
**关联**：ISSUE-026 / PR-027
**上一版**：v1.16.0（Amadeus 下线归档 + Duffel 接入）

## 一句话

用一次性 test token 打了三条真实航线，把 v1.16.0 里"按文档假设"的字段形态逐条验证并回填，
同时补上两道上游无法绕过的防线（响应级 `live_mode` 闸门、排序后截取）。

## 用户可见变化

**没有。** 本版不发任何真实数据，Duffel 渠道仍为 `not_available`（缺 live token）。
唯一可见的是渠道原因文案更准确：test token 现在会说清"混入的真实航司价格同样是沙盒值"。

## 三个必须知道的事实

### 1. 拿 live token 绕不开 Stripe Connect 入驻

`Go live` 不弹表单，直接跳 `connect.stripe.com/setup/c/acct_…`：选商家类型（个人选
`Individual`）→ 填法定名称/地址/出生日期/银行账户/税号 → hCaptcha → Stripe 审核。
**必须账号持有人本人填**，AI 不代填身份与金融信息。前置条件还有邮箱验证（24 小时有效的
确认链接，可重发）。

### 2. test 模式不只有虚构航司

实测 `CAN→CTU` 44 条里混着 `3U`/`BA`/`BR`/`CA`/`CZ`/`HR`/`HX`/`IB`/`MU`，只有 `ZZ`
（Duffel Airways）是纯虚构。但**混入的真实航司价格同样是沙盒值、不可订**，所以不能因此放行。

### 3. Duffel 给不了人民币可比价

三条航线（含两条国内线）货币**全部 USD**：

| 航线 | offers | 价格区间（USD） |
|---|---|---|
| PVG→LHR | 82 | 350.52 – 5375.23 |
| PVG→PEK | 13 | 69.71 – 799.60 |
| CAN→CTU | 44 | 71.88 – 1818.80 |

按"外币不折算"红线，这些行 `price=None`、不参与比价。**结论：即便 KYC 通过，Duffel 带来的
是"航班时刻/航司/舱位 + 外币标价"，不能替代 Juhe 的国内人民币票价。**

## 代码改动

1. **响应级闸门**：`data.live_mode is False` → 整批不采用，缓存标记 + `empty_note` 说明
   "账号未激活"。与 token 前缀闸门互为兜底。
2. **排序后截取**：人民币优先 → 同币种总价升序 → 无法解析排最后，再取前 12 条。
   上游不承诺按价返回（本次 82 条近似升序纯属巧合），不排序会漏低价。
3. **文案修正**：`_NOTE_TEST_TOKEN` 按实测重写；新增 `_NOTE_UPSTREAM_TEST`。
4. **fixture 按实测回填**：时间无时区、航班号整数、`aircraft: null`、`stops: []`，
   补 `live_mode`/`conditions`/`passengers`/`total_emissions_kg`。

## 验证

| 项 | 结果 |
|---|---|
| `python -m unittest discover tests` | **289 例全绿**（v1.16.0 为 286） |
| Duffel 单测 | 25 例（+3） |
| 零真实请求 | juhe 计数器 20→20；未生成 `.duffel_cache/` |
| 真实调用位置 | 仓库外一次性脚本；test token 事后已从 Duffel 删除 |

## 升级说明

无需任何操作，本版对现有四个可用源（snapshot_pack / rapidapi_sky / variflight / juhe）
行为无影响。

## 下一步（需产品决策）

1. 是否走 Stripe Connect 完成 KYC，拿 live token；
2. 若坚持"不要外币"，Duffel 接入后的实际价值可能低于预期，可考虑把精力放回国内线
   （Juhe ¥99/450 次档）；
3. 汇率折算是否要开独立开关（当前明确不做）。
