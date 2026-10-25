"""抖音直播间票源：限量闪购券——价低但库存少、易售罄、有效期短（3 日）。

闪购场次按天确定性轮换（PRD v1.3 第 7 节确定性硬约束），券有效期为
"自查询日起 3 天的可出行窗口"（ISSUE-003 口径：有效期＝可出行窗口）。
"""

import datetime as dt

from . import cities, pricing
from .base import Source

_TIER = 0.78  # 闪购深折扣：非售罄时常为全场最低
_FLASH_POOL = (  # v1.4 城市全集收缩：闪购场次全部改为国内航线（不再出国际航线报价）
    ("SHA", "CTU"), ("SHA", "PEK"), ("PEK", "SHA"), ("CAN", "SZX"),
    ("KMG", "HRB"), ("HRB", "SYX"), ("CTU", "CAN"), ("SZX", "KMG"),
)
_FLASH_ROUTES_PER_DAY = 4
_STOCK_BANDS = ((25, "售罄"), (60, "紧张"))  # 哈希余数 <25 售罄、<60 紧张、其余充足
_VALID_DAYS = 3  # 自查询日起的有效天数


class DouyinLiveSource(Source):
    """抖音直播间：is_deal 语义（券）——受售罄与有效期准入约束，并进入团购专区。"""

    source_id = "douyin_live"
    display_name = "抖音直播间"
    tagline = "直播间限量闪购：价低但库存少、易售罄、有效期短，需进直播间下单"
    entry_url = "https://www.douyin.com/"
    entry_note = "抖音 App 内进入对应直播间"

    def availability(self, origin, destination, date):
        """仅当日闪购场次航线有销售（场次按天确定性轮换）。"""
        if (origin, destination) in self.flash_routes():
            return True, None
        return False, "今日闪购场次未含该航线"

    def flash_routes(self):
        """今日闪购场次（确定性按天轮换），供 search 与 deals 共用。"""
        today = dt.date.today().isoformat()
        pool = sorted(_FLASH_POOL,
                      key=lambda route: pricing.stable_hash(f"live|schedule|{today}|{route[0]}{route[1]}"))
        return pool[:_FLASH_ROUTES_PER_DAY]

    def _flash(self, origin, destination, date):
        if (origin, destination) not in self.flash_routes():
            return None
        key = f"live|{origin}{destination}|{date.isoformat()}"
        price = pricing.quote(origin, destination, date, key=key, tier=_TIER)
        if price is None:
            return None
        remainder = pricing.stable_hash(key) % 100
        stock = "充足"
        for band, label in _STOCK_BANDS:
            if remainder < band:
                stock = label
                break
        valid_from = dt.date.today()
        return self._card(origin, destination, price, stock, key, valid_from,
                          valid_from + dt.timedelta(days=_VALID_DAYS - 1))

    @staticmethod
    def _card(origin, destination, price, stock, key, valid_from, valid_until):
        return {
            "deal_id": f"LIVE-{pricing.stable_hash(key) % 900 + 100}",
            "source_id": "douyin_live",
            "source_name": "抖音直播间",
            "origin": origin,
            "destination": destination,
            "route": f"{origin}-{destination}",
            "name": f"{cities.city_name(destination)} 直播闪购 {(valid_until - valid_from).days + 1}日券",
            "price": price,
            "tax_included": True,
            "valid_from": valid_from.isoformat(),
            "valid_until": valid_until.isoformat(),
            "stock": stock,
            "purchase_entry": "需进抖音直播间抢购，本工具不提供购买",
            "refund_change_rules": "闪购商品不可退不可改，过期作废",
            "note": "限时限量，售完即止；具体场次以直播间为准",
            "updated_at": valid_from.isoformat(),
        }

    def search(self, origin, destination, date):
        deal = self._flash(origin, destination, date)
        if deal is None:
            return []
        return [{
            "source_id": self.source_id,
            "source_name": self.display_name,
            "product": deal["name"],
            "price": deal["price"],
            "tax_included": deal["tax_included"],
            "is_deal": True,
            "stock": deal["stock"],
            "refund_disclosed": True,
            "valid_from": deal["valid_from"],
            "valid_until": deal["valid_until"],
            "note": f"直播闪购 ▶ 需进抖音直播间下单｜有效期 {deal['valid_from']}~{deal['valid_until']}",
        }]

    def deals(self, origin=None, destination=None):
        cards = []
        for origin_code, destination_code in self.flash_routes():
            if origin and origin_code != origin:
                continue
            if destination and destination_code != destination:
                continue
            deal = self._flash(origin_code, destination_code, dt.date.today())
            if deal:
                cards.append(deal)
        return cards
