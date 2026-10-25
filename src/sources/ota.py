"""OTA 比价渠道（v1.5.0 退场，ISSUE-012 修复裁定）。

历史：v1.4 及之前本渠道产出两类引擎行——"特惠代理票"（复用编造正班表 +
引擎定价）与"低价引流票"（无航班号的引流行），均为模拟数据，经评审人对照
航旅纵横实测证实与真实行情不符，已全部退场。

现状：携程等同程/OTA 平台无面向第三方的公开报价 API，未经授权抓取违反其
条款——按评审人约束，本渠道显式声明"暂无合规公开接口，未接入"，保留渠道
概念、不假装接入；真实行情来自实测快照（snapshot_pack）。
"""

from .base import Source


class OtaSource(Source):
    """OTA 比价：渠道概念保留，状态=未接入（不出任何数据行）。"""

    source_id = "ota"
    display_name = "OTA 比价"
    tagline = "多平台聚合比价（暂无合规公开接口，未接入）"
    entry_url = "https://www.ctrip.com/"
    entry_note = "示例入口：携程官网（OTA 平台之一）"
    status = "not_available"
    status_reason = "暂无合规公开接口，未接入"

    def availability(self, origin, destination, date):
        return False, self.status_reason

    def search(self, origin, destination, date):
        return []  # 未接入：不出任何比价行（真实行情见实测快照渠道）
