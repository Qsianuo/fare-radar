# ISSUE-014: 把 FareRadar 暴露为 MCP Server

- 提交人: 产品评审人（"我们做的项目是不是类似于一个mcp"→"可以，开动吧"） | 日期: 2026-10-25 | 状态: Closed（v1.8.0 交付）
- 类型: ✨新功能（架构升级）

## 需求

把产品能力注册为 MCP 工具，供 ZCode/Claude Desktop 等 AI 客户端直连调用；纯标准库（不引 mcp SDK），保持零依赖红线与规则唯一来源。

## 交付（总协调者实现，轻量流程）

- `src/mcp_server.py`：MCP stdio 传输（NDJSON JSON-RPC 2.0）完整生命周期（initialize/notifications/initialized/ping/tools/list/tools/call，未知方法 -32601、未知工具 -32602、工具异常转 isError 不崩协议）；4 工具：search_flights、list_deals（v1.5 载荷：特价票+卡两类）、list_sources、favorites（list/set/remove/check）。
- 数据层直接复用 webapp._api_* 处理器（与网页/CLI 同源，零规则复制）；输出中文文本并携带诚实声明；FARE_RADAR_ALERTS 环境变量隔离收藏文件（测试/多实例）。
- 测试 11 例（协议契约/四工具/子进程 stdio 集成往返），全库 **165 例全绿（0.9s 全离线）**；修复过程中处理共享状态污染（favorites 用例恢复 ALERTS_FILE 原值）与 v1.5 载荷结构差异（deals=special_fares+cards）。
- README"MCP Server"接入章节（ZCode/Claude Desktop 配置）。

## 验收

- stdio 冒烟：initialize→tools/list→search_flights(INC→CAN) 返回完整快照比价文本（含到手价口径、渠道披露、卡提示）。
- 客户端实测：待评审人在 ZCode 添加配置重启后体验。

## Closed

- v1.8.0（tag），PR-015。
