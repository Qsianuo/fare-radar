#!/usr/bin/env python3
"""低价票雷达 FareRadar Web 版：python src/webapp.py [--port 8080] → http://127.0.0.1:<port>

设计约束（ISSUE-007 分诊裁定 1）：本文件只做"HTTP 参数 → JSON → 渲染"的翻译，
全部业务规则（准入式最低价、口径标注、未披露兜底、alerts 数据保护）直接调用
fareradar.py 的同一批纯函数与 sources 适配器，禁止复制规则——网页与 CLI 必须说同一件事。
仅绑定本机回环地址；数据仍为内置模拟票源。
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
from sources import all_sources  # noqa: E402

try:  # 与 CLI 一致：Windows 控制台强制 UTF-8
    for _stream in (sys.stdout, sys.stderr):
        _stream.reconfigure(encoding="utf-8")
except AttributeError:
    pass

CORRUPTED_MSG = "关注文件 alerts.json 已损坏，操作已取消（你的数据未被改动）；请先备份修复或删除该文件后重试。"


def _api_search(params):
    try:
        for key, flag in (("from", "from"), ("to", "to"), ("date", "date")):
            if not params.get(key):
                raise fr.UsageError(f"缺少参数 --{flag}")
        origin = fr.normalize_code(params["from"], "from")
        destination = fr.normalize_code(params["to"], "to")
        if origin == destination:
            raise fr.UsageError("出发地与到达地不能相同")
        date = fr.parse_date(params["date"])
        sources = fr._selected_sources(params.get("source") or None)
    except fr.UsageError as exc:
        return 400, {"ok": False, "error": str(exc)}

    offers, failed = fr.collect(sources, origin, destination, date)
    offers = fr.rank_offers(offers, date)
    lowest = fr.lowest_eligible(offers, date)
    rows = [{
        "price": o["price"],
        "source_name": o["source_name"],
        "product": o["product"],
        "tax_label": fr.tax_label(o),
        "note": fr.search_note(o, date),
        "is_deal": bool(o.get("is_deal")),
        "is_lowest": o is lowest,
    } for o in offers]

    alert_hit, warning = None, None
    try:
        alert = fr.find_alert(fr.load_alerts(), origin, destination)
    except fr.AlertsCorrupted:
        alert, warning = None, "关注文件 alerts.json 已损坏，本次未做底价比对（数据未被改动）。"
    if alert and lowest and lowest["price"] <= alert["threshold"]:
        alert_hit = f"当前最低 ¥{lowest['price']} ≤ 你的底价 ¥{alert['threshold']}，可以出手了！"

    return 200, {
        "ok": True,
        "route": f"{fr.city(origin)} → {fr.city(destination)}",
        "date": date.isoformat(),
        "weekday": f"周{'一二三四五六日'[date.weekday()]}",
        "failed": failed,
        "offers": rows,
        "alert_hit": alert_hit,
        "warning": warning,
        "empty_hint": None if rows else "该航线暂无可用报价。已接入航线以上海(SHA)为一端，可试 SHA → KIX。",
    }


def _api_deals(params):
    try:
        origin = fr.normalize_code(params["from"], "from") if params.get("from") else None
        destination = fr.normalize_code(params["to"], "to") if params.get("to") else None
    except fr.UsageError as exc:
        return 400, {"ok": False, "error": str(exc)}
    deals = [d for s in all_sources() for d in s.deals(origin, destination)]
    cards = [{
        "deal_id": d.get("deal_id", "未披露"),
        "name": d.get("name", "未披露"),
        "price": d["price"],
        "tax_label": fr.tax_label(d),
        "stock": fr.disclose(d.get("stock"), "未披露"),
        "route_text": f"{fr.city(d['origin'])} → {fr.city(d['destination'])}",
        "validity": f"{fr.disclose(d.get('valid_from'), '未披露')} ~ {fr.disclose(d.get('valid_until'), '未披露')}",
        "refund": fr.disclose(d.get("refund_change_rules")),
        "purchase": fr.disclose(d.get("purchase_entry")),
        "note": d.get("note") or "无",
        "updated": fr.disclose(d.get("updated_at"), "未披露"),
    } for d in deals]
    return 200, {"ok": True, "deals": cards}


def _api_watch_list(_params):
    try:
        alerts = fr.load_alerts()
    except fr.AlertsCorrupted:
        return 409, {"ok": False, "error": CORRUPTED_MSG}
    return 200, {"ok": True, "alerts": [
        {"origin": a.get("origin", ""), "destination": a.get("destination", ""),
         "threshold": a.get("threshold", ""), "created_at": a.get("created_at", "")}
        for a in alerts if isinstance(a, dict)]}


def _api_watch_set(params):
    try:
        if not (params.get("from") and params.get("to") and params.get("price")):
            raise fr.UsageError("设置底价需同时提供出发、到达与底价")
        origin = fr.normalize_code(params["from"], "from")
        destination = fr.normalize_code(params["to"], "to")
        if origin == destination:
            raise fr.UsageError("出发地与到达地不能相同")
        try:
            threshold = int(str(params["price"]))
        except ValueError:
            threshold = -1
        if threshold <= 0:
            raise fr.UsageError(f"底价需为正整数（元），收到：{params['price']}")
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
    return 200, {"ok": True,
                 "message": f"已保存关注：{fr.city(origin)} → {fr.city(destination)}，底价 ¥{threshold}。"}


def _api_watch_remove(params):
    try:
        if not (params.get("from") and params.get("to")):
            raise fr.UsageError("删除关注需同时提供出发与到达")
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
        return 200, {"ok": True, "message": f"未找到关注：{origin} → {destination}。"}
    fr.save_alerts(rest, fr.ALERTS_FILE)
    return 200, {"ok": True, "message": f"已删除关注：{fr.city(origin)} → {fr.city(destination)}。"}


def _api_watch_check(_params):
    try:
        alerts = fr.load_alerts()
    except fr.AlertsCorrupted:
        return 409, {"ok": False, "error": CORRUPTED_MSG}
    date = dt.date.today() + dt.timedelta(days=1)
    lines = []
    for a in alerts:
        route = f"{fr.city(a['origin'])} → {fr.city(a['destination'])}"
        offers, failed = fr.collect(all_sources(), a["origin"], a["destination"], date)
        if not offers:
            extra = f"（{'、'.join(failed)} 源不可用）" if failed else ""
            lines.append({"route": route, "status": "none", "text": f"暂无报价数据{extra}"})
            continue
        lowest = fr.lowest_eligible(offers, date)
        if lowest is None:
            lines.append({"route": route, "status": "none",
                          "text": "无可比最低价（售罄/不含税/有效期不覆盖的报价均不参与竞争，原因见比价页备注）"})
        elif lowest["price"] <= a["threshold"]:
            lines.append({"route": route, "status": "hit",
                          "text": f"当前最低 ¥{lowest['price']} ≤ 底价 ¥{a['threshold']}，可以出手了！"})
        else:
            lines.append({"route": route, "status": "wait",
                          "text": f"当前最低 ¥{lowest['price']}，未达底价 ¥{a['threshold']}，继续等待。"})
    return 200, {"ok": True, "date": date.isoformat(), "notice": fr.SIM_NOTICE, "lines": lines}


GET_APIS = {
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
    print("数据为内置模拟票源；按 Ctrl+C 停止。")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
