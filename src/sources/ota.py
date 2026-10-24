"""OTA 比价票源：聚合代理报价，常出全场最低或次低，偶有不含税低价引流。"""

from . import pricing
from .base import Source

_LISTINGS = (
    # (产品名, 定价档位, 是否含税, 备注)
    ("特惠代理票", 0.9, True, "代理直飞，退改更严，可能收服务费"),
    ("低价引流票", 0.8, False, "引流特价，票面不含税，另收服务费"),
)


class OtaSource(Source):
    """OTA 比价：PRD v1.3 票源家族之一，多方向渠道。"""

    source_id = "ota"
    display_name = "OTA 比价"
    tagline = "多平台聚合比价，代理票退改更严、可能收服务费"

    def search(self, origin, destination, date):
        offers = []
        for label, tier, tax_included, note in _LISTINGS:
            price = pricing.quote(origin, destination, date,
                                  key=f"ota|{label}|{origin}{destination}", tier=tier)
            if price is None:  # 城市码不在注册表或同城
                return []
            offers.append({
                "source_id": self.source_id,
                "source_name": self.display_name,
                "product": label,
                "price": price,
                "tax_included": tax_included,
                "is_deal": False,
                "note": note,
            })
        return offers
