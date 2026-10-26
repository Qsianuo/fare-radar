# RELEASE v1.16.0 —— Amadeus 不能注册了，国际线换成 Duffel

| 项 | 内容 |
|---|---|
| 发布日期 | 2026-10-26 |
| 关联 | **ISSUE-025**｜PR-026 |
| 升级建议 | 建议升级。纯增量 + 文案修正，不改任何既有接口字段 |

## 一句话

原计划"注册 Amadeus 拿免费 Key"这条路**已经不存在**（自助门户 2026-07-17 整体下线），
国际线第二真源改为 **Duffel**：注册即用、搜索不收费、REST + JSON、覆盖 300+ 航司。

## Amadeus 现状（为什么这个选项作废）

| 时间 | 事件 |
|---|---|
| 2026-02 | 停止自助平台新注册 |
| **2026-07-17** | 自助门户整体下线，存量自助 Key 同日失效 |
| 现在 | 只剩 Enterprise：需 IATA/ARC 资质 + 顾问对接 + 合同谈判，多数接口仍是 SOAP |

本版把 `amadeus.py`、渠道清单与 README 里所有"注册后自配 Key 启用"的过时文案改掉，
代码保留备查（将来走企业版可复用），渠道恒为 `not_available` 并写明下线日期。

## 新增渠道：Duffel 真实接口（`duffel`）

| 项 | 说明 |
|---|---|
| 覆盖 | 300+ 航司（NDC / GDS / LCC），**国际线为主**；国内线多数不在其分销范围 |
| 成本 | **搜索免费**（下单才结算），故不设计费护栏 |
| 口径 | `total_amount` 已含税费（`tax_included=True`），引擎不再补机建/燃油 |
| 护栏 | 同航线同日期缓存 15 分钟、空结果 30 分钟、当日 200 次自我上限、25 秒超时 |
| 凭证 | 只要 **live** token（`duffel_live_`） |

## 两条你会感知到的硬规则

1. **测试模式 token 一律拒绝**：`duffel_test_` 返回的是沙盒航司 Duffel Airways（IATA `ZZ`）的
   **虚构航班与虚构价格**（官方原话 "you won't see realistic flight schedules or prices"）。
   与本工具「零编造」红线冲突，故不接入、**也不发任何请求**——不是打个水印就放行。
2. **外币报价不折算**：Duffel 请求侧没有币种参数，同一批结果可能混着 CNY / GBP / USD。
   非人民币的行照常展示航班与外币金额，但金额位标「未披露」、不参与比价——汇率是会变的
   第三方数据，写死等于替你编价。

## 你会看到的变化

- `/api/sources`、CLI 与网页渠道清单多一项「Duffel 真实接口」（未配 token 时 `not_available`
  且不发请求）；
- 「Amadeus 真实接口」的原因文案改为"已下线"，不再引导你去注册；
- 国际航司中文名补齐：国泰、长荣、全日空、新航、英航、汉莎、阿联酋、美联航等 ~40 家
  （未收录的按"本地表 → 上游原名 → 两字码"回退，不猜译名）；
- 常见国际机场显示中文名（伦敦希思罗、戴高乐、法兰克福、樟宜、仁川…），未收录的仍显示上游原名。

## 验证

- 全量 **286 例全绿**（264 + 22）；
- 测试期间**零真实请求**：`juhe/day_counter.json` 前后均为 `{"calls": 20}`，且未生成 `.duffel_cache/`。

## 已知限制

- **尚未做真实联调**：缺 live token。拿到后按 `docs/10-duffel-integration.md` §6 复验
  `PVG→LHR` / `CAN→SIN` / `PEK→NRT` 三条国际线 + 一条国内线，并用实测响应替换 fixture；
- 国内线在 Duffel 大概率无报价（如实显示，不编）；
- 外币行占比若过高，会在"不引入汇率折算"的前提下再评估展示方式。

## 升级方式

```bash
git pull && git checkout v1.16.0
# 配置 live token（可选）
echo '{"access_token":"duffel_live_xxx","proxy":""}' > src/sources/duffel_config.json
python src/webapp.py --port 8080
```
