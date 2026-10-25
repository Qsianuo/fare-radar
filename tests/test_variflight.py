# -*- coding: utf-8 -*-
"""飞常准 Aviation MCP 适配器离线单测（全 mock 网络，零真实请求）。

覆盖：未配 Key 不接入、schema 未校准不出数据（不猜）、积分护栏日/月硬停、
JSON 与 SSE 两种响应解析、401 明确报错不重试、参数未校准时拒绝构造请求。

运行：python -m unittest discover tests -v
"""

import json
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import sources.variflight as vf  # noqa: E402
from sources.variflight import VariflightSource  # noqa: E402


class _Resp:
    def __init__(self, body):
        self._body = body.encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class VariflightOfflineTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)

    def _source(self, with_key=True):
        cfg = self.dir / "variflight_config.json"
        if with_key:
            cfg.write_text(json.dumps({"api_key": "TESTKEY",
                                       "endpoint": vf.DEFAULT_ENDPOINT}), encoding="utf-8")
        return VariflightSource(config_path=cfg)

    def _patched(self, body=None, error=None):
        def fake_open(request, timeout=0):
            if error is not None:
                raise error
            return _Resp(body)
        return mock.patch.object(vf.urllib.request, "urlopen", fake_open)

    # ---------- 接入状态 ----------

    def test_no_key_not_available(self):
        source = self._source(with_key=False)
        self.assertFalse(source.has_key)
        self.assertEqual(source.status, "not_available")
        self.assertIn("未配置 API Key", source.status_reason)
        self.assertEqual(source.search("INC", "CTU", __import__("datetime").date(2026, 11, 1)), [])

    def test_key_but_uncalibrated_does_not_fetch(self):
        """有 Key 但 schema 未校准：不得发起任何联网调用。"""
        source = self._source()
        with mock.patch.object(vf, "SCHEMA_CALIBRATED", False):
            self.assertEqual(source.status, "not_available")
            self.assertIn("校准", source.status_reason)
            with mock.patch.object(vf.urllib.request, "urlopen",
                                   side_effect=AssertionError("不得联网")):
                rows = source.search("INC", "CTU", __import__("datetime").date(2026, 11, 1))
        self.assertEqual(rows, [])

    # ---------- 积分护栏 ----------

    def test_points_guard_hard_stops(self):
        source = self._source()
        source._cache_dir.mkdir()
        import datetime as dt
        today = dt.date.today().isoformat()
        source._cache_write("day_points.json", {"date": today, "points": vf.DAILY_POINT_CAP})
        with self.assertRaises(RuntimeError) as ctx:
            source._points_guard()
        self.assertIn("当日积分预算已用尽", str(ctx.exception))

        source._cache_write("day_points.json", {"date": today, "points": 0})
        source._cache_write("month_points.json",
                            {"month": today[:7], "points": vf.MONTHLY_POINT_CAP})
        with self.assertRaises(RuntimeError) as ctx:
            source._points_guard()
        self.assertIn("当月积分预算已用尽", str(ctx.exception))
        self.assertIn("不自动充值", str(ctx.exception))

    def test_points_guard_precharges_worst_case(self):
        """按最坏单价 50 预扣，不按最小 5 乐观估算。"""
        source = self._source()
        source._points_guard()
        used = source.points_used()
        self.assertEqual(used["day"], vf.POINT_PER_CALL_WORST)
        self.assertEqual(used["month"], vf.POINT_PER_CALL_WORST)

    # ---------- 传输层 ----------

    def test_rpc_parses_plain_json(self):
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"ok": True}})
        with self._patched(body):
            out = vf._rpc("tools/list", {}, "TESTKEY")
        self.assertEqual(out["result"], {"ok": True})

    def test_rpc_parses_sse(self):
        body = ('event: message\n'
                'data: {"jsonrpc":"2.0","id":1,"result":{"tools":[]}}\n\n')
        with self._patched(body):
            out = vf._rpc("tools/list", {}, "TESTKEY")
        self.assertEqual(out["result"], {"tools": []})

    def test_rpc_401_raises_without_retry(self):
        err = urllib.error.HTTPError("u", 401, "Unauthorized", None,
                                     __import__("io").BytesIO(b'{"message":"auth"}'))
        calls = []

        def fake_open(request, timeout=0):
            calls.append(request.full_url)
            raise err
        with mock.patch.object(vf.urllib.request, "urlopen", fake_open):
            with self.assertRaises(RuntimeError) as ctx:
                vf._rpc("tools/list", {}, "BADKEY")
        self.assertIn("401", str(ctx.exception))
        self.assertEqual(len(calls), 1, "401 后不得重试")

    def test_rpc_error_message_surfaced(self):
        body = json.dumps({"jsonrpc": "2.0", "id": 1,
                           "error": {"code": -32602, "message": "bad params"}})
        with self._patched(body):
            with self.assertRaises(RuntimeError) as ctx:
                vf._rpc("tools/call", {}, "TESTKEY")
        self.assertIn("bad params", str(ctx.exception))

    # ---------- 参数构造不猜 ----------

    def test_build_args_refuses_when_uncalibrated(self):
        source = self._source()
        import datetime as dt
        with mock.patch.object(vf, "SCHEMA_CALIBRATED", False), \
                mock.patch.object(vf, "PRICING_TOOL_ARGS", None):
            with self.assertRaises(RuntimeError) as ctx:
                source._build_args("INC", "CTU", dt.date(2026, 11, 1))
        self.assertIn("未校准", str(ctx.exception))

    def test_build_args_uses_calibrated_mapping(self):
        source = self._source()
        import datetime as dt
        args = source._build_args("INC", "CTU", dt.date(2026, 11, 1))
        self.assertEqual(args, {"dep_city": "INC", "arr_city": "CTU",
                               "dep_date": "2026-11-01"})

    # ---------- 映射（用 2026-10-25 真实返回的字段形状） ----------

    _FLIGHT = {
        "flightcompany": "3U", "flightno": "3U6542",
        "flightdepcode": "INC", "flightarrcode": "TFU",
        "depaptcname": "银川河东", "arraptcname": "成都天府",
        "depcitycode": "INC", "arrcitycode": "CTU",
        "flighthterminal": "T3", "flightterminal": "T2",
        "flightdeptimeplandate": 1793500500, "depdate": "2026-11-01",
        "flightarrtimeplandate": 1793508000, "arrdate": "2026-11-01",
        "generic": "321", "distance": 890, "stopflag": 0,
        "cabins": [
            {"cabinclass": "C", "classname": "公务舱", "cabincode": "J",
             "price": 1960, "stprice": 3020},
            {"cabinclass": "Y", "classname": "经济舱", "cabincode": "M",
             "price": 1060, "stprice": 1510},
        ],
    }

    def test_mapping_picks_economy_price(self):
        source = self._source()
        row = source._to_offer(dict(self._FLIGHT))
        self.assertIsNotNone(row)
        self.assertEqual(row["price"], 1060, "必须取经济舱，不取公务舱 1960")
        self.assertEqual(row["flight_no"], "3U6542")
        # 3U 经补充名册映射为"川航"；未收录的代码留空，绝不拿二字码冒充中文名
        self.assertEqual(row["carrier_name"], "川航")
        self.assertIn("川航", row["product"])
        self.assertEqual(row["depart_time"], "10:35", "北京时间 epoch 需 +8h 换算")
        self.assertEqual(row["arrive_time"], "12:40")
        self.assertEqual(row["duration_text"], "2小时05分")
        self.assertFalse(row["cross_day"])
        self.assertIn("银川河东", row["dep_airport"])
        self.assertIn("T3", row["dep_airport"])
        self.assertIn("成都天府", row["arr_airport"])
        self.assertFalse(row["tax_included"], "上游未明示含税 → 按票面价交引擎补费用")
        self.assertEqual(row["source_id"], "variflight")

    def test_mapping_drops_flight_without_price(self):
        source = self._source()
        bad = dict(self._FLIGHT, cabins=[])
        self.assertIsNone(source._to_offer(bad), "无舱位价格必须丢弃，不编造")
        bad2 = dict(self._FLIGHT, cabins=[{"cabinclass": "Y", "price": "x"}])
        self.assertIsNone(source._to_offer(bad2), "非数字价格必须丢弃")

    def test_mapping_cross_day_and_transfer(self):
        source = self._source()
        night = dict(self._FLIGHT,
                     flightdeptimeplandate=1793541900,   # 北京 22:05
                     flightarrtimeplandate=1793546700)   # 北京 23:25
        row = source._to_offer(night)
        self.assertEqual(row["depart_time"], "22:05")
        transfer = dict(self._FLIGHT, stopflag=1, stopcityname="重庆")
        row2 = source._to_offer(transfer)
        self.assertIn("经重庆中转", row2["product"])

    def test_to_offers_surfaces_upstream_code_error(self):
        source = self._source()
        text = "Flight prices: {'code': 400, 'message': 'bad city', 'data': []}"
        parsed = {"result": {"content": [{"text": text}], "isError": False}}
        rows, note = source._to_offers(parsed)
        self.assertEqual(rows, [])
        self.assertIn("code=400", note)
        self.assertIn("bad city", note)

    def test_to_offers_empty_data_note(self):
        source = self._source()
        text = "Flight prices: {'code': 200, 'message': 'Success', 'data': []}"
        parsed = {"result": {"content": [{"text": text}], "isError": False}}
        rows, note = source._to_offers(parsed)
        self.assertEqual(rows, [])
        self.assertIn("无可售航班", note)


if __name__ == "__main__":
    unittest.main()
