# -*- coding: utf-8 -*-
"""FareRadar 核心逻辑单测（v1.4）。

覆盖：多源合并与到手价排序、票源失败降级、日期/三字码校验、缺失字段兜底、
提醒阈值命中、alerts 读写与损坏保护、参数错误退出码；v1.4 增量：50 城注册表、
税费与到手价口径（修订 ISSUE-001）、跨渠道去重、卡提示、已下线城市兼容、
分钟级演示波动确定性。
运行：python -m unittest discover tests -v
"""

import argparse
import datetime as dt
import io
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import fareradar as fr  # noqa: E402
import webapp  # noqa: E402
from sources import all_sources, cities, fees, pricing, source_catalog  # noqa: E402
from sources.douyin_groupbuy import DouyinGroupbuySource  # noqa: E402
from sources.douyin_live import DouyinLiveSource  # noqa: E402

FUTURE = (dt.date.today() + dt.timedelta(days=30)).isoformat()
PINNED_MINUTE = 29_500_000  # 涉及具体价格的断言先把查询时刻固定到该分钟桶
NEW_LINK = "https://v.douyin.com/USSyX47SP7s/"


class _BrokenSource:
    """始终抛异常的故障源，用于验证单源失败不拖垮整体（PRD AC2）。"""

    source_id = "broken"
    display_name = "故障源(模拟)"

    def search(self, origin, destination, date):
        raise RuntimeError("模拟票源故障")

    def deals(self, origin=None, destination=None):
        raise RuntimeError("模拟票源故障")


class MergeAndSortTest(unittest.TestCase):
    def test_collect_merges_flight_sources(self):
        offers, failed, _unavailable = fr.collect(all_sources(), "SHA", "CTU", dt.date(2026, 11, 15))
        self.assertEqual(failed, [])
        source_ids = {o["source_id"] for o in offers}
        self.assertEqual(source_ids, {"airline", "ota", "douyin_miniapp", "douyin_live"},
                         "航班票类四票源必须进入合并结果（团购为卡类渠道不出行）")

    def test_rank_offers_by_total_price(self):
        date = dt.date(2026, 11, 15)
        offers, _failed, _unavailable = fr.collect(all_sources(), "SHA", "CTU", date)
        offers = fr.rank_offers(offers, date)
        eligible = sorted(fr.total_price(o) for o in offers if fr.eligible_for_lowest(o, date))
        ineligible = sorted(fr.total_price(o) for o in offers if not fr.eligible_for_lowest(o, date))
        self.assertEqual([fr.total_price(o) for o in offers], eligible + ineligible)

    def test_search_output_contract(self):
        args = argparse.Namespace(from_code="SHA", to_code="CTU", date=FUTURE, source=None)
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = fr.cmd_search(args)
        out = buf.getvalue()
        self.assertEqual(code, 0)
        self.assertIn("[演示数据]", out.splitlines()[0], "首行声明措辞统一为'演示数据'")
        self.assertIn("◀ 最低价", out)
        self.assertIn("时间", out) and self.assertIn("机场", out)
        self.assertIn("到手价", out)


class TotalPriceComparabilityTest(unittest.TestCase):
    """v1.4 口径修订（PRD 第 8 节）：全部行按到手价统一可比，"税费另计"退出竞争的
    旧规则退役（ISSUE-001 裁定的正式修订，旧测试随口径改写）。"""

    def test_non_tax_rows_compete_via_total(self):
        # 不含税行票面 820 + 机建燃油 90 = 900 到手；含税行 890 到手——含税行仍最低
        offers = [{"price": 820, "tax_included": False, "total_estimate": 900},
                  {"price": 890, "tax_included": True, "total_estimate": 890}]
        self.assertEqual(fr.total_price(fr.lowest_eligible(offers, dt.date(2026, 11, 3))), 890)

    def test_non_tax_row_wins_when_total_lower(self):
        # 票面 780 + 70 = 850 < 含税 890：到手价低的行获胜（旧口径会错判）
        offers = [{"price": 780, "tax_included": False, "total_estimate": 850},
                  {"price": 890, "tax_included": True, "total_estimate": 890}]
        self.assertEqual(fr.total_price(fr.lowest_eligible(offers, dt.date(2026, 11, 3))), 850)

    def test_all_engine_rows_have_fee_breakdown_and_total(self):
        offers, _f, _u = fr.collect(all_sources(), "SHA", "PEK", dt.date(2026, 11, 3))
        self.assertTrue(offers)
        for offer in offers:
            fb = offer["fee_breakdown"]
            self.assertEqual(fb["airport_fee"], 50)
            self.assertIn(fb["fuel_fee"], (20, 40))
            self.assertEqual(fb["included_desc"],
                             fees.included_desc(offer["tax_included"], fb["fuel_fee"]))
            expected = (offer["price"] if offer["tax_included"]
                        else offer["price"] + 50 + fb["fuel_fee"])
            self.assertEqual(offer["total_estimate"], expected)

    def test_search_marks_lowest_by_total(self):
        with pricing.fixed_minute(PINNED_MINUTE):
            buf = io.StringIO()
            with redirect_stdout(buf):
                fr.main(["search", "--from", "SHA", "--to", "PEK", "--date", "2026-11-03"])
            offers, _f, _u = fr.collect(all_sources(), "SHA", "PEK", dt.date(2026, 11, 3))
        expected = fr.total_price(fr.lowest_eligible(offers, dt.date(2026, 11, 3)))
        lines = buf.getvalue().splitlines()
        markers = [i for i, ln in enumerate(lines) if "◀ 最低价" in ln]
        self.assertEqual(len(markers), 1)
        self.assertTrue(lines[markers[0] - 1].startswith(f"¥{expected}"),
                        "标记落在引擎到手最低价行（表格首列为到手价）")

    def test_tax_retired_flag_absent(self):
        with pricing.fixed_minute(PINNED_MINUTE):
            buf = io.StringIO()
            with redirect_stdout(buf):
                fr.main(["search", "--from", "SHA", "--to", "PEK", "--date", "2026-11-03"])
        self.assertNotIn("税费另计", buf.getvalue(), "'税费另计'风险标记退役（fee_breakdown 取代）")
        self.assertIn("另加机建", buf.getvalue(), "不含税行必须展示费用明细文案")


class SoldOutEligibilityTest(unittest.TestCase):
    """ISSUE-004 回归：售罄商品不参与"◀ 最低价"竞争，展示必含"已售罄"。"""

    def test_sold_out_deal_not_eligible(self):
        offer = {"price": 599, "total_estimate": 599, "is_deal": True, "stock": "售罄",
                 "valid_from": "2026-11-01", "valid_until": "2026-11-15"}
        self.assertFalse(fr.eligible_for_lowest(offer, dt.date(2026, 11, 10)))
        offer["stock"] = "充足"
        self.assertTrue(fr.eligible_for_lowest(offer, dt.date(2026, 11, 10)))

    def test_live_sold_out_row_disclosed_when_hit(self):
        # 直播间闪购保留确定性售罄带：出现售罄行时不得取得最低价标记
        live = DouyinLiveSource()
        date = dt.date.today() + dt.timedelta(days=1)
        found = False
        with pricing.fixed_minute(PINNED_MINUTE):
            for origin, destination in live.flash_routes():
                offers, _f, _u = fr.collect(all_sources(), origin, destination, date)
                for offer in offers:
                    if offer.get("stock") == "售罄":
                        found = True
                        self.assertFalse(fr.eligible_for_lowest(offer, date))
        self.assertTrue(found, "闪购场次中应存在确定性售罄带（测试资产不消灭）")


class ValidityEligibilityTest(unittest.TestCase):
    """ISSUE-003 回归：券有效期＝可出行窗口，不覆盖出发日期不得标最低价、不得催"出手"。"""

    def test_not_covered_live_coupon_excluded(self):
        # 直播闪购 3 日券只覆盖查询日附近；查 30 天后必然不覆盖
        date = dt.date.today() + dt.timedelta(days=30)
        live = DouyinLiveSource()
        origin, destination = live.flash_routes()[0]
        offers, _f, _u = fr.collect(all_sources(), origin, destination, date)
        coupons = [o for o in offers if o["source_id"] == "douyin_live"]
        self.assertTrue(coupons)
        for coupon in coupons:
            self.assertFalse(fr.eligible_for_lowest(coupon, date))
            self.assertIn("有效期不覆盖", fr.search_note(coupon, date))

    def test_unknown_validity_treated_as_not_covered(self):
        offer = {"price": 100, "total_estimate": 100, "is_deal": True}  # 缺有效期字段
        self.assertFalse(fr.eligible_for_lowest(offer, dt.date(2026, 11, 20)))
        self.assertIn("有效期无法确认", fr.search_note(offer, dt.date(2026, 11, 20)))

    def test_no_alert_triggered_by_uncovered_coupon_only(self):
        date = dt.date.today() + dt.timedelta(days=30)
        live = DouyinLiveSource()
        origin, destination = live.flash_routes()[0]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "alerts.json"
            fr.save_alerts([{"origin": origin, "destination": destination, "threshold": 1,
                             "created_at": "t"}], path)
            old = fr.ALERTS_FILE
            fr.ALERTS_FILE = path
            try:
                buf = io.StringIO()
                with redirect_stdout(buf):
                    code = fr.main(["search", "--from", origin, "--to", destination,
                                    "--date", date.isoformat()])
            finally:
                fr.ALERTS_FILE = old
        out = buf.getvalue()
        self.assertEqual(code, 0)
        self.assertNotIn("可以出手了", out, "用不上的券不得触发行动建议")


class SourceFailureTest(unittest.TestCase):
    def test_broken_source_degrades(self):
        offers, failed, _unavailable = fr.collect(all_sources() + [_BrokenSource()], "SHA", "PEK",
                                                  dt.date(2026, 11, 15))
        self.assertEqual(failed, ["故障源(模拟)"])
        self.assertTrue(offers, "其余票源应照常返回")

    def test_search_reports_unavailable_source(self):
        args = argparse.Namespace(from_code="SHA", to_code="PEK", date=FUTURE, source=None)
        buf = io.StringIO()
        with redirect_stdout(buf):
            fr.cmd_search(args)  # 真实源正常，无降级提示
        self.assertNotIn("暂不可用", buf.getvalue())


class DateValidationTest(unittest.TestCase):
    def test_valid_date(self):
        self.assertEqual(fr.parse_date("2026-11-15"), dt.date(2026, 11, 15))

    def test_bad_format_rejected(self):
        for bad in ("2026/11/15", "15-11-2026", "2026-13-01", "abc", ""):
            with self.assertRaises(fr.UsageError):
                fr.parse_date(bad)

    def test_past_date_rejected(self):
        yesterday = (dt.date.today() - dt.timedelta(days=1)).isoformat()
        with self.assertRaises(fr.UsageError):
            fr.parse_date(yesterday)


class CodeValidationTest(unittest.TestCase):
    def test_lowercase_normalized(self):
        self.assertEqual(fr.normalize_code("sha", "from"), "SHA")

    def test_invalid_code_rejected(self):
        for bad in ("上海", "SHAA", "S1", ""):
            with self.assertRaises(fr.UsageError):
                fr.normalize_code(bad, "from")


class DiscloseTest(unittest.TestCase):
    def test_disclose_fallback(self):
        self.assertTrue(fr.disclose(None).startswith("未披露"))
        self.assertTrue(fr.disclose("").startswith("未披露"))
        self.assertEqual(fr.disclose("有内容"), "有内容")

    def test_card_without_refund_rules_discloses(self):
        # DY-005 资产新形态：缺退改规则的卡必须显著标"退改规则未披露"
        cards = DouyinGroupbuySource().deals()
        incomplete = [c for c in cards if not c.get("refund_change_rules")]
        self.assertGreaterEqual(len(incomplete), 4, "省钱卡+三张权益卡缺退改规则（真实截图未展示）")
        for card in incomplete:
            text = fr.card_refund_text(card)
            self.assertIn("退改规则未披露", text)
            self.assertIn("核对退改条款", text)

    def test_card_with_refund_rules_kept(self):
        single = [c for c in DouyinGroupbuySource().deals() if c.get("card_type") == "single"]
        self.assertTrue(all("过期退/随时退" in c["refund_change_rules"] for c in single))


class AlertTest(unittest.TestCase):
    ALERT = {"origin": "SHA", "destination": "PEK", "threshold": 700, "created_at": "2026-10-24 10:00"}

    def test_alerts_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "alerts.json"
            fr.save_alerts([self.ALERT], path)
            self.assertEqual(fr.load_alerts(path), [self.ALERT])

    def test_load_missing_returns_empty(self):
        self.assertEqual(fr.load_alerts(Path(tempfile.gettempdir()) / "fare-radar-no-such.json"), [])

    def test_watch_check_hit_by_total_price(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            fr._watch_check([{**self.ALERT, "origin": "SHA", "destination": "CTU", "threshold": 1000}])
        self.assertIn("[低价提醒]", buf.getvalue())
        self.assertIn("到手价", buf.getvalue())

    def test_watch_check_miss(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            fr._watch_check([{**self.ALERT, "origin": "SHA", "destination": "CTU", "threshold": 100}])
        self.assertNotIn("[低价提醒]", buf.getvalue())
        self.assertIn("未达底价", buf.getvalue())


class WatchNullableThresholdTest(unittest.TestCase):
    """v1.3 授权演进延续：收藏底价允许为空（null），存量数据完全兼容。"""

    def test_roundtrip_with_and_without_threshold(self):
        alerts = [
            {"origin": "SHA", "destination": "PEK", "threshold": 700, "created_at": "t1"},
            {"origin": "SHA", "destination": "CTU", "threshold": None, "created_at": "t2"},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "alerts.json"
            fr.save_alerts(alerts, path)
            self.assertEqual(fr.load_alerts(path), alerts)

    def test_list_and_check_display_unthresholded_collection(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "alerts.json"
            fr.save_alerts([{"origin": "SHA", "destination": "CTU", "threshold": None,
                             "created_at": "2026-10-25 10:00"}], path)
            old = fr.ALERTS_FILE
            fr.ALERTS_FILE = path
            try:
                with pricing.fixed_minute(PINNED_MINUTE):
                    buf = io.StringIO()
                    with redirect_stdout(buf):
                        code = fr.main(["watch", "--list"])
                        fr.main(["watch"])
            finally:
                fr.ALERTS_FILE = old
        out = buf.getvalue()
        self.assertEqual(code, 0)
        self.assertIn("未设底价", out)
        self.assertIn("仅收藏", out)
        self.assertNotIn("可以出手了", out)

    def test_search_does_not_crash_on_null_threshold(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "alerts.json"
            fr.save_alerts([{"origin": "SHA", "destination": "CTU", "threshold": None,
                             "created_at": "t"}], path)
            old = fr.ALERTS_FILE
            fr.ALERTS_FILE = path
            try:
                buf = io.StringIO()
                with redirect_stdout(buf):
                    code = fr.main(["search", "--from", "SHA", "--to", "CTU", "--date", FUTURE])
            finally:
                fr.ALERTS_FILE = old
        self.assertEqual(code, 0)
        self.assertNotIn("可以出手了", buf.getvalue())


class AlertsCorruptionTest(unittest.TestCase):
    """ISSUE-002 回归：alerts.json 损坏必须中文警告，且绝不以新清单覆盖原文件。"""

    def setUp(self):
        self._old_alerts_file = fr.ALERTS_FILE

    def tearDown(self):
        fr.ALERTS_FILE = self._old_alerts_file

    @staticmethod
    def _corrupt(path):
        path.write_text("{oops broken", encoding="utf-8")
        return path.read_text(encoding="utf-8")

    def test_load_corrupted_warns_and_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "alerts.json"
            self._corrupt(path)
            fr.ALERTS_FILE = path
            buf = io.StringIO()
            with redirect_stdout(buf), self.assertRaises(fr.AlertsCorrupted):
                fr.load_alerts()
            self.assertIn("已损坏", buf.getvalue())

    def test_watch_check_warns_instead_of_showing_empty_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "alerts.json"
            self._corrupt(path)
            fr.ALERTS_FILE = path
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = fr.main(["watch"])
            self.assertEqual(code, 2)
            self.assertIn("已损坏", buf.getvalue())

    def test_set_refuses_and_keeps_corrupted_file_intact(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "alerts.json"
            original = self._corrupt(path)
            fr.ALERTS_FILE = path
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = fr.main(["watch", "--from", "SHA", "--to", "CTU", "--price", "500"])
            self.assertEqual(code, 2)
            self.assertNotIn("已保存收藏", buf.getvalue())
            self.assertEqual(path.read_text(encoding="utf-8"), original, "损坏文件必须原样保留")


class CitiesRegistryTest(unittest.TestCase):
    """PRD v1.4 第 4 节：50 城纯国内注册表是城市唯一事实来源。"""

    RETIRED = {"HKG", "KIX", "NRT", "ICN", "BKK"}

    def test_registry_has_exactly_50_unique_cities(self):
        self.assertEqual(len(cities.CITIES), 50)
        codes = list(cities.CITIES)
        self.assertEqual(len(set(codes)), 50, "三字码不得重复")
        names = [e["name"] for e in cities.CITIES.values()]
        self.assertEqual(len(set(names)), 50, "中文城市名不得重复")
        airports = [e["airport"] for e in cities.CITIES.values()]
        self.assertEqual(len(set(airports)), 50, "机场全称不得重复")
        for entry in cities.CITIES.values():
            self.assertTrue(15 < entry["lat"] < 55 and 75 < entry["lon"] < 135,
                            "坐标必须落在中国范围（纯国内城市全集）")
            self.assertTrue(entry["airport"].endswith(("机场", "国际机场")))

    def test_no_international_city_remains(self):
        for code in self.RETIRED:
            self.assertNotIn(code, cities.CITIES, f"{code} 已随 v1.4 下线")
            self.assertTrue(cities.is_retired(code))
            self.assertTrue(cities.retired_name(code))

    def test_active_codes_not_retired(self):
        for code in ("SHA", "PEK", "JJN", "OHE"):
            self.assertFalse(cities.is_retired(code))
            self.assertIsNone(cities.retired_name(code))

    def test_distance_known_pair_and_unknown_code(self):
        sha_ctu = cities.distance_km("SHA", "CTU")
        self.assertTrue(1400 < sha_ctu < 1900, f"上海-成都实际距离应在 1400-1900km，收到 {sha_ctu}")
        self.assertIsNone(cities.distance_km("SHA", "HKG"), "已下线城市不参与距离计算")

    def test_all_cities_carries_airport(self):
        listing = cities.all_cities()
        self.assertEqual(len(listing), 50)
        self.assertEqual(listing[0], {"code": "PEK", "name": "北京", "airport": "北京首都国际机场"})


class AnyRoutePricingTest(unittest.TestCase):
    """PRD v1.4 AC：注册表内任意国内起讫组合出价，远贵近廉，覆盖模型可解释。"""

    def test_domestic_routes_all_sources_accounted(self):
        for origin, destination in (("HRB", "SYX"), ("KMG", "URC"), ("SHA", "OHE"),
                                    ("TNA", "NNG"), ("LXA", "HAK")):
            with pricing.fixed_minute(PINNED_MINUTE):
                offers, failed, unavailable = fr.collect(all_sources(), origin, destination,
                                                         dt.date(2026, 11, 20))
            self.assertEqual(failed, [])
            quoted = {o["source_id"] for o in offers}
            self.assertIn("airline", quoted, f"{origin}→{destination} 航司直连必须出价")
            self.assertIn("ota", quoted)
            for offer in offers:
                self.assertGreater(offer["total_estimate"], 0)
                self.assertIn("entry_url", offer, "比价行由引擎附上渠道入口字段")
            # 覆盖模型不变式：每个票源要么有报价、要么显式报"无销售"，不得凭空消失
            accounted = quoted | {u["source_id"] for u in unavailable}
            self.assertEqual(accounted, {s.source_id for s in all_sources()},
                             f"{origin}→{destination}: 票源有报价/无销售必须二选一且可解释")
            reasons = {u["source_id"]: u["reason"] for u in unavailable}
            self.assertIn("卡类渠道", reasons["douyin_groupbuy"])
            for u in unavailable:
                self.assertTrue(u["name"] and u["reason"])

    def test_far_routes_cost_more_than_near_ones(self):
        far = pricing.route_base("HRB", "SYX")     # 哈尔滨-三亚 ≈ 3300km
        near = pricing.route_base("SHA", "NKG")    # 上海-南京 ≈ 270km
        self.assertGreater(far, near * 2, "远贵近廉的大趋势必须成立")

    def test_retired_city_yields_friendly_empty(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = fr.main(["search", "--from", "SHA", "--to", "HKG", "--date", FUTURE])
        self.assertEqual(code, 0)
        self.assertIn("不在已支持的 50 城清单", buf.getvalue())
        self.assertIn("城市已下线", buf.getvalue())
        self.assertNotIn("Traceback", buf.getvalue())


class FiveSourceFamilyTest(unittest.TestCase):
    """PRD v1.4：票源家族清单、命名与渠道入口（新地点页短链）。"""

    FAMILY = [("airline", "航司直连"), ("ota", "OTA 比价"), ("douyin_groupbuy", "抖音团购"),
              ("douyin_miniapp", "抖音小程序·春秋航空"), ("douyin_live", "抖音直播间")]

    def test_catalog_matches_prd(self):
        catalog = source_catalog()
        self.assertEqual([(s["id"], s["name"]) for s in catalog], self.FAMILY)
        for entry in catalog:
            self.assertNotIn("模拟", entry["name"])
            self.assertTrue(entry["description"], "每个票源需有一句话说明")

    def test_douyin_channels_use_new_location_link(self):
        catalog = {s["id"]: s for s in source_catalog()}
        self.assertEqual(catalog["douyin_miniapp"]["entry_url"], NEW_LINK)
        self.assertEqual(catalog["douyin_groupbuy"]["entry_url"], NEW_LINK)
        self.assertIn("特价票", catalog["douyin_miniapp"]["entry_note"])
        self.assertIn("次卡", catalog["douyin_groupbuy"]["entry_note"])
        self.assertEqual(catalog["douyin_live"]["entry_url"], "https://www.douyin.com/")
        self.assertEqual(catalog["airline"]["entry_url"], "", "多家航司无单一入口，允许为空")
        self.assertIn("示例入口", catalog["ota"]["entry_note"])

    def test_groupbuy_is_card_channel(self):
        source = DouyinGroupbuySource()
        self.assertEqual(source.search("SHA", "CTU", dt.date(2026, 11, 15)), [],
                         "团购为卡类渠道，不卖具体航班——search 恒空")
        available, reason = source.availability("SHA", "CTU", dt.date(2026, 11, 15))
        self.assertFalse(available)
        self.assertIn("卡类渠道", reason)
        cards = source.deals()
        self.assertEqual(len(cards), 6, "卡池 6 张（PRD v1.4 第 7.2 节）")

    def test_line_names_carry_no_mock_word(self):
        with pricing.fixed_minute(PINNED_MINUTE):
            offers, _failed, _unavailable = fr.collect(all_sources(), "SHA", "CTU", dt.date(2026, 11, 20))
        for offer in offers:
            self.assertNotIn("模拟", offer["source_name"], "行内票源名去术语（ISSUE-009 第 3 点）")

    def test_live_flash_schedule_is_deterministic_and_domestic(self):
        live = DouyinLiveSource()
        schedule = live.flash_routes()
        self.assertEqual(len(schedule), 4)
        self.assertEqual(schedule, live.flash_routes(), "同一天场次固定（确定性）")
        for origin, destination in schedule:
            self.assertIn(origin, cities.CITIES) and self.assertIn(destination, cities.CITIES)
            offers = live.search(origin, destination, dt.date.today() + dt.timedelta(days=1))
            self.assertEqual(len(offers), 1)
            self.assertEqual(offers[0]["is_deal"], True)


class DemoFluctuationTest(unittest.TestCase):
    """PRD：分钟级演示波动——规则唯一来源的专门单测。"""

    def test_same_minute_cli_and_web_identical(self):
        with pricing.fixed_minute(PINNED_MINUTE):
            buf = io.StringIO()
            with redirect_stdout(buf):
                fr.main(["search", "--from", "SHA", "--to", "CTU", "--date", "2026-11-20"])
            _, payload = webapp._api_search({"from": "SHA", "to": "CTU", "date": "2026-11-20"})
        lines = buf.getvalue().splitlines()
        header = next(i for i, ln in enumerate(lines) if ln.startswith("到手价"))
        first_row = lines[header + 2]  # 表头 + 分隔线之后第一行数据
        self.assertTrue(first_row.startswith(f"¥{payload['offers'][0]['total_estimate']}"),
                        "同一分钟内 CLI 首行到手价与网页 API 完全一致（规则唯一来源）")
        eligible = [o for o in payload["offers"] if fr.eligible_for_lowest(o, fr.parse_date("2026-11-20"))]
        self.assertEqual(min(fr.total_price(o) for o in eligible),
                         payload["offers"][0]["total_estimate"],
                         "首行 = 准入合格行中的到手最低价（不覆盖有效期的券排序在后）")

    def test_same_minute_output_deterministic(self):
        outputs = []
        for _ in range(2):
            buf = io.StringIO()
            with pricing.fixed_minute(PINNED_MINUTE), redirect_stdout(buf):
                fr.main(["search", "--from", "SHA", "--to", "PEK", "--date", "2026-11-03"])
            outputs.append(buf.getvalue())
        self.assertEqual(outputs[0], outputs[1], "同输入同分钟输出逐字节一致（无随机）")

    def test_cross_minute_visible_change_within_amplitude(self):
        base = pricing.route_base("SHA", "PEK")
        mu_prices = set()
        for offset in range(30):
            with pricing.fixed_minute(PINNED_MINUTE + offset):
                offers, _failed, _unavailable = fr.collect(all_sources(), "SHA", "PEK", dt.date(2026, 11, 3))
            for offer in offers:
                # 只看同一承运人同一舱位：价差才只反映演示波动（跨档位价差另计）
                if offer["source_id"] == "airline" and offer.get("carrier_code") == "MU":
                    mu_prices.add(offer["price"])
        self.assertGreaterEqual(len(mu_prices), 3, "分钟之间必须可见变化")
        self.assertLessEqual(max(mu_prices) - min(mu_prices),
                             int(base * 0.07) + 2, "波动幅度必须小幅（±3.5% 设计上限）")
        self.assertGreater(min(mu_prices), 0, "波动不得产生 0 元/负数")


class ExitCodeTest(unittest.TestCase):
    def test_missing_date_returns_nonzero(self):
        self.assertEqual(fr.main(["search", "--from", "SHA", "--to", "PEK"]), 2)

    def test_same_origin_destination_returns_nonzero(self):
        self.assertEqual(fr.main(["search", "--from", "SHA", "--to", "SHA", "--date", FUTURE]), 2)

    def test_unknown_source_returns_nonzero(self):
        self.assertEqual(
            fr.main(["search", "--from", "SHA", "--to", "PEK", "--date", FUTURE, "--source", "nope"]), 2)

    def test_no_command_returns_nonzero(self):
        self.assertEqual(fr.main([]), 2)


class SourceCoverageTest(unittest.TestCase):
    """ISSUE-010 回归（v1.4 口径）：真实渠道不存在的航班不编价。"""

    def test_cgo_xiy_has_no_miniapp_quote(self):
        miniapp = [s for s in all_sources() if s.source_id == "douyin_miniapp"][0]
        available, reason = miniapp.availability("CGO", "XIY", dt.date(2026, 10, 26))
        self.assertFalse(available, "非春秋基地航线不得出价（不编造）")
        self.assertIn("基地航线", reason)
        with pricing.fixed_minute(PINNED_MINUTE):
            offers, _failed, unavailable = fr.collect(all_sources(), "CGO", "XIY", dt.date(2026, 10, 26))
        self.assertNotIn("douyin_miniapp", {o["source_id"] for o in offers})
        self.assertIn("douyin_miniapp", {u["source_id"] for u in unavailable})

    def test_cli_reports_unavailable_sources(self):
        with pricing.fixed_minute(PINNED_MINUTE):
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = fr.main(["search", "--from", "CGO", "--to", "XIY", "--date", "2026-10-26"])
        out = buf.getvalue()
        self.assertEqual(code, 0)
        self.assertIn("以下渠道本次无销售", out, "与'[提示] 暂不可用'降级提示区分")
        self.assertIn("抖音小程序·春秋航空", out)
        self.assertIn("可能未执飞", out)

    def test_base_route_no_seat_day_deterministic(self):
        miniapp = [s for s in all_sources() if s.source_id == "douyin_miniapp"][0]
        available, _ = miniapp.availability("SHA", "CGO", dt.date(2026, 11, 2))
        self.assertTrue(available)
        # 无座日按日期哈希确定性复现，频率约 1/7（35 天 3-7 天）
        no_seat = [d for d in (dt.date(2026, 11, 1) + dt.timedelta(days=i) for i in range(35))
                   if not miniapp.availability("SHA", "CGO", d)[0]]
        self.assertTrue(3 <= len(no_seat) <= 7, f"无座日频率应约 1/7，实际 {len(no_seat)}/35")
        probe = no_seat[0]
        self.assertEqual(miniapp.availability("SHA", "CGO", probe),
                         miniapp.availability("SHA", "CGO", probe), "同日期两次判定一致")
        with pricing.fixed_minute(PINNED_MINUTE):
            offers, _f, _u = fr.collect([miniapp], "SHA", "CGO", probe)
        self.assertEqual(offers, [])
        # 同日特价票不消费"无座"机制：固定日期特价票独立于常规航班无座判定
        special_date = dt.date(2026, 10, 27)
        if not miniapp.availability("SHA", "CKG", special_date)[0]:
            offers, _f, _u = fr.collect([miniapp], "SHA", "CKG", special_date)
            self.assertTrue(any(o.get("is_special_fare") for o in offers),
                            "特价票仅按固定日期可售判定，不随当日无座消失")

    def test_offers_carry_channel_entry(self):
        catalog = {s["id"]: s for s in source_catalog()}
        with pricing.fixed_minute(PINNED_MINUTE):
            offers, _f, _u = fr.collect(all_sources(), "SHA", "CGO", dt.date(2026, 11, 2))
        for offer in offers:
            self.assertEqual(offer["entry_url"], catalog[offer["source_id"]]["entry_url"])


if __name__ == "__main__":
    unittest.main()
