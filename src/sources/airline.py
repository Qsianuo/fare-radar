"""航司直连票源：6 航司正班表（确定性生成），每航线早/午/晚各 1 班。

正班表来自 airlines.route_flights（承运人按枢纽规则确定性分配）；
春秋 9C 为低成本舱位（票面不含税），其余航司含税（保留两种口径的真实
产品形态；v1.4 起全部行统一参与到手价比较，口径明细见 fee_breakdown）。
"""

from . import airlines, pricing
from .base import Source


class AirlineSource(Source):
    """航司直连：display_name 为 PRD 裁定的用户语言，"演示"由输出层声明承担。"""

    source_id = "airline"
    display_name = "航司直连"
    tagline = "国航/东航/南航/海航/春秋/吉祥官网直飞正班，退改按航司规则"
    entry_url = ""  # 多家航司无单一入口；前端对空链接不渲染
    entry_note = "各航司官网/官方小程序"

    def search(self, origin, destination, date):
        flights = airlines.route_flights(origin, destination)
        if flights is None:  # 城市码不在注册表或同城
            return []
        offers = []
        for flight in flights:
            carrier = airlines.AIRLINES[flight["carrier_code"]]
            price = pricing.quote(origin, destination, date,
                                  key=f"airline|{flight['flight_no']}",
                                  tier=carrier["tier"])
            offers.append({
                "source_id": self.source_id,
                "source_name": self.display_name,
                "product": flight["flight_no"],
                "price": price,
                "tax_included": carrier["tax_included"],
                "is_deal": False,
                "note": f"{flight['carrier_name']}{flight['carrier_code']} 直飞正班",
                **{k: flight[k] for k in ("depart_time", "arrive_time", "duration_text",
                                          "dep_airport", "arr_airport",
                                          "carrier_code", "carrier_name", "flight_no")},
            })
        return offers
