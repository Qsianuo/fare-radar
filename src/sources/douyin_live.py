"""抖音直播间渠道（v1.5.0 退场，ISSUE-012 修复裁定）。

历史：v1.4 及之前本渠道按天轮换"闪购场次"，券价由引擎定价生成（模拟数据），
经评审人裁定与真实商品不符，已全部退场（搜索比价行与 deals 卡池均不再出现）。

现状：抖音直播间闪购无面向第三方的公开 API——按评审人约束，本渠道显式
声明"暂无合规公开接口，未接入"，保留渠道概念、不假装接入。
"""

from .base import Source


class DouyinLiveSource(Source):
    """抖音直播间：渠道概念保留，状态=未接入（不出任何数据行）。"""

    source_id = "douyin_live"
    display_name = "抖音直播间"
    tagline = "直播间限量闪购（暂无合规公开接口，未接入）"
    entry_url = "https://www.douyin.com/"
    entry_note = "抖音 App 内进入对应直播间"
    status = "not_available"
    status_reason = "暂无合规公开接口，未接入"

    def availability(self, origin, destination, date):
        return False, self.status_reason

    def search(self, origin, destination, date):
        return []  # 未接入：不出闪购券行（v1.4 引擎生成的场次券已退场）

    def deals(self, origin=None, destination=None):
        return []  # 闪购卡价由引擎生成（模拟数据），随渠道一并退场
