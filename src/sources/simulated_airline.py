"""模拟航司票源：固定航线 + 按星期浮动的确定性价格（无随机，同日期结果可复现、可测试）。
"""

import datetime as dt

from .base import Source

# (出发, 到达) -> [(航班号, 基础价, 是否含税, 备注)]
_ROUTES = {
    ("SHA", "PEK"): [
        ("CZ3908", 780, False, "波音787 直飞"),
        ("MU5101", 850, True, "空客A330 直飞"),
    ],
    ("SHA", "CTU"): [
        ("9C8905", 540, False, "春秋航空 直飞，无免费餐食"),
        ("MU5401", 690, True, "空客A321 直飞"),
    ],
    ("SHA", "KIX"): [
        ("9C8999", 899, False, "春秋航空 浦东-关西 直飞"),
        ("MU271", 1290, True, "空客A320 直飞"),
    ],
    ("PEK", "SHA"): [("MU5102", 760, False, "空客A330 直飞")],
    ("CTU", "SHA"): [("3U8961", 620, True, "空客A321 直飞")],
}

# 周一..周日的价格浮动（周五/周末偏高），模拟真实票价梯度
_WEEKDAY_SURCHARGE = [0, 40, 20, 10, 90, 150, 110]


class SimulatedAirlineSource(Source):
    """模拟航司直连票源，只提供比价行，无团购卡。"""

    source_id = "simulated_airline"
    display_name = "模拟航司源"

    def search(self, origin, destination, date):
        assert isinstance(date, dt.date)  # 防适配器误收字符串日期
        offers = []
        for flight_no, base_price, tax_included, note in _ROUTES.get((origin, destination), []):
            offers.append({
                "source_id": self.source_id,
                "source_name": self.display_name,
                "product": flight_no,
                "price": base_price + _WEEKDAY_SURCHARGE[date.weekday()],
                "tax_included": tax_included,
                "is_deal": False,
                "note": note,
            })
        return offers
