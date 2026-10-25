"""抖音团购票源 v1.4：次卡/省钱卡（②类产品）——卖卡不卖具体航班。

真实团购商品是"卡类产品"（固定有效期+固定价+适用航线集合），不是一张
具体航班票（PRD v1.4 第 7.2 节）：因此本渠道不再出航班比价行（search 恒空、
coverage 恒报"卡类渠道"），命中的卡由引擎以独立 card_hints 区块展示，
deals 视图输出卡池全量。

测试资产迁移（迁移≠消灭）：
- DY-004 售罄场景 → DY-103 秋季单次卡（确定性售罄）延续；
- DY-005 缺退改规则场景 → DY-101/104/105/106 四张权益卡以"退改规则未披露"
  新形态保留（不许补规则"消灭"场景）；
- DY-003 上海-大阪国际券随国际城市下线一并移除。
卡面文案照抄真实商品（含"潮汕"等注册表外城市，仅注册表内城市参与命中）。
"""

from .base import Source

_SOURCE_NAME = "抖音团购"  # PRD v1.3 裁定行内名
_PURCHASE_ENTRY = "需在抖音 App 内下单，本工具不提供购买"
_UPDATED_AT = "2026-10-25"

# 卡面城市名 → 注册表码（"潮汕"不在 50 城注册表：卡面照抄、不参与命中判定）
_FACE_CITY_CODES = {
    "上海": "SHA", "兰州": "LHW", "重庆": "CKG", "厦门": "XMN", "哈尔滨": "HRB",
    "石家庄": "SJW", "大连": "DLC", "西安": "XIY",
}

_CARDS = (
    {
        "card_id": "DY-101", "card_type": "entitlement",
        "source_id": "douyin_groupbuy", "source_name": _SOURCE_NAME,
        "name": "省钱卡单人版365天", "price": 79,
        "validity_text": "365 天（自购买日起）", "valid_from": None, "valid_until": None,
        "scope": "每张机票同日同航班优惠 40 元起，支持国内(含港澳台)/国际机票",
        "hit_rule": "all_routes", "hit_cities": (),
        "stock": "充足", "purchase_entry": _PURCHASE_ENTRY, "updated_at": _UPDATED_AT,
        "note": "下单后立减，具体以下单页为准",
        # 无 refund_change_rules 键 → 上层必须显示"退改规则未披露"（DY-005 资产新形态）
    },
    {
        "card_id": "DY-102", "card_type": "single",
        "source_id": "douyin_groupbuy", "source_name": _SOURCE_NAME,
        "name": "199 秋冬机票单次卡·上海/兰州/重庆/潮汕/厦门/哈尔滨", "price": 199,
        "validity_text": "2026-12-01 ~ 2026-12-31",
        "valid_from": "2026-12-01", "valid_until": "2026-12-31",
        "scope": "可兑 1 张单程，适用约 188 条航线，过期退/随时退",
        "hit_rule": "city_ends", "hit_cities": ("SHA", "LHW", "CKG", "XMN", "HRB"),
        "stock": "充足", "purchase_entry": _PURCHASE_ENTRY, "updated_at": _UPDATED_AT,
        "refund_change_rules": "过期退/随时退（照抄卡面，细则以抖音购买页为准）",
        "note": "可兑航班的出发日期须落在有效期内",
    },
    {
        "card_id": "DY-103", "card_type": "single",
        "source_id": "douyin_groupbuy", "source_name": _SOURCE_NAME,
        "name": "199 秋季机票单次卡·石家庄/兰州/大连/重庆/潮汕/上海/西安", "price": 199,
        "validity_text": "2026-10-25 ~ 2026-11-30",
        "valid_from": "2026-10-25", "valid_until": "2026-11-30",
        "scope": "可兑 1 张单程，适用约 188 条航线，过期退/随时退",
        "hit_rule": "city_ends", "hit_cities": ("SJW", "LHW", "DLC", "CKG", "SHA", "XIY"),
        "stock": "售罄",  # DY-004 售罄测试资产的新形态（确定性售罄，不许消灭）
        "purchase_entry": _PURCHASE_ENTRY, "updated_at": _UPDATED_AT,
        "refund_change_rules": "过期退/随时退（照抄卡面，细则以抖音购买页为准）",
        "note": "可兑航班的出发日期须落在有效期内",
    },
    {
        "card_id": "DY-104", "card_type": "entitlement",
        "source_id": "douyin_groupbuy", "source_name": _SOURCE_NAME,
        "name": "秋冬 MAX 版尊卡", "price": 299,
        "validity_text": "即日起（2026-10-25）~ 2027-03-27",
        "valid_from": "2026-10-25", "valid_until": "2027-03-27",
        "scope": "升舱一次（即日起生效）",
        "hit_rule": "all_routes", "hit_cities": (),
        "stock": "充足", "purchase_entry": _PURCHASE_ENTRY, "updated_at": _UPDATED_AT,
        "note": "升舱权益卡，不含具体航班",
        # 无 refund_change_rules 键 → "退改规则未披露"
    },
    {
        "card_id": "DY-105", "card_type": "entitlement",
        "source_id": "douyin_groupbuy", "source_name": _SOURCE_NAME,
        "name": "秋冬 MAX PLUS 版", "price": 239,
        "validity_text": "即日起（2026-10-25）~ 2027-03-27",
        "valid_from": "2026-10-25", "valid_until": "2027-03-27",
        "scope": "升舱一次",
        "hit_rule": "all_routes", "hit_cities": (),
        "stock": "充足", "purchase_entry": _PURCHASE_ENTRY, "updated_at": _UPDATED_AT,
        "note": "升舱权益卡，不含具体航班",
        # 无 refund_change_rules 键 → "退改规则未披露"
    },
    {
        "card_id": "DY-106", "card_type": "entitlement",
        "source_id": "douyin_groupbuy", "source_name": _SOURCE_NAME,
        "name": "秋冬 MAX 国内版", "price": 149,
        "validity_text": "即日起（2026-10-25）~ 2027-03-27",
        "valid_from": "2026-10-25", "valid_until": "2027-03-27",
        "scope": "升舱一次",
        "hit_rule": "all_routes", "hit_cities": (),
        "stock": "充足", "purchase_entry": _PURCHASE_ENTRY, "updated_at": _UPDATED_AT,
        "note": "升舱权益卡，不含具体航班",
        # 无 refund_change_rules 键 → "退改规则未披露"
    },
)

# 卡面价固定（真实团购"固定价"），不叠加分钟级演示波动——卡不是比价报价行。
CARDS = [dict(card) for card in _CARDS]


def _hit(card, origin, destination, date):
    """PRD v1.4 第 7.2 节命中规则（确定性）：
    - 全航线命中：省钱卡与三张权益卡；
    - 单次卡：搜索航线至少一端 ∈ 卡面城市∩注册表，且出发日期落在有效期内
      （ISSUE-003 可出行窗口裁定的延续）。
    """
    if card["hit_rule"] == "all_routes":
        return True
    if not (origin in card["hit_cities"] or destination in card["hit_cities"]):
        return False
    if date is not None:
        if card["valid_from"] and date.isoformat() < card["valid_from"]:
            return False
        if card["valid_until"] and date.isoformat() > card["valid_until"]:
            return False
    return True


class DouyinGroupbuySource(Source):
    """抖音团购：次卡/省钱卡渠道——search 恒空（不卖航班票），卡走 cards_for/deals。"""

    source_id = "douyin_groupbuy"
    display_name = _SOURCE_NAME
    tagline = "次卡/省钱卡：固定有效期+固定价+适用航线集合，需抖音 App 下单"
    entry_url = "https://v.douyin.com/USSyX47SP7s/"
    entry_note = "春秋航空·次卡与省钱卡（抖音地点页）"

    def availability(self, origin, destination, date):
        """卡类渠道不卖具体航班：恒报"无航班销售"（覆盖模型语义保持可解释）。"""
        return False, "卡类渠道：卖次卡/省钱卡不卖具体航班（命中卡见结果页卡提示）"

    def search(self, origin, destination, date):
        return []  # 团购卖的是卡，不是航班票——不出比价行（PRD v1.4 第 7.2 节）

    def cards_for(self, origin, destination, date):
        """本次搜索命中的卡（独立数据块 card_hints，不计入比价行）。"""
        return [dict(card) for card in CARDS if _hit(card, origin, destination, date)]

    def deals(self, origin=None, destination=None):
        """卡池全量（deals 视图）；origin/destination 过滤仅对城市命中卡生效。"""
        if origin is None and destination is None:
            return [dict(card) for card in CARDS]
        return self.cards_for(origin, destination, None)
