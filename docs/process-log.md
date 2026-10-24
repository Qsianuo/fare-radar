# FareRadar 全程流程记录（总协调者维护）

> 本文件按时间序记录各阶段的动作、产出物与交接，供最终评审追溯。评审总结见 docs/99-review-summary.md。

## S0 启动（总协调者）

- 环境核验：Python 3.10.11 / Git 2.54 / Windows + Git Bash。
- 建立 Git 仓库（`main` 分支）与目录约定，落盘《角色分工与流程总纲》docs/01。
- 产出物：docs/01-roles-and-process.md；提交 `100d1c3`。

## S1 需求（PM 子代理）

- 交付：docs/02-requirements-research.md（用户画像与 5 问调研）、docs/02-PRD-v1.0.md（V1.0 收敛为 search/deals/watch 三命令，P0×3 + P1×2）、docs/04-douyin-groupbuy-research.md（抖音团购接入调研：只走官方开放平台/商务合作，明确不爬取，MVP 用模拟源+字段契约）、docs/handoffs/handoff-pm-to-dev.md。
- 提交 `b6aa607`。协调者核验：5 份文档齐全，均含用户视角小节。

## S2 研发（Dev 子代理）

- 交付：docs/03-tech-design.md（含对 PM 三个待确认问题的技术决定）、src/fareradar.py + src/sources/（497 行，纯标准库，Source 可插拔适配器：simulated_airline + douyin_groupbuy）、tests/test_fareradar.py（21 例）、README.md、prs/PR-001.md、docs/handoffs/handoff-dev-to-qa.md。
- 分支 feature/v1.0 开发，`--no-ff` 合并回 main（`d2a7e1a`）。

## S2 评审（总协调者）

- 冒烟：search（SHA→PEK/SHA→CTU）、deals、watch（设置/--list/裸检查）、4 条错误路径，全部符合 PRD 口径；独立复跑单测 Ran 21 tests, OK。
- 疑点排查：`python -m unittest discover tests` 输出尾部出现 CLI 帮助文本，定位为 `test_no_command_returns_nonzero` 用例未捕获 stdout（非测试失败），记为评审意见 3。
- PR-001 评审结论：**通过**，4 条非阻塞意见（含税/不含税混排比价、最低价标记排版、测试输出卫生+alerts 数据安全、先评审后合并流程），转 Issue 处理；提交 `29f9b4e`。

## S3 测试与用户反馈（Tester、User 子代理并行）

- Tester：docs/05-test-plan.md 20 条黑盒用例（通过 17 / 失败 3 / 阻塞 0），按 PRD 验收标准逐条核验；提交 ISSUE-001~003；docs/handoffs/handoff-tester-to-pm.md；提交 `48431de`。
- User A：docs/06-user-journey.md（三目标真实旅程 + 综合感受）；提交 ISSUE-004~006；提交 `a30598c`。
- 过程备注：两个代理并行工作，Tester 的 `git add -A` 曾扫入用户旅程初稿，User 代理后续提交已覆盖为定稿——多角色并行协作的提交纪律在 S6 已改进（各自只提交自己改的文件，执行良好）。
- 用户最有价值的反馈：ISSUE-004（售罄团购券仍标"◀ 最低价"）——"工具的卖点就是坑写在明面上，结果最大的坑是它自己藏的"。

## S4 分诊（PM 子代理）

- 6 个 Issue 全部公开答复（提交 `5bae8ed`）：001/002/004 定 Bug/P1 本期修；003 升级 Bug/P1 本期修（有效期=可出行窗口的口径裁定）；006 修正 Bug/P2 本期修（禁止用补数据消灭场景）；005 与 001 同根因，重复关闭互链。
- 产出：docs/07-triage-record.md、docs/02-PRD-v1.1.md、docs/handoffs/handoff-pm-to-dev-fix.md（含"不许顺手改"清单）。

## S5 修复（Dev 子代理）与 Code Review（总协调者）

- Dev 逐单：复现留痕（issue Reproduce 节，根因定位到代码）→ fix/issue-00X 独立分支（001→006 依次叠放）→ 修复+回归测试（新增 14 例）→ PR-002~006（状态"待评审"，**未自行合并**，落实 PR-001 流程意见 4）→ RELEASE-v1.1.0.md 草稿 + handoff-dev-to-qa-fix.md。
- 总协调者逐支审阅 diff + 链尾复跑全量单测（35 例 OK）+ 实测各单场景（001 口径排序、002 损坏拒写且文件逐字节保留、003/004 准入生效、006 用户语言兜底），5 个 PR 全部**通过**，评审记录写入各 PR 文件（提交 `01a0472`）。
- 按链序合并 PR-002→PR-006（提交 `625e794`/`3ad3676`/`25d3ee8`/`131de13`/`921dba4`），合并后 main 复验 35 例全绿。

## S6 回归、验收与关闭（Tester、User、PM 子代理 + 总协调者）

- Tester 回归（提交 `c5f4184`）：5/5 单原复现不再复现；既有 20 用例（5 条按 PRD v1.1 新语义改写）通过；单测 35 例全绿；无新问题。Test 节写实。
- User A 验收（提交 `1a58f57`）：ISSUE-004/005/006 全部通过；"箭头终于只指我真能买到的便宜"；原有好用的地方未损坏。User Acceptance 节写实。
- 总协调者补全 6 个 Issue 的 Code Review 节（指向 PR 评审记录，提交 `e4f8976`）。
- PM 关闭（提交 `cc58a0e`）：001~006 全部 Closed（005 重复关闭）；002/003 以"QA 回归代替用户验收"说明理由与重开路径；用户两条新观察列入 backlog（B1 提醒行随附退改不明标记、B2 alerts 共享隔离）。
- 版本标签：v1.0.0（`d2a7e1a`）、v1.1.0（`921dba4`）。

## Issue 生命周期跟踪表（全部环节留痕）

| Issue | 标题摘要 | 提交人 | Submitted | Triage | Reproduce | Fix | Code Review | Test | User Acceptance | Closed |
|---|---|---|---|---|---|---|---|---|---|---|
| 001 | 含税/不含税混排，最低价未必真最低 | Tester | ✅ 48431de | ✅ 5bae8ed | ✅ issue 内 | ✅ 356c7cc | ✅ PR-002 | ✅ c5f4184 | ✅ 经 005 的用户确认 | ✅ cc58a0e |
| 002 | alerts.json 损坏被静默清空 | Tester | ✅ 48431de | ✅ 5bae8ed | ✅ issue 内 | ✅ 488743f | ✅ PR-003 | ✅ c5f4184 | QA 回归代替（理由见 Closed 节） | ✅ cc58a0e |
| 003 | 券有效期不覆盖出发日仍提示出手 | Tester | ✅ 48431de | ✅ 5bae8ed | ✅ issue 内 | ✅ da071f3 | ✅ PR-004 | ✅ c5f4184 | 用户整体验收间接覆盖 | ✅ cc58a0e |
| 004 | 售罄团购券照样标最低价 | User A | ✅ a30598c | ✅ 5bae8ed | ✅ issue 内 | ✅ ff565a1 | ✅ PR-005 | ✅ c5f4184 | ✅ 1a58f57 | ✅ cc58a0e |
| 005 | 混排比价（与 001 重复） | User A | ✅ a30598c | ✅ 5bae8ed 重复关闭 | —（随 001） | —（随 PR-002） | —（并入 PR-002） | ✅ c5f4184 | ✅ 1a58f57 | ✅ cc58a0e |
| 006 | 内部术语透出给用户 | User A | ✅ a30598c | ✅ 5bae8ed | ✅ issue 内 | ✅ 9dbe94f | ✅ PR-006 | ✅ c5f4184 | ✅ 1a58f57 | ✅ cc58a0e |

## 版本记录

| 版本 | 内容 | 位置 |
|---|---|---|
| v1.0.0 | 首发：search/deals/watch + 可插拔模拟票源 | tag v1.0.0（d2a7e1a），PR-001 |
| v1.1.0 | 修复 5 个 Issue（001/002/003/004/006）+ 术语退场 | tag v1.1.0（921dba4），PR-002~006 |

## 过程改进记录（复盘用）

1. PR-001 曾"先合并后评审"→ 从 PR-002 起改为"评审通过后由协调者合并"，全程执行到位。
2. S3 并行代理曾互相扫入未完成文件 → S6 各角色只提交自己改的文件，无污染。
3. 单测输出卫生问题（帮助文本混入测试输出）记入 PR-001 评审意见，已随 v1.1 修复链改写测试时处理。
