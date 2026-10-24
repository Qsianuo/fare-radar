"""抖音小程序·春秋航空票源：航司自营小程序特价直飞（会员价特征）。"""

from . import pricing
from .base import Source

_TIER = 0.85  # 会员/小程序渠道价：常低于航司直连


class DouyinMiniappSource(Source):
    """抖音小程序·春秋航空：直飞机票票（非券），退改严、不含行李额。"""

    source_id = "douyin_miniapp"
    display_name = "抖音小程序·春秋航空"
    tagline = "航司自营抖音小程序特价，不含行李额、时段偏早晚、退改较严"

    def search(self, origin, destination, date):
        price = pricing.quote(origin, destination, date,
                              key=f"miniapp|{origin}{destination}", tier=_TIER)
        if price is None:  # 城市码不在注册表或同城
            return []
        return [{
            "source_id": self.source_id,
            "source_name": self.display_name,
            "product": "春秋特价直飞",
            "price": price,
            "tax_included": True,
            "is_deal": False,
            "note": "需抖音 App 跳转小程序下单｜不含行李额，时段偏早晚，退改较严",
        }]
