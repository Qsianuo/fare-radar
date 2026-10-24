# ISSUE-002: 提醒文件一损坏，我设的全部关注被静默清空，我毫不知情
- 提交人: Tester | 日期: 2026-10-24 | 状态: Submitted
- 类型: 🐛Bug

## 问题描述（我在做什么时遇到了什么，用户视角）

我在工具里设了关注航线等降价（存放在 `alerts.json`）。某天这个文件坏了（编辑器误存、磁盘写入中断都可能）。我并不知情：运行 `watch` 只显示"暂无关注航线"，没有任何"文件损坏"的警告；随后我随手设置一条新关注，工具直接用这唯一一条**覆盖了整个文件**——我之前设的关注无声无息地全没了，没有警告、没有备份。我还以为工具在帮我盯着降价。

## 复现步骤

1. 正常设置关注：`python src/fareradar.py watch --from SHA --to PEK --price 700`（输出"已保存关注"）。
2. 模拟文件损坏：`printf '{oops broken' > alerts.json`（手工写入非法 JSON）。
3. 运行裸 `watch`（只读检查）→ 显示"暂无关注航线。"，无任何损坏警告，退出码 0。
4. 运行设置操作：`python src/fareradar.py watch --from SHA --to CTU --price 500` → 输出"已保存关注：上海(SHA) → 成都(CTU)，底价 ¥500。"
5. 查看 `alerts.json`：只剩 CTU 一条，原 PEK ¥700 关注永久丢失。

## 预期结果

读取 `alerts.json` 失败时应明确警告用户（如"[警告] 关注文件已损坏，为避免覆盖你的数据，本次设置已取消，请先备份 alerts.json"），并且**不得**用空/单条清单直接覆盖原文件；至少应先备份原文件再写入。

## 实际结果

损坏状态下一切操作显示正常（退出码 0、"已保存关注"），文件被无提示覆盖，用户数据丢失且用户毫无察觉。README 已知限制只写了"删除 alerts.json 即清空全部关注"，未提示"文件损坏同样会静默清空"。

## 证据（命令输出/日志摘录/截图路径）

```
--- 损坏前 alerts.json（1 条关注）---
[ { "origin": "SHA", "destination": "PEK", "threshold": 700, ... } ]

$ printf '{oops broken' > alerts.json
$ python src/fareradar.py watch
[模拟数据] 以下价格来自内置模拟票源，非真实报价，不能用于实际购票。
暂无关注航线。示例：watch --from SHA --to PEK --price 700，再运行 watch 检查。   ← exit=0，无损坏警告

$ python src/fareradar.py watch --from SHA --to CTU --price 500
已保存关注：上海(SHA) → 成都(CTU)，底价 ¥500。                                  ← exit=0，无警告

--- 操作后 alerts.json（原 PEK ¥700 丢失）---
[ { "origin": "SHA", "destination": "CTU", "threshold": 500, ... } ]
```

## 对用户的影响

用户的关注清单是"等降价"的全部投入，静默丢失意味着：用户以为工具还在盯着 SHA→PEK，实际提醒早已消失 → **错过降价、多花冤枉钱**，且事后毫无线索（没有任何警告可回忆）。这属于用户数据静默丢失，比功能不可用更伤信任；文件损坏是真实场景（手工编辑、异常退出、同步盘冲突都可能产生），不是抬杠用例。

---
## Triage（PM 填）

## Reproduce（Dev 填）

## Fix（Dev 填）

## Code Review（总协调者填）

## Test（Tester 回归填）

## User Acceptance（User 填）

## Closed（PM 填，关联 PR/版本）
