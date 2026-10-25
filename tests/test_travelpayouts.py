# -*- coding: utf-8 -*-
"""Travelpayouts 价格日历适配器离线单测（全 mock 网络，零真实请求）。

覆盖：未配 token 不接入、离线开关短路、不产出比价行（契约）、
日历解析（排序/脏价丢弃/非法日期丢弃）、缓存命中零请求、上游失败透出。

运行：python -m unittest discover tests -v
"""

import datetime as dt  # noqa: F401
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import sources.travelpayouts as tp  # noqa: E402
from sources.travelpayouts import TravelpayoutsCalendarSource  # noqa: E402


class _Resp:
    def __init__(self, body):
        self._body = body.encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


_CALENDAR = {
    "success": True, "currency": "cny",
    "data": {
        "2026-11-03": {"price": 1496, "airline": "HU", "flight_number": 7609},
        "2026-11-01": {"price": 1267, "airline": "MU", "flight_number": 5100},
        "2026-11-02": {"price": None, "airline": "CA", "flight_number": 1883},
        "2026-11-04": {"price": 0, "airline": "CZ", "flight_number": 3},
        "not-a-date": {"price": 999, "airline": "XX", "flight_number": 1},
    },
}


class TravelpayoutsOfflineTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)

    def _source(self, with_token=True):
        cfg = self.dir / "travelpayouts_config.json"
        if with_token:
            cfg.write_text(json.dumps({"token": "TESTTOKEN"}), encoding="utf-8")
        return TravelpayoutsCalendarSource(config_path=cfg)

    def _patched(self, payload=None, error=None):
        body = json.dumps(payload if payload is not None else _CALENDAR)

        def fake_open(request, timeout=0):
            if error is not None:
                raise error
            return _Resp(body)
        return mock.patch.object(tp.urllib.request, "urlopen", fake_open)

    def test_no_token_not_available_and_empty(self):
        source = self._source(with_token=False)
        self.assertFalse(source.has_token)
        self.assertEqual(source.status, "not_available")
        self.assertIn("未配置 token", source.status_reason)
        self.assertEqual(source.calendar("PEK", "SHA", "2026-11"), [])

    def test_is_not_an_offer_channel(self):
        """契约：本渠道永远不产出比价行，且 availability 恒为 False。"""
        source = self._source()
        self.assertEqual(source.search("PEK", "SHA", dt.date(2026, 11, 1)), [])
        available, reason = source.availability("PEK", "SHA", dt.date(2026, 11, 1))
        self.assertFalse(available)
        self.assertIn("不产出可比价报价", reason)

    def test_offline_mode_short_circuits(self):
        source = self._source()
        with mock.patch.object(tp, "offline_mode", lambda: True), \
                mock.patch.object(tp.urllib.request, "urlopen",
                                  side_effect=AssertionError("不得联网")):
            self.assertEqual(source.calendar("PEK", "SHA", "2026-11"), [])
            self.assertEqual(source.status, "not_available")

    def test_calendar_parses_sorts_and_drops_dirty(self):
        source = self._source()
        with self._patched():
            days = source.calendar("PEK", "SHA", "2026-11")
        self.assertEqual([d["date"] for d in days], ["2026-11-01", "2026-11-03"],
                         "脏价（None/0）与非法日期必须丢弃，并按日期升序")
        self.assertEqual(days[0]["price"], 1267)
        self.assertEqual(days[0]["flight_no"], "MU5100")

    def test_cache_hit_zero_requests(self):
        source = self._source()
        with self._patched() as _:
            source.calendar("PEK", "SHA", "2026-11")
        with mock.patch.object(tp.urllib.request, "urlopen",
                               side_effect=AssertionError("命中缓存不应请求")):
            days = source.calendar("PEK", "SHA", "2026-11")
        self.assertEqual(len(days), 2)

    def test_upstream_failure_surfaces(self):
        source = self._source()
        with self._patched(payload={"success": False, "error": "invalid token"}):
            with self.assertRaises(RuntimeError) as ctx:
                source.calendar("PEK", "SHA", "2026-11")
        self.assertIn("invalid token", str(ctx.exception))

    def test_empty_data_returns_empty_list(self):
        source = self._source()
        with self._patched(payload={"success": True, "data": {}}):
            self.assertEqual(source.calendar("INC", "CAN", "2026-11"), [])


if __name__ == "__main__":
    unittest.main()
