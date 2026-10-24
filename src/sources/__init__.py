"""票源适配器注册表——可插拔的唯一改动点。

新增模拟票源的步骤（验收口径：search/deals/watch 逻辑零改动）：
1. 新建 simulated_xxx.py，实现 base.Source 子类；
2. 在 all_sources() 的返回列表中登记。
"""

from .base import Source
from .douyin_groupbuy import DouyinGroupbuySource
from .simulated_airline import SimulatedAirlineSource

__all__ = ["Source", "all_sources", "source_by_id"]


def all_sources():
    """返回全部已接入票源的新实例列表（注册顺序即展示顺序）。"""
    return [SimulatedAirlineSource(), DouyinGroupbuySource()]


def source_by_id(source_id):
    """按 source_id 查找票源，未注册返回 None。"""
    for source in all_sources():
        if source.source_id == source_id:
            return source
    return None
