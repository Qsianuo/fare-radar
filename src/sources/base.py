"""统一票源抽象。团购卡字段契约定于 docs/04 第三节：deal_id / route / 价格与含税口径 /
有效期 / 库存 / 下单入口 / 退改规则全文；缺失字段由上层以"未披露"兜底，适配器不做展示。
"""


class Source:
    """票源适配器接口：search 返回比价行；deals 返回团购卡（非团购源返回空）。"""

    source_id = ""
    display_name = ""

    def search(self, origin, destination, date):
        """返回比价行 dict 列表，字段：source_id / source_name / product /
        price / tax_included / is_deal / note。"""
        return []

    def deals(self, origin=None, destination=None):
        """返回团购卡 dict 列表（字段契约见模块 docstring）；
        origin / destination 为 None 时不按航线过滤。"""
        return []
