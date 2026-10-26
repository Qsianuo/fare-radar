#!/usr/bin/env python3
"""低价票雷达 FareRadar —— 单文件 CLI 入口：python src/fareradar.py <search|deals|watch>。

输出铁律：含价格的输出首行必标数据来源声明；字段缺失显示"未披露"；
全中文；关键标记用文字符号，不依赖 ANSI 颜色。

v1.5.0 口径（ISSUE-012 修复裁定）：比价行仅来自实测快照（snapshot_pack，
人工核验、逐行标注来源）与真实商品（21 条固定特价票/次卡卡池）；引擎编造
的正班/代理/引流/闪购行全部退场；航班号为"数据模型→票源→渲染"一等字段，
展示统一"南航 CZ9782"格式（禁止承运人二字码重复拼接）；快照为静态数据，
演示波动在比价行全面移除，"临近日期最低价条"用快照真实数据实现。
"""

import argparse
import datetime as dt
import json
import sys
import unicodedata
from pathlib import Path

from sources import all_sources, cities, fees, snapshot_pack, source_by_id, spring_store

try:  # Windows 控制台默认 GBK，强制 UTF-8 防中文乱码（交接坑提示 1）
    for _stream in (sys.stdout, sys.stderr):
        _stream.reconfigure(encoding="utf-8")
except AttributeError:
    pass

ROOT = Path(__file__).resolve().parents[1]
ALERTS_FILE = ROOT / "alerts.json"
LINE = "-" * 64
DISCLOSE = "未披露，下单前请在购买页确认"
REFUND_UNDISCLOSED = "退改规则未披露"
REFUND_DISCLOSE = ("商家未公布退改规则，此类商品风险最高；"
                   "下单前务必在抖音购买页核对退改条款，并截图留证")
DATA_NOTICE = ("[实测快照] 以下比价数据来自人工核验的实测快照与真实商品（来源逐行标注，"
               "静态数据不随查询时刻变化）；到手价=票面价+机建+燃油，不能用于实际购票。")
DEALS_NOTICE = ("[真实商品] 以下特价票与卡类商品信息来自评审人提供的真实商品（抖音渠道截图），"
                "本工具不提供购买；请以抖音购买页为准。")
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


def flight_display(offer):
    """航班号一等字段的展示名（ISSUE-012 问题 1/3）：统一"南航 CZ9782"格式。

    航班号本身已含承运人二字码（如 CZ9782 / CA4270|CA4349），展示时只拼
    承运人中文名——禁止再现"南航 CZ · CZ9782"式二字码重复拼接；无航班号的
    行（特价票卡面未展示/卡类）返回空串，由调用方按"未披露"兜底。
    """
    flight_no = offer.get("flight_no")
    if not flight_no:
        return ""
    carrier_name = (offer.get("carrier_name") or "").strip()
    return f"{carrier_name} {flight_no}" if carrier_name else flight_no


def card_refund_text(card):
    """卡的退改披露：缺失必须显著标"退改规则未披露"（DY-005 资产新形态）。"""
    return card.get("refund_change_rules") or f"{REFUND_UNDISCLOSED}（{REFUND_DISCLOSE}）"


def print_table(headers, rows, mark_index=None):
    widths = [max(display_width(h), max(display_width(r[i]) for r in rows))
              for i, h in enumerate(headers)]
    fmt = lambda cells: "  ".join(pad(c, w) for c, w in zip(cells, widths)).rstrip()
    # 分隔线与渲染后最宽行一致（末列窄表头/宽内容时按内容宽度取齐）
    rule = "-" * max([display_width(fmt(headers))] + [display_width(fmt(r)) for r in rows])
    print(fmt(headers))
    print(rule)
    for i, row in enumerate(rows):
        print(fmt(row))
        if i == mark_index:
            print(" " * (widths[0] + 2) + "◀ 最低价")


# ---------- 校验与核心逻辑（纯函数，供单元测试） ----------

def normalize_code(raw, flag):
    code = str(raw).strip().upper()
    if len(code) != 3 or not code.isalpha():
        raise UsageError(f"--{flag} 需为三字城市码（如 SHA/PEK/CTU/URC），收到：{raw}")
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


def total_price(offer):
    """到手价（排序/最低价/提醒唯一口径）；缺 total_estimate 时退回票面价。

    v1.14（ISSUE-023）：上游"有航班、无报价"的行两者都可能为 None，
    此时返回 None——调用方必须当"无价"处理，不得拿 0 占位。
    """
    total = offer.get("total_estimate")
    return total if total is not None else offer.get("price")


def has_price(offer):
    """该行是否有可用价格（票面价或到手价任一存在）。"""
    return total_price(offer) is not None


def _price_sort_key(offer):
    """价格升序键：无价行恒排在所有有价行之后（None 不可比较，且不该占首位）。"""
    price = total_price(offer)
    return (price is None, price if price is not None else 0)


def price_text(offer):
    """价格展示文案（CLI/MCP 同源）：有价 → ¥N；无价 → 「价格未披露」，绝不出 ¥。"""
    price = total_price(offer)
    return f"¥{price}" if price is not None else "价格未披露"


def retired_names(alert):
    """收藏行中已下线城市的中文名列表（v1.4 城市全集收缩兼容）。"""
    return [cities.retired_name(code) for code in (alert.get("origin"), alert.get("destination"))
            if cities.is_retired(code)]


def collect(sources, origin, destination, date):
    """逐源收集报价 → 统一附加费用/到手价 → 跨渠道去重（规则唯一来源）。

    返回 (offers, failed, unavailable)：
    - unavailable：本次"覆盖不含此航线/卡类渠道不出航班/未接入"的渠道
      [{source_id, name, reason, status}]，与 failed（适配器异常降级）语义
      不同；status="not_available" 的渠道是"暂无合规公开接口，未接入"；
    - 比价行由引擎统一附上所在渠道 entry_url/entry_note；费用明细/到手价
      仅对未自带 total_estimate 的行按费用引擎附加（距离取注册表大圆距离），
      实测快照行自带实测含税价（fee_breakdown 已标注来源），引擎不覆盖；
    - 去重（PRD v1.4 第 6 节）：键=出发日期+航班号，仅跨渠道合并，保留到手
      最低价并标注 channel_count；同渠道多班次、券/特价票/卡不参与。
    """
    offers, failed, unavailable = [], [], []
    km = cities.distance_km(origin, destination)
    for source in sources:
        try:
            available, reason = source.availability(origin, destination, date)
            if not available:
                unavailable.append({"source_id": source.source_id,
                                    "name": source.display_name, "reason": reason,
                                    "status": getattr(source, "status", "active"),
                                    "offer_channel": getattr(source, "offer_channel", True)})
                continue
            for offer in source.search(origin, destination, date):
                offer["entry_url"] = source.entry_url
                offer["entry_note"] = source.entry_note
                # v1.14：无价行不进费用引擎（price=None 参与运算只会造出假数字）
                if (offer.get("total_estimate") is None and km is not None
                        and offer.get("price") is not None):
                    offer["fee_breakdown"] = fees.fee_breakdown(
                        offer["price"], offer.get("tax_included", True), km,
                        offer.get("included_desc"))
                    offer["total_estimate"] = fees.total_estimate(
                        offer["price"], offer.get("tax_included", True), km)
                offers.append(offer)
        except Exception:
            failed.append(source.display_name)
    return dedup_offers(offers, date), failed, unavailable


def dedup_offers(offers, date):
    """跨渠道去重：键=出发日期+航班号（同批查询共享出发日期）。

    同一航班被 ≥2 个渠道售卖 → 合并为 1 行，保留到手价最低者的全部字段并
    标注 channel_count/channel_names；同渠道多时段班次（不同航班号）不去重；
    无航班号的行（券/卡）与特价票（no_dedup）不参与。
    """
    groups = {}
    for offer in offers:
        flight_no = offer.get("flight_no")
        if not flight_no or offer.get("no_dedup"):
            continue
        groups.setdefault((date.isoformat(), flight_no), []).append(offer)

    dropped, annotated = set(), {}
    for group in groups.values():
        channels = {o["source_id"] for o in group}
        if len(channels) < 2:
            continue  # 单渠道航班（含同渠道多班次）一律保留
        ordered = sorted(group, key=lambda o: (_price_sort_key(o), o["source_id"],
                                               o.get("product", "")))
        winner = ordered[0]
        for loser in ordered[1:]:
            dropped.add(id(loser))
        annotated[id(winner)] = {
            "channel_count": len(channels),
            "channel_names": sorted({o["source_name"] for o in group}),
        }
    result = []
    for offer in offers:
        if id(offer) in dropped:
            continue
        if id(offer) in annotated:
            offer = {**offer, **annotated[id(offer)]}
        result.append(offer)
    return result


def card_hints(origin, destination, date, sources=None):
    """本次搜索命中的卡（独立数据块 card_hints，不计入比价行，PRD 第 7.2 节）。"""
    hints = []
    for source in (sources if sources is not None else all_sources()):
        cards_for = getattr(source, "cards_for", None)
        if cards_for is None:
            continue
        for card in cards_for(origin, destination, date):
            card = dict(card)
            card["entry_url"] = source.entry_url
            card["entry_note"] = source.entry_note
            hints.append(card)
    return hints


def special_fare_catalog(origin=None, destination=None):
    """固定特价票目录（deals 视图用）；来自声明了 special_fares 的票源。"""
    items = []
    for source in all_sources():
        catalog = getattr(source, "special_fares", None)
        if catalog is None:
            continue
        for item in catalog(origin, destination):
            entry = dict(item)
            entry.setdefault("source_id", source.source_id)
            entry["entry_url"] = source.entry_url
            entry["entry_note"] = source.entry_note
            items.append(entry)
    return items


def _validity_state(offer, date):
    """团购券有效期相对出发日期的状态（ISSUE-003；口径裁定：有效期＝可出行窗口）。"""
    try:
        covered = (dt.date.fromisoformat(offer["valid_from"]) <= date
                   <= dt.date.fromisoformat(offer["valid_until"]))
        return "covered" if covered else "not_covered"
    except (KeyError, TypeError, ValueError):
        return "unknown"  # 缺失/无法解析——无法确认覆盖，宁可不推荐


def eligible_for_lowest(offer, date):
    """可竞争"◀ 最低价"的准入条件（PRD v1.4 第 6/8 节）。

    v1.4 口径修订：费用确定化后所有行按到手价统一可比（"税费另计"退出竞争
    的旧规则退役，由 fee_breakdown 明细取代）；仅售罄券与有效期不覆盖
    （或无法确认覆盖）出发日期的券退出竞争。
    """
    if offer.get("price") is None and offer.get("total_estimate") is None:
        return False
    if offer.get("is_deal") and offer.get("stock") == "售罄":
        return False  # ISSUE-004：售罄券买不到，不参与最低价竞争
    if offer.get("is_deal") and _validity_state(offer, date) != "covered":
        return False  # ISSUE-003：券有效期不覆盖（或无法确认覆盖）出发日期
    return True


def rank_offers(offers, date):
    """展示排序：可竞争最低价的行在前按到手价升序，其余（含被披露原因的行）在后。

    v1.14（ISSUE-023）：无价行（上游未给价）排在所有有价行之后，且不参与最低价。
    """
    return sorted(offers, key=lambda offer: (not eligible_for_lowest(offer, date),
                                             *_price_sort_key(offer),
                                             offer["source_id"], offer.get("product", "")))


def lowest_eligible(offers, date):
    """可取得"◀ 最低价"并触发低价提醒的到手最低价行；没有准入合格的行返回
    None——宁可不给标记，不给假箭头。"""
    eligible = [offer for offer in offers if eligible_for_lowest(offer, date)]
    return (min(eligible, key=total_price) if eligible else None)


def price_insights(sources, origin, destination, date):
    """价格行情参考（当前由 RapidAPI·Google Flights 渠道提供）。

    只读各票源**已缓存**的洞察，不回源（零额外配额）；没有就返回 None——
    零编造：不推算、不拿历史价格凑一个"贵/便宜"的判断。网页/CLI/MCP 共用本函数，
    保证三入口同源。
    """
    for source in sources:
        reader = getattr(source, "insights", None)
        if not callable(reader):
            continue
        try:
            found = reader(origin, destination, date)
        except Exception:          # 单渠道异常不影响比价主流程
            found = None
        if found:
            return found
    return None


def empty_notes(sources, origin, destination, date):
    """空结果的原因说明（三入口共用，只读票源已缓存的判定，不回源）。

    用于区分"上游没有这条航线的数据"与"查询失败/配额受限"——笼统的
    "暂无可用报价"会让用户误以为工具坏了。
    """
    notes = []
    for source in sources:
        reader = getattr(source, "empty_note", None)
        if not callable(reader):
            continue
        try:
            note = reader(origin, destination, date)
        except Exception:
            note = None
        if note:
            notes.append(f"{getattr(source, 'display_name', source.source_id)}：{note}")
    return notes


def month_lowest(sources, origin, destination, month, cheapest=5):
    """整月最低**参考价**（v1.12，不参与比价、不进到手价排序）。

    返回 (days, source_name)：days 为按价格升序的前 cheapest 天，
    每项 {date, price, airline, flight_no}；无数据返回 ([], None)。
    三入口共用，规则唯一来源。
    """
    for source in sources:
        reader = getattr(source, "calendar", None)
        if not callable(reader):
            continue
        try:
            days = reader(origin, destination, month) or []
        except Exception:            # 单渠道异常不影响比价主流程
            days = []
        if days:
            ordered = sorted(days, key=lambda item: (item["price"], item["date"]))
            return ordered[:cheapest], getattr(source, "display_name", source.source_id)
    return [], None


LEVEL_TEXT = {"low": "偏低", "typical": "正常价", "high": "偏高"}


def insight_text(insight, sep="｜"):
    """行情参考的一句话渲染（三入口共用词表；无数据的项不出现）。"""
    parts = [f"行情参考（{insight.get('source_name') or '上游'} 口径）"]
    level = LEVEL_TEXT.get(insight.get("price_level") or "")
    if level:
        parts.append(level)
    if insight.get("lowest_price") is not None:
        parts.append(f"本次最低 ¥{insight['lowest_price']}")
    rng = insight.get("typical_price_range")
    if rng:
        parts.append(f"常规区间 ¥{rng[0]}–{rng[1]}")
    history = insight.get("history") or []
    if history:
        prices = [h["price"] for h in history]
        parts.append(f"近 {len(prices)} 天 ¥{min(prices)}–{max(prices)}（抽样）")
    return sep.join(parts)


def risk_flags(offer, date):
    """行内风险标记（结构化，PRD v1.3：网页与 CLI 同词表渲染）。

    v1.4："税费另计"标记退役（费用明细由 fee_breakdown 承载）。
    """
    flags = []
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
    """结果行备注：原始备注 + 跨渠道有售 + 风险标记（PRD v1.4 第 6 节）。"""
    note = offer.get("note", "")
    middle = []
    if offer.get("channel_count", 1) > 1:
        middle.append(f"另有 {offer['channel_count'] - 1} 个渠道有售")
    middle.extend(risk_flags(offer, date))
    return f"{note}｜{'｜'.join(middle)}" if middle else note


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


def save_alerts(alerts, path=None):
    path = ALERTS_FILE if path is None else path
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


def _unknown_city_text(codes):
    """未知城市码的中文提示（含 v1.4 已下线城市的显式标注）。"""
    parts = []
    for code in codes:
        retired = cities.retired_name(code)
        parts.append(f"{code}（{retired}，城市已下线）" if retired else code)
    return (f"城市码 {'、'.join(parts)} 不在已支持的 50 城清单中，"
            f"可查看 README 城市全集或访问网页版下拉选择。")


# ---------- 命令：search / deals / watch ----------

def _time_text(offer):
    if offer.get("depart_time") and offer.get("arrive_time"):
        cross = "(+1)" if offer.get("cross_day") else ""
        return f"{offer['depart_time']}→{offer['arrive_time']}{cross}"
    return "—"


def _flight_text(offer):
    """航班/套餐列：有航班号的行展示"厦航 MF8753"，否则展示产品名。"""
    return flight_display(offer) or offer["product"]


def _airport_text(offer):
    if offer.get("dep_airport") and offer.get("arr_airport"):
        return f"{offer['dep_airport']}→{offer['arr_airport']}"
    return "—"


def _fee_text(offer):
    breakdown = offer.get("fee_breakdown")
    if breakdown:
        return breakdown["included_desc"]
    return "价格未披露（无税费口径可算）" if not has_price(offer) else "—"


def _print_card_hints(hints):
    """卡提示块：命中卡在比价行前输出（PRD v1.4 第 7.2 节）。"""
    print(f"[卡提示] {len(hints)} 张卡适用本航线（卡不计入比价，需抖音 App 下单）：")
    for i, card in enumerate(hints, 1):
        print(f"  {i}. {card['name']} ¥{card['price']}｜{card['validity_text']}"
              f"｜{card_refund_text(card)}｜{card['stock']}")


def cmd_search(args):
    origin = normalize_code(args.from_code, "from")
    destination = normalize_code(args.to_code, "to")
    if origin == destination:
        raise UsageError("出发地与到达地不能相同")
    date = parse_date(args.date)
    sources = _selected_sources(args.source)

    offers, failed, unavailable = collect(sources, origin, destination, date)
    print(DATA_NOTICE)
    weekday = "一二三四五六日"[date.weekday()]
    print(f"航线：{city(origin)} → {city(destination)}   日期：{date.isoformat()}（周{weekday}）")
    for name in failed:
        print(f"[提示] {name} 暂不可用，已跳过（不影响其他票源）。")
    not_integrated = [u for u in unavailable if u.get("status") == "not_available"]
    if not_integrated:
        names = "、".join(u["name"] for u in not_integrated)
        print(f"未接入渠道（暂无合规公开接口）：{names}")
    on_sale_off = [u for u in unavailable
                   if u.get("status") != "not_available" and u.get("offer_channel", True)]
    if on_sale_off:
        detail = "、".join(f"{u['name']}（{u['reason']}）" for u in on_sale_off)
        print(f"以下渠道本次无销售：{detail}")
    hints = card_hints(origin, destination, date, sources)
    if hints:
        _print_card_hints(hints)
    nearby = snapshot_pack.nearby_lowest(origin, destination)
    if nearby:
        bar = "｜".join(f"{point['label']} ¥{point['price']}" for point in nearby)
        print(f"临近日期最低价（裸价，来源：{snapshot_pack.DATA_SOURCE}）：{bar}")
    insight = price_insights(sources, origin, destination, date)
    if insight:
        print(insight_text(insight))
        print(f"  {insight.get('basis', '')}；{insight.get('history_note', '')}")
    cheapest_days, cal_source = month_lowest(sources, origin, destination,
                                             date.isoformat()[:7])
    if cheapest_days:
        bar = "｜".join(f"{item['date'][5:]} ¥{item['price']}" for item in cheapest_days)
        print(f"整月最便宜（参考价·非可售，来源：{cal_source}）：{bar}")
    if not offers:
        unknown = [c for c in (origin, destination) if cities.city_name(c) is None]
        notes = empty_notes(sources, origin, destination, date)
        snapshot_selected = any(s.source_id == snapshot_pack.SOURCE_ID for s in sources)
        if notes:
            print("；".join(notes))
            print("可更换日期或航线再试。")
        elif unknown:
            print(_unknown_city_text(unknown))
        elif snapshot_selected and not snapshot_pack.is_covered(origin, destination, date):
            print(f"该航线暂无真实数据快照；当前收录范围：{snapshot_pack.coverage_text()}。")
            print("可到航旅纵横/航司官网等官方渠道查询实时报价。")
        elif unavailable and not failed:
            print("本次查询所选渠道均无销售，原因见上；可更换日期或航线再试。")
        else:
            names = "、".join(s.display_name for s in sources)
            print(f"该航线暂无可用报价（已登记票源：{names}）。")
        return 0

    offers = rank_offers(offers, date)
    lowest = lowest_eligible(offers, date)
    try:
        alert = find_alert(load_alerts(), origin, destination)
    except AlertsCorrupted:
        alert = None  # 警告已打印；关注文件损坏不阻断搜索主流程，本次仅跳过提醒比对
    if (alert and lowest and alert.get("threshold") is not None
            and total_price(lowest) <= alert["threshold"]):
        print(f"[低价提醒] 当前最低到手价 ¥{total_price(lowest)} ≤ 你的底价 "
              f"¥{alert['threshold']}，可以出手了！")
    if lowest is None and offers and not any(has_price(o) for o in offers):
        print(f"[提示] 本次找到 {len(offers)} 个航班，但上游均未提供报价（价格标为「未披露」，"
              "不做排序、不做最低价判断）。航班信息真实，购票前请到官方渠道核实价格。")
    elif lowest is None:
        print("本次结果中没有可竞争的最低价（原因见各行备注）。")
    print_table(("到手价", "票面价", "票源", "航班/套餐", "时间", "机场", "税费", "备注"),
                [(price_text(o),
                  f"¥{o['price']}" if o.get("price") is not None else "未披露",
                  o["source_name"], _flight_text(o),
                  _time_text(o), _airport_text(o), _fee_text(o), search_note(o, date))
                 for o in offers], mark_index=offers.index(lowest) if lowest else None)
    return 0


def _card_lines(card):
    """卡面渲染（次卡/省钱卡/闪购卡通用）；缺失字段按"未披露"兜底。"""
    card_id = card.get("card_id") or card.get("deal_id")
    lines = [f"┌ 卡面：{disclose(card.get('name'))} [{disclose(card_id)}"
             f"｜渠道：{disclose(card.get('source_name'))}]"]
    if card.get("origin") and card.get("destination"):
        lines.append(f"│ 航线：{city(card['origin'])} → {city(card['destination'])}")
    if card.get("scope"):
        lines.append(f"│ 适用范围：{card['scope']}")
    lines.append(f"│ 价格：¥{card['price']}")
    validity = card.get("validity_text") or (
        f"{disclose(card.get('valid_from'))} ~ {disclose(card.get('valid_until'))}")
    lines.append(f"│ 有效期：{validity}")
    lines.append(f"│ 退改规则：{card_refund_text(card)}")
    lines.append(f"│ 库存：{disclose(card.get('stock'))}")
    lines.append(f"│ 下单方式：{disclose(card.get('purchase_entry'))}")
    if card.get("note"):
        lines.append(f"│ 限制条件：{card['note']}")
    lines.append(f"└ 更新时间：{disclose(card.get('updated_at'))}")
    return lines


def cmd_deals(args):
    origin = normalize_code(args.from_code, "from") if args.from_code else None
    destination = normalize_code(args.to_code, "to") if args.to_code else None
    specials = special_fare_catalog(origin, destination)
    cards = [d for s in all_sources() for d in s.deals(origin, destination)]
    print(DEALS_NOTICE)
    scope = f"{city(origin) if origin else '全部'} → {city(destination) if destination else '全部'}"
    print(f"特价票与卡类专区｜航线过滤：{scope}")
    print(f"—— 固定特价票（{len(specials)} 条｜渠道：抖音小程序·春秋航空｜仅固定日期可售）——")
    if specials:
        # 卡面未展示航班号/时刻：按"未披露"诚实渲染，禁止用生成值填充（ISSUE-012）
        print_table(("日期", "航线", "票价", "航班", "时间", "标签"),
                    [(s["date"], f"上海→{s['destination_display']}", f"¥{s['price']}",
                      disclose(s.get("flight_no"), "未披露（以下单页为准）"),
                      disclose(s.get("depart_time"), "未披露（以下单页为准）"),
                      "特价票·不含机建燃油费｜省 ¥41") for s in specials])
    else:
        print("当前过滤无特价票；去掉过滤参数可查看全部 21 条固定特价票。")
    print(f"—— 卡池（{len(cards)} 张，不含具体航班）——")
    if not cards:
        print("当前航线暂无卡类商品；去掉过滤参数可查看全部卡池。")
        return 0
    for card in cards:
        print("\n".join(_card_lines(card)))
    return 0


def cmd_spring(args):
    """春秋券库两级查询（v1.13，ISSUE-022）：先查可兑性（不含价），再折算单价。

    输出顺序刻意与 tool 契约一致：第①段绝不出现金额，第②段才给折算单价，
    避免"票面价"与"券折算价"混在一张表里被误读。
    """
    date = parse_date(args.date) if args.date else None
    date_text = date.isoformat() if date else "未指定日期"
    flights = spring_store.available_flights(args.from_code, args.to_code, date_text
                                            if date else None)
    if not flights.get("ok"):
        raise UsageError(flights.get("error", "查询失败"))
    quote = spring_store.coupon_quote(args.from_code, args.to_code,
                                      date.isoformat() if date else None)
    if not quote.get("ok"):
        raise UsageError(quote.get("error", "折算失败"))

    cover = flights["coverage"]
    query = flights["query"]
    print(f"[春秋券库] 数据来源：{cover['source_kind']}（{cover['captured_at']}）｜"
          f"批次 {cover['batch_id']}")
    print(f"航线：{query['dep_city']} → {query['arr_city']} ｜ {date_text}"
          f" ｜ 承运人：春秋航空(9C)")

    print(f"\n—— ① 可用航班/商品（{len(flights['flights'])} 条｜不含价格）——")
    if flights["flights"]:
        for index, item in enumerate(flights["flights"], 1):
            print(f"{index}. {item['product']}｜{item['sale_status_text']}"
                  f"｜日期：{item['date'] or '不限（券未绑定日期）'}"
                  f"｜航班号/时刻：{disclose(None, '未披露（以下单页为准）')}")
    else:
        print(flights.get("miss_hint") or "无可用商品。")
    print("※ 本段按设计不输出任何价格：价格只能由券规则折算（见第②段）。")

    print(f"\n—— ② 券匹配与折算单价（{len(quote['quotes'])} 个）——")
    if not quote["quotes"]:
        print(quote.get("miss_hint") or "无匹配券。")
        return 0
    for index, item in enumerate(quote["quotes"], 1):
        unit = item.get("unit_price_cny")
        unit_text = f"¥{item['unit_price_cny']:.2f}".rstrip("0").rstrip(".") \
            if unit is not None else "暂不可给"
        print(f"{index}. {item['coupon_name']}｜折算 {unit_text}/人次/单程"
              f"｜口径：{item['price_basis']}")
        print(f"   {item['formula']}")
        if item.get("candidate_dates"):
            for cand in item["candidate_dates"]:
                print(f"   可售 {cand['date']}：¥{cand['price_cny']:g}（{cand['price_basis']}）")
        if item.get("validity"):
            print(f"   有效期：{item['validity']}")
        for warning in item.get("warnings") or []:
            print(f"   ⚠ {warning}")
    best = quote.get("best_unit_price_cny")
    groups = quote.get("basis_groups") or []
    print("")
    if groups:
        print("—— ③ 按税费口径分组（口径不同不合并排名）——")
        for group in groups:
            print(f"· {group['basis']}：最低 ¥{group['lowest_unit_cny']:g}"
                  f"（{group['lowest_coupon_name']}，共 {group['count']} 个）")
    if best is not None:
        print(f"\n结论：券 {quote['best_coupon_id']} 折算价最低，¥{best:g}/人次/单程"
              f"（{quote['best_reason']}）。")
    else:
        print(f"\n结论：不自动排名——{quote.get('best_reason')}。")
        if quote.get("cross_basis_note"):
            print(quote["cross_basis_note"])
    print(quote["notice"])
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
    print("每次 search 或 watch 时自动比对（快照数据，按到手价判断），命中即输出 [低价提醒]。")
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
    print(DATA_NOTICE)
    if not alerts:
        print(f"暂无收藏航线。{WATCH_USAGE}，再运行 watch 检查。")
        return 0
    date = dt.date.today() + dt.timedelta(days=1)
    print(f"检查 {len(alerts)} 条收藏航线｜按明天（{date.isoformat()}）出发的到手价判断")
    print(LINE)
    hits = 0
    for a in alerts:
        route = f"{a['origin']} → {a['destination']}"
        retired = retired_names(a)
        if retired:
            print(f"— {route}：城市已下线（v1.4 城市全集收缩）：{'、'.join(retired)}；"
                  f"该收藏不参与比价与提醒，可删除或改藏其他航线。")
            continue
        offers, failed, _unavailable = collect(all_sources(), a["origin"], a["destination"], date)
        if not offers:
            extra = f"（{'、'.join(failed)} 源不可用）" if failed else ""
            print(f"— {route}：暂无报价数据{extra}")
            continue
        lowest = lowest_eligible(offers, date)
        threshold = a.get("threshold")
        if lowest is None:
            print(f"— {route}：无可比最低价（原因见各行备注），本次不计入提醒。")
        elif threshold is None:
            print(f"— {route} 当前最低到手价 ¥{total_price(lowest)}（未设底价，仅收藏）。")
        elif total_price(lowest) <= threshold:
            hits += 1
            print(f"[低价提醒] {route} 当前最低到手价 ¥{total_price(lowest)} "
                  f"≤ 底价 ¥{threshold}，可以出手了！")
        else:
            print(f"— {route} 当前最低到手价 ¥{total_price(lowest)}，"
                  f"未达底价 ¥{threshold}，继续等待。")
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
            retired = retired_names(a)
            mark = (f"   ⚠ 城市已下线（v1.4 城市全集收缩）：{'、'.join(retired)}，"
                    f"不再触发查询" if retired else "")
            print(f"{i}. {a['origin']} → {a['destination']}   {threshold}"
                  f"   收藏时间 {a.get('created_at', '未披露')}{mark}")
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
        description="低价票雷达：实测快照与真实特价票/卡类商品比价（数据逐行标注来源，不能购票）。",
        epilog="示例：python src/fareradar.py search --from INC --to CAN --date 2026-11-01",
    )
    sub = parser.add_subparsers(dest="command")

    p_search = sub.add_parser("search", help="搜索比价：到手价升序，含时间/机场/航班号/税费明细")
    p_search.add_argument("--from", dest="from_code", help="出发三字码，如 INC")
    p_search.add_argument("--to", dest="to_code", help="到达三字码，如 CAN")
    p_search.add_argument("--date", help="出发日期 YYYY-MM-DD")
    p_search.add_argument("--source",
                          help="只查指定票源（snapshot_pack / airline / ota / douyin_groupbuy /"
                               " douyin_miniapp / douyin_live；未接入渠道仅返回状态提示）")
    p_search.set_defaults(func=cmd_search)

    p_deals = sub.add_parser("deals", help="特价票清单+次卡/省钱卡/闪购卡卡面（含退改披露）")
    p_deals.add_argument("--from", dest="from_code", help="按出发三字码过滤（可选）")
    p_deals.add_argument("--to", dest="to_code", help="按到达三字码过滤（可选）")
    p_deals.set_defaults(func=cmd_deals)

    p_spring = sub.add_parser(
        "spring", help="春秋券库：先查可用航班（不含价），再按券规则折算单价")
    p_spring.add_argument("--from", dest="from_code", help="出发城市（三字码或中文名）")
    p_spring.add_argument("--to", dest="to_code", help="到达城市（三字码或中文名）")
    p_spring.add_argument("--date", help="出发日期 YYYY-MM-DD（可选；省钱卡专享价按日期定价）")
    p_spring.set_defaults(func=cmd_spring)

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
