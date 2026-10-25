"""实测快照数据层（ISSUE-012 修复裁定）：v1.5.0 起唯一进入比价的航班行来源。

背景：引擎生成的"航司正班 / OTA 代理票 / 低价引流票 / 直播闪购"行全部是
模拟数据——航班号与时刻为哈希编造值，与真实航班计划零重合（评审人对照
航旅纵横实测证实），v1.5.0 全部退场。而航旅纵横、同程旅行与各航司官网均
无面向第三方的公开 API，未经授权抓取违反其条款——本项目不假装接入。
合规可用的真实航班数据只有一类：人工核验的实测快照。

数据铁律（评审人《修复执行提示词》约束）：
- 只收录评审人实测证据中出现过的航班，禁止虚构快照之外的任何航班号；
- 裸价/含税价逐字采用快照，含税差额（¥140）由两个实测值相减得到，不拆分
  机建/燃油明细（拆分即编造）；快照是静态数据，无演示波动；
- 每行标注来源与抓取时刻；结构支持人工扩充：新增航线只需扩展
  _DIRECT / _TRANSFERS / _NEARBY_LOWEST 三组常量。
"""

from .base import Source

SOURCE_ID = "snapshot_pack"
SOURCE_NAME = "实测快照"
DATA_SOURCE = "航旅纵横（评审人 2026-10-25 实测截图）"
SNAPSHOT_DATE = "2026-11-01"
ROUTE = ("INC", "CAN")  # 银川河东 → 广州白云（本次唯一收录航线）

# 直飞航班（逐字采用 ISSUE-012 实测证据）：flight_no/时刻/航站楼/机型/餐食/
# 裸价 price/含税 total/余票/标记 全部来自快照原文。
# dep_airport 按城市注册表全称 + 快照标注航站楼（T2/T3；快照未标注航站楼
# 的航班不加后缀）。
_DIRECT = (
    {
        "flight_no": "MF8753", "carrier_code": "MF", "carrier_name": "厦航",
        "depart_time": "07:40", "arrive_time": "10:50", "cross_day": False,
        "arr_terminal": "T2", "aircraft": "波音738(窄)", "meal": "早餐",
        "price": 500, "total": 640, "stock": None, "tags": ("当日低价",),
    },
    {
        "flight_no": "GJ8258", "carrier_code": "GJ", "carrier_name": "长龙航",
        "depart_time": "22:05", "arrive_time": "01:15", "cross_day": True,
        "arr_terminal": "T2", "aircraft": "空客320(窄)", "meal": "无餐食",
        "price": 980, "total": 1120, "stock": "少量",
        "tags": ("另有南航共享航班售卖",),
    },
    {
        "flight_no": "ZH8942", "carrier_code": "ZH", "carrier_name": "深航",
        "depart_time": "13:50", "arrive_time": "16:55", "cross_day": False,
        "arr_terminal": "T3", "aircraft": "空客32N(窄)", "meal": "点心餐",
        "price": 1000, "total": 1140, "stock": "少量", "tags": (),
    },
    {
        "flight_no": "AQ1275", "carrier_code": "AQ", "carrier_name": "九元",
        "depart_time": "15:20", "arrive_time": "18:30", "cross_day": False,
        "arr_terminal": None, "aircraft": "B738(窄)", "meal": "无餐食",
        "price": 1049, "total": 1189, "stock": "少量", "tags": (),
    },
    {
        "flight_no": "CZ3270", "carrier_code": "CZ", "carrier_name": "南航",
        "depart_time": "16:45", "arrive_time": "20:00", "cross_day": False,
        "arr_terminal": "T2", "aircraft": "空客320(窄)", "meal": "正餐",
        "price": 1170, "total": 1310, "stock": None, "tags": (),
    },
)

# 中转组合（逐字采用 ISSUE-012 实测证据）：flight_no=两段航班号（"|"" 连接），
# 价格为快照展示的中转组合价（含税口径），via 为快照标注的中转城市与时长；
# 快照未标注中转城市的组合（G54338|3U8119）不留空字段编造。
_TRANSFERS = (
    {
        "flight_no": "CA4270|CA4349", "carrier_name": "国航",
        "depart_time": "08:00", "arrive_time": "16:05", "cross_day": False,
        "via": "转重庆 4h", "price": 550,
    },
    {
        "flight_no": "G54338|3U8119", "carrier_name": "华夏·川航",
        "depart_time": "21:40", "arrive_time": "09:05", "cross_day": True,
        "via": None, "price": 710,
    },
    {
        "flight_no": "9C6202|9C8680", "carrier_name": "春秋",
        "depart_time": "17:20", "arrive_time": "18:25", "cross_day": True,
        "via": "转石家庄", "price": 780,
    },
)

# 临近日期最低价条（裸价口径，逐字采用快照；替代原"近 24 小时演示走势"）。
_NEARBY_LOWEST = (
    ("2026-10-30", 450),
    ("2026-10-31", 450),
    ("2026-11-01", 500),
    ("2026-11-02", 490),
)

_DEP_AIRPORT = "银川河东国际机场"      # 城市注册表 INC 机场全称（与快照"河东"一致）
_ARR_AIRPORT = "广州白云国际机场"      # 城市注册表 CAN 机场全称（快照"白云"）


def is_covered(origin, destination, date):
    """该航线+日期是否在快照收录范围内（当前仅 INC→CAN 2026-11-01）。"""
    if (origin, destination) != ROUTE:
        return False
    return getattr(date, "isoformat", lambda: str(date))() == SNAPSHOT_DATE


def coverage_text():
    """当前收录范围（未收录航线的显式提示文案）。"""
    return f"银川(INC) → 广州(CAN) {SNAPSHOT_DATE}｜来源：{DATA_SOURCE}"


def nearby_lowest(origin, destination):
    """临近日期最低价条（裸价口径）；未收录航线返回空列表。"""
    if (origin, destination) != ROUTE:
        return []
    return [{"date": d, "label": d[5:], "price": p} for d, p in _NEARBY_LOWEST]


def _duration_text(dep, arr, cross_day):
    """航程时长文本（由快照时刻做纯算术推导，非编造）。"""
    dep_min = int(dep[:2]) * 60 + int(dep[3:])
    arr_min = int(arr[:2]) * 60 + int(arr[3:]) + (1440 if cross_day else 0)
    span = arr_min - dep_min
    return f"{span // 60}小时{span % 60:02d}分"


def _arr_airport(terminal):
    return f"{_ARR_AIRPORT}{terminal}" if terminal else _ARR_AIRPORT


def _snapshot_fee(price, total, tax_included):
    """快照行的费用说明：只给实测两个口径值，不拆分机建/燃油（拆分即编造）。"""
    if tax_included:
        desc = f"实测快照：¥{total}（中转组合价，含税口径）"
    else:
        desc = f"实测快照：含税 ¥{total}（含机建+燃油）"
    return {"airport_fee": None, "fuel_fee": None,
            "tax_included": tax_included, "included_desc": desc}


def _source_note(parts):
    return "｜".join([*parts, f"来源：{DATA_SOURCE}"])


def offers():
    """快照全部比价行（直飞 + 中转）；字段与 fareradar 比价行契约对齐。

    total_estimate 直接采用快照实测含税价（引擎费用模型不覆盖快照行，
    collect() 见 total_estimate 已存在则跳过附加）；中转行为含税口径
    （到手价 = 快照组合价）。
    """
    rows = []
    for flight in _DIRECT:
        total, price = flight["total"], flight["price"]
        parts = [*flight["tags"]]
        if flight["stock"]:
            parts.append(flight["stock"])
        rows.append({
            "source_id": SOURCE_ID,
            "source_name": SOURCE_NAME,
            "product": f"直飞｜{flight['aircraft']}｜{flight['meal']}",
            "price": price,
            "tax_included": False,
            "total_estimate": total,
            "fee_breakdown": _snapshot_fee(price, total, tax_included=False),
            "is_deal": False,
            "is_snapshot": True,
            "flight_no": flight["flight_no"],
            "carrier_code": flight["carrier_code"],
            "carrier_name": flight["carrier_name"],
            "depart_time": flight["depart_time"],
            "arrive_time": flight["arrive_time"],
            "cross_day": flight["cross_day"],
            "duration_text": _duration_text(flight["depart_time"],
                                            flight["arrive_time"], flight["cross_day"]),
            "dep_airport": _DEP_AIRPORT,
            "arr_airport": _arr_airport(flight["arr_terminal"]),
            "aircraft": flight["aircraft"],
            "meal": flight["meal"],
            "note": _source_note(parts),
        })
    for flight in _TRANSFERS:
        price = flight["price"]
        rows.append({
            "source_id": SOURCE_ID,
            "source_name": SOURCE_NAME,
            "product": f"中转｜{flight['via']}" if flight["via"] else "中转",
            "price": price,
            "tax_included": True,
            "total_estimate": price,
            "fee_breakdown": _snapshot_fee(price, price, tax_included=True),
            "is_deal": False,
            "is_snapshot": True,
            "flight_no": flight["flight_no"],
            "carrier_code": None,
            "carrier_name": flight["carrier_name"],
            "depart_time": flight["depart_time"],
            "arrive_time": flight["arrive_time"],
            "cross_day": flight["cross_day"],
            "duration_text": _duration_text(flight["depart_time"],
                                            flight["arrive_time"], flight["cross_day"]),
            "dep_airport": _DEP_AIRPORT,
            "arr_airport": _ARR_AIRPORT,
            "note": _source_note(["中转组合"] if not flight["via"] else []),
        })
    return rows


class SnapshotPackSource(Source):
    """实测快照票源：覆盖范围内返回实测行，范围外显式报"暂无快照"（不编价）。"""

    source_id = SOURCE_ID
    display_name = SOURCE_NAME
    tagline = "人工核验的实测快照（航旅纵横，评审人 2026-10-25 实测），逐行标注来源"
    entry_url = ""  # 快照无渠道入口链接；前端对空链接不渲染
    entry_note = f"数据来源：{DATA_SOURCE}"

    def availability(self, origin, destination, date):
        if is_covered(origin, destination, date):
            return True, None
        return False, (f"该航线与日期暂无实测快照（当前收录范围：{coverage_text()}）")

    def search(self, origin, destination, date):
        if not is_covered(origin, destination, date):
            return []
        return [dict(row) for row in offers()]
