#!/usr/bin/env python3
"""低价票雷达 FareRadar —— 单文件 CLI 入口：python src/fareradar.py <search|deals|watch>。

输出铁律：含价格的输出首行必标模拟数据声明；字段缺失显示"未披露"；
全中文；关键标记用文字符号，不依赖 ANSI 颜色。
"""

import argparse
import datetime as dt
import json
import sys
import unicodedata
from pathlib import Path

from sources import all_sources, source_by_id

try:  # Windows 控制台默认 GBK，强制 UTF-8 防中文乱码（交接坑提示 1）
    for _stream in (sys.stdout, sys.stderr):
        _stream.reconfigure(encoding="utf-8")
except AttributeError:
    pass

ROOT = Path(__file__).resolve().parents[1]
ALERTS_FILE = ROOT / "alerts.json"
LINE = "-" * 64
DISCLOSE = "未披露，下单前请在购买页确认"
SIM_NOTICE = "[模拟数据] 以下价格来自内置模拟票源，非真实报价，不能用于实际购票。"
DEALS_NOTICE = "[模拟数据] 以下团购套餐来自模拟数据源，非真实商品，不能直接购买。"
CITY = {"SHA": "上海", "PEK": "北京", "CTU": "成都", "KIX": "大阪关西", "CAN": "广州"}
WATCH_USAGE = "示例：watch --from SHA --to PEK --price 700"


class UsageError(Exception):
    """参数校验失败：由 main 统一捕获，输出中文说明并以退出码 2 结束。"""


# ---------- 格式化 ----------

def city(code):
    return f"{CITY[code]}({code})" if code in CITY else code


def display_width(text):  # 全角/宽字符按 2 列计，保证中文表格对齐
    return sum(2 if unicodedata.east_asian_width(ch) in "FW" else 1 for ch in text)


def pad(text, width):
    return text + " " * max(0, width - display_width(text))


def disclose(value, fallback=DISCLOSE):
    """诚实披露兜底：空/缺失字段禁止留空，统一显示"未披露"。"""
    text = str(value).strip() if value is not None else ""
    return text or fallback


def tax_label(item):
    return "含税" if item.get("tax_included") else "不含税"


def print_table(headers, rows, mark_first=False):
    widths = [max(display_width(h), max(display_width(r[i]) for r in rows))
              for i, h in enumerate(headers)]
    fmt = lambda cells: "  ".join(pad(c, w) for c, w in zip(cells, widths)).rstrip()
    print(fmt(headers))
    print(LINE)
    for i, row in enumerate(rows):
        print(fmt(row))
        if mark_first and i == 0:
            print(" " * (widths[0] + 2) + "◀ 最低价")


# ---------- 校验与核心逻辑（纯函数，供单元测试） ----------

def normalize_code(raw, flag):
    code = str(raw).strip().upper()
    if len(code) != 3 or not code.isalpha():
        raise UsageError(f"--{flag} 需为三字城市码（如 SHA/PEK/CTU/KIX），收到：{raw}")
    return code


def parse_date(raw):
    """--date 必须 YYYY-MM-DD 且不早于今天。"""
    try:
        date = dt.datetime.strptime(str(raw).strip(), "%Y-%m-%d").date()
    except ValueError:
        raise UsageError(f"--date 需为 YYYY-MM-DD 格式（如 2026-11-15），收到：{raw}")
    if date < dt.date.today():
        raise UsageError(f"--date 不能早于今天（{dt.date.today().isoformat()}），收到：{raw}")
    return date


def collect(sources, origin, destination, date):
    """逐源调用 search；单源异常降级为提示，不拖垮整体（PRD AC2）。"""
    offers, failed = [], []
    for source in sources:
        try:
            offers.extend(source.search(origin, destination, date))
        except Exception:
            failed.append(source.display_name)
    return offers, failed


def sort_offers(offers):
    return sorted(offers, key=lambda offer: offer["price"])


def load_alerts(path=ALERTS_FILE):
    """读取关注清单；文件缺失/损坏按"无关注"处理，不阻断查询。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return [a for a in data if isinstance(a, dict)] if isinstance(data, list) else []


def save_alerts(alerts, path=ALERTS_FILE):
    path.write_text(json.dumps(alerts, ensure_ascii=False, indent=2), encoding="utf-8")


def find_alert(alerts, origin, destination):
    for alert in alerts:
        if alert.get("origin") == origin and alert.get("destination") == destination:
            return alert
    return None


def _selected_sources(source_id):
    if not source_id:
        return all_sources()
    source = source_by_id(source_id)
    if source is None:
        valid = "、".join(s.source_id for s in all_sources())
        raise UsageError(f"--source 未知票源：{source_id}（可选：{valid}）")
    return [source]


# ---------- 命令：search / deals / watch ----------

def cmd_search(args):
    origin = normalize_code(args.from_code, "from")
    destination = normalize_code(args.to_code, "to")
    if origin == destination:
        raise UsageError("出发地与到达地不能相同")
    date = parse_date(args.date)
    sources = _selected_sources(args.source)

    offers, failed = collect(sources, origin, destination, date)
    print(SIM_NOTICE)
    weekday = "一二三四五六日"[date.weekday()]
    print(f"航线：{city(origin)} → {city(destination)}   日期：{date.isoformat()}（周{weekday}）")
    for name in failed:
        print(f"[提示] {name} 暂不可用，已跳过（不影响其他票源）。")
    if not offers:
        names = "、".join(s.display_name for s in sources)
        print(f"该航线暂无可用报价（已接入票源：{names}）。")
        print(f"可尝试已有航线，如：search --from SHA --to KIX --date {date.isoformat()}")
        return 0

    offers = sort_offers(offers)
    lowest = offers[0]
    alert = find_alert(load_alerts(), origin, destination)
    if alert and lowest["price"] <= alert["threshold"]:
        print(f"[低价提醒] 当前最低 ¥{lowest['price']} ≤ 你的底价 ¥{alert['threshold']}，可以出手了！")
    print_table(("价格", "票源", "航班/套餐", "含税", "备注"),
                [(f"¥{o['price']}", o["source_name"], o["product"], tax_label(o), o["note"])
                 for o in offers], mark_first=True)
    return 0


def _deal_card(deal):
    return [
        f"┌ 套餐：{deal['name']} [{deal['deal_id']}]",
        f"│ 航线：{city(deal['origin'])} → {city(deal['destination'])}",
        f"│ 价格：¥{deal['price']}（{tax_label(deal)}）｜库存：{disclose(deal.get('stock'), '未披露')}",
        f"│ 有效期：{disclose(deal.get('valid_from'), '未披露')} ~ {disclose(deal.get('valid_until'), '未披露')}",
        f"│ 退改规则：{disclose(deal.get('refund_change_rules'))}",
        f"│ 下单方式：{disclose(deal.get('purchase_entry'))}",
    ] + ([f"│ 限制条件：{deal['note']}"] if deal.get("note") else []) + [
        f"└ 更新时间：{disclose(deal.get('updated_at'), '未披露')}",
    ]


def cmd_deals(args):
    origin = normalize_code(args.from_code, "from") if args.from_code else None
    destination = normalize_code(args.to_code, "to") if args.to_code else None
    deals = [d for s in all_sources() for d in s.deals(origin, destination)]
    print(DEALS_NOTICE)
    scope = f"{city(origin) if origin else '全部'} → {city(destination) if destination else '全部'}"
    print(f"团购专区｜航线过滤：{scope}｜共 {len(deals)} 张套餐")
    if not deals:
        print("当前航线暂无团购套餐。可去掉过滤参数查看全部：deals")
        return 0
    for deal in deals:
        print("\n".join(_deal_card(deal)))
    return 0


def _watch_set(args, alerts):
    if not (args.from_code and args.to_code):
        raise UsageError(f"设置底价需同时提供 --from --to --price，{WATCH_USAGE}")
    origin = normalize_code(args.from_code, "from")
    destination = normalize_code(args.to_code, "to")
    if origin == destination:
        raise UsageError("出发地与到达地不能相同")
    try:
        threshold = int(str(args.price))
    except ValueError:
        threshold = -1
    if threshold <= 0:
        raise UsageError(f"--price 需为正整数（元），收到：{args.price}")
    alerts = [a for a in alerts
              if not (a.get("origin") == origin and a.get("destination") == destination)]
    alerts.append({"origin": origin, "destination": destination, "threshold": threshold,
                   "created_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M")})
    save_alerts(alerts)
    print(f"已保存关注：{city(origin)} → {city(destination)}，底价 ¥{threshold}。")
    print("每次 search 或 watch 时自动比对（模拟数据），命中即输出 [低价提醒]。")
    return 0


def _watch_remove(args, alerts):
    if not (args.from_code and args.to_code):
        raise UsageError("删除关注需同时提供 --from --to --remove，如：watch --from SHA --to PEK --remove")
    origin = normalize_code(args.from_code, "from")
    destination = normalize_code(args.to_code, "to")
    rest = [a for a in alerts
            if not (a.get("origin") == origin and a.get("destination") == destination)]
    if len(rest) == len(alerts):
        print(f"未找到关注：{origin} → {destination}。用 watch --list 查看现有关注。")
        return 0
    save_alerts(rest)
    print(f"已删除关注：{origin} → {destination}，此后不再触发该航线提醒。")
    return 0


def _watch_check(alerts):
    print(SIM_NOTICE)
    if not alerts:
        print(f"暂无关注航线。{WATCH_USAGE}，再运行 watch 检查。")
        return 0
    date = dt.date.today() + dt.timedelta(days=1)
    print(f"检查 {len(alerts)} 条关注航线｜按明天（{date.isoformat()}）出发的模拟最低价判断")
    print(LINE)
    hits = 0
    for a in alerts:
        offers, failed = collect(all_sources(), a["origin"], a["destination"], date)
        route = f"{a['origin']} → {a['destination']}"
        if not offers:
            extra = f"（{'、'.join(failed)} 源不可用）" if failed else ""
            print(f"— {route}：暂无报价数据{extra}")
        elif (lowest := min(o["price"] for o in offers)) <= a["threshold"]:
            hits += 1
            print(f"[低价提醒] {route} 当前最低 ¥{lowest} ≤ 底价 ¥{a['threshold']}，可以出手了！")
        else:
            print(f"— {route} 当前最低 ¥{lowest}，未达底价 ¥{a['threshold']}，继续等待。")
    if hits:
        print(f"共 {hits} 条航线命中底价。")
    return 0


def cmd_watch(args):
    alerts = load_alerts()
    if args.list:
        if not alerts:
            print(f"暂无关注航线。{WATCH_USAGE}")
            return 0
        print(f"共 {len(alerts)} 条关注航线：")
        for i, a in enumerate(alerts, 1):
            print(f"{i}. {a['origin']} → {a['destination']}   底价 ¥{a['threshold']}"
                  f"   设置时间 {a['created_at']}")
        return 0
    if args.remove:
        return _watch_remove(args, alerts)
    if args.price is not None:
        return _watch_set(args, alerts)
    if args.from_code or args.to_code:
        raise UsageError(f"设置底价需同时提供 --from --to --price；仅查看用 watch --list 或裸 watch")
    return _watch_check(alerts)


# ---------- 参数解析与入口 ----------

def build_parser():
    parser = argparse.ArgumentParser(
        prog="fareradar",
        description="低价票雷达：把航司直连价与抖音团购套餐放在一起比（内置模拟数据源，不能购票）。",
        epilog="示例：python src/fareradar.py search --from SHA --to PEK --date 2026-11-15",
    )
    sub = parser.add_subparsers(dest="command")

    p_search = sub.add_parser("search", help="搜索比价：多票源合并，价格升序，标注最低价")
    p_search.add_argument("--from", dest="from_code", help="出发三字码，如 SHA")
    p_search.add_argument("--to", dest="to_code", help="到达三字码，如 PEK")
    p_search.add_argument("--date", help="出发日期 YYYY-MM-DD")
    p_search.add_argument("--source", help="只查指定票源（如 simulated_airline / douyin_groupbuy）")
    p_search.set_defaults(func=cmd_search)

    p_deals = sub.add_parser("deals", help="团购专区：套餐卡含价格/有效期/退改规则/下单提示")
    p_deals.add_argument("--from", dest="from_code", help="按出发三字码过滤（可选）")
    p_deals.add_argument("--to", dest="to_code", help="按到达三字码过滤（可选）")
    p_deals.set_defaults(func=cmd_deals)

    p_watch = sub.add_parser("watch", help="低价提醒：设置/列表/删除底价；裸 watch 立即检查全部关注")
    p_watch.add_argument("--from", dest="from_code", help="出发三字码")
    p_watch.add_argument("--to", dest="to_code", help="到达三字码")
    p_watch.add_argument("--price", help="底价（正整数，元）")
    p_watch.add_argument("--list", action="store_true", help="列出全部关注航线")
    p_watch.add_argument("--remove", action="store_true", help="删除该航线关注")
    p_watch.set_defaults(func=cmd_watch)
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if getattr(args, "func", None) is None:
            parser.print_help()
            return 2
        return args.func(args)
    except UsageError as exc:
        print(f"参数错误：{exc}", file=sys.stderr)
        print("用法示例：python src/fareradar.py search --from SHA --to PEK --date 2026-11-15", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
