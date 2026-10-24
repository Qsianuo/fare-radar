# -*- coding: utf-8 -*-
"""FareRadar v1.0 核心逻辑单测。

覆盖：多源合并排序、票源失败降级、日期/三字码校验、缺失字段兜底（未披露）、
提醒阈值命中、alerts 读写、参数错误退出码。
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
from sources import all_sources  # noqa: E402
from sources.douyin_groupbuy import DouyinGroupbuySource  # noqa: E402

FUTURE = (dt.date.today() + dt.timedelta(days=30)).isoformat()


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
        offers, failed = fr.collect(all_sources(), "SHA", "CTU", dt.date(2026, 11, 15))
        self.assertEqual(failed, [])
        source_ids = {o["source_id"] for o in offers}
        self.assertIn("simulated_airline", source_ids)
        self.assertIn("douyin_groupbuy", source_ids)

    def test_rank_offers_comparable_first_then_price(self):
        offers, _ = fr.collect(all_sources(), "SHA", "CTU", dt.date(2026, 11, 15))
        offers = fr.rank_offers(offers, dt.date(2026, 11, 15))
        eligible = sorted(o["price"] for o in offers if o["tax_included"])
        ineligible = sorted(o["price"] for o in offers if not o["tax_included"])
        self.assertEqual([o["price"] for o in offers], eligible + ineligible)
        self.assertTrue(any(o["is_deal"] for o in offers), "合并结果应含团购行")

    def test_search_output_contract(self):
        args = argparse.Namespace(from_code="SHA", to_code="CTU", date=FUTURE, source=None)
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = fr.cmd_search(args)
        out = buf.getvalue()
        self.assertEqual(code, 0)
        self.assertIn("[模拟数据]", out.splitlines()[0])
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
        buf = io.StringIO()
        with redirect_stdout(buf):
            fr.main(["search", "--from", "SHA", "--to", "PEK", "--date", "2026-11-03"])
        lines = buf.getvalue().splitlines()
        self.assertTrue(any("CZ3908" in ln and "税费另计" in ln for ln in lines),
                        "不含税行必须含'税费另计'披露")
        markers = [i for i, ln in enumerate(lines) if "◀ 最低价" in ln]
        self.assertEqual(len(markers), 1)
        self.assertIn("¥890", lines[markers[0] - 1],
                      "标记落在含税可比行（¥649 券因有效期不覆盖退出竞争，见 ISSUE-003）")

    def test_kix_case_marker_on_comparable_row(self):
        # ISSUE-005 并入回归：SHA→KIX 2026-12-20，¥1009 不含税不得标最低价
        buf = io.StringIO()
        with redirect_stdout(buf):
            fr.main(["search", "--from", "SHA", "--to", "KIX", "--date", "2026-12-20"])
        lines = buf.getvalue().splitlines()
        marker = next(i for i, ln in enumerate(lines) if "◀ 最低价" in ln)
        self.assertIn("¥1099", lines[marker - 1], "标记应落在含税团购行而非不含税行")
        self.assertTrue(any("9C8999" in ln and "税费另计" in ln for ln in lines))


class SoldOutEligibilityTest(unittest.TestCase):
    """ISSUE-004 回归：售罄券不参与"◀ 最低价"竞争，展示必含"已售罄"。"""

    def test_sold_out_deal_not_marked_and_disclosed(self):
        # 提交人复现场景：PEK→SHA 2026-11-10（DY-004 售罄；MU5102 不含税）
        buf = io.StringIO()
        with redirect_stdout(buf):
            fr.main(["search", "--from", "PEK", "--to", "SHA", "--date", "2026-11-10"])
        out = buf.getvalue()
        self.assertNotIn("◀ 最低价", out, "售罄券与不含税行均不得取得标记")
        self.assertIn("已售罄", out)
        self.assertIn("税费另计", out)
        self.assertIn("没有口径可比的最低价", out)

    def test_sold_out_check_in_eligibility(self):
        offer = {"price": 599, "tax_included": True, "is_deal": True, "stock": "售罄",
                 "valid_from": "2026-11-01", "valid_until": "2026-11-15"}
        self.assertFalse(fr.eligible_for_lowest(offer, dt.date(2026, 11, 10)))
        offer["stock"] = "充足"
        self.assertTrue(fr.eligible_for_lowest(offer, dt.date(2026, 11, 10)))


class ValidityEligibilityTest(unittest.TestCase):
    """ISSUE-003 回归：券有效期＝可出行窗口，不覆盖出发日期不得标最低价、不得催"出手"。"""

    def test_not_covered_deal_excluded_and_no_alert(self):
        # 提交人复现场景：SHA→PEK 2026-11-03，DY-002 有效期 11-05 起，底价 700
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "alerts.json"
            fr.save_alerts([{"origin": "SHA", "destination": "PEK", "threshold": 700,
                             "created_at": "2026-10-24"}], path)
            old = fr.ALERTS_FILE
            fr.ALERTS_FILE = path
            try:
                buf = io.StringIO()
                with redirect_stdout(buf):
                    fr.main(["search", "--from", "SHA", "--to", "PEK", "--date", "2026-11-03"])
            finally:
                fr.ALERTS_FILE = old
        out = buf.getvalue()
        self.assertNotIn("可以出手了", out, "用不上的券不得触发行动建议")
        self.assertTrue(any("有效期不覆盖" in ln and "7日有效券" in ln
                            for ln in out.splitlines()), "不覆盖信息必须可见")
        lines = out.splitlines()
        marker = next(i for i, ln in enumerate(lines) if "◀ 最低价" in ln)
        self.assertIn("¥890", lines[marker - 1], "最低价落到含税航司行")

    def test_covered_deal_still_lowest(self):
        # 回归：有效期覆盖出发日期的券行为不变（DY-005 覆盖 2026-11-20）
        buf = io.StringIO()
        with redirect_stdout(buf):
            fr.main(["search", "--from", "SHA", "--to", "CTU", "--date", "2026-11-20"])
        lines = buf.getvalue().splitlines()
        marker = next(i for i, ln in enumerate(lines) if "◀ 最低价" in ln)
        self.assertIn("¥459", lines[marker - 1])

    def test_unknown_validity_treated_as_not_covered(self):
        offer = {"price": 100, "tax_included": True, "is_deal": True}  # 缺有效期字段
        self.assertFalse(fr.eligible_for_lowest(offer, dt.date(2026, 11, 20)))
        self.assertIn("有效期无法确认", fr.search_note(offer, dt.date(2026, 11, 20)))


class SourceFailureTest(unittest.TestCase):
    def test_broken_source_degrades(self):
        offers, failed = fr.collect(all_sources() + [_BrokenSource()], "SHA", "PEK",
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
        self.assertIn("退改规则：未披露", card)
        self.assertNotIn("退改规则：\n", card)

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
            self.assertNotIn("已保存关注", buf.getvalue())
            self.assertEqual(path.read_text(encoding="utf-8"), original, "损坏文件必须原样保留")


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


if __name__ == "__main__":
    unittest.main()
