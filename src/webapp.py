#!/usr/bin/env python3
"""低价票雷达 FareRadar Web 版：python src/webapp.py [--port 8080] → http://127.0.0.1:<port>

设计约束（PRD 红线"规则唯一来源"）：本文件只做"HTTP 参数 → JSON → 渲染"的
翻译，全部业务规则（航班号一等字段、费用明细与到手价、跨渠道去重、卡提示、
准入式最低价、未披露兜底、alerts 数据保护）直接调用 fareradar.py 与 sources
的同一批函数与适配器，禁止复制规则——网页与 CLI 必须说同一件事。仅绑定本机
回环地址；比价数据为实测快照与真实商品（静态，无演示波动）。
"""

import argparse
import datetime as dt
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

SRC = Path(__file__).resolve().parent
ROOT = SRC.parent
HTML_FILE = SRC / "webapp.html"

sys.path.insert(0, str(SRC))
import fareradar as fr  # noqa: E402
from sources import all_sources, cities, snapshot_pack, source_catalog  # noqa: E402

try:  # 与 CLI 一致：Windows 控制台强制 UTF-8
    for _stream in (sys.stdout, sys.stderr):
        _stream.reconfigure(encoding="utf-8")
except AttributeError:
    pass

CORRUPTED_MSG = "收藏文件 alerts.json 已损坏，操作已取消（你的数据未被改动）；请先备份修复或删除该文件后重试。"
NEARBY_NOTICE = ("临近日期最低价为实测快照静态数据（裸价口径，不含机建+燃油），"
                 f"来源：{snapshot_pack.DATA_SOURCE}。")
RETIRED_NOTE = "城市已下线（v1.4 城市全集收缩），不再参与比价与提醒"


def _route_from_params(params):
    """校验 from/to/date，返回 (origin, destination, date) 或 (None, 错误响应)。"""
    try:
        for key in ("from", "to", "date"):
            if not params.get(key):
                raise fr.UsageError(f"缺少参数 --{key}")
        origin = fr.normalize_code(params["from"], "from")
        destination = fr.normalize_code(params["to"], "to")
        if origin == destination:
            raise fr.UsageError("出发地与到达地不能相同")
        return (origin, destination, fr.parse_date(params["date"])), None
    except fr.UsageError as exc:
        return None, (400, {"ok": False, "error": str(exc)})


def _unknown_city_hint(origin, destination):
    unknown = [c for c in (origin, destination) if cities.city_name(c) is None]
    if unknown:
        parts = []
        for code in unknown:
            retired = cities.retired_name(code)
            parts.append(f"{code}（{retired}，城市已下线）" if retired else code)
        return f"城市码 {'、'.join(parts)} 不在已支持的 50 城清单中，请从城市下拉中选择。"
    return None


def _route_lowest(origin, destination, date):
    """某航线当前（全票源）可比最低到手价行：收藏页与检查共用，规则同 CLI。"""
    offers, _failed, _unavailable = fr.collect(all_sources(), origin, destination, date)
    return fr.lowest_eligible(offers, date), _failed


def _card_payload(card):
    """卡提示/卡池的对外 JSON 形状（CLI 与网页同词表）。"""
    return {
        "card_id": card.get("card_id") or card.get("deal_id") or "未披露",
        "name": card.get("name", "未披露"),
        "price": card["price"],
        "validity_text": card.get("validity_text")
        or f"{fr.disclose(card.get('valid_from'))} ~ {fr.disclose(card.get('valid_until'))}",
        "scope": card.get("scope", ""),
        "stock": fr.disclose(card.get("stock"), "未披露"),
        "refund_disclosed": bool(card.get("refund_change_rules")),
        "refund": fr.card_refund_text(card),
        "purchase": fr.disclose(card.get("purchase_entry")),
        "note": card.get("note") or "无",
        "updated": fr.disclose(card.get("updated_at"), "未披露"),
    }


def _api_cities(_params):
    return 200, {"ok": True, "count": len(cities.CITIES), "cities": cities.all_cities()}


def _api_sources(_params):
    return 200, {"ok": True, "sources": source_catalog()}


def _api_nearby(params):
    """临近日期最低价条（实测快照静态数据，替代原"近 24 小时演示走势"）。"""
    route, err = _route_from_params(params)
    if err:
        return err
    origin, destination, date = route
    if _unknown_city_hint(origin, destination):
        return 400, {"ok": False, "error": _unknown_city_hint(origin, destination)}
    bar = snapshot_pack.nearby_lowest(origin, destination)
    return 200, {
        "ok": True,
        "route": f"{fr.city(origin)} → {fr.city(destination)}",
        "date": date.isoformat(),
        "price_basis": "裸价（不含机建+燃油）",
        "covered": bool(bar),
        "bar": bar,
        "notice": NEARBY_NOTICE if bar else
                  f"该航线暂无临近日期快照数据；当前收录范围：{snapshot_pack.coverage_text()}。",
    }


def _api_search(params):
    route, err = _route_from_params(params)
    if err:
        return err
    origin, destination, date = route
    try:
        sources = fr._selected_sources(params.get("source") or None)
    except fr.UsageError as exc:
        return 400, {"ok": False, "error": str(exc)}

    offers, failed, unavailable = fr.collect(sources, origin, destination, date)
    offers = fr.rank_offers(offers, date)
    lowest = fr.lowest_eligible(offers, date)
    catalog = {s["id"]: s["description"] for s in source_catalog()}
    rows = [{
        "price": o["price"],                       # 票面价
        "total_estimate": fr.total_price(o),       # 到手价（排序唯一口径）
        "source_id": o["source_id"],
        "source_name": o["source_name"],
        "source_desc": catalog.get(o["source_id"], ""),
        "entry_url": o.get("entry_url", ""),
        "entry_note": o.get("entry_note", ""),
        "product": o["product"],
        "depart_time": o.get("depart_time"),       # 航班 6 要素（特价票/卡为 null）
        "arrive_time": o.get("arrive_time"),
        "cross_day": bool(o.get("cross_day")),
        "duration_text": o.get("duration_text"),
        "dep_airport": o.get("dep_airport"),
        "arr_airport": o.get("arr_airport"),
        "carrier_code": o.get("carrier_code"),
        "carrier_name": o.get("carrier_name"),
        "flight_no": o.get("flight_no"),
        "fee_breakdown": o.get("fee_breakdown"),
        "channel_count": o.get("channel_count", 1),
        "channel_names": o.get("channel_names", [o["source_name"]]),
        "is_special_fare": bool(o.get("is_special_fare")),
        "is_snapshot": bool(o.get("is_snapshot")),
        "note": fr.search_note(o, date),           # 含"另有 N 个渠道有售"
        "risk_flags": fr.risk_flags(o, date),
        "is_deal": bool(o.get("is_deal")),
        "is_lowest": o is lowest,
    } for o in offers]

    hints = [dict(_card_payload(c),
                  entry_url=c.get("entry_url", ""), entry_note=c.get("entry_note", ""))
             for c in fr.card_hints(origin, destination, date, sources)]

    alert_hit, warning = None, None
    try:
        alert = fr.find_alert(fr.load_alerts(), origin, destination)
    except fr.AlertsCorrupted:
        alert, warning = None, "收藏文件 alerts.json 已损坏，本次未做底价比对（数据未被改动）。"
    if (alert and lowest and alert.get("threshold") is not None
            and fr.total_price(lowest) <= alert["threshold"]):
        alert_hit = (f"当前最低到手价 ¥{fr.total_price(lowest)} ≤ 你的底价 "
                     f"¥{alert['threshold']}，可以出手了！")

    empty_hint = None
    if not rows:
        snapshot_selected = any(s.source_id == snapshot_pack.SOURCE_ID for s in sources)
        empty_hint = (_unknown_city_hint(origin, destination)
                      or (f"该航线暂无真实数据快照；当前收录范围：{snapshot_pack.coverage_text()}。"
                          if snapshot_selected
                          and not snapshot_pack.is_covered(origin, destination, date)
                          else ("本次查询所选渠道均无销售，原因见 unavailable_sources。"
                                if unavailable else
                                "该航线暂无可用报价（已登记票源见 /api/sources）。")))
    return 200, {
        "ok": True,
        "route": f"{fr.city(origin)} → {fr.city(destination)}",
        "date": date.isoformat(),
        "weekday": f"周{'一二三四五六日'[date.weekday()]}",
        "price_basis": "到手价（票面+机建+燃油）",
        "notice": fr.DATA_NOTICE,
        "failed": failed,
        "unavailable_sources": unavailable,  # 覆盖外/卡类/未接入渠道（区别于 failed 异常）
        "sources_status": [{"id": s["id"], "name": s["name"], "status": s["status"],
                            "reason": s["reason"]} for s in source_catalog()],
        "card_hints": hints,                 # 命中的次卡/省钱卡（不计入 offers）
        "offers": rows,
        "alert_hit": alert_hit,
        "warning": warning,
        "empty_hint": empty_hint,
    }


def _api_deals(params):
    try:
        origin = fr.normalize_code(params["from"], "from") if params.get("from") else None
        destination = fr.normalize_code(params["to"], "to") if params.get("to") else None
    except fr.UsageError as exc:
        return 400, {"ok": False, "error": str(exc)}
    specials = [{
        "deal_id": s["deal_id"],
        "origin": s["origin"],
        "destination": s["destination"],
        "destination_display": s["destination_display"],
        "route_text": f"上海→{s['destination_display']}",
        "date": s["date"],
        "price": s["price"],
        # 卡面未展示航班号/时刻/机场：出 null，前端按"未披露"渲染（禁止编造）
        "depart_time": s.get("depart_time"),
        "arrive_time": s.get("arrive_time"),
        "flight_no": s.get("flight_no"),
        "carrier_code": s.get("carrier_code"),
        "carrier_name": s.get("carrier_name"),
        "dep_airport": s.get("dep_airport"),
        "arr_airport": s.get("arr_airport"),
        "label": "特价票·不含机建燃油费｜省 ¥41",
        "note": "特价票·固定航班日期·不含机建燃油费",
        "entry_url": s.get("entry_url", ""),
        "entry_note": s.get("entry_note", ""),
    } for s in fr.special_fare_catalog(origin, destination)]
    cards = []
    for source in all_sources():
        for d in source.deals(origin, destination):
            entry = dict(_card_payload(d))
            if d.get("origin") and d.get("destination"):
                entry["route_text"] = f"{fr.city(d['origin'])} → {fr.city(d['destination'])}"
            else:
                entry["route_text"] = d.get("scope", "")
            entry["entry_url"] = source.entry_url      # 渠道入口（ISSUE-010），卡片可点击
            entry["entry_note"] = source.entry_note
            cards.append(entry)
    return 200, {"ok": True, "notice": fr.DEALS_NOTICE,
                 "special_fares": specials, "cards": cards}


def _watch_item(a, date):
    """收藏行 → 对外 JSON：已下线城市显式标注且不触发查询（不静默删除）。"""
    item = {
        "origin": a.get("origin", ""),
        "destination": a.get("destination", ""),
        "threshold": a.get("threshold"),  # v1.3 起允许 null（未设底价的收藏）
        "created_at": a.get("created_at", ""),
        "check_date": date.isoformat(),
        "price_basis": "到手价（票面+机建+燃油）",
    }
    retired = fr.retired_names(a)
    if retired:
        item["retired"] = True
        item["status_note"] = f"{RETIRED_NOTE}：{'、'.join(retired)}"
        item["current_lowest"] = None
        item["below_threshold"] = False
        return item
    lowest, _failed = _route_lowest(a.get("origin", ""), a.get("destination", ""), date)
    threshold = a.get("threshold")
    item["retired"] = False
    item["status_note"] = None
    item["current_lowest"] = fr.total_price(lowest) if lowest else None
    item["below_threshold"] = bool(lowest and threshold is not None
                                   and fr.total_price(lowest) <= threshold)
    return item


def _api_watch_list(_params):
    try:
        alerts = fr.load_alerts()
    except fr.AlertsCorrupted:
        return 409, {"ok": False, "error": CORRUPTED_MSG}
    date = dt.date.today() + dt.timedelta(days=1)
    items = [_watch_item(a, date) for a in alerts if isinstance(a, dict)]
    return 200, {"ok": True, "date": date.isoformat(), "alerts": items}


def _api_watch_set(params):
    try:
        if not (params.get("from") and params.get("to")):
            raise fr.UsageError("收藏航线需同时提供出发与到达")
        origin = fr.normalize_code(params["from"], "from")
        destination = fr.normalize_code(params["to"], "to")
        if origin == destination:
            raise fr.UsageError("出发地与到达地不能相同")
        raw_price = params.get("price")
        if raw_price:
            try:
                threshold = int(str(raw_price))
            except ValueError:
                threshold = -1
            if threshold <= 0:
                raise fr.UsageError(f"底价需为正整数（元），收到：{raw_price}")
        else:
            threshold = None  # v1.3 唯一授权演进：收藏可不设底价
        alerts = fr.load_alerts()
    except fr.UsageError as exc:
        return 400, {"ok": False, "error": str(exc)}
    except fr.AlertsCorrupted:
        return 409, {"ok": False, "error": CORRUPTED_MSG}
    alerts = [a for a in alerts
              if not (a.get("origin") == origin and a.get("destination") == destination)]
    alerts.append({"origin": origin, "destination": destination, "threshold": threshold,
                   "created_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M")})
    fr.save_alerts(alerts, fr.ALERTS_FILE)
    tail = f"，底价 ¥{threshold}" if threshold is not None else "（未设底价）"
    return 200, {"ok": True,
                 "message": f"已收藏航线：{fr.city(origin)} → {fr.city(destination)}{tail}。"}


def _api_watch_remove(params):
    try:
        if not (params.get("from") and params.get("to")):
            raise fr.UsageError("删除收藏需同时提供出发与到达")
        origin = fr.normalize_code(params["from"], "from")
        destination = fr.normalize_code(params["to"], "to")
        alerts = fr.load_alerts()
    except fr.UsageError as exc:
        return 400, {"ok": False, "error": str(exc)}
    except fr.AlertsCorrupted:
        return 409, {"ok": False, "error": CORRUPTED_MSG}
    rest = [a for a in alerts
            if not (a.get("origin") == origin and a.get("destination") == destination)]
    if len(rest) == len(alerts):
        return 200, {"ok": True, "message": f"未找到收藏：{origin} → {destination}。"}
    fr.save_alerts(rest, fr.ALERTS_FILE)
    return 200, {"ok": True, "message": f"已删除收藏：{fr.city(origin)} → {fr.city(destination)}。"}


def _api_watch_check(_params):
    try:
        alerts = fr.load_alerts()
    except fr.AlertsCorrupted:
        return 409, {"ok": False, "error": CORRUPTED_MSG}
    date = dt.date.today() + dt.timedelta(days=1)
    lines = []
    for a in alerts:
        route = f"{fr.city(a['origin'])} → {fr.city(a['destination'])}"
        retired = fr.retired_names(a)
        if retired:
            lines.append({"route": route, "status": "retired",
                          "text": f"城市已下线（v1.4 城市全集收缩）：{'、'.join(retired)}；"
                                  f"不参与比价与提醒，可删除或改藏其他航线。"})
            continue
        lowest, failed = _route_lowest(a["origin"], a["destination"], date)
        threshold = a.get("threshold")
        if lowest is None:
            extra = f"（{'、'.join(failed)} 源不可用）" if failed else ""
            lines.append({"route": route, "status": "none",
                          "text": f"暂无报价数据{extra}" if failed else
                                  "无可比最低价（售罄/有效期不覆盖的报价均不参与竞争）"})
        elif threshold is None:
            lines.append({"route": route, "status": "info",
                          "text": f"当前最低到手价 ¥{fr.total_price(lowest)}（未设底价，仅收藏）。"})
        elif fr.total_price(lowest) <= threshold:
            lines.append({"route": route, "status": "hit",
                          "text": f"当前最低到手价 ¥{fr.total_price(lowest)} "
                                  f"≤ 底价 ¥{threshold}，可以出手了！"})
        else:
            lines.append({"route": route, "status": "wait",
                          "text": f"当前最低到手价 ¥{fr.total_price(lowest)}，"
                                  f"未达底价 ¥{threshold}，继续等待。"})
    return 200, {"ok": True, "date": date.isoformat(), "notice": fr.DATA_NOTICE, "lines": lines}


GET_APIS = {
    "/api/cities": _api_cities,
    "/api/sources": _api_sources,
    "/api/nearby": _api_nearby,
    "/api/search": _api_search,
    "/api/deals": _api_deals,
    "/api/watch/list": _api_watch_list,
    "/api/watch/check": _api_watch_check,
}
POST_APIS = {
    "/api/watch/set": _api_watch_set,
    "/api/watch/remove": _api_watch_remove,
}


class Handler(BaseHTTPRequestHandler):
    def _send_bytes(self, status, content_type, body):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, status, payload):
        self._send_bytes(status, "application/json; charset=utf-8",
                         json.dumps(payload, ensure_ascii=False).encode("utf-8"))

    def _params(self):
        return {k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()}

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/":
            self._send_bytes(200, "text/html; charset=utf-8", HTML_FILE.read_bytes())
            return
        api = GET_APIS.get(path)
        if api is None:
            self._send_json(404, {"ok": False, "error": "接口不存在"})
            return
        status, payload = api(self._params())
        self._send_json(status, payload)

    def do_POST(self):
        api = POST_APIS.get(urlparse(self.path).path)
        if api is None:
            self._send_json(404, {"ok": False, "error": "接口不存在"})
            return
        status, payload = api(self._params())
        self._send_json(status, payload)

    def log_message(self, fmt, *args):  # 安静模式：仅保留错误，避免后台日志刷屏
        if args and (" 4" == str(args[0])[0:2] or " 5" == str(args[0])[0:2]):
            super().log_message(fmt, *args)


def main(argv=None):
    parser = argparse.ArgumentParser(description="低价票雷达 Web 版（本机演示）")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args(argv)
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"低价票雷达 Web 版已启动：http://{args.host}:{args.port}")
    print("比价数据为实测快照与真实商品（静态、来源逐行标注）；按 Ctrl+C 停止。")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
