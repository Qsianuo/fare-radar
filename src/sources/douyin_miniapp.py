"""抖音小程序·春秋航空票源：固定特价票（①类产品）+ 会员价常规航班。

两类产品分别建模（PRD v1.4 第 7.1 节）：
- 固定特价票：固定航线+固定日期+固定价+固定航班时间，逐字采用评审人 21 条
  真实国内数据，仅出发日期精确匹配时返回；固定标签"不含机建燃油费/省 ¥41"。
- 会员价常规航班：春秋基地航线（SHA/SHE/SZX 至少一端）∪ 21 条特价票航线，
  引擎定价（会员价特征），对覆盖航线按日期哈希确定性模拟"当日无座"
  （约 1/7 的日期无报价，还原真实渠道"暂无可销售座位"的体验）。

确定性红线：无随机数；特价票固定时刻同航线同日期任何入口一致。
"""

import datetime as dt

from . import airlines, cities, pricing
from .base import Source

_TIER = 0.85  # 会员/小程序渠道价：常低于航司直连
_BASE_CITIES = ("SHA", "SHE", "SZX")  # 春秋基地（演示口径，v1.4 移除大阪）
_NO_SEAT_MODULUS = 7  # 约每 7 天一个"当日无座"日
_FARE_YEAR = 2026     # PRD 裁定：21 条特价票年份 = 2026
_SAVE_TAG = "省 ¥41"  # 固定标签，照抄真实截图
_PERIOD_CYCLE = ("morning", "noon", "evening")  # 固定航班时段按价格表顺序早/中/晚循环

# 21 条固定特价票（PRD v1.4 第 7.1 节逐字：出发日期 MM-DD、到达城市码、票面价）。
# 顺序 = PRD 价格表顺序；时段按价格表顺序早/中/晚循环分布（确定性）。
# 黔江为重庆市下辖区：城市名保持"黔江"，卡面照抄评审人"黔江(重庆)"原口径。
_SPECIAL_FARES = (
    ("10-26", "LJG", 359),
    ("10-27", "JJN", 179),
    ("10-27", "CKG", 189),
    ("10-27", "CAN", 219),
    ("10-27", "CGD", 329),
    ("10-27", "JIQ", 339),
    ("10-27", "JHG", 349),
    ("10-28", "URC", 419),
    ("10-28", "SYX", 609),
    ("10-29", "SHE", 189),
    ("10-29", "KMG", 259),
    ("10-29", "OHE", 569),
    ("10-29", "HTN", 1049),
    ("11-02", "ZYI", 229),
    ("11-04", "ZQZ", 219),
    ("11-04", "SZX", 229),
    ("11-04", "KWE", 309),
    ("11-06", "AAT", 429),
    ("11-07", "MIG", 369),
    ("11-16", "DLU", 389),
    ("11-21", "XIY", 249),
)

# 特价票卡面目的地显示（照抄截图口径；未列出的按城市注册表名）
_SPECIAL_DEST_DISPLAY = {"JIQ": "黔江(重庆)"}


def _dest_display(code):
    return _SPECIAL_DEST_DISPLAY.get(code) or cities.city_name(code)


def special_fare_catalog(origin=None, destination=None):
    """21 条特价票全量目录（deals 视图用）；可按出发/到达三字码过滤。

    字段：origin/destination/route/date/price/时刻/航班号/承运人/机场全称。
    固定航班时段按价格表顺序早/中/晚循环分布（确定性）。
    """
    items = []
    for index, (mmdd, dest, price) in enumerate(_SPECIAL_FARES):
        if origin and origin != "SHA":
            continue
        if destination and destination != dest:
            continue
        date = dt.date(_FARE_YEAR, int(mmdd[:2]), int(mmdd[3:]))
        period = _PERIOD_CYCLE[index % len(_PERIOD_CYCLE)]
        flight = airlines.build_flight("SHA", dest, "9C", f"special|SHA{dest}|{mmdd}", set(),
                                       period=period)
        items.append({
            "deal_id": f"SP-{index + 1:02d}",
            "origin": "SHA",
            "destination": dest,
            "route": f"SHA-{dest}",
            "date": date.isoformat(),
            "price": price,
            "depart_time": flight["depart_time"],
            "arrive_time": flight["arrive_time"],
            "duration_text": flight["duration_text"],
            "flight_no": flight["flight_no"],
            "carrier_code": "9C",
            "carrier_name": "春秋",
            "dep_airport": flight["dep_airport"],
            "arr_airport": flight["arr_airport"],
            "destination_display": _dest_display(dest),
        })
    return items


class DouyinMiniappSource(Source):
    """抖音小程序·春秋航空：固定特价票 + 会员价常规航班（直飞机票，非券）。"""

    source_id = "douyin_miniapp"
    display_name = "抖音小程序·春秋航空"
    tagline = "春秋自营抖音小程序：固定特价票+会员价常规航班，不含行李额"
    entry_url = "https://v.douyin.com/USSyX47SP7s/"
    entry_note = "春秋航空·特价票与固定航线机票（抖音地点页）"

    def availability(self, origin, destination, date):
        """基地航线之外不覆盖；覆盖航线按日期确定性模拟当日无座（约 1/7）。"""
        if not (origin in _BASE_CITIES or destination in _BASE_CITIES):
            return False, "仅覆盖春秋基地航线（上海/沈阳/深圳至少一端），该航线可能未执飞"
        if pricing.stable_hash(f"miniapp|{origin}{destination}|{date.isoformat()}") % _NO_SEAT_MODULUS == 0:
            return False, "当日无座（可能未执飞或当日售罄），可换日期或到春秋官方渠道查询"
        return True, None

    def _special_fare(self, origin, destination, date):
        """出发日期精确匹配的固定特价票（仅当日可售，其余日期不出现）。"""
        if origin != "SHA":
            return None
        for index, (entry_date, dest, price) in enumerate(_SPECIAL_FARES):
            if dest != destination:
                continue
            fixed = dt.date(_FARE_YEAR, int(entry_date[:2]), int(entry_date[3:]))
            if date != fixed:
                continue
            flight = airlines.build_flight(
                "SHA", dest, "9C", f"special|SHA{dest}|{entry_date}", set(),
                period=_PERIOD_CYCLE[index % len(_PERIOD_CYCLE)])
            return {
                "source_id": self.source_id,
                "source_name": self.display_name,
                "product": f"特价票 上海→{_dest_display(dest)}",
                "price": price,
                "tax_included": False,       # "不含机建燃油费"：票面不含税费
                "is_deal": False,
                "is_special_fare": True,
                "no_dedup": True,            # 特价票不参与跨渠道去重（渠道独家固定产品）
                "included_desc": "不含机建燃油费",
                "note": f"特价票·固定航班日期·不含机建燃油费｜{_SAVE_TAG}"
                        f"｜限 {fixed.isoformat()} 出发｜需抖音 App 跳转小程序下单",
                **{k: flight[k] for k in ("depart_time", "arrive_time", "duration_text",
                                          "dep_airport", "arr_airport",
                                          "carrier_code", "carrier_name", "flight_no")},
            }
        return None

    def _regular_offer(self, origin, destination, date):
        """会员价常规航班（引擎定价，9C 承运，确定性时刻/航班号）。"""
        price = pricing.quote(origin, destination, date,
                              key=f"miniapp|{origin}{destination}", tier=_TIER)
        if price is None:
            return None
        flight = airlines.build_flight(origin, destination, "9C",
                                       f"miniapp|{origin}{destination}", set())
        return {
            "source_id": self.source_id,
            "source_name": self.display_name,
            "product": "春秋会员价直飞",
            "price": price,
            "tax_included": True,
            "is_deal": False,
            "note": "会员价常规航班｜需抖音 App 跳转小程序下单｜不含行李额，退改较严",
            **{k: flight[k] for k in ("depart_time", "arrive_time", "duration_text",
                                      "dep_airport", "arr_airport",
                                      "carrier_code", "carrier_name", "flight_no")},
        }

    def search(self, origin, destination, date):
        offers = []
        special = self._special_fare(origin, destination, date)
        if special is not None:
            offers.append(special)
        available, _reason = self.availability(origin, destination, date)
        if available:
            regular = self._regular_offer(origin, destination, date)
            if regular is not None:
                offers.append(regular)
        return offers

    def special_fares(self, origin=None, destination=None):
        """21 条固定特价票目录（deals 视图用，PRD v1.4 第 7.1 节）。"""
        return special_fare_catalog(origin, destination)
