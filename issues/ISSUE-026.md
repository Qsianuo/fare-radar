# ISSUE-026: Duffel 实测校准 —— live 前置是 Stripe KYC，且报价以 USD 为主

- 提交人: 产品侧（"duffel 已注册登录完成，你来控制浏览器做吧"） | 日期: 2026-10-26 | 状态: Closed（v1.16.1）
- 类型: 🔍实测校准 + 🐞缺陷（上游字段形态与假设不符）+ ✨防御（响应级闸门 / 排序）
- 前序: v1.16.0（ISSUE-025）只做了离线实现，fixture 是"照文档构造"的形态样本

## 1. 执行过程（浏览器侧全部由 AI 完成）

| 步骤 | 结果 |
|---|---|
| 接管已登录浏览器 | Edge 的 App-Bound Encryption 把 cookie 密钥绑在**原 user-data-dir 路径**上，复制 profile（`F:\edge-cdp-profile`）后 Duffel cookie 读出来是 **0 条**，页面直接跳登录。改用原始 `User Data` + `--profile-directory="Profile 1"` 才带出 11 条 cookie |
| 会话失效 | 两个实例同时使用同一 session 触发作废（cookie 里出现 `ff-last-self-invalidated`），团队级路由全部 302 到 `/sign-in?next=…`，只能重新登录 |
| 登录 | 邮箱自动填入；**密码由用户本人在窗口输入**（AI 不接触密码） |
| 邮箱验证 | 点 `Go live` 先跳 `/email` 等确认链接；AI 点"重发确认邮件"，用户点邮件链接后页面自动放行 |
| 定位令牌页 | 旧版 `app.duffel.com/{team}/test/developers/tokens`；新版 `dashboard.duffel.com/{team}/test/developers/tokens`，新建表单在 `…/tokens/new` |
| 完整 token | 详情页只显示掩码 `duffel_test_****EBmgs`；完整值只在创建瞬间出现。最终用**拦截创建接口响应**的方式取到（响应体 `data.token`） |
| live token | 令牌表单**没有环境选项**，环境由全局 Test/Live 开关决定；只有 live 模式下创建的才是 `duffel_live_`。`Go live` → `/activation` → **Stripe Connect 商家入驻** |

## 2. 核心结论（三条，都会影响后续决策）

### 2.1 拿 live token 绕不开 Stripe Connect 入驻

`Go live` 的落点是 `connect.stripe.com/setup/c/acct_1UUq4K4VSAt7t3Bo/…`，第 1 步选商家类型
（无公司注册文件选 `Individual`），随后要填法定名称、地址、出生日期、银行账户、税号并过
hCaptcha。**这属于真实身份与金融信息，AI 不代填**。用户本轮选择"先不 Go live"。

### 2.2 test 模式并非只有虚构航司

实测三条航线：`PVG→PEK` 13 条（`CA`/`MU`/`ZZ`）、`CAN→CTU` 44 条
（`3U`/`BA`/`BR`/`CA`/`CZ`/`HR`/`HX`/`IB`/`MU`/`ZZ`）、`PVG→LHR` 82 条
（`AA`/`BA`/`IB`/`LH`/`SQ`/`ZZ`）。

**混入真实航司不构成放行理由**——这些行的价格同样是沙盒值、不可订。原 `_NOTE_TEST_TOKEN`
文案（"报价来自沙盒航司 Duffel Airways"）据此修正。

### 2.3 报价货币全是 USD，Duffel 提供不了人民币可比价

三条航线（含两条国内线）的 `total_currency` **全部为 `USD`**：

| 航线 | offers | 货币 | 价格区间（USD） |
|---|---|---|---|
| PVG→LHR | 82 | USD | 350.52 – 5375.23 |
| PVG→PEK | 13 | USD | 69.71 – 799.60 |
| CAN→CTU | 44 | USD | 71.88 – 1818.80 |

按"外币不折算"红线，这些行 `price=None`、不参与比价。**即使用户完成 KYC 拿到 live token，
Duffel 的价值仍只落在"航班时刻/航司/舱位参考 + 外币标价"**，不能替代 Juhe 提供的国内人民币票价。

## 3. 字段形态实测（回写 fixture 与解析）

| 字段 | 实测 | 原假设 | 处理 |
|---|---|---|---|
| HTTP 状态 | `201` | 200 | 代码只判成功，无需改 |
| `departing_at` | `2026-11-15T10:50:00` **无时区** | 带 `+08:00` | `_hhmm` 取 `[11:16]`，安全；fixture 已改 |
| `marketing_carrier_flight_number` | 整数 `1274` | 字符串 | `str()` 拼接，安全 |
| `aircraft` | `null` | 有值 | 未依赖，安全 |
| `stops` | `[]` 空数组 | — | 中转段数仍用 `len(segments)-1` |
| `total_amount` | `350.52` = base 297.05 + tax 53.47 | 含税 | 证实 `tax_included=True` |
| `live_mode` | 顶层与 offer 均有布尔值 | 未知 | 新增响应级闸门 |

## 4. 代码改动

1. **响应级 `live_mode` 闸门**（第二道）：`data.live_mode is False` → 整批不采用，缓存
   `upstream_live_mode=false`，`empty_note` 给出"账号未激活"的可执行原因。前缀闸门只看 token
   形状，这条看上游自述，互为兜底。
2. **排序后截取**：上游未承诺按价升序（82 条近似升序只是巧合），改为"人民币优先 → 同币种总价
   升序 → 无法解析排最后"再取前 12 条，避免便宜的行被截掉。
3. `_NOTE_TEST_TOKEN` 文案按 2.2 修正；新增 `_NOTE_UPSTREAM_TEST`。
4. fixture 字段形态按实测回填（时间去时区、航班号整数、`aircraft: null`、`stops: []`、
   补 `live_mode`/`conditions`/`passengers`/`total_emissions_kg`），并重写 `_说明`。

## 5. 验收

- `tests/test_duffel.py` 22 → **25 例**（新增 `UpstreamLiveModeGateTest` 2 例、
  `SortingTest` 1 例）；全量 **289 例全绿**；
- 零真实请求核对：跑测试前后 `src/sources/.juhe_cache/day_counter.json` 保持 `{"calls": 20}`，
  未生成 `src/sources/.duffel_cache/`；
- 真实调用只发生在**独立探测脚本**中（`F:\cdp\`，不入库），用的是一次性 test token，
  事后已从 Duffel 删除。

## 6. 不做的事

- 不代填 Stripe KYC（身份与金融信息）；
- 不对测试模式数据做"水印放行"；
- 不引入汇率折算；
- 不把 sandbox 价格写进任何 fixture 断言值或文档示例价。
