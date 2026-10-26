# -*- coding: utf-8 -*-
"""聚合数据适配器单测（v1.15.0，ISSUE-024）——**零真实请求**。

本模块不联网：映射测试直接喂 tests/fixtures 里的**真实响应样本**（2026-10-26 实测
INC→SZX 2026-10-28），网络路径用 mock 替换 `_fetch`。之所以强调：聚合数据是
**按次计费**接口，试用额度只有 3 次；测试一旦真的发请求就是烧钱（ISSUE-020 的教训）。
"""

import datetime as dt
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sources import juhe  # noqa: E402

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "juhe_flight_query_sample.json"


def _load_flights():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))["result"]["flightInfo"]


def _flight(flight_no):
    for item in _load_flights():
        if item["flightNo"] == flight_no:
            return item
    raise AssertionError(f"样本里没有 {flight_no}")


class _TmpSourceCase(unittest.TestCase):
    """每个用例独立的临时配置目录，避免 quota 计数器互相污染。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.cfg = Path(self._tmp.name) / "juhe_config.json"

    def tearDown(self):
        self._tmp.cleanup()

    def _source(self, api_key="test-key-not-real", **extra):
        payload = {"api_key": api_key}
        payload.update(extra)
        self.cfg.write_text(json.dumps(payload), encoding="utf-8")
        return juhe.JuheFlightSource(config_path=self.cfg)


class MappingTest(_TmpSourceCase):
    def test_direct_flight_maps_completely(self):
        row = self._source()._to_offer(_flight("BK3037"))
        self.assertEqual(row["product"], "直飞｜奥凯｜738")
        self.assertEqual(row["flight_no"], "BK3037")
        self.assertEqual(row["carrier_code"], "BK")
        self.assertEqual(row["carrier_name"], "奥凯")
        self.assertEqual(row["depart_time"], "11:30")
        self.assertEqual(row["arrive_time"], "15:00")
        self.assertFalse(row["cross_day"])
        self.assertEqual(row["duration_text"], "3小时30分")
        self.assertEqual(row["dep_airport"], "河东机场（INC）")
        self.assertEqual(row["arr_airport"], "宝安国际机场（SZX）")
        self.assertEqual(row["price"], 918)
        self.assertIsNone(row["price_note"])

    def test_upstream_mistranslation_never_shown(self):
        """BK 的上游 airlineName 是 "Okay航空公司"（机翻），不得出现在任何字段。"""
        row = self._source()._to_offer(_flight("BK3037"))
        blob = json.dumps(row, ensure_ascii=False)
        self.assertNotIn("Okay", blob)
        self.assertNotIn("快运", blob)

    def test_transfer_num_is_segment_count_not_stop_count(self):
        """transferNum=1 的直飞航班不得被标成"中转 1 次"（实测坑）。"""
        row = self._source()._to_offer(_flight("BK3037"))
        self.assertIn("直飞", row["product"])
        self.assertNotIn("中转", row["product"])

    def test_multi_leg_and_cross_day(self):
        row = self._source()._to_offer(_flight("G54338 | PN6229"))
        self.assertIn("2 段联程", row["product"])
        self.assertIn("中转城市未披露", row["product"])
        self.assertEqual(row["carrier_name"], "华夏")          # G5，不是"中国快运"
        self.assertEqual(row["flight_no"], "G54338/PN6229")     # 竖线规范成斜杠
        self.assertTrue(row["cross_day"])                       # 10-28 → 10-29
        self.assertEqual(row["duration_text"], "11小时00分")

    def test_code_share_kept_as_is(self):
        row = self._source()._to_offer(_flight("CA3496(ZH9234)"))
        self.assertEqual(row["carrier_code"], "CA")
        self.assertEqual(row["carrier_name"], "国航")
        self.assertEqual(row["flight_no"], "CA3496(ZH9234)")

    def test_missing_time_is_dropped(self):
        src = self._source()
        for patch in ({"departureTime": ""}, {"arrivalTime": ""}, {"flightNo": ""}):
            self.assertIsNone(src._to_offer(dict(_flight("BK3037"), **patch)))

    def test_bad_price_kept_but_marked(self):
        """v1.14 口径：无价航班保留展示，price=None + 明确说明，绝不填 0。"""
        src = self._source()
        for bad in (None, 0, "918", -5):
            row = src._to_offer(dict(_flight("BK3037"), ticketPrice=bad))
            self.assertIsNotNone(row)
            self.assertIsNone(row["price"])
            self.assertIsNone(row["tax_included"])
            self.assertIn("上游未提供该航班报价", row["price_note"])
            self.assertIn("价格未披露", row["note"])

    def test_tax_not_assumed_included(self):
        """上游未明示含税 → tax_included=False，由引擎补费用明细（不声称含税）。"""
        row = self._source()._to_offer(_flight("BK3037"))
        self.assertFalse(row["tax_included"])
        self.assertIsNone(row["total_estimate"])
        self.assertIn("未明示", row["note"])


class DurationParseTest(unittest.TestCase):
    def test_known_formats(self):
        cases = {"3h30m": "3小时30分", "11h00m": "11小时00分", "0h45m": "0小时45分"}
        for raw, want in cases.items():
            self.assertEqual(juhe.JuheFlightSource._duration_text(raw), want)

    def test_unknown_formats_not_guessed(self):
        for raw in ("", None, "abc", "3小时", "3h70m", "-1h10m"):
            self.assertEqual(juhe.JuheFlightSource._duration_text(raw), "未披露")


class GateTest(_TmpSourceCase):
    def test_no_key_means_not_available_and_no_request(self):
        src = juhe.JuheFlightSource(config_path=Path(self._tmp.name) / "absent.json")
        self.assertEqual(src.status, "not_available")
        self.assertIn("未配置 API Key", src.status_reason)
        available, reason = src.availability("INC", "SZX", dt.date(2026, 10, 28))
        self.assertFalse(available)
        self.assertIn("未配置 API Key", reason)
        with mock.patch.object(src, "_fetch") as fetch:
            self.assertEqual(src.search("INC", "SZX", dt.date(2026, 10, 28)), [])
            fetch.assert_not_called()

    def test_offline_env_short_circuits(self):
        src = self._source()
        with mock.patch.dict("os.environ", {"FARE_RADAR_OFFLINE": "1"}):
            self.assertEqual(src.status, "not_available")
            available, _ = src.availability("INC", "SZX", dt.date(2026, 10, 28))
            self.assertFalse(available)
            with mock.patch.object(src, "_fetch") as fetch:
                self.assertEqual(src.search("INC", "SZX", dt.date(2026, 10, 28)), [])
                fetch.assert_not_called()

    def test_daily_cap_hard_stop(self):
        src = self._source()
        src._cache_write("day_counter.json",
                         {"date": dt.date.today().isoformat(), "calls": juhe.DAILY_CALL_CAP})
        with self.assertRaises(RuntimeError) as ctx:
            src._quota_guard()
        self.assertIn("当日调用已达自我上限", str(ctx.exception))

    def test_monthly_cap_hard_stop(self):
        src = self._source()
        src._cache_write("month_counter.json",
                         {"month": dt.date.today().isoformat()[:7], "calls": juhe.MONTHLY_CALL_CAP})
        with self.assertRaises(RuntimeError) as ctx:
            src._quota_guard()
        self.assertIn("当月调用已达自我上限", str(ctx.exception))
        self.assertIn("不会自动充值", str(ctx.exception))

    def test_counter_increments_once_per_call(self):
        src = self._source()
        src._quota_guard()
        state = src.quota_state()
        self.assertEqual((state["day_used"], state["month_used"]), (1, 1))


class SearchTest(_TmpSourceCase):
    def test_search_maps_all_rows_and_caches(self):
        src = self._source()
        payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
        with mock.patch.object(src, "_fetch", return_value=payload) as fetch:
            rows = src.search("INC", "SZX", dt.date(2026, 10, 28))
            again = src.search("INC", "SZX", dt.date(2026, 10, 28))
        self.assertEqual(len(rows), 5)
        self.assertEqual(fetch.call_count, 1, "第二次必须命中缓存，不得再请求")
        self.assertEqual(len(again), 5)
        self.assertTrue(all(r["price"] for r in rows))

    def test_error_code_raises_chinese_reason(self):
        src = self._source()
        for code, keyword in ((10001, "Key 无效"), (10012, "次数已用尽"), (10022, "地址不存在")):
            with mock.patch.object(src, "_fetch",
                                   side_effect=RuntimeError(f"聚合数据返回错误 {code}")):
                with self.assertRaises(RuntimeError):
                    src.search("INC", "SZX", dt.date(2026, 10, 28))

    def test_empty_result_note(self):
        src = self._source()
        payload = {"error_code": 0, "reason": "成功", "result": {"orderid": "x", "flightInfo": []}}
        with mock.patch.object(src, "_fetch", return_value=payload):
            rows = src.search("INC", "CTU", dt.date(2026, 10, 28))
        self.assertEqual(rows, [])
        note = src.empty_note("INC", "CTU", dt.date(2026, 10, 28))
        self.assertIn("无航班记录", note)

    def test_empty_note_silent_when_rows_exist(self):
        src = self._source()
        payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
        with mock.patch.object(src, "_fetch", return_value=payload):
            src.search("INC", "SZX", dt.date(2026, 10, 28))
        self.assertIsNone(src.empty_note("INC", "SZX", dt.date(2026, 10, 28)))


class RegistryTest(unittest.TestCase):
    def test_registered_in_all_sources(self):
        from sources import all_sources, source_by_id
        self.assertIsNotNone(source_by_id("juhe_flight"))
        ids = [s.source_id for s in all_sources()]
        self.assertIn("juhe_flight", ids)

    def test_offer_channel_is_true(self):
        """本渠道产出比价行（与 travelpayouts 的旁路渠道不同）。"""
        self.assertTrue(juhe.JuheFlightSource.offer_channel)


if __name__ == "__main__":
    unittest.main()
