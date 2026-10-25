# -*- coding: utf-8 -*-
"""Amadeus 适配器离线单测（全部 mock 网络，零真实请求——调用纪律红线）。

覆盖：未配 Key 不接入、Key 激活后映射正确、Token/结果缓存命中、
当日自我上限、429 即停不重试。
"""

import datetime as dt
import json
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from sources.amadeus import AmadeusSource, DAILY_CALL_CAP  # noqa: E402

DATE = dt.date(2026, 11, 1)

_TOKEN_JSON = {"access_token": "T0", "expires_in": 1700}
_OFFERS_JSON = {"data": [{
    "itineraries": [{"duration": "PT3H10M", "segments": [{
        "carrierCode": "HO", "number": "1251",
        "departure": {"at": "2026-11-01T07:40:00", "iataCode": "INC"},
        "arrival": {"at": "2026-11-01T10:50:00", "iataCode": "CAN"}}]}],
    "price": {"grandTotal": "640.00"},
}]}


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
    """按 URL 前缀返回脚本化响应并记录全部请求（测试断言调用次数与纪律）。"""

    def __init__(self, fail_with=None):
        self.calls = []
        self.fail_with = fail_with

    def open(self, request, timeout=0):
        self.calls.append(request.full_url)
        if self.fail_with is not None:
            raise self.fail_with
        url = request.full_url
        if "oauth2/token" in url:
            return _Resp(_TOKEN_JSON)
        if "flight-offers" in url:
            return _Resp(_OFFERS_JSON)
        raise AssertionError(f"未预期的请求: {url}")


class AmadeusSourceTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)

    def _source(self, with_key=True, opener=None):
        cfg = self.dir / "amadeus_config.json"
        if with_key:
            cfg.write_text(json.dumps(
                {"client_id": "id", "client_secret": "sec",
                 "env": "test", "proxy": ""}), encoding="utf-8")
        source = AmadeusSource(config_path=cfg)
        if opener is not None:
            source._opener = lambda: opener
        return source

    def test_no_key_not_available(self):
        source = self._source(with_key=False)
        self.assertFalse(source.has_key)
        self.assertEqual(source.status, "not_available")
        available, reason = source.availability("INC", "CAN", DATE)
        self.assertFalse(available)
        self.assertIn("未配置 API Key", reason)
        self.assertEqual(source.search("INC", "CAN", DATE), [])

    def test_key_active_and_mapping(self):
        opener = _FakeOpener()
        source = self._source(opener=opener)
        self.assertEqual(source.status, "active")
        rows = source.search("INC", "CAN", DATE)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["flight_no"], "HO1251")
        self.assertEqual(row["depart_time"], "07:40")
        self.assertEqual(row["arrive_time"], "10:50")
        self.assertFalse(row["cross_day"])
        self.assertEqual(row["price"], 640)
        self.assertEqual(row["total_estimate"], 640)
        self.assertIn("真实接口", row["note"])
        # 首次查询恰好 2 次网络调用（token + offers），无多余请求
        self.assertEqual(len(opener.calls), 2)

    def test_offer_and_token_cache_hit(self):
        opener = _FakeOpener()
        source = self._source(opener=opener)
        source.search("INC", "CAN", DATE)
        source.search("INC", "CAN", DATE)  # 30 分钟缓存内：零新增请求
        self.assertEqual(len(opener.calls), 2)

    def test_daily_cap_blocks(self):
        source = self._source()
        source._cache_dir.mkdir()
        source._cache_write("day_counter.json",
                            {"date": dt.date.today().isoformat(), "calls": DAILY_CALL_CAP})
        with self.assertRaises(RuntimeError) as ctx:
            source._token()
        self.assertIn("自我上限", str(ctx.exception))

    def test_429_stops_without_retry(self):
        opener = _FakeOpener(fail_with=urllib.error.HTTPError(
            "u", 429, "Too Many Requests", None, None))
        source = self._source(opener=opener)
        source._cache_write("token.json", {"access_token": "T", "expire_at": 9e9})
        with self.assertRaises(RuntimeError) as ctx:
            source.search("INC", "CAN", DATE)
        self.assertIn("429", str(ctx.exception))
        self.assertEqual(len(opener.calls), 1, "429 后不得自动重试")


if __name__ == "__main__":
    unittest.main()
