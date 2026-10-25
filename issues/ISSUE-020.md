# ISSUE-020: 子进程测试绕过离线补丁，真实调用付费接口（P0，已实际扣费）

- 提交人: 总协调者（v1.11.0 接入飞常准后自查发现） | 日期: 2026-10-25 | 状态: Closed（v1.11.0）
- 类型: 🐛Bug（测试体系／资金风险）
- 严重程度: **P0**——测试会消耗真实费用，且历史机制一直没覆盖这类路径

## 现象与证据

飞常准接入并校准后跑全量测试，发现仓库里凭空出现 `src/sources/.variflight_cache/`：

```
day_points.json    {"date":"2026-10-25","points":50}
month_points.json  {"month":"2026-10","points":50}
offers_INC_CAN_2026-11-01.json   rows=4
```

即：**测试跑出了一笔真实付费调用**（INC→CAN，4 条真实数据）。而当时五个测试模块都已按
HANDOFF §6 第 1 条打了 `CONFIG_NAME` 补丁。

## 根因

`CONFIG_NAME` 补丁只修改**当前进程**里的模块常量。`tests/test_mcp_server.py` 的
`StdioIntegrationTest` 会 `subprocess.run([sys.executable, src/mcp_server.py])` 拉起真实服务，
**子进程重新导入模块，补丁完全失效**，于是用真实 Key 发起了调用。

这是"测试零真实请求"红线的第 4 次破口，但成因与前三类不同：前三次是「模块漏加补丁」，
这次是「补丁机制本身覆盖不到子进程」。

## 修复

1. **新增全局离线开关**（`sources/base.py`）：环境变量 `FARE_RADAR_OFFLINE=1` 时，
   所有联网票源一律 `not_available` 且不发起任何请求（`offline_mode()` 判定）。
   rapidapi / amadeus / variflight 三个适配器的 `status`、`status_reason`、`availability`
   全部接入；`OFFLINE_REASON` 作为统一原因文案（与"未配置 Key"语义区分）。
2. **子进程显式传环境变量**：`StdioIntegrationTest` 的 `env` 增加 `FARE_RADAR_OFFLINE="1"`。
3. 保留 `CONFIG_NAME` 补丁作为进程内隔离（成本更低、更精确），两者互补。

### 一次失败的尝试（记录在案）

最初在五个测试模块里**全局**设置 `os.environ["FARE_RADAR_OFFLINE"]="1"`，结果污染了同进程的
适配器单测——`test_rapidapi_sky` / `test_amadeus` 的用例需要 `status="active"` 才能测映射，
被离线开关拦住导致 6 例失败。已回退：**环境变量只在子进程传，不污染当前进程。**

## 验证

- 清空 `.variflight_cache/` 后跑全量测试：**191 例全绿，测试结束缓存目录不存在**（零真实请求）
- `FARE_RADAR_OFFLINE=1` 下手动跑 CLI：联网渠道显示"测试离线模式"，不发请求

## 已发生的费用

| 调用 | 说明 |
|---|---|
| 1 次 | 本次测试泄露（INC→CAN，4 条真实数据） |
| 2 次 | 校准探测（tools/list 免费；票价工具调用 2 次，均成功） |
| 2 次 | 端到端验证（CLI + 缓存刷新各 1 次） |

自设计数器记录 100 积分（¥1，按最坏单价 50/次）；另有 2 次直接 `_rpc` 调用未经过护栏，
未计入计数器。**实际总消耗以飞常准平台账单为准**（预计 ≤ ¥2.5）。

## 改进建议

1. 任何新增**子进程**测试必须在 `env` 里显式带 `FARE_RADAR_OFFLINE=1`。
2. `FARE_RADAR_OFFLINE` 已写入 `docs/03` 第 9 节通用约定（待补）。
3. 计费型接口接入时，先确认"跑一次测试是否花钱"，再跑。

## Closed

- v1.11.0（tag），PR-021。
