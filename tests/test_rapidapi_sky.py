# -*- coding: utf-8 -*-
"""RapidAPI 适配器离线单测（全 mock 网络，零真实请求）。

覆盖：未配 Key 不接入、Key 激活与映射、entityId 永久缓存、结果缓存命中、
当日自我上限、429 即停、无价格行程丢弃（不编造）。
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

from sources.rapidapi_sky import RapidapiFlightSource, DAILY_CALL_CAP  # noqa: E402

DATE = dt.date(2026, 11, 5)

_AUTOCOMPLETE = {"data": [{"iata": "SHA", "navigation": {"entityId": "27537508"}}]}
_SEARCH = {"data": {"itineraries": [{
    "pricingOptions": [{"price": {"amount": "559.40"}}],
    "legs": [{"durationInMinutes": 190, "segments": [{
        "marketingCarrier": {"alternateId": "HO"},
        "flightNumber": "1251",
        "origin": {"displayCode": "SHA"}, "destination": {"displayCode": "CAN"},
        "departure": "2026-11-05T07:40:00", "arrival": "2026-11-05T10:50:00"}]}],
}, {
    "pricingOptions": [],   # 无价格 → 必须丢弃，不编造
    "legs": [{"segments": [{"marketingCarrier": {"alternateId": "CZ"}, "flightNumber": "1"}]}],
}]}}


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
    def __init__(self, routes, fail_with=None):
        self.routes = routes
        self.calls = []
        self.fail_with = fail_with

    def open(self, request, timeout=0):
        self.calls.append(request.full_url)
        if self.fail_with is not None:
            raise self.fail_with
        url = request.full_url
        for fragment, payload in self.routes:
            if fragment in url:
                return _Resp(payload)
        raise AssertionError(f"未预期的请求: {url}")


class RapidapiSourceTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)

    def _source(self, with_key=True, opener=None):
        cfg = self.dir / "rapidapi_config.json"
        if with_key:
            cfg.write_text(json.dumps({"api_key": "K", "host": "flights-sky.p.rapidapi.com",
                                       "proxy": ""}), encoding="utf-8")
        source = RapidapiFlightSource(config_path=cfg)
        if opener is not None:
            original = source._get
            def patched(path, params):
                import sources.rapidapi_sky as mod
                with unittest.mock.patch.object(urllib.request, "build_opener",
                                                lambda *a, **k: opener):
                    return original(path, params)
            source._get = patched
        return source

    def test_no_key_not_available(self):
        source = self._source(with_key=False)
        self.assertEqual(source.status, "not_available")
        available, reason = source.availability("SHA", "CAN", DATE)
        self.assertFalse(available)
        self.assertIn("未配置 API Key", reason)
        self.assertEqual(source.search("SHA", "CAN", DATE), [])

    def test_key_active_mapping_and_dedup_of_unpriced(self):
        import unittest.mock
        opener = _FakeOpener([("auto-complete", _AUTOCOMPLETE),
                              ("search-one-way", _SEARCH)])
        source = self._source(opener=None)
        # 手动注入 opener 到 _get 的调用路径
        with unittest.mock.patch("urllib.request.build_opener", lambda *a, **k: opener):
            rows = source.search("SHA", "CAN", DATE)
        self.assertEqual(source.status, "active")
        self.assertEqual(len(rows), 1, "无价格行程必须丢弃")
        row = rows[0]
        self.assertEqual(row["flight_no"], "HO1251")
        self.assertEqual(row["depart_time"], "07:40")
        self.assertEqual(row["arrive_time"], "10:50")
        self.assertEqual(row["price"], 559)
        self.assertIn("真实接口", row["note"])
        # 首次查询：auto-complete×2（SHA/CAN）+ search×1 = 3 次调用
        self.assertEqual(len(opener.calls), 3)

    def test_entity_and_offer_cache_hit(self):
        import unittest.mock
        opener = _FakeOpener([("auto-complete", _AUTOCOMPLETE),
                              ("search-one-way", _SEARCH)])
        source = self._source()
        with unittest.mock.patch("urllib.request.build_opener", lambda *a, **k: opener):
            source.search("SHA", "CAN", DATE)
            source.search("SHA", "CAN", DATE)  # 全部命中缓存：零新增
        self.assertEqual(len(opener.calls), 3)

    def test_daily_cap_blocks(self):
        source = self._source()
        source._cache_dir.mkdir(exist_ok=True)
        source._cache_write("day_counter.json",
                            {"date": dt.date.today().isoformat(), "calls": DAILY_CALL_CAP})
        with self.assertRaises(RuntimeError) as ctx:
            source._get("/web/flights/auto-complete", {"query": "SHA"})
        self.assertIn("自我上限", str(ctx.exception))

    def test_429_stops_without_retry(self):
        import unittest.mock
        opener = _FakeOpener([], fail_with=urllib.error.HTTPError(
            "u", 429, "Too Many Requests", None, None))
        source = self._source()
        with unittest.mock.patch("urllib.request.build_opener", lambda *a, **k: opener):
            with self.assertRaises(RuntimeError) as ctx:
                source._get("/web/flights/auto-complete", {"query": "SHA"})
        self.assertIn("429", str(ctx.exception))
        self.assertEqual(len(opener.calls), 1, "429 后不得重试")


if __name__ == "__main__":
    unittest.main()
