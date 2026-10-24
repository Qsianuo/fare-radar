#!/usr/bin/env python3
"""低价票雷达 FareRadar —— 单文件 CLI 入口：python src/fareradar.py <search|deals|watch>。

输出铁律：含价格的输出首行必标演示数据声明；字段缺失显示"未披露"；
全中文；关键标记用文字符号，不依赖 ANSI 颜色。
"""

import argparse
import datetime as dt
import json
import sys
import unicodedata
from pathlib import Path

from sources import all_sources, cities, source_by_id

try:  # Windows 控制台默认 GBK，强制 UTF-8 防中文乱码（交接坑提示 1）
    for _stream in (sys.stdout, sys.stderr):
        _stream.reconfigure(encoding="utf-8")
except AttributeError:
    pass

ROOT = Path(__file__).resolve().parents[1]
ALERTS_FILE = ROOT / "alerts.json"
LINE = "-" * 64
DISCLOSE = "未披露，下单前请在购买页确认"
REFUND_DISCLOSE = ("商家未公布退改规则，此类券风险最高；"
                   "下单前务必在抖音购买页核对退改条款，并截图留证")
SIM_NOTICE = "[演示数据] 以下价格来自内置演示票源，非真实报价；价格含分钟级演示波动，不能用于实际购票。"
DEALS_NOTICE = "[演示数据] 以下团购套餐来自内置演示票源，非真实商品，不能直接购买。"
WATCH_USAGE = "示例：watch --from SHA --to PEK --price 700"


class UsageError(Exception):
    """参数校验失败：由 main 统一捕获，输出中文说明并以退出码 2 结束。"""


# ---------- 格式化 ----------

def city(code):
    """三字码 → 中文展示名（城市注册表为唯一事实来源），未知码原样返回。"""
    name = cities.city_name(code)
    return f"{name}({code})" if name else code


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


def print_table(headers, rows, mark_index=None):
    widths = [max(display_width(h), max(display_width(r[i]) for r in rows))
              for i, h in enumerate(headers)]
    fmt = lambda cells: "  ".join(pad(c, w) for c, w in zip(cells, widths)).rstrip()
    print(fmt(headers))
    print(LINE)
    for i, row in enumerate(rows):
        print(fmt(row))
        if i == mark_index:
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


def _validity_state(offer, date):
    """团购券有效期相对出发日期的状态（ISSUE-003；口径裁定：有效期＝可出行窗口）。"""
    try:
        covered = (dt.date.fromisoformat(offer["valid_from"]) <= date
                   <= dt.date.fromisoformat(offer["valid_until"]))
        return "covered" if covered else "not_covered"
    except (KeyError, TypeError, ValueError):
        return "unknown"  # 缺失/无法解析——无法确认覆盖，宁可不推荐


def eligible_for_lowest(offer, date):
    """可竞争"◀ 最低价"的准入条件（PRD v1.1 AC1-R/AC6/AC7）。"""
    if not offer.get("tax_included"):
        return False  # ISSUE-001：口径不可比（不含税）
    if offer.get("is_deal") and offer.get("stock") == "售罄":
        return False  # ISSUE-004：售罄券买不到，不参与最低价竞争
    if offer.get("is_deal") and _validity_state(offer, date) != "covered":
        return False  # ISSUE-003：券有效期不覆盖（或无法确认覆盖）出发日期
    return True


def rank_offers(offers, date):
    """展示排序：可竞争最低价的行在前按价格升序，其余（含被披露原因的行）在后。"""
    return sorted(offers, key=lambda offer: (not eligible_for_lowest(offer, date), offer["price"]))


def lowest_eligible(offers, date):
    """可取得"◀ 最低价"并触发低价提醒的最低价；没有准入合格的行返回 None——
    宁可不给标记，不给假箭头。"""
    eligible = [offer for offer in offers if eligible_for_lowest(offer, date)]
    return min(eligible, key=lambda offer: offer["price"]) if eligible else None


def risk_flags(offer, date):
    """行内风险标记（结构化，PRD v1.3：网页与 CLI 同词表渲染）。"""
    flags = []
    if not offer.get("tax_included"):
        flags.append("税费另计，到手价高于票面")
    if offer.get("is_deal"):
        if offer.get("stock") == "售罄":
            flags.append("已售罄，暂时抢不到")
        if not offer.get("refund_disclosed", True):
            flags.append("退改不明")  # AC8：退改规则缺失的券必须带标记，不能只标最低价
        state = _validity_state(offer, date)
        if state == "not_covered":
            flags.append(f"有效期不覆盖 {date.isoformat()} 出发")
        elif state == "unknown":
            flags.append("有效期无法确认是否覆盖出发日期，请到购买页核对")
    return flags


def search_note(offer, date):
    """结果行备注：原始备注 + 风险标记，"不覆盖"信息必须可见（PRD v1.1 AC6）。"""
    note = offer.get("note", "")
    flags = risk_flags(offer, date)
    return f"{note}｜{'｜'.join(flags)}" if flags else note


class AlertsCorrupted(Exception):
    """alerts.json 存在但无法解析或结构不对：拒绝操作以保护用户数据（PRD v1.1 AC9/AC10）。"""


def _warn_alerts_corrupted(path):
    print(f"[警告] 关注文件 {path.name} 已损坏，无法读取，本次操作已取消（你的数据不会被覆盖）。")
    print(f"请先用文本编辑器备份并修复 {path.name}；确认不再需要后也可删除它，再重新设置关注。")


def load_alerts(path=None):
    """读取关注清单。文件缺失＝无关注（返回 []）；文件损坏或结构不对＝打印警告并抛
    AlertsCorrupted——宁可拒绝服务，不静默覆盖用户数据（ISSUE-002）。"""
    path = ALERTS_FILE if path is None else path
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (OSError, json.JSONDecodeError):
        _warn_alerts_corrupted(path)
        raise AlertsCorrupted from None
    if not isinstance(data, list) or not all(isinstance(a, dict) for a in data):
        _warn_alerts_corrupted(path)  # 能解析但不是"关注清单"的结构，视同损坏
        raise AlertsCorrupted from None
    return data


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
        unknown = [c for c in (origin, destination) if cities.city_name(c) is None]
        if unknown:
            print(f"城市码 {'、'.join(unknown)} 不在已支持的 25 城清单中，"
                  f"可查看 README 城市全集或访问网页版下拉选择。")
        else:
            names = "、".join(s.display_name for s in sources)
            print(f"该航线暂无可用报价（已接入票源：{names}）。")
        return 0

    offers = rank_offers(offers, date)
    lowest = lowest_eligible(offers, date)
    try:
        alert = find_alert(load_alerts(), origin, destination)
    except AlertsCorrupted:
        alert = None  # 警告已打印；关注文件损坏不阻断搜索主流程，本次仅跳过提醒比对
    if (alert and lowest and alert.get("threshold") is not None
            and lowest["price"] <= alert["threshold"]):
        print(f"[低价提醒] 当前最低 ¥{lowest['price']} ≤ 你的底价 ¥{alert['threshold']}，可以出手了！")
    if lowest is None:
        print("本次结果中没有口径可比的最低价，原因见各行备注。")
    print_table(("价格", "票源", "航班/套餐", "含税", "备注"),
                [(f"¥{o['price']}", o["source_name"], o["product"], tax_label(o), search_note(o, date))
                 for o in offers], mark_index=offers.index(lowest) if lowest else None)
    return 0


def _deal_card(deal):
    return [
        f"┌ 套餐：{deal['name']} [{deal['deal_id']}｜票源：{disclose(deal.get('source_name'), '未披露')}]",
        f"│ 航线：{city(deal['origin'])} → {city(deal['destination'])}",
        f"│ 价格：¥{deal['price']}（{tax_label(deal)}）｜库存：{disclose(deal.get('stock'), '未披露')}",
        f"│ 有效期：{disclose(deal.get('valid_from'), '未披露')} ~ {disclose(deal.get('valid_until'), '未披露')}",
        f"│ 退改规则：{disclose(deal.get('refund_change_rules'), REFUND_DISCLOSE)}",
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
    print(f"已保存收藏：{city(origin)} → {city(destination)}，底价 ¥{threshold}。")
    print("每次 search 或 watch 时自动比对（演示数据），命中即输出 [低价提醒]。")
    return 0


def _watch_remove(args, alerts):
    if not (args.from_code and args.to_code):
        raise UsageError("删除收藏需同时提供 --from --to --remove，如：watch --from SHA --to PEK --remove")
    origin = normalize_code(args.from_code, "from")
    destination = normalize_code(args.to_code, "to")
    rest = [a for a in alerts
            if not (a.get("origin") == origin and a.get("destination") == destination)]
    if len(rest) == len(alerts):
        print(f"未找到收藏：{origin} → {destination}。用 watch --list 查看现有收藏。")
        return 0
    save_alerts(rest)
    print(f"已删除收藏：{origin} → {destination}。")
    return 0


def _watch_check(alerts):
    print(SIM_NOTICE)
    if not alerts:
        print(f"暂无收藏航线。{WATCH_USAGE}，再运行 watch 检查。")
        return 0
    date = dt.date.today() + dt.timedelta(days=1)
    print(f"检查 {len(alerts)} 条收藏航线｜按明天（{date.isoformat()}）出发的演示最低价判断")
    print(LINE)
    hits = 0
    for a in alerts:
        offers, failed = collect(all_sources(), a["origin"], a["destination"], date)
        route = f"{a['origin']} → {a['destination']}"
        if not offers:
            extra = f"（{'、'.join(failed)} 源不可用）" if failed else ""
            print(f"— {route}：暂无报价数据{extra}")
            continue
        lowest = lowest_eligible(offers, date)
        threshold = a.get("threshold")
        if lowest is None:
            print(f"— {route}：无可比最低价（原因见各行备注），本次不计入提醒。")
        elif threshold is None:
            print(f"— {route} 当前最低 ¥{lowest['price']}（未设底价，仅收藏）。")
        elif lowest["price"] <= threshold:
            hits += 1
            print(f"[低价提醒] {route} 当前最低 ¥{lowest['price']} ≤ 底价 ¥{threshold}，可以出手了！")
        else:
            print(f"— {route} 当前最低 ¥{lowest['price']}，未达底价 ¥{threshold}，继续等待。")
    if hits:
        print(f"共 {hits} 条航线命中底价。")
    return 0


def cmd_watch(args):
    try:
        alerts = load_alerts()
    except AlertsCorrupted:
        return 2  # 警告已打印；损坏状态下拒绝一切读/写操作，保护原文件不被覆盖
    if args.list:
        if not alerts:
            print(f"暂无收藏航线。{WATCH_USAGE}")
            return 0
        print(f"共 {len(alerts)} 条收藏航线：")
        for i, a in enumerate(alerts, 1):
            threshold = (f"底价 ¥{a['threshold']}" if a.get("threshold") is not None else "未设底价")
            print(f"{i}. {a['origin']} → {a['destination']}   {threshold}"
                  f"   收藏时间 {a.get('created_at', '未披露')}")
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
        description="低价票雷达：航司直连、OTA 比价与抖音系票源放在一起比（内置演示数据，不能购票）。",
        epilog="示例：python src/fareradar.py search --from SHA --to PEK --date 2026-11-15",
    )
    sub = parser.add_subparsers(dest="command")

    p_search = sub.add_parser("search", help="搜索比价：5 大票源合并，价格升序，标注最低价")
    p_search.add_argument("--from", dest="from_code", help="出发三字码，如 SHA")
    p_search.add_argument("--to", dest="to_code", help="到达三字码，如 PEK")
    p_search.add_argument("--date", help="出发日期 YYYY-MM-DD")
    p_search.add_argument("--source",
                          help="只查指定票源（airline / ota / douyin_groupbuy / douyin_miniapp / douyin_live）")
    p_search.set_defaults(func=cmd_search)

    p_deals = sub.add_parser("deals", help="团购专区：套餐卡含价格/有效期/退改规则/下单提示")
    p_deals.add_argument("--from", dest="from_code", help="按出发三字码过滤（可选）")
    p_deals.add_argument("--to", dest="to_code", help="按到达三字码过滤（可选）")
    p_deals.set_defaults(func=cmd_deals)

    p_watch = sub.add_parser("watch", help="收藏航线：设置/列表/删除底价；裸 watch 立即检查全部收藏")
    p_watch.add_argument("--from", dest="from_code", help="出发三字码")
    p_watch.add_argument("--to", dest="to_code", help="到达三字码")
    p_watch.add_argument("--price", help="底价（正整数，元）")
    p_watch.add_argument("--list", action="store_true", help="列出全部收藏航线")
    p_watch.add_argument("--remove", action="store_true", help="删除该航线收藏")
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
