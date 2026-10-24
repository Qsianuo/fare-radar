# -*- coding: utf-8 -*-
"""FareRadar 核心逻辑单测（v1.3）。

覆盖：多源合并排序、票源失败降级、日期/三字码校验、缺失字段兜底（未披露）、
提醒阈值命中、alerts 读写与损坏保护、参数错误退出码；v1.3 增量：城市注册表、
任意航线定价、5 票源家族、分钟级演示波动确定性（同分钟一致/跨分钟变化）。
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
from sources import all_sources, cities, pricing, source_catalog  # noqa: E402
from sources.douyin_groupbuy import DouyinGroupbuySource  # noqa: E402
from sources.douyin_live import DouyinLiveSource  # noqa: E402

FUTURE = (dt.date.today() + dt.timedelta(days=30)).isoformat()
PINNED_MINUTE = 29_500_000  # 涉及具体价格的断言先把查询时刻固定到该分钟桶


class _BrokenSource:
    """始终抛异常的故障源，用于验证单源失败不拖垮整体（PRD AC2）。"""

    source_id = "broken"
    display_name = "故障源(模拟)"

    def search(self, origin, destination, date):
        raise RuntimeError("模拟票源故障")

    def deals(self, origin=None, destination=None):
        raise RuntimeError("模拟票源故障")


class MergeAndSortTest(unittest.TestCase):
    def test_collect_merges_all_sources(self):
        offers, failed, _unavailable = fr.collect(all_sources(), "SHA", "CTU", dt.date(2026, 11, 15))
        self.assertEqual(failed, [])
        source_ids = {o["source_id"] for o in offers}
        self.assertEqual(
            source_ids,
            {"airline", "ota", "douyin_groupbuy", "douyin_miniapp", "douyin_live"},
            "v1.3 五票源家族必须全部进入合并结果")

    def test_rank_offers_comparable_first_then_price(self):
        date = dt.date(2026, 11, 15)
        offers, _failed, _unavailable = fr.collect(all_sources(), "SHA", "CTU", date)
        offers = fr.rank_offers(offers, date)
        eligible = sorted(o["price"] for o in offers if fr.eligible_for_lowest(o, date))
        ineligible = sorted(o["price"] for o in offers if not fr.eligible_for_lowest(o, date))
        self.assertEqual([o["price"] for o in offers], eligible + ineligible)
        self.assertTrue(any(o["is_deal"] for o in offers), "合并结果应含团购行")

    def test_search_output_contract(self):
        args = argparse.Namespace(from_code="SHA", to_code="CTU", date=FUTURE, source=None)
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = fr.cmd_search(args)
        out = buf.getvalue()
        self.assertEqual(code, 0)
        self.assertIn("[演示数据]", out.splitlines()[0], "首行声明措辞统一为'演示数据'")
        self.assertIn("◀ 最低价", out)


class TaxComparabilityTest(unittest.TestCase):
    """ISSUE-001 回归：口径可比（含税）才可竞争"◀ 最低价"，不含税行必须披露"税费另计"。"""

    def test_lowest_eligible_ignores_non_tax_rows(self):
        offers = [{"price": 820, "tax_included": False}, {"price": 890, "tax_included": True}]
        self.assertEqual(fr.lowest_eligible(offers, dt.date(2026, 11, 3))["price"], 890)

    def test_no_eligible_lowest_when_all_non_tax(self):
        self.assertIsNone(fr.lowest_eligible([{"price": 820, "tax_included": False}],
                                             dt.date(2026, 11, 3)))

    def test_search_discloses_tax_and_marks_comparable_lowest(self):
        # 引擎定价随分钟波动：先固定查询时刻，标记行必须等于引擎算出的可比最低价
        with pricing.fixed_minute(PINNED_MINUTE):
            buf = io.StringIO()
            with redirect_stdout(buf):
                fr.main(["search", "--from", "SHA", "--to", "PEK", "--date", "2026-11-03"])
            offers, _failed, _unavailable = fr.collect(all_sources(), "SHA", "PEK", dt.date(2026, 11, 3))
        expected = fr.lowest_eligible(offers, dt.date(2026, 11, 3))["price"]
        lines = buf.getvalue().splitlines()
        self.assertTrue(any("税费另计" in ln for ln in lines),
                        "不含税行必须含'税费另计'披露")
        markers = [i for i, ln in enumerate(lines) if "◀ 最低价" in ln]
        self.assertEqual(len(markers), 1)
        self.assertIn(f"¥{expected}", lines[markers[0] - 1], "标记落在引擎可比最低价行")

    def test_kix_case_marker_on_comparable_row(self):
        # ISSUE-005 并入回归：SHA→KIX，不含税行（引流/低成本舱）不得标最低价
        with pricing.fixed_minute(PINNED_MINUTE):
            buf = io.StringIO()
            with redirect_stdout(buf):
                fr.main(["search", "--from", "SHA", "--to", "KIX", "--date", "2026-12-20"])
            offers, _failed, _unavailable = fr.collect(all_sources(), "SHA", "KIX", dt.date(2026, 12, 20))
        expected = fr.lowest_eligible(offers, dt.date(2026, 12, 20))["price"]
        lines = buf.getvalue().splitlines()
        marker = next(i for i, ln in enumerate(lines) if "◀ 最低价" in ln)
        self.assertIn(f"¥{expected}", lines[marker - 1], "标记应落在含税可比行而非不含税行")
        self.assertTrue(any("税费另计" in ln for ln in lines))


class SoldOutEligibilityTest(unittest.TestCase):
    """ISSUE-004 回归：售罄券不参与"◀ 最低价"竞争，展示必含"已售罄"。"""

    def test_sold_out_deal_not_marked_and_disclosed(self):
        # 提交人复现场景：PEK→SHA 2026-11-10（DY-004 售罄资产保留；引擎源该日另有可比报价）
        with pricing.fixed_minute(PINNED_MINUTE):
            buf = io.StringIO()
            with redirect_stdout(buf):
                fr.main(["search", "--from", "PEK", "--to", "SHA", "--date", "2026-11-10"])
        out = buf.getvalue()
        self.assertIn("已售罄", out, "售罄券展示必含'已售罄'")
        self.assertIn("税费另计", out)
        lines = out.splitlines()
        sold_out_lines = [i for i, ln in enumerate(lines) if "已售罄" in ln]
        self.assertTrue(sold_out_lines)
        markers = [i for i, ln in enumerate(lines) if "◀ 最低价" in ln]
        for marker in markers:
            self.assertNotIn("已售罄", lines[marker - 1], "售罄行不得取得最低价标记")

    def test_sold_out_only_route_has_no_lowest(self):
        # 只看抖音团购（仅 DY-004 且售罄）→ 无可比最低价，输出中文提示而非报错
        with pricing.fixed_minute(PINNED_MINUTE):
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = fr.main(["search", "--from", "PEK", "--to", "SHA", "--date", "2026-11-10",
                                "--source", "douyin_groupbuy"])
        self.assertEqual(code, 0)
        self.assertNotIn("◀ 最低价", buf.getvalue())
        self.assertIn("已售罄", buf.getvalue())
        self.assertIn("没有口径可比的最低价", buf.getvalue())

    def test_sold_out_check_in_eligibility(self):
        offer = {"price": 599, "tax_included": True, "is_deal": True, "stock": "售罄",
                 "valid_from": "2026-11-01", "valid_until": "2026-11-15"}
        self.assertFalse(fr.eligible_for_lowest(offer, dt.date(2026, 11, 10)))
        offer["stock"] = "充足"
        self.assertTrue(fr.eligible_for_lowest(offer, dt.date(2026, 11, 10)))


class ValidityEligibilityTest(unittest.TestCase):
    """ISSUE-003 回归：券有效期＝可出行窗口，不覆盖出发日期不得标最低价、不得催"出手"。"""

    def test_not_covered_deal_excluded_and_no_alert(self):
        # 提交人复现场景（隔离变量）：只看抖音团购，SHA→PEK 2026-11-03，DY-002 有效期 11-05 起
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "alerts.json"
            fr.save_alerts([{"origin": "SHA", "destination": "PEK", "threshold": 700,
                             "created_at": "2026-10-24"}], path)
            old = fr.ALERTS_FILE
            fr.ALERTS_FILE = path
            try:
                buf = io.StringIO()
                with redirect_stdout(buf):
                    code = fr.main(["search", "--from", "SHA", "--to", "PEK", "--date", "2026-11-03",
                                    "--source", "douyin_groupbuy"])
            finally:
                fr.ALERTS_FILE = old
        out = buf.getvalue()
        self.assertEqual(code, 0)
        self.assertNotIn("可以出手了", out, "用不上的券不得触发行动建议")
        self.assertNotIn("◀ 最低价", out, "用不上的券不得取得最低价标记")
        self.assertTrue(any("有效期不覆盖" in ln and "7日有效券" in ln
                            for ln in out.splitlines()), "不覆盖信息必须可见")
        self.assertIn("没有口径可比的最低价", out)

    def test_covered_deal_still_lowest(self):
        # 回归：有效期覆盖出发日期的券正常参与竞争（SHA→CTU 2026-11-20，DY-005 覆盖）
        with pricing.fixed_minute(PINNED_MINUTE):
            buf = io.StringIO()
            with redirect_stdout(buf):
                fr.main(["search", "--from", "SHA", "--to", "CTU", "--date", "2026-11-20"])
            offers, _failed, _unavailable = fr.collect(all_sources(), "SHA", "CTU", dt.date(2026, 11, 20))
        expected = fr.lowest_eligible(offers, dt.date(2026, 11, 20))["price"]
        self.assertTrue(430 <= expected <= 500, "覆盖日最低价应为精选券价格带")
        lines = buf.getvalue().splitlines()
        marker = next(i for i, ln in enumerate(lines) if "◀ 最低价" in ln)
        self.assertIn(f"¥{expected}", lines[marker - 1])

    def test_unknown_validity_treated_as_not_covered(self):
        offer = {"price": 100, "tax_included": True, "is_deal": True}  # 缺有效期字段
        self.assertFalse(fr.eligible_for_lowest(offer, dt.date(2026, 11, 20)))
        self.assertIn("有效期无法确认", fr.search_note(offer, dt.date(2026, 11, 20)))


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

    def test_deal_without_refund_rules_shows_disclose(self):
        incomplete = [d for d in DouyinGroupbuySource().deals("SHA", "CTU")
                      if "refund_change_rules" not in d]
        self.assertEqual(len(incomplete), 1, "模拟数据应含一条退改规则缺失的套餐(DY-005)")
        card = "\n".join(fr._deal_card(incomplete[0]))
        self.assertIn("商家未公布退改规则", card, "兜底文案必须是用户语言的风险提示")
        self.assertIn("核对退改条款", card)
        self.assertIn("截图留证", card)
        self.assertNotIn("退改规则：\n", card)


class RefundDisclosureTest(unittest.TestCase):
    """ISSUE-006 回归：内部术语不得透出；search 结果中退改不明的券必须带标记。"""

    JARGON = ("字段缺失", "数据不完整", "模拟源数据")

    def test_search_row_marks_refund_unclear(self):
        # SHA→CTU 2026-11-20：DY-005 覆盖且充足 → 参与竞争，行内必须带"退改不明"
        with pricing.fixed_minute(PINNED_MINUTE):
            buf = io.StringIO()
            with redirect_stdout(buf):
                fr.main(["search", "--from", "SHA", "--to", "CTU", "--date", "2026-11-20"])
        lines = buf.getvalue().splitlines()
        self.assertTrue(any("双城券" in ln and "退改不明" in ln for ln in lines),
                        "退改不明的券行内必须带标记")
        markers = [i for i, ln in enumerate(lines) if "◀ 最低价" in ln]
        self.assertEqual(len(markers), 1, "最低价标记唯一")
        if "退改不明" in lines[markers[0] - 1]:
            self.assertIn("双城券", lines[markers[0] - 1])  # 标记与最低价可在同一行

    def test_no_internal_jargon_in_any_command_output(self):
        for argv in (["search", "--from", "SHA", "--to", "CTU", "--date", "2026-11-20"],
                     ["deals"], ["deals", "--from", "SHA", "--to", "CTU"], ["watch"]):
            buf = io.StringIO()
            with redirect_stdout(buf):
                fr.main(argv)
            out = buf.getvalue()
            for word in self.JARGON:
                self.assertNotIn(word, out, f"{argv[0]} 输出透出内部术语：{word}")
            self.assertNotIn("Traceback", out)

    def test_complete_deal_card_contract(self):
        card = "\n".join(fr._deal_card(DouyinGroupbuySource().deals("SHA", "PEK")[0]))
        for required in ("需在抖音 App 内下单", "有效期：", "退改规则：", "库存：", "DY-"):
            self.assertIn(required, card)


class AlertTest(unittest.TestCase):
    ALERT = {"origin": "SHA", "destination": "PEK", "threshold": 700, "created_at": "2026-10-24 10:00"}

    def test_alerts_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "alerts.json"
            fr.save_alerts([self.ALERT], path)
            self.assertEqual(fr.load_alerts(path), [self.ALERT])

    def test_load_missing_returns_empty(self):
        self.assertEqual(fr.load_alerts(Path(tempfile.gettempdir()) / "fare-radar-no-such.json"), [])

    def test_watch_check_hit(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            fr._watch_check([{**self.ALERT, "origin": "SHA", "destination": "CTU", "threshold": 1000}])
        self.assertIn("[低价提醒]", buf.getvalue())

    def test_watch_check_miss(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            fr._watch_check([{**self.ALERT, "origin": "SHA", "destination": "CTU", "threshold": 100}])
        self.assertNotIn("[低价提醒]", buf.getvalue())
        self.assertIn("未达底价", buf.getvalue())


class WatchNullableThresholdTest(unittest.TestCase):
    """v1.3 唯一授权演进：收藏底价允许为空（null），存量数据完全兼容。"""

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


class WebV13ContractTest(unittest.TestCase):
    """v1.3 新端点与载荷增量：城市全集、票源清单、24 小时演示曲线。"""

    def test_cities_endpoint_contract(self):
        status, payload = webapp._api_cities({})
        self.assertEqual((status, payload["ok"]), (200, True))
        self.assertEqual(payload["count"], 25)
        self.assertEqual(len(payload["cities"]), 25)
        self.assertEqual(payload["cities"][0], {"code": "PEK", "name": "北京"})
        self.assertTrue(all(c["code"] and c["name"] for c in payload["cities"]))

    def test_sources_endpoint_contract(self):
        status, payload = webapp._api_sources({})
        self.assertEqual((status, payload["ok"]), (200, True))
        self.assertEqual(len(payload["sources"]), 5)
        self.assertEqual(len({s["id"] for s in payload["sources"]}), 5)
        for s in payload["sources"]:
            self.assertNotIn("模拟", s["name"] + s["description"])
            self.assertTrue(s["name"] and s["description"])

    def test_trend_endpoint_contract(self):
        params = {"from": "SHA", "to": "CTU", "date": "2026-11-20"}
        with pricing.fixed_minute(PINNED_MINUTE):
            status, payload = webapp._api_trend(dict(params))
            _, again = webapp._api_trend(dict(params))
        self.assertEqual((status, payload["ok"]), (200, True))
        self.assertEqual(len(payload["points"]), 24, "近 24 小时逐小时点集")
        self.assertEqual(payload["points"][0]["hours_ago"], 23)
        self.assertEqual(payload["points"][-1]["hours_ago"], 0)
        prices = [p["price"] for p in payload["points"]]
        self.assertTrue(all(p > 0 for p in prices))
        self.assertEqual(payload["current"], prices[-1])
        self.assertEqual(payload["day_low"], min(prices))
        self.assertEqual(payload["day_high"], max(prices))
        self.assertEqual(payload, again, "同一分钟内曲线重放必须逐字节一致（确定性）")
        self.assertIn("演示波动", payload["notice"])
        self.assertNotIn("行情", payload["notice"] + payload["notice"])

    def test_trend_rejects_bad_input(self):
        for params in ({"from": "SHA", "to": "SHA", "date": "2026-11-20"},
                       {"from": "SHA", "to": "URC", "date": "2026-11-20"},
                       {"from": "SHA", "to": "CTU"}):
            status, payload = webapp._api_trend(dict(params))
            self.assertEqual(status, 400, params)
            self.assertFalse(payload["ok"])

    def test_search_payload_includes_source_info_and_flags(self):
        status, payload = webapp._api_search({"from": "SHA", "to": "CTU", "date": "2026-11-20"})
        self.assertEqual(status, 200)
        self.assertEqual(len({o["source_id"] for o in payload["offers"]}), 5)
        for offer in payload["offers"]:
            self.assertTrue(offer["source_desc"], "每行需带票源说明")
            self.assertIsInstance(offer["risk_flags"], list)
        self.assertTrue(any("税费另计" in "、".join(o["risk_flags"]) for o in payload["offers"]))
        self.assertEqual(payload["notice"], fr.SIM_NOTICE)

    def test_watch_set_without_threshold(self):
        status, payload = webapp._api_watch_set({"from": "SHA", "to": "CTU"})
        self.assertEqual((status, payload["ok"]), (200, True))
        self.assertIn("未设底价", payload["message"])
        _, listed = webapp._api_watch_list({})
        self.assertIsNone(listed["alerts"][0]["threshold"])
        self.assertIsNotNone(listed["alerts"][0]["current_lowest"], "收藏页应带当前最低价")
        self.assertFalse(listed["alerts"][0]["below_threshold"])
        status, _ = webapp._api_watch_remove({"from": "SHA", "to": "CTU"})
        self.assertEqual(status, 200)


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
            self.assertNotIn("暂无关注航线", buf.getvalue())

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
    """PRD v1.3 第 4 节：25 城注册表是城市唯一事实来源。"""

    def test_registry_has_exactly_25_unique_cities(self):
        self.assertEqual(len(cities.CITIES), 25)
        codes = list(cities.CITIES)
        self.assertEqual(len(set(codes)), 25, "三字码不得重复")
        names = [e["name"] for e in cities.CITIES.values()]
        self.assertEqual(len(set(names)), 25, "中文城市名不得重复")
        for entry in cities.CITIES.values():
            self.assertTrue(0 < entry["lat"] < 60 and 90 < entry["lon"] < 150)

    def test_distance_known_pair_and_unknown_code(self):
        sha_ctu = cities.distance_km("SHA", "CTU")
        self.assertTrue(1400 < sha_ctu < 1900, f"上海-成都实际距离应在 1400-1900km，收到 {sha_ctu}")
        self.assertIsNone(cities.distance_km("SHA", "URC"),
                          "URC 不在 25 城清单（PRD 边界）；任务书示例差异已记入交接文档")


class AnyRoutePricingTest(unittest.TestCase):
    """PRD v1.3 AC2：注册表内任意起讫组合出价，远贵近廉。"""

    def test_remote_routes_all_sources_quote(self):
        for origin, destination in (("HRB", "SYX"), ("KMG", "HKG"), ("SHA", "NRT")):
            with pricing.fixed_minute(PINNED_MINUTE):
                offers, failed, unavailable = fr.collect(all_sources(), origin, destination,
                                                         dt.date(2026, 11, 20))
            self.assertEqual(failed, [])
            quoted = {o["source_id"] for o in offers}
            self.assertGreaterEqual(len(quoted), 2, f"{origin}→{destination} 至少航司与 OTA 出价")
            self.assertIn("airline", quoted)
            self.assertIn("ota", quoted)
            for offer in offers:
                self.assertGreater(offer["price"], 0)
                self.assertIn(offer["tax_included"], (True, False))
                self.assertIn("entry_url", offer, "比价行由引擎附上渠道入口字段")
            # 覆盖模型不变式：每个票源要么有报价、要么显式报"无销售"，不得凭空消失
            accounted = quoted | {u["source_id"] for u in unavailable}
            self.assertEqual(accounted, {s.source_id for s in all_sources()},
                             f"{origin}→{destination}: 票源有报价/无销售必须二选一且可解释")
            self.assertIn("douyin_groupbuy", {u["source_id"] for u in unavailable})
            for u in unavailable:
                self.assertTrue(u["name"] and u["reason"])

    def test_far_routes_cost_more_than_near_ones(self):
        far = pricing.route_base("HRB", "SYX")     # 哈尔滨-三亚 ≈ 3300km
        near = pricing.route_base("SHA", "NKG")    # 上海-南京 ≈ 270km
        self.assertGreater(far, near * 2, "远贵近廉的大趋势必须成立")

    def test_unknown_city_yields_friendly_empty(self):
        with pricing.fixed_minute(PINNED_MINUTE):
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = fr.main(["search", "--from", "SHA", "--to", "URC", "--date", FUTURE])
        self.assertEqual(code, 0)
        self.assertIn("不在已支持的 25 城清单", buf.getvalue())
        self.assertNotIn("Traceback", buf.getvalue())


class FiveSourceFamilyTest(unittest.TestCase):
    """PRD v1.3 第 5 节：票源家族清单与命名（去"模拟"术语）。"""

    FAMILY = [("airline", "航司直连"), ("ota", "OTA 比价"), ("douyin_groupbuy", "抖音团购"),
              ("douyin_miniapp", "抖音小程序·春秋航空"), ("douyin_live", "抖音直播间")]

    def test_catalog_matches_prd(self):
        catalog = source_catalog()
        self.assertEqual([(s["id"], s["name"]) for s in catalog], self.FAMILY)
        for entry in catalog:
            self.assertNotIn("模拟", entry["name"])
            self.assertNotIn("模拟", entry["description"])
            self.assertTrue(entry["description"], "每个票源需有一句话说明")

    def test_line_names_carry_no_mock_word(self):
        with pricing.fixed_minute(PINNED_MINUTE):
            offers, _failed, _unavailable = fr.collect(all_sources(), "SHA", "CTU", dt.date(2026, 11, 20))
        for offer in offers:
            self.assertNotIn("模拟", offer["source_name"], "行内票源名去术语（ISSUE-009 第 3 点）")

    def test_test_assets_preserved(self):
        # 不许顺手改红线：售罄券、缺退改券、不含税报价必须继续存在
        deals = DouyinGroupbuySource().deals()
        self.assertEqual(len(deals), 5, "DY-001~005 精选套餐保留")
        self.assertIn("售罄", [d["stock"] for d in deals])
        self.assertIn(None, [d.get("refund_change_rules") for d in deals],
                      "DY-005 缺退改规则的'未披露'测试资产必须保留")
        with pricing.fixed_minute(PINNED_MINUTE):
            offers, _failed, _unavailable = fr.collect(all_sources(), "SHA", "CTU", dt.date(2026, 11, 20))
        self.assertTrue(any(not o["tax_included"] for o in offers), "不含税报价资产必须存在")

    def test_live_flash_schedule_is_deterministic(self):
        live = DouyinLiveSource()
        schedule = live.flash_routes()
        self.assertEqual(len(schedule), 4)
        self.assertEqual(schedule, live.flash_routes(), "同一天场次固定（确定性）")
        for route in schedule:
            offers = live.search(*route, dt.date.today() + dt.timedelta(days=1))
            self.assertEqual(len(offers), 1)
            self.assertEqual(offers[0]["is_deal"], True)


class DemoFluctuationTest(unittest.TestCase):
    """PRD v1.3 第 7 节：分钟级演示波动——规则唯一来源的专门单测（PM 指定锁定项）。"""

    def test_same_minute_cli_and_web_identical(self):
        # 同一分钟内：CLI 表格里的价格集合与网页 API 载荷完全一致
        with pricing.fixed_minute(PINNED_MINUTE):
            buf = io.StringIO()
            with redirect_stdout(buf):
                fr.main(["search", "--from", "SHA", "--to", "CTU", "--date", "2026-11-20"])
            _, payload = webapp._api_search({"from": "SHA", "to": "CTU", "date": "2026-11-20"})
        cli_prices = {int(token[1:]) for token in buf.getvalue().split()
                      if token.startswith("¥") and token[1:].isdigit()}
        api_prices = {o["price"] for o in payload["offers"]}
        self.assertEqual(cli_prices, api_prices, "同一分钟内 CLI 与网页必须同价（规则唯一来源）")
        self.assertEqual(min(api_prices), payload["offers"][0]["price"])

    def test_cross_minute_visible_change_within_amplitude(self):
        base = pricing.route_base("SHA", "PEK")
        airline_prices = set()
        for offset in range(30):
            with pricing.fixed_minute(PINNED_MINUTE + offset):
                offers, _failed, _unavailable = fr.collect(all_sources(), "SHA", "PEK", dt.date(2026, 11, 3))
            for offer in offers:
                if offer["source_id"] == "airline" and offer["tax_included"]:
                    airline_prices.add(offer["price"])
        self.assertGreaterEqual(len(airline_prices), 3, "分钟之间必须可见变化")
        self.assertLessEqual(max(airline_prices) - min(airline_prices),
                             int(base * 0.07) + 2, "波动幅度必须小幅（±3.5% 设计上限）")
        self.assertGreater(min(airline_prices), 0, "波动不得产生 0 元/负数")


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
    """ISSUE-010 回归：票源覆盖模型——真实渠道不存在的航班不编价。"""

    OFFICIAL_MINIAPP_URL = ("https://www.iesdouyin.com/share/microapp/"
                            "?token=QjdDMUQzOTlCQkU2NzZqc2I2YTdt&share_channel=copy")

    def test_cgo_xiy_has_no_miniapp_quote(self):
        # 提交人复现场景：郑州→西安 2026-10-26，真实春秋小程序"暂无可销售座位"
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
                code = fr.main(["search", "--from", "CGO", "--to", "XIY",
                                "--date", "2026-10-26"])
        out = buf.getvalue()
        self.assertEqual(code, 0)
        self.assertIn("以下渠道本次无销售", out, "与'[提示] 暂不可用'降级提示区分")
        self.assertIn("抖音小程序·春秋航空", out)
        self.assertIn("可能未执飞", out)
        self.assertNotIn("春秋特价直飞", out, "无销售的渠道不得出现在报价行")

    def test_base_route_still_quotes_and_no_seat_day_deterministic(self):
        miniapp = [s for s in all_sources() if s.source_id == "douyin_miniapp"][0]
        # 基地航线（SHA 为一端）通常可出价
        available, _ = miniapp.availability("SHA", "CGO", dt.date(2026, 11, 2))
        self.assertTrue(available)
        # 无座日按日期哈希确定性复现，且频率约 1/7（35 天 3-7 天）
        no_seat = [d for d in (dt.date(2026, 11, 1) + dt.timedelta(days=i) for i in range(35))
                   if not miniapp.availability("SHA", "CGO", d)[0]]
        self.assertTrue(3 <= len(no_seat) <= 7, f"无座日频率应约 1/7，实际 {len(no_seat)}/35")
        probe = no_seat[0]
        self.assertEqual(miniapp.availability("SHA", "CGO", probe),
                         miniapp.availability("SHA", "CGO", probe), "同日期两次判定一致")
        with pricing.fixed_minute(PINNED_MINUTE):
            offers, _f, unavailable = fr.collect(all_sources(), "SHA", "CGO", probe)
        self.assertNotIn("douyin_miniapp", {o["source_id"] for o in offers})
        reasons = {u["source_id"]: u["reason"] for u in unavailable}
        self.assertIn("当日无座", reasons["douyin_miniapp"])

    def test_entry_url_contract(self):
        catalog = {s["id"]: s for s in source_catalog()}
        self.assertEqual(catalog["douyin_miniapp"]["entry_url"], self.OFFICIAL_MINIAPP_URL)
        self.assertEqual(catalog["douyin_miniapp"]["entry_note"], "春秋航空抖音小程序")
        self.assertEqual(catalog["douyin_groupbuy"]["entry_url"],
                         "https://v.douyin.com/dIviP77Fqow/")
        self.assertEqual(catalog["douyin_groupbuy"]["entry_note"],
                         "春秋航空 · 固定航线机票（抖音地点页）")
        self.assertEqual(catalog["douyin_live"]["entry_url"], "https://www.douyin.com/")
        self.assertEqual(catalog["airline"]["entry_url"], "", "多家航司无单一入口，允许为空")
        self.assertTrue(catalog["airline"]["entry_note"])
        self.assertIn("示例入口", catalog["ota"]["entry_note"])
        for entry in catalog.values():
            self.assertIn("entry_url", entry)
            self.assertIn("entry_note", entry)
        # 报价行携带所在渠道入口（引擎统一附上）
        with pricing.fixed_minute(PINNED_MINUTE):
            offers, _f, _u = fr.collect(all_sources(), "SHA", "CGO", dt.date(2026, 11, 2))
        for offer in offers:
            self.assertEqual(offer["entry_url"], catalog[offer["source_id"]]["entry_url"])


if __name__ == "__main__":
    unittest.main()
