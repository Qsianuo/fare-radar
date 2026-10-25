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
    def __init__(self, fail_with=None):
        self.calls = []
        self.fail_with = fail_with

    def open(self, request, timeout=0):
        self.calls.append(request.full_url)
        if self.fail_with is not None:
            raise self.fail_with
        return _Resp(_SEARCH)


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


if __name__ == "__main__":
    unittest.main()
