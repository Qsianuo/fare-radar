"""统一票源抽象（接口契约 + 覆盖模型，ISSUE-010；v1.4 扩展两类产品）。

v1.4 起票源分两类产品（PRD 第 7 节）：
- 航班票类（search 返回比价行）：行字段含航班 6 要素（起飞/到达时间、起降
  机场、航班号、承运人），费用明细与到手价由引擎统一附加；
- 卡类（cards_for 返回命中的卡、deals 返回卡池全量）：不卖具体航班，
  不出比价行。

覆盖模型（v1.3.1 延续）：票源用 availability() 声明"本次查询是否有销售"——
覆盖不含该航线、当日无座或"卡类渠道不卖航班"时返回 (False, 原因)，引擎
（fareradar.collect）统一执行并汇总为 unavailable_sources；search() 对不可用
查询返回空列表。演示数据必须讲道理：真实渠道不存在的航班不编价。

渠道状态（v1.5.0，ISSUE-012 修复裁定）：status="not_available" 的渠道是
"暂无合规公开接口，未接入"的渠道概念保留但不出任何数据行（不假装接入、
不编造）；比价行仅允许来自实测快照（snapshot_pack）与真实商品（特价票/卡）。
"""


class Source:
    """票源适配器接口：search 返回比价行；cards_for/deals 返回卡类产品。"""

    source_id = ""
    display_name = ""
    tagline = ""
    entry_url = ""    # 渠道入口链接；空串表示无单一入口（前端不渲染链接）
    entry_note = ""   # 入口说明（如"各航司官网/官方小程序"）
    status = "active"          # "active"=有真实数据行 / "not_available"=未接入
    status_reason = ""         # not_available 时的显式原因（供 /api/sources 与提示）

    def availability(self, origin, destination, date):
        """本次查询该票源是否有销售：(True, None) 可用，(False, 中文原因) 不可用。

        默认全渠道可用；有覆盖限制的票源必须覆写，且 search() 行为须与本
        判定一致（不可用时返回空列表）。"""
        return True, None

    def search(self, origin, destination, date):
        """返回比价行 dict 列表，字段：source_id / source_name / product /
        price / tax_included / is_deal / note（+航班 6 要素，v1.4）；
        entry_url/entry_note 由引擎统一附上。"""
        return []

    def cards_for(self, origin, destination, date):
        """本次搜索命中的卡（v1.4 card_hints 独立数据块，不计入比价行）；
        非卡类票源返回空列表。"""
        return []

    def special_fares(self, origin=None, destination=None):
        """固定特价票目录（v1.4 ①类产品，deals 视图用）；无此产品返回空。"""
        return []

    def deals(self, origin=None, destination=None):
        """返回卡池全量 dict 列表（deals 视图用）；
        origin / destination 为 None 时不按航线过滤。"""
        return []
