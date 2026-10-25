# -*- coding: utf-8 -*-
"""FareRadar v1.5 实测快照层单测（ISSUE-012 修复验证）。

覆盖（评审人《修复执行提示词》成功标准）：
- 快照 5 直飞逐字断言：航班号/时刻/裸价/含税价/航站楼/餐食/机型；
- 3 条中转组合逐字断言；
- 临近日期最低价条（10-30 ¥450 / 10-31 ¥450 / 11-01 ¥500 / 11-02 ¥490）；
- INC→CAN 2026-11-01 输出与航旅纵横快照逐字一致（价差 0 ≤ ¥50）；
- 比价结果 0 编造航班号（含 v1.4 编造值 CZ9782/CZ8645/CZ7836 反向断言）；
- 渲染层航班号去重拼接（"南航 CZ9782"，禁止"南航 CZ · CZ9782"）；
- 未收录航线（SHA→PEK）返回空 + 明确提示；
- sources_status 契约（未接入渠道显式声明"暂无合规公开接口，未接入"）。
运行：python -m unittest discover tests -v
"""

import datetime as dt
import io
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import fareradar as fr  # noqa: E402

# 测试一律离线：网络类真实接口票源视为未配置 Key（零真实请求）。
# 注：unittest discover 以顶层模块导入，tests/__init__.py 不执行，故逐模块注入。
import sources.amadeus as _am, sources.rapidapi_sky as _rp  # noqa: E402
_am.CONFIG_NAME = _rp.CONFIG_NAME = "_test_offline_absent.json"
import webapp  # noqa: E402
from sources import all_sources, snapshot_pack, source_catalog  # noqa: E402

SNAPSHOT_DATE = dt.date(2026, 11, 1)

# 评审人实测快照（航旅纵横，评审人 2026-10-25 实测截图；ISSUE-012 逐字证据）
DIRECT_VERBATIM = {
    "MF8753": {"carrier_name": "厦航", "depart": "07:40", "arrive": "10:50",
               "arr_airport": "广州白云国际机场T2", "aircraft": "波音738(窄)",
               "meal": "早餐", "price": 500, "total": 640},
    "GJ8258": {"carrier_name": "长龙航", "depart": "22:05", "arrive": "01:15",
               "arr_airport": "广州白云国际机场T2", "aircraft": "空客320(窄)",
               "meal": "无餐食", "price": 980, "total": 1120},
    "ZH8942": {"carrier_name": "深航", "depart": "13:50", "arrive": "16:55",
               "arr_airport": "广州白云国际机场T3", "aircraft": "空客32N(窄)",
               "meal": "点心餐", "price": 1000, "total": 1140},
    "AQ1275": {"carrier_name": "九元", "depart": "15:20", "arrive": "18:30",
               "arr_airport": "广州白云国际机场", "aircraft": "B738(窄)",
               "meal": "无餐食", "price": 1049, "total": 1189},
    "CZ3270": {"carrier_name": "南航", "depart": "16:45", "arrive": "20:00",
               "arr_airport": "广州白云国际机场T2", "aircraft": "空客320(窄)",
               "meal": "正餐", "price": 1170, "total": 1310},
}
TRANSFER_VERBATIM = {
    "CA4270|CA4349": {"carrier_name": "国航", "depart": "08:00", "arrive": "16:05",
                      "price": 550},
    "G54338|3U8119": {"carrier_name": "华夏·川航", "depart": "21:40", "arrive": "09:05",
                      "price": 710},
    "9C6202|9C8680": {"carrier_name": "春秋", "depart": "17:20", "arrive": "18:25",
                      "price": 780},
}
REAL_FLIGHT_NOS = set(DIRECT_VERBATIM) | set(TRANSFER_VERBATIM)
# 工具 v1.4 输出的编造航班号（评审人判定与真实航班计划零重合）——必须绝迹
FABRICATED_NOS = ("CZ9782", "CZ8645", "CZ7836")
NEARBY_BAR = (("2026-10-30", 450), ("2026-10-31", 450),
              ("2026-11-01", 500), ("2026-11-02", 490))


def _pinned(date=SNAPSHOT_DATE):
    """旁路 parse_date 的"不早于今天"校验（快照日期可能成为过去）。"""
    return mock.patch.object(fr, "parse_date", lambda raw: date)


def _snapshot_offers():
    offers, _failed, _unavailable = fr.collect(all_sources(), "INC", "CAN", SNAPSHOT_DATE)
    return offers


def _run_cli_search(origin="INC", destination="CAN", date="2026-11-01"):
    buf = io.StringIO()
    with _pinned(dt.date.fromisoformat(date)), redirect_stdout(buf):
        code = fr.main(["search", "--from", origin, "--to", destination, "--date", date])
    return code, buf.getvalue()


class SnapshotVerbatimTest(unittest.TestCase):
    """快照 5 直飞 + 3 中转逐字校验（航班号/时刻/裸价/含税/航站楼/餐食/机型）。"""

    def setUp(self):
        self.rows = {o["flight_no"]: o for o in _snapshot_offers()}

    def test_direct_flights_verbatim(self):
        self.assertEqual(len(self.rows), 8, "5 直飞 + 3 中转，不得多不得少")
        for flight_no, expected in DIRECT_VERBATIM.items():
            row = self.rows[flight_no]
            self.assertEqual(row["carrier_name"], expected["carrier_name"], flight_no)
            self.assertEqual(row["depart_time"], expected["depart"], flight_no)
            self.assertEqual(row["arrive_time"], expected["arrive"], flight_no)
            self.assertEqual(row["dep_airport"], "银川河东国际机场", flight_no)
            self.assertEqual(row["arr_airport"], expected["arr_airport"],
                             f"{flight_no} 航站楼逐字（T2/T3）")
            self.assertEqual(row["aircraft"], expected["aircraft"], flight_no)
            self.assertEqual(row["meal"], expected["meal"], flight_no)
            self.assertEqual(row["price"], expected["price"], f"{flight_no} 裸价逐字")
            self.assertEqual(row["total_estimate"], expected["total"],
                             f"{flight_no} 含税价逐字")
            self.assertEqual(row["source_id"], "snapshot_pack")
            self.assertIn("来源：航旅纵横（评审人 2026-10-25 实测截图）", row["note"])

    def test_transfer_flights_verbatim(self):
        for flight_no, expected in TRANSFER_VERBATIM.items():
            row = self.rows[flight_no]
            self.assertEqual(row["carrier_name"], expected["carrier_name"], flight_no)
            self.assertEqual(row["depart_time"], expected["depart"], flight_no)
            self.assertEqual(row["arrive_time"], expected["arrive"], flight_no)
            self.assertEqual(row["price"], expected["price"], flight_no)
            self.assertEqual(row["total_estimate"], expected["price"],
                             "中转组合价 = 到手价（含税口径）")

    def test_transfer_via_cities_verbatim(self):
        self.assertIn("转重庆", self.rows["CA4270|CA4349"]["product"], "快照：转重庆 4h")
        self.assertIn("转石家庄", self.rows["9C6202|9C8680"]["product"], "快照：转石家庄")

    def test_cross_day_and_duration_arithmetic(self):
        self.assertTrue(self.rows["GJ8258"]["cross_day"], "22:05→01:15(+1) 跨日")
        self.assertTrue(self.rows["G54338|3U8119"]["cross_day"])
        self.assertTrue(self.rows["9C6202|9C8680"]["cross_day"])
        self.assertFalse(self.rows["MF8753"]["cross_day"])
        self.assertEqual(self.rows["MF8753"]["duration_text"], "3小时10分")
        self.assertEqual(self.rows["GJ8258"]["duration_text"], "3小时10分")
        self.assertEqual(self.rows["CA4270|CA4349"]["duration_text"], "8小时05分")

    def test_snapshot_tags_and_stock_verbatim(self):
        self.assertIn("当日低价", self.rows["MF8753"]["note"], "快照【当日低价】标记")
        self.assertIn("另有南航共享航班售卖", self.rows["GJ8258"]["note"])
        for flight_no in ("GJ8258", "ZH8942", "AQ1275"):
            self.assertIn("少量", self.rows[flight_no]["note"], "快照余票状态")

    def test_snapshot_fee_disclosure_no_fabricated_split(self):
        # 含税差额 ¥140 = 两个实测值相减；不拆分机建/燃油（拆分即编造）
        mf = self.rows["MF8753"]
        self.assertEqual(mf["total_estimate"] - mf["price"], 140)
        self.assertIsNone(mf["fee_breakdown"]["airport_fee"])
        self.assertIsNone(mf["fee_breakdown"]["fuel_fee"])
        self.assertEqual(mf["fee_breakdown"]["included_desc"],
                         "实测快照：含税 ¥640（含机建+燃油）")


class ZeroFabricatedFlightNoTest(unittest.TestCase):
    """成功标准 ②：比价结果 0 个编造航班号。"""

    def test_all_flight_nos_from_snapshot_whitelist(self):
        for offer in _snapshot_offers():
            self.assertIn(offer["flight_no"], REAL_FLIGHT_NOS,
                          f"{offer['flight_no']} 不在实测快照白名单（编造嫌疑）")
            self.assertNotIn("·", offer["flight_no"])

    def test_special_fares_carry_no_flight_no(self):
        offers, _f, _u = fr.collect(all_sources(), "SHA", "JJN", dt.date(2026, 10, 27))
        special = [o for o in offers if o.get("is_special_fare")]
        self.assertTrue(special)
        self.assertIsNone(special[0]["flight_no"], "特价票卡面未展示航班号——禁止编造填充")
        self.assertIsNone(special[0].get("depart_time"), "特价票卡面未展示时刻——禁止编造填充")

    def test_fabricated_numbers_absent_from_cli_output(self):
        _code, out = _run_cli_search()
        for number in FABRICATED_NOS:
            self.assertNotIn(number, out, f"编造航班号 {number} 不得再出现在任何输出")
        for old_time in ("10:10", "21:00→"):
            self.assertNotIn(old_time, out, "v1.4 编造时刻不得再出现")

    def test_fabricated_numbers_absent_from_web_payload(self):
        _, payload = webapp._api_search({"from": "INC", "to": "CAN", "date": "2026-11-01"})
        blob = repr(payload["offers"])
        for number in FABRICATED_NOS:
            self.assertNotIn(number, blob)


class SearchVerbatimTest(unittest.TestCase):
    """成功标准 ③：INC→CAN 2026-11-01 输出与航旅纵横快照逐字一致（价差 0 ≤ ¥50）。"""

    def test_cli_output_contains_every_snapshot_row(self):
        _code, out = _run_cli_search()
        for flight_no, expected in {**DIRECT_VERBATIM, **TRANSFER_VERBATIM}.items():
            self.assertIn(f"{expected['carrier_name']} {flight_no}", out,
                          "航班号以'承运人中文名 + 航班号'统一格式贯穿渲染")
        for total in (640, 1120, 1140, 1189, 1310, 550, 710, 780):
            self.assertIn(f"¥{total}", out, "含税价逐字出现")
        lines = out.splitlines()
        marker = next(i for i, ln in enumerate(lines) if "◀ 最低价" in ln)
        self.assertTrue(lines[marker - 1].startswith("¥550"),
                        "最低价标记落在中转 ¥550 行（与基准'中转 ¥550 起'一致）")

    def test_no_price_gap_vs_benchmark(self):
        # 基准（航旅纵横）：直飞含税低至 ¥640（裸价 ¥500）、中转 ¥550 起
        offers = _snapshot_offers()
        direct = [o for o in offers if not o["product"].startswith("中转")]
        transfer = [o for o in offers if o["product"].startswith("中转")]
        self.assertEqual(min(o["total_estimate"] for o in direct), 640,
                         "直飞含税最低 = 基准 ¥640，价差 0 ≤ ¥50")
        self.assertEqual(min(o["total_estimate"] for o in transfer), 550,
                         "中转最低 = 基准 ¥550，价差 0 ≤ ¥50")
        self.assertEqual(min(o["price"] for o in direct), 500, "直飞裸价最低 = 基准 ¥500")

    def test_nearby_bar_cli_line(self):
        _code, out = _run_cli_search()
        self.assertIn("临近日期最低价", out)
        self.assertIn("10-30 ¥450｜10-31 ¥450｜11-01 ¥500｜11-02 ¥490", out)


class NearbyBarTest(unittest.TestCase):
    """临近日期最低价条（快照真实数据，替代原"近 24 小时演示走势"）。"""

    def test_pack_data_verbatim(self):
        bar = snapshot_pack.nearby_lowest("INC", "CAN")
        self.assertEqual([(p["date"], p["price"]) for p in bar], list(NEARBY_BAR))
        self.assertEqual(snapshot_pack.nearby_lowest("SHA", "PEK"), [],
                         "未收录航线无临近数据（不编造）")
        self.assertEqual(snapshot_pack.SNAPSHOT_DATE, "2026-11-01")
        self.assertEqual(snapshot_pack.ROUTE, ("INC", "CAN"))

    def test_web_api_nearby(self):
        with _pinned():
            status, payload = webapp._api_nearby({"from": "INC", "to": "CAN",
                                                  "date": "2026-11-01"})
        self.assertEqual(status, 200)
        self.assertTrue(payload["covered"])
        self.assertEqual([(p["date"], p["price"]) for p in payload["bar"]], list(NEARBY_BAR))
        self.assertEqual(payload["price_basis"], "裸价（不含机建+燃油）")
        self.assertIn("航旅纵横", payload["notice"])

    def test_trend_endpoint_retired(self):
        self.assertNotIn("/api/trend", webapp.GET_APIS,
                         "演示走势接口随演示波动一并退役")
        self.assertIn("/api/nearby", webapp.GET_APIS)


class RenderDedupTest(unittest.TestCase):
    """渲染层航班号去重拼接：统一"南航 CZ9782"，禁止"南航 CZ · CZ9782"。"""

    def test_flight_display_no_double_prefix(self):
        row = {"flight_no": "CZ9782", "carrier_code": "CZ", "carrier_name": "南航"}
        self.assertEqual(fr.flight_display(row), "南航 CZ9782")
        self.assertNotIn(" · ", fr.flight_display(row))
        self.assertEqual(fr.flight_display({"flight_no": "MF8753", "carrier_name": None}),
                         "MF8753")
        self.assertEqual(fr.flight_display({"flight_no": None, "carrier_name": "春秋"}), "")
        self.assertEqual(fr.flight_display({"flight_no": "CA4270|CA4349",
                                            "carrier_name": "国航"}),
                         "国航 CA4270|CA4349")

    def test_html_dup_pattern_removed(self):
        html = (ROOT / "src" / "webapp.html").read_text(encoding="utf-8")
        self.assertNotIn('carrier + " · " + o.flight_no', html,
                         "旧'二字码重复拼接'模式必须移除")
        self.assertNotIn("tag-demo", html, "'演示波动'标签必须移除")
        self.assertNotIn("演示波动", html)
        self.assertIn('carrier + " " + o.flight_no', html, "新拼接规则：中文名 + 航班号")
        self.assertIn("v1.5.0", html, "页脚版本号升级")
        self.assertNotIn("/api/trend", html, "演示走势调用移除")

    def test_cli_rows_show_flight_no_first_class(self):
        _code, out = _run_cli_search()
        self.assertIn("厦航 MF8753", out)
        self.assertIn("南航 CZ3270", out)
        self.assertNotIn(" · ", out, "CLI 输出不得出现'二字码 · 航班号'重复拼接")


class UncoveredRouteDisclosureTest(unittest.TestCase):
    """未收录航线返回空 + 明确提示（评审人验证用例：SHA→PEK）。"""

    def test_sha_pek_empty_with_disclosure(self):
        offers, failed, unavailable = fr.collect(all_sources(), "SHA", "PEK",
                                                 dt.date(2026, 11, 15))
        self.assertEqual((offers, failed), ([], []))
        by_id = {u["source_id"]: u for u in unavailable}
        self.assertIn("snapshot_pack", by_id)
        self.assertIn("暂无实测快照", by_id["snapshot_pack"]["reason"])

    def test_web_empty_hint_lists_coverage(self):
        with _pinned(dt.date(2026, 11, 15)):
            status, payload = webapp._api_search({"from": "SHA", "to": "PEK",
                                                  "date": "2026-11-15"})
        self.assertEqual(status, 200)
        self.assertEqual(payload["offers"], [])
        self.assertIn("该航线暂无真实数据快照", payload["empty_hint"])
        self.assertIn("银川(INC) → 广州(CAN) 2026-11-01", payload["empty_hint"])
        self.assertIn("航旅纵横", payload["empty_hint"])


class SourcesStatusContractTest(unittest.TestCase):
    """成功标准 ④：未接入源显式声明"暂无合规公开接口，未接入"。"""

    def test_catalog_status_contract(self):
        catalog = {s["id"]: s for s in source_catalog()}
        for sid in ("airline", "ota", "douyin_live"):
            self.assertEqual(catalog[sid]["status"], "not_available")
            self.assertEqual(catalog[sid]["reason"], "暂无合规公开接口，未接入")
        for sid in ("snapshot_pack", "douyin_miniapp", "douyin_groupbuy"):
            self.assertEqual(catalog[sid]["status"], "active")

    def test_retired_channels_produce_no_rows_anywhere(self):
        for sid in ("airline", "ota", "douyin_live"):
            source = next(s for s in all_sources() if s.source_id == sid)
            self.assertEqual(source.search("INC", "CAN", SNAPSHOT_DATE), [])
            self.assertEqual(source.search("SHA", "PEK", dt.date(2026, 11, 15)), [])
            self.assertEqual(source.deals(), [])

    def test_snapshot_channel_is_only_flight_row_source(self):
        offers = _snapshot_offers()
        self.assertTrue(offers)
        self.assertEqual({o["source_id"] for o in offers}, {"snapshot_pack"},
                         "航班比价行唯一来源 = 实测快照（active 行仅快照+特价票）")


if __name__ == "__main__":
    unittest.main()
