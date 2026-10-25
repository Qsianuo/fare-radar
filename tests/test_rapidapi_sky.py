# -*- coding: utf-8 -*-
"""RapidAPI·Google Flights 适配器离线单测（全 mock 网络，零真实请求）。

覆盖：未配 Key 不接入、真实结构映射（航班号/时刻/机场英文全称/含税价）、
无价格行程丢弃、缓存命中零请求、日/月双重配额、429 即停。
"""

import datetime as dt
import json
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from sources.rapidapi_sky import (RapidapiFlightSource,  # noqa: E402
                                  DAILY_CALL_CAP, MONTHLY_CALL_CAP)

DATE = dt.date(2026, 11, 5)

_SEARCH = {"status": 200, "data": {
    "price_insights": {"lowest_price": 400, "typical_price_range": [440, 680]},
    "best_flights": [],
    "other_flights": [
        {"price": 400, "currency": "CNY", "stops": 0, "total_duration": 150,
         "legs": [{"airline_code": "9C", "flight_number": "9C 8835", "airline": "Spring",
                   "airplane": "Airbus A321", "overnight": False, "travel_class": "Economy",
                   "departure_airport": {"code": "SHA", "name": "Shanghai Hongqiao International Airport",
                                          "time": "2026-11-05 07:20"},
                   "arrival_airport": {"code": "CAN", "name": "Guangzhou Baiyun International Airport",
                                       "time": "2026-11-05 09:50"}}]},
        {"price": 530, "currency": "CNY", "stops": 1, "total_duration": 300,
         "legs": [
             {"airline_code": "MU", "flight_number": "MU 5101",
              "departure_airport": {"code": "SHA", "name": "Shanghai Hongqiao International Airport",
                                     "time": "2026-11-05 10:00"},
              "arrival_airport": {"code": "CSX", "name": "Changsha Huanghua International Airport",
                                  "time": "2026-11-05 12:15"}},
             {"airline_code": "CZ", "flight_number": "CZ 3375",
              "departure_airport": {"code": "CSX", "name": "Changsha Huanghua International Airport",
                                    "time": "2026-11-05 13:30"},
              "arrival_airport": {"code": "CAN", "name": "Guangzhou Baiyun International Airport",
                                  "time": "2026-11-05 14:40"}}]},
        {"price": None, "legs": []},  # 无价格/无段 → 丢弃不编造
    ]}}


class _Resp:
    def __init__(self, payload):
        self._payload = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeOpener:
    def __init__(self, fail_with=None, payload=None):
        self.calls = []
        self.fail_with = fail_with
        self.payload = _SEARCH if payload is None else payload

    def open(self, request, timeout=0):
        self.calls.append(request.full_url)
        if self.fail_with is not None:
            raise self.fail_with
        return _Resp(self.payload)


class RapidapiGoogleFlightsTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)

    def _source(self, with_key=True):
        cfg = self.dir / "rapidapi_config.json"
        if with_key:
            cfg.write_text(json.dumps({"api_key": "K",
                                       "host": "google-flights-search-prices.p.rapidapi.com",
                                       "proxy": ""}), encoding="utf-8")
        return RapidapiFlightSource(config_path=cfg)

    def _patched(self, source, opener):
        import unittest.mock
        return unittest.mock.patch("urllib.request.build_opener", lambda *a, **k: opener)

    def test_no_key_not_available(self):
        source = self._source(with_key=False)
        self.assertEqual(source.status, "not_available")
        available, reason = source.availability("SHA", "CAN", DATE)
        self.assertFalse(available)
        self.assertIn("未配置 API Key", reason)
        self.assertEqual(source.search("SHA", "CAN", DATE), [])

    def test_mapping_from_real_structure(self):
        opener = _FakeOpener()
        source = self._source()
        with self._patched(source, opener):
            rows = source.search("SHA", "CAN", DATE)
        self.assertEqual(len(rows), 2, "无价格行程必须丢弃")
        direct, transfer = rows
        self.assertEqual(direct["flight_no"], "9C8835")
        self.assertEqual(direct["depart_time"], "07:20")
        self.assertEqual(direct["arrive_time"], "09:50")
        self.assertEqual(direct["price"], 400)
        self.assertEqual(direct["total_estimate"], 400)
        self.assertIn("Hongqiao", direct["dep_airport"])
        self.assertIn("Baiyun", direct["arr_airport"])
        self.assertIn("直飞", direct["product"])
        self.assertFalse(direct["cross_day"])
        # 中转：两段航班号拼接 + 总时长
        self.assertEqual(transfer["flight_no"], "MU5101/CZ3375")
        self.assertIn("中转1站", transfer["product"])
        self.assertEqual(transfer["duration_text"], "5小时00分")
        # IATA 直查：单次请求，无城市映射调用
        self.assertEqual(len(opener.calls), 1)
        self.assertIn("departure_id=SHA", opener.calls[0])

    def test_cache_hit_zero_calls(self):
        opener = _FakeOpener()
        source = self._source()
        with self._patched(source, opener):
            source.search("SHA", "CAN", DATE)
            source.search("SHA", "CAN", DATE)  # 6 小时缓存内：零新增请求
        self.assertEqual(len(opener.calls), 1)

    def test_daily_and_monthly_caps(self):
        source = self._source()
        source._cache_dir.mkdir()
        source._cache_write("day_counter.json",
                            {"date": dt.date.today().isoformat(), "calls": DAILY_CALL_CAP})
        with self.assertRaises(RuntimeError) as ctx:
            source._fetch("SHA", "CAN", DATE)
        self.assertIn("当日", str(ctx.exception))
        source._cache_write("day_counter.json",
                            {"date": dt.date.today().isoformat(), "calls": 0})
        source._cache_write("month_counter.json",
                            {"month": dt.date.today().isoformat()[:7],
                             "calls": MONTHLY_CALL_CAP})
        with self.assertRaises(RuntimeError) as ctx:
            source._fetch("SHA", "CAN", DATE)
        self.assertIn("当月", str(ctx.exception))

    def test_429_stops_without_retry(self):
        opener = _FakeOpener(fail_with=urllib.error.HTTPError(
            "u", 429, "Too Many Requests", None, None))
        source = self._source()
        with self._patched(source, opener):
            with self.assertRaises(RuntimeError) as ctx:
                source.search("SHA", "CAN", DATE)
        self.assertIn("429", str(ctx.exception))
        self.assertEqual(len(opener.calls), 1, "429 后不得重试")


    # ---------- 价格洞察 price_insights（v1.10.0） ----------

    def test_insights_captured_and_cached(self):
        """price_insights 随比价结果一并落缓存；未提供的字段不猜。"""
        opener = _FakeOpener()
        source = self._source()
        with self._patched(source, opener):
            source.search("SHA", "CAN", DATE)
        pi = source.insights("SHA", "CAN", DATE)
        self.assertIsNotNone(pi)
        self.assertEqual(pi["lowest_price"], 400)
        self.assertEqual(pi["typical_price_range"], [440, 680])
        self.assertEqual(pi["currency"], "CNY")
        self.assertIsNone(pi["price_level"], "样本无 price_level → 不得编造等级")
        self.assertEqual(pi["history"], [])
        self.assertEqual(pi["source_id"], "rapidapi_sky")
        self.assertIn("同口径", pi["basis"])

    def test_insights_cache_only_never_calls_api(self):
        """缓存缺失时返回 None，绝不为了行情额外烧配额。"""
        source = self._source()
        opener = _FakeOpener()
        with self._patched(source, opener):
            self.assertIsNone(source.insights("SHA", "CTU", DATE))
        self.assertEqual(opener.calls, [], "insights() 不得发起任何请求")

    def test_insights_old_cache_without_field(self):
        """v1.9 及更早的缓存没有 insights 字段 → 优雅返回 None。"""
        source = self._source()
        source._cache_dir.mkdir()
        source._cache_write(f"offers_SHA_CAN_{DATE.isoformat()}.json",
                            {"expire_at": 9e18, "rows": []})
        self.assertIsNone(source.insights("SHA", "CAN", DATE))

    def test_insights_empty_payload_returns_none(self):
        """零编造：没有最低价也没有常规区间，就没有可展示的内容。"""
        source = self._source()
        self.assertIsNone(source._to_insights(None))
        self.assertIsNone(source._to_insights({}))
        self.assertIsNone(source._to_insights({"price_level": "low"}))
        # 只有区间没有最低价：仍是可用信息，保留
        only_range = source._to_insights({"typical_price_range": [440, 680]})
        self.assertIsNotNone(only_range)
        self.assertIsNone(only_range["lowest_price"])

    def test_insights_unknown_level_survives(self):
        """未知 price_level 值原样保留，渲染层不猜中文（前端映射不到就不显示）。"""
        source = self._source()
        pi = source._to_insights({"lowest_price": 630, "price_level": "weird",
                                 "typical_price_range": [440, 680],
                                 "price_history": [{"date": "2026-10-01", "price": 490},
                                                   {"date": "2026-10-02", "price": "bad"},
                                                   {"date": "2026-10-03", "price": 630.4}]})
        self.assertEqual(pi["price_level"], "weird")
        self.assertEqual(pi["history"], [{"date": "2026-10-01", "price": 490},
                                         {"date": "2026-10-03", "price": 630}],
                         "无价格/脏数据的历史点丢弃，不编造")


    # ---------- 响应体 status 校验与空结果缓存（v1.10.1，ISSUE-017） ----------

    def _body(self, status, description="", flights=None, insights=None):
        return {"status": status, "description": description,
                "data": {"search": {"departure_id": "INC", "arrival_id": "CTU"},
                         "google_flights_url": "https://www.google.com/travel/flights",
                         "price_insights": insights or {},
                         "best_flights": flights or [], "other_flights": []}}

    def test_body_status_204_is_no_data_not_error(self):
        """实测：上游用 HTTP 200 + body status 204 表达"provider 无数据"。"""
        opener = _FakeOpener(payload=self._body(204, "No content!"))
        source = self._source()
        with self._patched(source, opener):
            rows = source.search("INC", "CTU", DATE)
        self.assertEqual(rows, [], "204 是无数据，不是异常")
        note = source.empty_note("INC", "CTU", DATE)
        self.assertIn("无该航线数据", note)
        self.assertIn("204", note)

    def test_body_status_429_is_raised_not_swallowed(self):
        """HTTP 200 + body 429 必须抛出，不能被当成"无报价"。"""
        opener = _FakeOpener(payload=self._body(429, "Too many requests"))
        source = self._source()
        with self._patched(source, opener):
            with self.assertRaises(RuntimeError) as ctx:
                source.search("INC", "CTU", DATE)
        self.assertIn("429", str(ctx.exception))
        self.assertIsNone(source.insights("INC", "CTU", DATE), "失败不落缓存")

    def test_body_status_5xx_carries_description(self):
        opener = _FakeOpener(payload=self._body(503, "provider down"))
        source = self._source()
        with self._patched(source, opener):
            with self.assertRaises(RuntimeError) as ctx:
                source.search("INC", "CTU", DATE)
        self.assertIn("503", str(ctx.exception))
        self.assertIn("provider down", str(ctx.exception))

    def test_empty_result_uses_short_cache(self):
        """空结果只缓存 30 分钟，有结果才 6 小时。"""
        import time
        opener = _FakeOpener(payload=self._body(204))
        source = self._source()
        with self._patched(source, opener):
            source.search("INC", "CTU", DATE)
        cached = source._cache_read(f"offers_INC_CTU_{DATE.isoformat()}.json")
        self.assertLessEqual(cached["expire_at"] - time.time(), 31 * 60)
        self.assertEqual(cached["body_status"], 204)

        opener2 = _FakeOpener()
        with self._patched(source, opener2):
            source.search("SHA", "CAN", DATE)
        cached2 = source._cache_read(f"offers_SHA_CAN_{DATE.isoformat()}.json")
        self.assertGreater(cached2["expire_at"] - time.time(), 5 * 3600)

    def test_empty_note_none_when_has_rows_or_no_cache(self):
        source = self._source()
        self.assertIsNone(source.empty_note("SHA", "CTU", DATE), "无缓存不猜原因")
        opener = _FakeOpener()
        with self._patched(source, opener):
            source.search("SHA", "CAN", DATE)
        self.assertIsNone(source.empty_note("SHA", "CAN", DATE), "有报价就不该有原因说明")


    def test_body_status_200_empty_differs_from_204(self):
        """200 + 0 条 ≠ 204：前者是"查询成功但无可售行程"，必须与"无此航线"区分。"""
        opener = _FakeOpener(payload=self._body(200, "OK"))
        source = self._source()
        with self._patched(source, opener):
            rows = source.search("INC", "PEK", DATE)
        self.assertEqual(rows, [])
        note = source.empty_note("INC", "PEK", DATE)
        self.assertIn("未返回可售行程", note)
        self.assertNotIn("204", note)


if __name__ == "__main__":
    unittest.main()
