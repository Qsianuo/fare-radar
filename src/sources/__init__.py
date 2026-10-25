"""票源适配器注册表——可插拔的唯一改动点。

v1.5.0 票源家族（ISSUE-012 修复裁定）：实测快照（唯一航班比价行来源）/
航司直连 / OTA 比价 / 抖音团购（卡类）/ 抖音小程序·春秋航空（固定特价票）/
抖音直播间——后三者为"暂无合规公开接口，未接入"的渠道概念（status=
not_available，不出数据行）。全部走本注册表，CLI 与网页共用。
新增票源的步骤：新建模块实现 base.Source 子类 → 在 all_sources() 登记。
"""

from . import airlines, cities, fees, pricing, snapshot_pack
from .airline import AirlineSource
from .amadeus import AmadeusSource
from .base import Source
from .douyin_groupbuy import DouyinGroupbuySource
from .douyin_live import DouyinLiveSource
from .douyin_miniapp import DouyinMiniappSource
from .ota import OtaSource
from .rapidapi_sky import RapidapiFlightSource
from .snapshot_pack import SnapshotPackSource
from .variflight import VariflightSource

__all__ = ["Source", "airlines", "cities", "fees", "pricing", "snapshot_pack",
           "all_sources", "source_by_id", "source_catalog"]


def all_sources():
    """返回全部已登记票源的新实例列表（注册顺序即展示顺序）。

    真实接口适配器（均默认关闭/按配置激活）：AmadeusSource（自助门户已下线，
    仅存量企业 Key 可用）、RapidapiFlightSource（v1.7，免费档订阅）、
    VariflightSource（v1.11 骨架，国内航班与票价；schema 待校准，未校准前不出数据）。
    未配置 Key 时 status=not_available，不出数据行、如实声明。
    """
    return [SnapshotPackSource(), RapidapiFlightSource(), VariflightSource(), AmadeusSource(),
            AirlineSource(), OtaSource(),
            DouyinGroupbuySource(), DouyinMiniappSource(), DouyinLiveSource()]


def source_by_id(source_id):
    """按 source_id 查找票源，未注册返回 None。"""
    for source in all_sources():
        if source.source_id == source_id:
            return source
    return None


def source_catalog():
    """票源清单（id/显示名/说明/渠道入口/接入状态），供 /api/sources 与 CLI 提示。

    status="not_available" 的渠道为"暂无合规公开接口，未接入"——显式声明，
    不假装接入（ISSUE-012 成功标准 ④）。
    """
    return [{"id": s.source_id, "name": s.display_name, "description": s.tagline,
             "entry_url": s.entry_url, "entry_note": s.entry_note,
             "status": s.status, "reason": s.status_reason}
            for s in all_sources()]
