"""税费引擎（PRD v1.4 第 8 节）：机建 + 燃油确定化，到手价统一可比。

- 机建费 50 元/人（国内航线统一）；
- 燃油附加费按航线大圆距离分档：≤800km 20 元/人，>800km 40 元/人；
- 到手价 total_estimate = 票面价 + 未含费用（"已含"报价的到手价 = 票面价）。
  自 v1.4 起"到手价"统一定义为票面价+机建+燃油，取代此前"含税价"口径
  （对 ISSUE-001 裁定的正式修订）；排序/最低价标记/提醒判定全部切换本口径。

本模块为纯函数；fee_breakdown 由引擎（fareradar.collect）统一附加到每条
报价行——CLI 与网页同函数（规则唯一来源）。
"""

AIRPORT_FEE = 50          # 机建费：元/人
FUEL_SHORT_HAUL = 20      # 燃油费：≤800km
FUEL_LONG_HAUL = 40       # 燃油费：>800km
FUEL_BOUNDARY_KM = 800    # 分档边界（含）

INCLUDED_DESC_TAXED = "已含机建+燃油"
SPECIAL_FARE_DESC = "不含机建燃油费"   # 特价票固定标签（照抄真实截图口径）


def fuel_fee(distance_km):
    """燃油附加费：距离 ≤800km 20 元，>800km 40 元；距离未知返回 None。"""
    if distance_km is None:
        return None
    return FUEL_SHORT_HAUL if distance_km <= FUEL_BOUNDARY_KM else FUEL_LONG_HAUL


def included_desc(tax_included, fee, special=False):
    """展示文案二选一（PRD 第 8 节）：已含 / 另加明细；特价票固定标签。"""
    if tax_included:
        return INCLUDED_DESC_TAXED
    if special:
        return SPECIAL_FARE_DESC
    return f"另加机建 ¥{AIRPORT_FEE} + 燃油 ¥{fee}"


def fee_breakdown(price, tax_included, distance_km, desc_override=None):
    """一条报价的费用明细 dict（tax_included=票面价是否已含机建+燃油）。"""
    fee = fuel_fee(distance_km)
    return {
        "airport_fee": AIRPORT_FEE,
        "fuel_fee": fee,
        "tax_included": bool(tax_included),
        "included_desc": desc_override or included_desc(tax_included, fee),
    }


def total_estimate(price, tax_included, distance_km):
    """到手价 = 票面价 + 未含费用；"已含"报价到手价 = 票面价。"""
    if tax_included:
        return price
    return price + AIRPORT_FEE + fuel_fee(distance_km)
