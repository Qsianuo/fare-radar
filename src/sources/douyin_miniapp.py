"""抖音小程序·春秋航空票源：航司自营小程序特价直飞（会员价特征）。

覆盖模型（ISSUE-010）：仅春秋基地航线（至少一端 ∈ 基地城市），且对覆盖航线
按日期哈希确定性模拟"当日无座"（约 1/7 的日期无报价，还原真实渠道
"暂无可销售座位"的体验）；确定性红线：无随机数。
"""

import datetime as dt

from . import pricing
from .base import Source

_TIER = 0.85  # 会员/小程序渠道价：常低于航司直连
_BASE_CITIES = ("SHA", "SHE", "SZX", "KIX")  # 春秋基地（演示口径）
_NO_SEAT_MODULUS = 7  # 约每 7 天一个"当日无座"日


class DouyinMiniappSource(Source):
    """抖音小程序·春秋航空：直飞机票票（非券），退改严、不含行李额。"""

    source_id = "douyin_miniapp"
    display_name = "抖音小程序·春秋航空"
    tagline = "航司自营抖音小程序特价，不含行李额、时段偏早晚、退改较严"
    entry_url = "https://www.iesdouyin.com/share/microapp/?token=QjdDMUQzOTlCQkU2NzZqc2I2YTdt&share_channel=copy"
    entry_note = "春秋航空抖音小程序"

    def availability(self, origin, destination, date):
        """基地航线之外不覆盖；覆盖航线按日期确定性模拟当日无座（约 1/7）。"""
        if not (origin in _BASE_CITIES or destination in _BASE_CITIES):
            return False, "仅覆盖春秋基地航线（上海/沈阳/深圳/大阪至少一端），该航线可能未执飞"
        if pricing.stable_hash(f"miniapp|{origin}{destination}|{date.isoformat()}") % _NO_SEAT_MODULUS == 0:
            return False, "当日无座（可能未执飞或当日售罄），可换日期或到春秋官方渠道查询"
        return True, None

    def search(self, origin, destination, date):
        available, _reason = self.availability(origin, destination, date)
        if not available:
            return []
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
