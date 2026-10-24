"""模拟抖音团购票源：套餐价明显低于直连，但附带有效期/预约/退改限制。

字段契约见 docs/04 第三节与 base.py；DY-005 故意缺失退改规则，
用于演示与测试上层"未披露"兜底（诚实披露红线）。
"""

from .base import Source

_SOURCE_NAME = "抖音团购源(模拟)"
_PURCHASE_ENTRY = "需在抖音 App 内下单，本工具不提供购买"

_DEALS = [
    {"deal_id": "DY-001", "origin": "SHA", "destination": "CTU", "route": "SHA-CTU",
     "name": "春秋航空 上海-成都 3日有效券", "price": 499, "tax_included": True,
     "valid_from": "2026-11-01", "valid_until": "2026-11-30", "stock": "紧张",
     "purchase_entry": _PURCHASE_ENTRY, "updated_at": "2026-10-23",
     "refund_change_rules": "未使用可退，收 30% 手续费；过期作废；不可签转",
     "note": "限指定日期航班，需提前 3 天在抖音预约"},
    {"deal_id": "DY-002", "origin": "SHA", "destination": "PEK", "route": "SHA-PEK",
     "name": "南航联营 上海-北京 7日有效券", "price": 649, "tax_included": True,
     "valid_from": "2026-11-05", "valid_until": "2026-12-15", "stock": "充足",
     "purchase_entry": _PURCHASE_ENTRY, "updated_at": "2026-10-22",
     "refund_change_rules": "未使用可退，收 20% 手续费；使用后按航司规则退改",
     "note": "限工作日航班，节假日不可用"},
    {"deal_id": "DY-003", "origin": "SHA", "destination": "KIX", "route": "SHA-KIX",
     "name": "春秋航空 上海-大阪 5日有效券", "price": 1099, "tax_included": True,
     "valid_from": "2026-11-10", "valid_until": "2027-01-31", "stock": "充足",
     "purchase_entry": _PURCHASE_ENTRY, "updated_at": "2026-10-23",
     "refund_change_rules": "出票后不可退不可改；未出票可退，收 50% 手续费",
     "note": "不含行李额，需另行购买；往返均需提前 7 天预约"},
    {"deal_id": "DY-004", "origin": "PEK", "destination": "SHA", "route": "PEK-SHA",
     "name": "京沪快线 15日有效券", "price": 599, "tax_included": True,
     "valid_from": "2026-11-01", "valid_until": "2026-11-15", "stock": "售罄",
     "purchase_entry": _PURCHASE_ENTRY, "updated_at": "2026-10-21",
     "refund_change_rules": "本券不可退不可改，过期作废",
     "note": "限早班机，具体航班以下单页可约日期为准"},
    {"deal_id": "DY-005", "origin": "SHA", "destination": "CTU", "route": "SHA-CTU",
     "name": "川航联营 上海-成都 双城券", "price": 459, "tax_included": True,
     "valid_from": "2026-11-15", "valid_until": "2026-12-10", "stock": "充足",
     "purchase_entry": _PURCHASE_ENTRY, "updated_at": "2026-10-24",
     "note": "模拟源数据不完整：退改规则字段缺失"},
    # DY-005 无 refund_change_rules 键 → 上层必须显示"未披露"
]


class DouyinGroupbuySource(Source):
    """模拟抖音团购票源：deals 返回完整团购卡，search 将卡片降为比价行参与合并排序。"""

    source_id = "douyin_groupbuy"
    display_name = _SOURCE_NAME

    def search(self, origin, destination, date):
        return [self._to_offer(d) for d in self._filter(origin, destination)]

    def deals(self, origin=None, destination=None):
        return [dict(d) for d in self._filter(origin, destination)]

    def _filter(self, origin, destination):
        return [d for d in _DEALS
                if (origin is None or d["origin"] == origin)
                and (destination is None or d["destination"] == destination)]

    @staticmethod
    def _to_offer(deal):
        return {
            "source_id": "douyin_groupbuy",
            "source_name": _SOURCE_NAME,
            "product": deal["name"],
            "price": deal["price"],
            "tax_included": deal["tax_included"],
            "is_deal": True,
            "note": ("团购 ▶ 需抖音App下单｜"
                     f"有效期 {deal.get('valid_from', '未披露')}~{deal.get('valid_until', '未披露')}"),
        }
