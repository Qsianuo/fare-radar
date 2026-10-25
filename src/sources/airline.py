"""航司直连渠道（v1.5.0 退场，ISSUE-012 修复裁定）。

历史：v1.4 及之前本渠道由 airlines.route_flights 确定性生成"6 航司正班"
（航班号/时刻为哈希编造值），经评审人对照航旅纵横实测证实与真实航班计划
零重合——属编造数据，已全部退场（渲染层与比价行不再出现）。

现状：航旅纵横、同程旅行与各航司官网（如春秋 https://www.ch.com/）均无
面向第三方的公开 API，且春秋航空《法律声明》明文禁止程序自动获取官网数据
——按评审人约束，本渠道显式声明"暂无合规公开接口，未接入"，保留渠道概念、
不假装接入；真实直飞行情来自实测快照（snapshot_pack）。
"""

from .base import Source


class AirlineSource(Source):
    """航司直连：渠道概念保留，状态=未接入（不出任何数据行）。"""

    source_id = "airline"
    display_name = "航司直连"
    tagline = "各航司官网/官方小程序直飞正班（暂无合规公开接口，未接入）"
    entry_url = ""  # 多家航司无单一入口；前端对空链接不渲染
    entry_note = "各航司官网/官方小程序"
    status = "not_available"
    status_reason = "暂无合规公开接口，未接入"

    def availability(self, origin, destination, date):
        return False, self.status_reason

    def search(self, origin, destination, date):
        return []  # 未接入：不出任何比价行（真实直飞行情见实测快照渠道）
