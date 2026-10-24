"""统一票源抽象（接口契约 + 覆盖模型，ISSUE-010）。

团购卡字段契约定于 docs/04 第三节与 PRD v1.3 第 5 节；缺失字段由上层以
"未披露"兜底，适配器不做展示。

覆盖模型（v1.3.1）：票源用 availability() 声明"本次查询是否有销售"——
覆盖不含该航线、或当日无座时返回 (False, 原因)，引擎（fareradar.collect）
统一执行并汇总为 unavailable_sources；search() 对不可用查询返回空列表。
演示数据必须讲道理：真实渠道不存在的航班不编价。
"""


class Source:
    """票源适配器接口：search 返回比价行；deals 返回团购卡（非团购源返回空）。"""

    source_id = ""
    display_name = ""
    tagline = ""
    entry_url = ""    # 渠道入口链接；空串表示无单一入口（前端不渲染链接）
    entry_note = ""   # 入口说明（如"各航司官网/官方小程序"）

    def availability(self, origin, destination, date):
        """本次查询该票源是否有销售：(True, None) 可用，(False, 中文原因) 不可用。

        默认全渠道可用；有覆盖限制的票源必须覆写，且 search() 行为须与本
        判定一致（不可用时返回空列表）。"""
        return True, None

    def search(self, origin, destination, date):
        """返回比价行 dict 列表，字段：source_id / source_name / product /
        price / tax_included / is_deal / note；entry_url/entry_note 由引擎统一附上。"""
        return []

    def deals(self, origin=None, destination=None):
        """返回团购卡 dict 列表（字段契约见模块 docstring）；
        origin / destination 为 None 时不按航线过滤。"""
        return []
