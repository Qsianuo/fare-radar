#!/usr/bin/env python3
"""低价票雷达 FareRadar —— MCP Server（stdio 传输，纯标准库零依赖）。

把产品的四个能力注册为 MCP 工具，供 ZCode / Claude Desktop 等 MCP 客户端调用：
  - search_flights  航班比价（聚合全部渠道，到手价排序，最低价标注）
  - list_deals      特价票清单 + 次卡/省钱卡卡池
  - list_sources    渠道清单与接入状态（未配置/未接入如实披露）
  - favorites       收藏航线（list / set / remove / check）

协议实现：MCP stdio 传输 = 按行分隔的 JSON-RPC 2.0（initialize →
notifications/initialized → tools/list → tools/call）。数据与网页/CLI
共用同一批处理器（规则唯一来源）：直接调用 webapp 的 *_api_* 函数，
输出为中文文本并携带演示数据声明（诚实披露红线）。

运行：python src/mcp_server.py（由 MCP 客户端拉起；stdin/stdout 即协议通道，
本进程不得向 stdout 打印任何非协议内容）。
环境变量 FARE_RADAR_ALERTS 可覆盖收藏文件路径（测试/多实例用）。
"""

import json
import os
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent
sys.path.insert(0, str(SRC))

import fareradar as fr  # noqa: E402
import webapp  # noqa: E402

PROTOCOL_VERSION = "2025-06-18"
SERVER_INFO = {"name": "fareradar", "version": "1.8.0",
               "title": "低价票雷达 FareRadar"}
SUPPORTED_METHODS = ("initialize", "ping", "tools/list", "tools/call")

try:  # 与 CLI 一致：Windows 控制台 GBK 兜底
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
except AttributeError:
    pass

if os.environ.get("FARE_RADAR_ALERTS"):
    fr.ALERTS_FILE = Path(os.environ["FARE_RADAR_ALERTS"])


# ---------- 工具定义（模型可见的"产品说明书"） ----------

_TOOLS = [
    {"name": "search_flights",
     "description": ("航班比价：聚合已接入的全部票源渠道（实测快照、RapidAPI 实时、"
                     "春秋特价票等），按到手价（票面+机建+燃油）升序，标注最低价与"
                     "风险（售罄/退改不明/有效期不覆盖）；无销售渠道如实列明原因。"
                     "注意：数据含演示快照与真实接口两类，输出首行有声明。"),
     "inputSchema": {"type": "object", "required": ["from", "to", "date"],
                     "properties": {
                         "from": {"type": "string", "description": "出发城市 IATA 三字码，如 SHA、INC"},
                         "to": {"type": "string", "description": "到达城市 IATA 三字码，如 CAN、JJN"},
                         "date": {"type": "string", "description": "出发日期 YYYY-MM-DD"},
                         "source": {"type": "string",
                                    "description": "可选：只查指定渠道 id（见 list_sources），如 rapidapi_sky"}}}},
    {"name": "list_deals",
     "description": ("特价与卡池：春秋抖音渠道的固定日期特价票清单（仅当日可售）"
                     "与次卡/省钱卡卡面（退改未披露如实标注）。"),
     "inputSchema": {"type": "object",
                     "properties": {
                         "from": {"type": "string", "description": "可选：按出发城市过滤"},
                         "to": {"type": "string", "description": "可选：按到达城市过滤"}}}},
    {"name": "list_sources",
     "description": "渠道清单：每个票源的接入状态（active / not_available）与原因（未配置 Key、暂无合规接口等）。",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "favorites",
     "description": ("收藏航线管理：list 查看（含当前最低价与是否低于底价）、"
                     "set 收藏（底价可选）、remove 删除、check 立即检查。"),
     "inputSchema": {"type": "object", "required": ["action"],
                     "properties": {
                         "action": {"type": "string", "enum": ["list", "set", "remove", "check"]},
                         "from": {"type": "string", "description": "set/remove 必填：出发三字码"},
                         "to": {"type": "string", "description": "set/remove 必填：到达三字码"},
                         "price": {"type": "integer", "description": "可选：心理底价（元，正整数）"}}}},
]
_TOOL_BY_NAME = {t["name"]: t for t in _TOOLS}


# ---------- 工具处理器（返回中文文本，含诚实声明） ----------

def _fmt_search(params):
    status, payload = webapp._api_search(params)
    if status != 200:
        return f"查询失败：{payload.get('error')}"
    lines = [payload.get("notice") or "[演示数据] 以下价格含演示快照与真实接口数据，不能用于实际购票。",
             f"航线：{payload['route']} ｜ {payload['date']}（{payload.get('weekday', '')}）"]
    offers = payload.get("offers") or []
    for i, o in enumerate(offers):
        mark = "◀最低价" if o.get("is_lowest") else ""
        flags = "、".join(o.get("risk_flags") or [])
        line = (f"{i + 1}. ¥{o.get('total_estimate')}{mark} "
                f"{o.get('flight_no', '—')} {o.get('depart_time')}→{o.get('arrive_time')} "
                f"{o.get('duration_text', '')} {o.get('carrier_name', '')}"
                f"〔{o.get('source_name')}〕{o.get('product', '')}")
        if flags:
            line += f" ⚠{flags}"
        fee = (o.get("fee_breakdown") or {}).get("included_desc")
        if fee:
            line += f"｜{fee}"
        lines.append(line)
    if not offers:
        lines.append(f"（无报价）{payload.get('empty_hint', '')}")
    for u in payload.get("unavailable_sources") or []:
        lines.append(f"· 渠道「{u['name']}」本次无销售：{u['reason']}")
    for n in payload.get("failed") or []:
        lines.append(f"· 渠道「{n}」暂不可用（已跳过）")
    hints = payload.get("card_hints") or []
    if hints:
        names = "、".join(h.get("name", "?") for h in hints[:3])
        lines.append(f"· 省钱卡提示：命中 {len(hints)} 张（{names}…），卡类不计入上方比价")
    return "\n".join(lines)


def _fmt_deals(params):
    status, payload = webapp._api_deals(params)
    if status != 200:
        return f"查询失败：{payload.get('error')}"
    fares = payload.get("special_fares") or []
    cards = payload.get("cards") or []
    if not fares and not cards:
        return "当前过滤条件下暂无特价票/卡。"
    lines = [payload.get("notice") or "[演示商品] 以下为真实商品的静态快照（抖音渠道），购买以官方页面为准。"]
    if fares:
        lines.append(f"── 固定特价票（{len(fares)} 条，仅固定日期可售）──")
        for d in fares:
            stock = f"｜库存 {d.get('stock')}" if d.get("stock") else ""
            lines.append(f"· {d.get('date')} {d.get('name')}〔{d.get('deal_id')}〕"
                         f"¥{d.get('price')} {d.get('depart_time')}→{d.get('arrive_time')}"
                         f" {d.get('dep_airport')}→{d.get('arr_airport')}{stock}"
                         f"｜{d.get('route_text') or ''}")
    if cards:
        lines.append(f"── 次卡/省钱卡（{len(cards)} 张，卡类不计入航班比价）──")
        for c in cards:
            refund = str(c.get("refund", ""))
            flag = " ⚠退改未披露" if "未披露" in refund else ""
            lines.append(f"· {c.get('name')} ¥{c.get('price')}｜有效期 {c.get('validity', '未披露')}"
                         f"｜适用 {c.get('scope', c.get('note', '见卡面'))}{flag}")
    return "\n".join(lines)


def _fmt_sources(_params):
    status, payload = webapp._api_sources({})
    lines = ["渠道清单（active=已接入，not_available=未接入/未配置）："]
    for s in payload.get("sources") or []:
        state = "✅" if s.get("status") == "active" else "⛔"
        reason = f"——{s['reason']}" if s.get("reason") else ""
        lines.append(f"{state} {s['id']}｜{s['name']}{reason}")
    return "\n".join(lines)


def _fmt_favorites(params):
    action = params.get("action")
    if action == "set":
        status, payload = webapp._api_watch_set(params)
    elif action == "remove":
        status, payload = webapp._api_watch_remove(params)
    elif action == "check":
        status, payload = webapp._api_watch_check({})
    else:
        status, payload = webapp._api_watch_list({})
    if status != 200:
        return f"操作失败：{payload.get('error')}"
    if action == "check":
        lines = [payload.get("notice", ""),
                 f"按 {payload.get('date')} 出发的到手价口径检查 {len(payload.get('lines') or [])} 条收藏："]
        for ln in payload.get("lines") or []:
            icon = {"hit": "🟢", "wait": "⚪", "none": "🟡"}.get(ln.get("status"), "·")
            lines.append(f"{icon} {ln.get('route')}：{ln.get('text')}")
        return "\n".join(lines)
    if action in ("set", "remove"):
        return payload.get("message", "已操作。")
    alerts = payload.get("alerts") or []
    if not alerts:
        return "暂无收藏航线。"
    lines = [f"共 {len(alerts)} 条收藏："]
    for a in alerts:
        threshold = f"底价 ¥{a['threshold']}" if a.get("threshold") else "未设底价"
        lowest = a.get("current_lowest")
        low_text = f"当前最低 ¥{lowest}" if isinstance(lowest, int) else ""
        below = " 🟢低于底价" if a.get("below_threshold") else ""
        lines.append(f"· {a.get('origin')}→{a.get('destination')}｜{threshold}{low_text}{below}")
    return "\n".join(lines)


_HANDLERS = {"search_flights": _fmt_search, "list_deals": _fmt_deals,
             "list_sources": _fmt_sources, "favorites": _fmt_favorites}


# ---------- JSON-RPC 分发 ----------

def _result(req_id, result):
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _error(req_id, code, message):
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def handle_message(message):
    """处理一条 JSON-RPC 消息，返回应答 dict 或 None（通知无应答）。"""
    method = message.get("method", "")
    req_id = message.get("id")  # None = 通知
    params = message.get("params") or {}

    if method == "initialize":
        return _result(req_id, {
            "protocolVersion": params.get("protocolVersion", PROTOCOL_VERSION),
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": SERVER_INFO,
            "instructions": ("低价票雷达：多渠道机票比价。先用 list_sources 了解渠道，"
                             "再 search_flights 查询；数据含演示快照与真实接口，输出含声明。")})
    if method == "notifications/initialized":
        return None
    if method == "ping":
        return _result(req_id, {})
    if method == "tools/list":
        return _result(req_id, {"tools": _TOOLS})
    if method == "tools/call":
        name = params.get("name", "")
        if name not in _TOOL_BY_NAME:
            return _error(req_id, -32602, f"未知工具：{name}")
        arguments = params.get("arguments") or {}
        try:
            text = _HANDLERS[name](arguments)
            return _result(req_id, {"content": [{"type": "text", "text": text}],
                                    "isError": False})
        except Exception as exc:  # 工具内异常转为 isError 文本，不崩协议
            return _result(req_id, {"content": [{"type": "text",
                                                 "text": f"工具执行失败：{exc}"}],
                                    "isError": True})
    if req_id is None:
        return None  # 其余通知（如 notifications/cancelled）静默
    return _error(req_id, -32601, f"方法不存在：{method}")


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            response = _error(None, -32700, "解析错误：非法 JSON")
        else:
            response = handle_message(message)
        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
