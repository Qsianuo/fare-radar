"""票源适配器注册表——可插拔的唯一改动点。

v1.3 票源家族（PRD 第 5 节）：航司直连 / OTA 比价 / 抖音团购 /
抖音小程序·春秋航空 / 抖音直播间，全部走本注册表，CLI 与网页共用。
新增票源的步骤：新建模块实现 base.Source 子类 → 在 all_sources() 登记。
"""

from . import cities, pricing
from .airline import AirlineSource
from .base import Source
from .douyin_groupbuy import DouyinGroupbuySource
from .douyin_live import DouyinLiveSource
from .douyin_miniapp import DouyinMiniappSource
from .ota import OtaSource

__all__ = ["Source", "cities", "pricing", "all_sources", "source_by_id", "source_catalog"]


def all_sources():
    """返回全部已接入票源的新实例列表（注册顺序即展示顺序）。"""
    return [AirlineSource(), OtaSource(), DouyinGroupbuySource(),
            DouyinMiniappSource(), DouyinLiveSource()]


def source_by_id(source_id):
    """按 source_id 查找票源，未注册返回 None。"""
    for source in all_sources():
        if source.source_id == source_id:
            return source
    return None


def source_catalog():
    """票源清单（id/显示名/一句话说明），供 /api/sources 与 CLI 提示使用。"""
    return [{"id": s.source_id, "name": s.display_name, "description": s.tagline}
            for s in all_sources()]
