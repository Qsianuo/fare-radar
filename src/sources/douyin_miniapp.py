"""抖音小程序·春秋航空票源：固定特价票（①类真实商品）。

v1.5.0（ISSUE-012 修复裁定）：
- 保留 21 条固定特价票——出发日期/到达城市/票面价/固定标签逐字采用评审人
  提供的真实商品信息（抖音小程序·春秋航空截图），仅出发日期精确匹配时返回；
- "会员价常规航班"退场：其价格由引擎定价生成、航班号/时刻由 airlines 模块
  哈希编造，与真实航班计划零重合，属编造数据；
- 特价票卡面未展示航班号与起降时刻——诚实披露为"未披露"，禁止用生成值
  填充（评审人约束：禁止编造或拼凑航班号）。

确定性红线：无随机数；特价票仅固定日期可售，同航线同日期任何入口一致。
"""

import datetime as dt

from . import cities
from .base import Source

_FARE_YEAR = 2026     # PRD 裁定：21 条特价票年份 = 2026
_SAVE_TAG = "省 ¥41"  # 固定标签，照抄真实截图

# 21 条固定特价票（PRD v1.4 第 7.1 节逐字：出发日期 MM-DD、到达城市码、票面价）。
# 顺序 = PRD 价格表顺序。
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

    字段：origin/destination/route/date/price/承运人（渠道=春秋航空为事实）/
    卡面目的地显示；航班号与起降时刻卡面未展示，不出字段（上层按"未披露"
    渲染，禁止编造填充）。
    """
    items = []
    for index, (mmdd, dest, price) in enumerate(_SPECIAL_FARES):
        if origin and origin != "SHA":
            continue
        if destination and destination != dest:
            continue
        date = dt.date(_FARE_YEAR, int(mmdd[:2]), int(mmdd[3:]))
        items.append({
            "deal_id": f"SP-{index + 1:02d}",
            "origin": "SHA",
            "destination": dest,
            "route": f"SHA-{dest}",
            "date": date.isoformat(),
            "price": price,
            "carrier_code": "9C",
            "carrier_name": "春秋",
            "destination_display": _dest_display(dest),
        })
    return items


class DouyinMiniappSource(Source):
    """抖音小程序·春秋航空：仅固定特价票（真实商品），仅固定日期可售。"""

    source_id = "douyin_miniapp"
    display_name = "抖音小程序·春秋航空"
    tagline = "春秋自营抖音小程序：21 条固定特价票（真实商品），不含行李额"
    entry_url = "https://v.douyin.com/USSyX47SP7s/"
    entry_note = "春秋航空·特价票与固定航线机票（抖音地点页）"

    def _special_fare(self, origin, destination, date):
        """出发日期精确匹配的固定特价票（仅当日可售，其余日期不出现）。"""
        if origin != "SHA":
            return None
        for entry_date, dest, price in _SPECIAL_FARES:
            if dest != destination:
                continue
            fixed = dt.date(_FARE_YEAR, int(entry_date[:2]), int(entry_date[3:]))
            if date != fixed:
                continue
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
                "carrier_code": "9C",
                "carrier_name": "春秋",
                "flight_no": None,           # 卡面未展示航班号——禁止编造填充
                "note": f"特价票·固定航班日期·不含机建燃油费｜{_SAVE_TAG}"
                        f"｜限 {fixed.isoformat()} 出发｜航班号与时刻未披露（以下单页为准）"
                        f"｜需抖音 App 跳转小程序下单",
            }
        return None

    def availability(self, origin, destination, date):
        """仅当该航线+日期恰有固定特价票时可用；其余显式报无（不编常规票）。"""
        if self._special_fare(origin, destination, date) is not None:
            return True, None
        return False, ("该航线/日期无固定特价票（特价票仅固定日期可售），"
                       "可到春秋官方渠道查询常规航班")

    def search(self, origin, destination, date):
        special = self._special_fare(origin, destination, date)
        return [special] if special is not None else []

    def special_fares(self, origin=None, destination=None):
        """21 条固定特价票目录（deals 视图用，PRD v1.4 第 7.1 节）。"""
        return special_fare_catalog(origin, destination)
