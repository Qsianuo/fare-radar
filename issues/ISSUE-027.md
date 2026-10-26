# ISSUE-027 · Duffel live 激活对大陆个人不可行，渠道文案需改真

- 状态：已关闭（结论=不可用，代码保留、文案改真）
- 发现：2026-10-27（浏览器实测 KYC 全流程）
- 关联：ISSUE-025（接入 Duffel）、ISSUE-026（实测校准）；PR-028；RELEASE-v1.16.2
- 影响面：`src/sources/duffel.py` 的三处对外文案、README、`docs/10`

## 1. 背景

v1.16.0 接入 Duffel 后，渠道一直是 `not_available`（无 token）。用户注册账号后要求
「你控制浏览器拿 live token，美元价格也可以」，于是完整走了一遍 Go live。目标是把
「缺 token」这个待办真正关掉，而不是继续挂在那里。

## 2. 实测路径与卡点

| 步骤 | 结果 |
|---|---|
| Edge 接管 | 必须用**原始** `User Data` + `--profile-directory="Profile 1"`；复制 profile 后 cookie 解不开（App-Bound Encryption 绑路径） |
| `agent-browser` | 连接 9222 反复超时并导致 Edge 退出 → 改用自建 CDP 客户端（`F:\cdp\cdp.py`） |
| 网络 | 直连 Duffel 可用；**Stripe 的 CDN / hCaptcha 直连被挡**，表单一直转圈 → 打开 Clash（7897）后正常 |
| 表单定位 | 表单在 `connect-js.stripe.com/ui_layer_*.html` 子框架内，外层 DOM 取不到 → 用 `Page.getFrameTree` + `Page.createIsolatedWorld` 进入 |
| 第 1 步 商家类型 | 选 `Individual` → 通过（点 `a[role=button]`「继续」有效） |
| 第 2 步 个人详情 | 姓名、邮箱、出生日期、家庭住址、电话、**SSN 后四位** |
| **国家/地区** | **下拉只有「美国」一项且为灰色**；固定 `+1` 电话；强制「州」+ 5 位邮编 |
| 结论 | 要的是美国身份。Stripe 支持国家/地区列表不含中国大陆，账户也无法改成内地 |

## 3. 判定

1. **不编造。** 美国住址/SSN 属身份信息，编造即欺诈，且 Stripe 风控会拒。
2. **能力上也不解决问题。** 即便激活，实测三条航线报价全为 USD（含国内线），按
   「外币不折算」红线的行不参与比价，撑不起「低于底价」判定。所以这不是「值不值得
   折腾」的问题，是「折腾完也没用」。
3. 想走通只剩三条路：美国主体（EIN + 美国地址 + SSN/ITIN）、香港主体、新加坡主体。

## 4. 处置

- `_NOTE_NO_KEY`：点明 live 需过 Stripe KYC、账户国家锁美国、要求 SSN，大陆个人无法满足；
- `tagline` / `entry_note`：改为「本渠道实际不可启用」，不再写「注册后自配 Key 启用」；
- README：新增 ⚠️ 实测结论块（含三条可行路径）；
- `docs/10`：新增 §0 结论节，把 KYC 卡点与 USD 报价两条结论落文；
- 测试 +2 例（`test_no_config_reason_states_kyc_blocker`、`test_entry_note_matches_kyc_reality`），
  锁住「原因里必须写清 Stripe/美国/SSN/中国大陆」——防止文案被改回误导版本。

## 5. 遗留

- 代码不删：将来若有海外主体，`duffel_config.json` 填 live token 即可启用，无需改代码；
- 本次未激活、未创建任何 live token；Duffel 上的探测令牌已全部删除，账号保持原样。
