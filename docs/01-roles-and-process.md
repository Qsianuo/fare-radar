# 低价票雷达 FareRadar —— 角色分工与流程总纲

> 模拟真实软件公司研发流程（参照《周末实战挑战 project.md》），由总协调者牵头，4 个角色协作交付。
> 本文档是全项目的"宪法"：所有角色开工前必读，所有产出物落盘到指定目录。

## 一、项目一句话

**低价票雷达（FareRadar）**：帮预算敏感的旅客在多个票源（航司直连 + 抖音团购套餐）中找到最低价的机票查询工具。
V1.0 范围刻意保持"小而完整"：单机 Python 命令行工具 + 模拟数据源 + 可插拔票源适配器（为抖音团购真实接入预留）。

## 二、角色分工

| 角色 | 扮演者 | 核心职责 | 关键产出物 |
|---|---|---|---|
| 总协调者 | 主 Agent | 拆解派工、中转交接、代码评审（Code Review）、流程记录、最终评审报告 | 本文档、PR 评审意见、流程记录、评审总结 |
| 产品经理 PM | 子代理 1 | 用户调研、PRD v1.0/v1.1、Issue Triage 定级、产品验收口径 | 需求调研、PRD、Triage 记录 |
| 研发工程师 Dev | 子代理 2 | 技术方案、AI 辅助编码、复现与修复 Bug、版本发布 | 技术方案、源代码、README、PR、Release |
| 测试工程师 Tester | 子代理 3 | 测试计划与执行、提交正式 Issue、修复后回归 | 测试计划与结果、Issues |
| 真实用户 User A | 子代理 4 | 以真实旅客身份使用产品、反馈体验、验收修复 | 用户旅程记录、Issues、验收结论 |

**沟通机制**：子代理之间不直接对话，一律通过总协调者中转；每次交接必须落盘一份交接说明（`docs/handoffs/`），写清"交付了什么、下一步谁做什么、有什么坑"。

## 三、铁律：始终体现"产品在用户眼中是怎样的"

每一份产出物（PRD、README、Issue、Release Notes、验收记录）都必须回答三个问题：

1. **用户能看到什么？**（界面/输出文案是否清晰、诚实，如"团购票需在抖音 App 下单"必须显著提示）
2. **用户能做到什么？**（能否独立按 README 完成核心任务：找到一张更便宜的票）
3. **用户会感受到什么？**（哪里省心、哪里担心：价格是否含税费？团购票能不能退改？）

用户视角是唯一验收标准：代码跑通 ≠ 产品可用，**用户说 OK 才算 OK**。

## 四、整体流程（映射 project.md 作业二～作业六）

| 阶段 | 内容 | 责任角色 | 产出物 |
|---|---|---|---|
| S0 启动 | 立项、分工、流程定义 | 总协调者 | 本文档、Git 仓库 |
| S1 需求 | 用户调研 → PRD v1.0 → 抖音团购接入调研 | PM（协调者评审） | 02-需求调研、02-PRD-v1.0、04-抖音团购接入调研 |
| S2 研发 | 技术方案 → 开发 v1.0 → 自测 → PR → README | Dev（协调者 Code Review） | 03-技术方案、src/、tests/、README、PR-001 |
| S3 测试反馈 | 黑盒测试 + 真实用户旅程 → 提交 Issues | Tester、User（并行） | 05-测试计划与结果、06-用户旅程、issues/ISSUE-00X |
| S4 分诊 | Triage：定性、定级、答复、排期；PRD 更新 v1.1 | PM | issue 内 Triage 节、07-Triage 记录、02-PRD-v1.1 |
| S5 修复 | 复现 → 根因 → 修复分支 → PR → 回归 → 发布 v1.1.0 | Dev（协调者 Review、Tester 回归） | issue 内 Reproduce/Fix 节、PR-00X、RELEASE-v1.1.0 |
| S6 验收关闭 | 用户验收 → 关闭 Issue → 复盘 | User、PM、协调者 | issue 内 User Acceptance/Closed 节、99-评审总结 |

## 五、Issue 生命周期（本项目的核心演练，必须逐环节留痕）

```
Issue Submitted → Triage → Reproduce → Fix → Code Review → Test → User Acceptance → Closed
```

| 环节 | 责任角色 | 必须留下的记录 |
|---|---|---|
| 1. Issue Submitted | Tester / User | issues/ISSUE-00X.md：问题描述 → 复现步骤 → 预期结果 → 实际结果 → 证据（输出/日志）→ 对用户的影响 |
| 2. Triage | PM | 类型判定（Bug/文档/需求遗漏/体验/新功能）、优先级 P0-P3、是否本期修复、给用户的公开答复 |
| 3. Reproduce | Dev | 复现结果 + 根因分析（写回 issue 文件） |
| 4. Fix | Dev | fix/issue-00X 分支、修复提交、补充的测试 |
| 5. Code Review | 总协调者 | prs/PR-00X.md 评审节：意见、结论（通过/打回） |
| 6. Test | Tester | 回归记录：原问题不再复现 + 既有用例不回归 |
| 7. User Acceptance | User | 以用户身份重走旅程，明确"通过/不通过"+ 证据 |
| 8. Closed | PM/协调者 | 关闭说明、关联 PR 与 Issue、版本号 |

规则：P0/P1 问题必须走完全部 8 个环节；P2/P3 允许 Triage 后明确"列入 backlog"，但必须有书面答复。**禁止为凑数量制造问题，禁止未经用户验收就关闭 Bug。**

## 六、Issue 模板

```markdown
# ISSUE-00X: [一句话标题，用户口吻]
- 提交人: User A / Tester | 日期: 2026-10-24 | 状态: Submitted
- 类型: 🐛Bug / 📖文档 / ⚙️需求遗漏 / 💡体验 / ✨新功能建议
## 问题描述（我在做什么时遇到了什么，用户视角）
## 复现步骤
## 预期结果
## 实际结果
## 证据（命令输出/日志摘录/截图路径）
## 对用户的影响
---
## Triage（PM 填）
## Reproduce（Dev 填）
## Fix（Dev 填）
## Code Review（总协调者填）
## Test（Tester 回归填）
## User Acceptance（User 填）
## Closed（PM 填，关联 PR/版本）
```

## 七、仓库目录约定

```
fare-radar/
├── README.md              # 用户第一眼看到的产品说明（Dev 维护）
├── docs/                  # 01 总纲 | 02 需求调研 | 02-PRD v1.0/v1.1 | 03 技术方案
│   │                      # 04 抖音团购接入调研 | 05 测试计划与结果 | 06 用户旅程
│   │                      # 07 Triage 记录 | handoffs/ 交接说明 | 99 评审总结
├── src/                   # fareradar.py + sources/（票源适配器）
├── tests/                 # 自动化测试
├── issues/                # ISSUE-00X.md（唯一事实来源，全生命周期在单个文件内推进）
├── prs/                   # PR-00X.md（模拟 GitHub PR：描述/变更/自测/评审）
└── releases/              # RELEASE-vX.Y.Z.md（用户视角的发布说明）
```

## 八、版本与分支策略

- 分支：`main`（稳定）+ `feature/v1.0`（首发）+ `fix/issue-00X`（修复）；模拟 GitHub Flow，PR 记录落盘 `prs/`。
- 版本：v1.0.0 首发；修复 + 小改进 → v1.1.0；只修 Bug → v1.0.1。严重问题随时修，不等凑数。
- 环境：Windows + Git Bash + Python 3.10，产品只依赖 Python 标准库，保证评审人零依赖运行。
