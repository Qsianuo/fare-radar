# RELEASE v1.8.0 —— FareRadar 成为 MCP Server

发布日期：2026-10-25 ｜ 关联：ISSUE-014 ｜ 上一版本：v1.7.0

## 🆕 MCP Server（stdio，零依赖手写协议）

- 4 个 MCP 工具：**search_flights**（多渠道比价，到手价排序）、**list_deals**（特价票+卡池）、**list_sources**（渠道状态如实披露）、**favorites**（收藏管理）。
- 纯标准库实现 JSON-RPC 2.0 / NDJSON 生命周期；工具异常转 isError，协议不崩。
- 数据与网页/CLI 完全同源（复用同一批处理器）；输出携带诚实声明。
- 接入：ZCode 设置→MCP 添加 `python src/mcp_server.py`（README 有完整 JSON），重启即用。

## 🔧 质量

- 新增 11 例 MCP 测试（含子进程 stdio 集成往返）；全库 **165 例全绿，0.9s 全离线**。
