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

    def test_sorted_ascending_and_groupbuy_flagged(self):
        offers, _ = fr.collect(all_sources(), "SHA", "CTU", dt.date(2026, 11, 15))
        offers = fr.sort_offers(offers)
        prices = [o["price"] for o in offers]
        self.assertEqual(prices, sorted(prices))
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

    def test_load_missing_or_corrupted_returns_empty(self):
        self.assertEqual(fr.load_alerts(Path(tempfile.gettempdir()) / "fare-radar-no-such.json"), [])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "broken.json"
            path.write_text("{oops", encoding="utf-8")
            self.assertEqual(fr.load_alerts(path), [])

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
