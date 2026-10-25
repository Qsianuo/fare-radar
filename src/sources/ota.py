"""OTA 比价票源：代理报价 + 引流特价。

- "特惠代理票"：代理同一物理航班——航班号/时刻确定性取自航司正班表
  （airlines.route_flights），与航司直连形成真实跨渠道重复，
  由引擎按"出发日期+航班号"去重、保留到手最低价（PRD v1.4 第 6 节）；
- "低价引流票"：引流特价不指定航班（无航班号），不参与去重。
"""

from . import airlines, pricing
from .base import Source


class OtaSource(Source):
    """OTA 比价：PRD v1.3 票源家族之一，多方向渠道。"""

    source_id = "ota"
    display_name = "OTA 比价"
    tagline = "多平台聚合比价，代理票退改更严、可能收服务费"
    entry_url = "https://www.ctrip.com/"
    entry_note = "示例入口：携程官网（OTA 平台之一）"

    def search(self, origin, destination, date):
        flights = airlines.route_flights(origin, destination)
        if flights is None:  # 城市码不在注册表或同城
            return []
        agent_flight = flights[pricing.stable_hash(f"ota|agent|{origin}{destination}") % len(flights)]
        agent_price = pricing.quote(origin, destination, date,
                                    key=f"ota|特惠代理票|{origin}{destination}", tier=0.9)
        lead_price = pricing.quote(origin, destination, date,
                                   key=f"ota|低价引流票|{origin}{destination}", tier=0.8)
        flight_fields = {k: agent_flight[k] for k in ("depart_time", "arrive_time", "duration_text",
                                                      "dep_airport", "arr_airport",
                                                      "carrier_code", "carrier_name", "flight_no")}
        return [
            {
                "source_id": self.source_id,
                "source_name": self.display_name,
                "product": f"特惠代理票·{agent_flight['flight_no']}",
                "price": agent_price,
                "tax_included": True,
                "is_deal": False,
                "note": "代理直飞，退改更严，可能收服务费",
                **flight_fields,
            },
            {
                "source_id": self.source_id,
                "source_name": self.display_name,
                "product": "低价引流票",
                "price": lead_price,
                "tax_included": False,
                "is_deal": False,
                "note": "引流特价，票面不含税，不指定航班，另收服务费",
            },
        ]
