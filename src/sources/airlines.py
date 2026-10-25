"""航司注册表与确定性航班生成器（PRD v1.4 第 5/6 节）。

硬约束：全程确定性、无随机——承运人、航班号、时刻全部由 md5 稳定哈希
派生；同一航线同日期任何入口查询结果一致。

- 名册 6 家：国航 CA（枢纽北京）、东航 MU（枢纽上海）、南航 CZ（枢纽广州）、
  海航 HU（枢纽北京/海口）、春秋 9C（枢纽上海/沈阳/深圳）、吉祥 HO（枢纽上海/杭州）。
- 承运人分配：航线两端触及枢纽 → 枢纽航司内确定性分配（如 SHA 出发优先
  MU/9C/HO）；两端均非枢纽的通达航线 → 全名册确定性分布。
- 时段覆盖不变式：每航线每日 ≥3 班，早 06:00-11:59 / 午 12:00-17:59 /
  晚 18:00-23:59 各 ≥1 班；起飞时刻 5 分钟粒度；不允许跨日到达——若
  起飞+航程时长溢出 23:59 则前移起飞时刻（钳制在时段窗口内，确定性）。
"""

from . import cities, pricing

# 承运人名册：code → (中文名, 枢纽城市集, 定价档位, 是否含税)
_AIRLINES = (
    ("CA", "国航", ("PEK",), 1.02, True),
    ("MU", "东航", ("SHA",), 1.00, True),
    ("CZ", "南航", ("CAN",), 0.98, True),
    ("HU", "海航", ("PEK", "HAK"), 0.96, True),
    ("9C", "春秋", ("SHA", "SHE", "SZX"), 0.86, False),
    ("HO", "吉祥", ("SHA", "HGH"), 0.92, True),
)
AIRLINES = {code: {"name": name, "hubs": hubs, "tier": tier, "tax_included": tax}
            for code, name, hubs, tier, tax in _AIRLINES}
AIRLINE_ORDER = tuple(code for code, *_ in _AIRLINES)

# 时段窗口（分钟）：早/午/晚各 6 小时；时刻取窗口内 5 分钟粒度槽位
_PERIODS = (
    ("morning", 6 * 60, "早"),
    ("noon", 12 * 60, "午"),
    ("evening", 18 * 60, "晚"),
)
_PERIOD_WINDOW_MIN = 355          # 窗口内可选槽位数 ×5 分钟（留 5 分钟余量防跨窗）
_DAY_LAST_MINUTE = 23 * 60 + 59   # 不允许跨日到达的硬上限
_CRUISE_KMH = 780                 # 巡航速度估算
_GROUND_MINUTES = 35              # 起降滑行等固定开销


def carrier_name(code):
    """航司二字码 → 中文名（如"国航"）；未注册返回 None。"""
    entry = AIRLINES.get(code)
    return entry["name"] if entry else None


def _pick_carrier(origin, destination, slot):
    """枢纽规则 + 确定性哈希 → 承运人二字码。

    枢纽航线在枢纽航司内分配（哈希决定具体哪家）；两端均非枢纽的通达航线
    在全名册内确定性分布（PRD v1.4 第 6 节"哈希决定，无随机"）。
    """
    hub_pool = [c for c in AIRLINE_ORDER
                if origin in AIRLINES[c]["hubs"] or destination in AIRLINES[c]["hubs"]]
    pool = hub_pool or list(AIRLINE_ORDER)
    digest = pricing.stable_hash(f"carrier|{origin}{destination}|{slot}")
    return pool[digest % len(pool)]


def _slot_departure(origin, destination, period_start, salt):
    """窗口内 5 分钟粒度的确定性起飞时刻（分钟数）。

    跨日钳制由 build_flight 按航程时长统一处理（此处只落在时段窗口内）。
    """
    digest = pricing.stable_hash(f"slot|{origin}{destination}|{salt}")
    return period_start + (digest % (_PERIOD_WINDOW_MIN // 5)) * 5


def duration_minutes(origin, destination):
    """航段时长（分钟，向上取整至 5 分钟）：大圆距离 ÷ 巡航速度 + 地面开销。

    同城或任一城市码不在注册表返回 None（同城无航段）。
    """
    if origin == destination:
        return None
    km = cities.distance_km(origin, destination)
    if km is None:
        return None
    raw = km / _CRUISE_KMH * 60 + _GROUND_MINUTES
    return int(-(-raw // 5) * 5)  # 向上取整到 5 分钟（ceil）


def format_minutes(minutes):
    """分钟数 → HH:MM。"""
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def duration_text(minutes):
    """分钟数 → 中文航程时长（如"2小时45分"）。"""
    return f"{minutes // 60}小时{minutes % 60:02d}分"


def _flight_no(carrier, origin, destination, salt, taken):
    """承运人二字码 + 4 位数字（按航线+班次确定性取值）；同航线同承运人去重。"""
    for attempt in range(10):
        digest = pricing.stable_hash(f"flightno|{carrier}|{origin}{destination}|{salt}|{attempt}")
        number = f"{carrier}{1000 + digest % 9000}"
        if number not in taken:
            taken.add(number)
            return number
    raise AssertionError("确定性航班号去重失败（不应到达）")


def build_flight(origin, destination, carrier, salt, taken, period=None):
    """构造一个确定性航班 dict（时刻/时长/航班号/机场全称/承运人）。

    salt 区分同航线不同班次（正班槽位用序号，专营渠道用渠道盐）；period 可
    显式指定时段（早/午/晚按清单顺序分布的场景），缺省按哈希从三时段中取。
    到达时间 = 起飞 + 航程时长；若溢出 23:59 则前移起飞时刻（不跨日，
    钳制仍落在 5 分钟粒度与时段窗口内）。
    """
    duration = duration_minutes(origin, destination)
    if duration is None:
        return None
    if period is None:
        period = _PERIODS[int(pricing.stable_hash(
            f"period|{origin}{destination}|{salt}") % len(_PERIODS))][0]
    period_key, period_start, period_label = next(p for p in _PERIODS if p[0] == period)
    dep = _slot_departure(origin, destination, period_start, salt)
    latest = ((_DAY_LAST_MINUTE - duration) // 5) * 5   # 不跨日：最晚可起飞（5 分钟粒度）
    dep = min(dep, latest)
    if dep < period_start:                        # 极端长航线钳出窗口：整体前移到窗口起点
        dep = period_start
    entry = AIRLINES[carrier]
    return {
        "period": period_key,
        "period_label": period_label,
        "depart_time": format_minutes(dep),
        "arrive_time": format_minutes(dep + duration),
        "duration_minutes": duration,
        "duration_text": duration_text(duration),
        "flight_no": _flight_no(carrier, origin, destination, salt, taken),
        "carrier_code": carrier,
        "carrier_name": entry["name"],
        "dep_airport": cities.airport_name(origin),
        "arr_airport": cities.airport_name(destination),
    }


def route_flights(origin, destination):
    """一条航线的每日正班表：早/午/晚各 1 班（共 3 班，确定性）。

    返回航班 dict 列表（按早→午→晚排序）；未知城市/同城返回 None。
    时段与班次一一对应（时段覆盖不变式的实现载体）；航司直连与 OTA 共用
    本表——OTA 代理票携带同一航班号即形成真实跨渠道重复。
    """
    if origin == destination or cities.distance_km(origin, destination) is None:
        return None
    taken, flights = set(), []
    for slot in range(3):
        carrier = _pick_carrier(origin, destination, slot)
        flight = build_flight(origin, destination, carrier, f"slot{slot}", taken,
                              period=_PERIODS[slot][0])
        if flight is None:
            return None
        flights.append(flight)
    return sorted(flights, key=lambda f: f["depart_time"])
