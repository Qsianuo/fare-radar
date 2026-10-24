"""航司直连票源：注册表内任意航线的直飞正班，由定价引擎出价。

每航线两个舱位：含税主舱 + 不含税低成本舱（保留 ISSUE-001"税费另计"披露资产）。
"""

from . import pricing
from .base import Source

_FLEET = (
    # (航司前缀, 定价档位, 是否含税, 备注)
    ("MU", 1.0, True, "直飞正班"),
    ("9C", 0.86, False, "低成本直飞，无免费餐食"),
)


class AirlineSource(Source):
    """航司直连：display_name 为 PRD v1.3 裁定的用户语言，"演示"由输出层声明承担。"""

    source_id = "airline"
    display_name = "航司直连"
    tagline = "航司官网/小程序直飞正班，退改按航司规则"

    def search(self, origin, destination, date):
        offers = []
        for carrier, tier, tax_included, note in _FLEET:
            key = f"airline|{carrier}|{origin}{destination}"
            price = pricing.quote(origin, destination, date, key=key, tier=tier)
            if price is None:  # 城市码不在注册表或同城
                return []
            flight_no = f"{carrier}{1000 + pricing.stable_hash(key) % 9000}"
            offers.append({
                "source_id": self.source_id,
                "source_name": self.display_name,
                "product": flight_no,
                "price": price,
                "tax_included": tax_included,
                "is_deal": False,
                "note": note,
            })
        return offers
