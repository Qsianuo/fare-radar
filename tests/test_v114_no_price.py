# -*- coding: utf-8 -*-
"""v1.14（ISSUE-023）：上游「有航班、无报价」的全链路口径测试。

背景：银川→深圳 2026-10-27 实测，上游 HTTP 200 返回 1 条真实行程
（BK3037 11:30→15:00），但 `price: null`。旧口径「无价格行程丢弃」导致
三入口全空，用户看到「查不到航班」——航班其实存在，只是没报价。

新口径（本文件守住的规则）：
1. 有航段、无报价 → **保留行程**，price/total_estimate 为 None + price_note；
2. 无价行**不参与**排序首位、不参与最低价「◀」标记、不参与低价提醒比对；
3. 跨渠道去重时，有价行胜出（不能让无价行把有价行顶掉）；
4. 展示层（CLI/MCP/Web）金额位显示「价格未披露」，绝不出现 ¥None / ¥0。

零真实请求：本文件全部用假票源与桩数据，不触碰任何联网适配器。
"""

import datetime as dt
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import fareradar as fr  # noqa: E402
import mcp_server  # noqa: E402
import webapp  # noqa: E402
from sources.base import NO_PRICE_NOTE  # noqa: E402

DATE = dt.date(2026, 12, 1)


class _FakeSource:
    """最小票源：availability 恒可用，search 返回给定行。"""

    source_id = "fake"
    display_name = "假票源"
    entry_url = "https://example.invalid"
    entry_note = "测试用"
    status = "active"
    offer_channel = True

    def __init__(self, rows, source_id="fake", name="假票源"):
        self._rows = rows
        self.source_id = source_id
        self.display_name = name

    def availability(self, origin, destination, date):
        return True, None

    def search(self, origin, destination, date):
        return [dict(r) for r in self._rows]


def _priced(flight_no="BK3037", price=880, source_id="fake", name="假票源"):
    return {"source_id": source_id, "source_name": name, "product": f"直飞｜奥凯｜{flight_no}",
            "flight_no": flight_no, "carrier_code": "BK", "carrier_name": "奥凯航空",
            "depart_time": "11:30", "arrive_time": "15:00", "cross_day": False,
            "duration_text": "3小时30分", "dep_airport": "银川河东（INC）",
            "arr_airport": "深圳宝安（SZX）", "price": price, "tax_included": True,
            "is_deal": False, "fee_breakdown": None, "total_estimate": price,
            "price_note": None, "note": "测试行"}


def _no_price(flight_no="BK3037", source_id="fake", name="假票源"):
    row = _priced(flight_no=flight_no, source_id=source_id, name=name)
    row.update({"price": None, "tax_included": None, "total_estimate": None,
                "fee_breakdown": None, "price_note": NO_PRICE_NOTE,
                "note": "测试行·价格未披露"})
    return row


class CollectKeepsNoPriceRowTest(unittest.TestCase):
    def test_no_price_row_survives_collect(self):
        offers, failed, unavailable = fr.collect(
            [_FakeSource([_no_price()])], "INC", "SZX", DATE)
        self.assertEqual(len(offers), 1, "有航班无报价的行必须保留")
        row = offers[0]
        self.assertIsNone(row["price"])
        self.assertIsNone(row["total_estimate"])
        self.assertEqual(row["price_note"], NO_PRICE_NOTE)
        self.assertIn("价格未披露", row["note"])
        self.assertEqual(row["flight_no"], "BK3037", "航班字段必须原样带出")
        self.assertEqual(row["depart_time"], "11:30")

    def test_no_price_row_skips_fee_engine(self):
        """price=None 进费用引擎只会算出假数字，必须短路。"""
        offers, _, _ = fr.collect([_FakeSource([_no_price()])], "INC", "SZX", DATE)
        self.assertIsNone(offers[0]["fee_breakdown"], "无价行不得生成税费明细")
        self.assertIsNone(offers[0]["total_estimate"], "无价行不得生成到手价")

    def test_priced_row_still_gets_fee_engine(self):
        row = _priced()
        row["total_estimate"] = None
        row["tax_included"] = False
        offers, _, _ = fr.collect([_FakeSource([row])], "INC", "SZX", DATE)
        self.assertIsNotNone(offers[0]["total_estimate"], "有价行仍须补到手价")


class RankingTest(unittest.TestCase):
    def test_rank_puts_no_price_last(self):
        offers = [_no_price(flight_no="BK3037"), _priced(flight_no="CZ3001")]
        ranked = fr.rank_offers(offers, DATE)
        self.assertEqual([o["flight_no"] for o in ranked], ["CZ3001", "BK3037"],
                         "无价行必须排在所有有价行之后")

    def test_rank_all_no_price_does_not_crash(self):
        ranked = fr.rank_offers([_no_price("A1"), _no_price("A2")], DATE)
        self.assertEqual(len(ranked), 2)

    def test_lowest_ignores_no_price(self):
        offers = [_no_price(), _priced(flight_no="CZ3001", price=900)]
        lowest = fr.lowest_eligible(offers, DATE)
        self.assertIsNotNone(lowest)
        self.assertEqual(lowest["flight_no"], "CZ3001", "未给价的航班不得被标最低价")

    def test_lowest_is_none_when_all_no_price(self):
        self.assertIsNone(fr.lowest_eligible([_no_price(), _no_price("X")], DATE))

    def test_total_price_none_safe(self):
        self.assertIsNone(fr.total_price(_no_price()))
        self.assertEqual(fr.price_text(_no_price()), "价格未披露")
        self.assertEqual(fr.price_text(_priced(price=880)), "¥880")
        self.assertEqual(fr.price_text(_no_price()).count("¥"), 0, "无价不得出现 ¥")


class DedupTest(unittest.TestCase):
    def test_priced_channel_wins_over_no_price_channel(self):
        """同航班号跨渠道：有价行必须胜出，否则会丢掉真实报价。"""
        offers = [_no_price(flight_no="BK3037", source_id="a", name="渠道A"),
                  _priced(flight_no="BK3037", price=760, source_id="b", name="渠道B")]
        merged = fr.dedup_offers(offers, DATE)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["source_id"], "b")
        self.assertEqual(merged[0]["price"], 760)
        self.assertEqual(merged[0]["channel_count"], 2)

    def test_all_no_price_channels_keep_one_row(self):
        offers = [_no_price(flight_no="BK3037", source_id="a", name="渠道A"),
                  _no_price(flight_no="BK3037", source_id="b", name="渠道B")]
        merged = fr.dedup_offers(offers, DATE)
        self.assertEqual(len(merged), 1, "全无价也要合并为 1 行，不重复展示")
        self.assertIsNone(merged[0]["price"])


class RenderTest(unittest.TestCase):
    def _payload(self):
        offers = fr.rank_offers([_no_price()], DATE)
        return {
            "ok": True, "route": "银川 → 深圳", "date": DATE.isoformat(), "weekday": "周二",
            "price_basis": "到手价（票面+机建+燃油）", "notice": "[测试] 演示数据",
            "failed": [], "unavailable_sources": [], "sources_status": [], "card_hints": [],
            "price_insights": None, "alert_hit": None, "warning": None, "empty_hint": None,
            "offers": [{
                "price": o.get("price"), "total_estimate": fr.total_price(o),
                "source_id": o["source_id"], "source_name": o["source_name"],
                "product": o["product"], "flight_no": o["flight_no"],
                "depart_time": o["depart_time"], "arrive_time": o["arrive_time"],
                "duration_text": o["duration_text"], "carrier_name": o["carrier_name"],
                "price_note": o.get("price_note"), "risk_flags": [], "is_lowest": False,
                "fee_breakdown": o.get("fee_breakdown"), "channel_count": 1,
                "channel_names": [o["source_name"]], "note": o.get("note"),
            } for o in offers],
            "no_price_count": sum(1 for o in offers if fr.total_price(o) is None),
        }

    def test_mcp_text_marks_price_undisclosed(self):
        payload = self._payload()
        with mock.patch.object(webapp, "_api_search", return_value=(200, payload)):
            text = mcp_server._fmt_search({"from": "INC", "to": "SZX", "date": "2026-12-01"})
        self.assertIn("价格未披露", text)
        self.assertNotIn("¥None", text)
        self.assertNotIn("¥0", text)
        self.assertIn("BK3037", text, "航班号必须仍然展示")

    def test_web_rows_carry_price_note_and_count(self):
        rows = self._payload()["offers"]
        self.assertEqual(self._payload()["no_price_count"], 1)
        self.assertEqual(rows[0]["price_note"], NO_PRICE_NOTE)
        self.assertIsNone(rows[0]["total_estimate"])


class ApiSearchTest(unittest.TestCase):
    """走真实 _api_search 组装逻辑（票源全部桩掉，零联网）。"""

    def test_payload_counts_no_price_rows(self):
        offers = fr.rank_offers([_no_price(), _priced(flight_no="CZ3001", price=900)], DATE)
        with mock.patch.object(webapp.fr, "_selected_sources", return_value=[]), \
                mock.patch.object(webapp.fr, "collect",
                                  return_value=(offers, [], [])), \
                mock.patch.object(webapp.fr, "card_hints", return_value=[]), \
                mock.patch.object(webapp.fr, "price_insights", return_value=None), \
                mock.patch.object(webapp.fr, "load_alerts", return_value=[]):
            status, payload = webapp._api_search(
                {"from": "INC", "to": "SZX", "date": DATE.isoformat()})
        self.assertEqual(status, 200)
        self.assertEqual(payload["no_price_count"], 1)
        self.assertEqual(len(payload["offers"]), 2)
        lowest = [o for o in payload["offers"] if o["is_lowest"]]
        self.assertEqual(len(lowest), 1)
        self.assertEqual(lowest[0]["flight_no"], "CZ3001", "最低价必须落在有价行")
        no_price = [o for o in payload["offers"] if o["flight_no"] == "BK3037"][0]
        self.assertIsNone(no_price["total_estimate"])
        self.assertEqual(no_price["price_note"], NO_PRICE_NOTE)

    def test_payload_json_has_no_nan(self):
        """无价行的 null 必须能正常 JSON 序列化（前端拿到的不是字符串 "None"）。"""
        offers = fr.rank_offers([_no_price()], DATE)
        with mock.patch.object(webapp.fr, "_selected_sources", return_value=[]), \
                mock.patch.object(webapp.fr, "collect",
                                  return_value=(offers, [], [])), \
                mock.patch.object(webapp.fr, "card_hints", return_value=[]), \
                mock.patch.object(webapp.fr, "price_insights", return_value=None), \
                mock.patch.object(webapp.fr, "load_alerts", return_value=[]):
            _, payload = webapp._api_search(
                {"from": "INC", "to": "SZX", "date": DATE.isoformat()})
        blob = json.dumps(payload, ensure_ascii=False)
        self.assertIn('"total_estimate": null', blob)
        self.assertNotIn('"total_estimate": "None"', blob)


class AdapterNoPriceTest(unittest.TestCase):
    """适配器层：上游 price=null 的行程不再被丢弃。"""

    def test_rapidapi_keeps_itinerary_without_price(self):
        from sources.rapidapi_sky import RapidapiFlightSource
        source = RapidapiFlightSource(config_path=Path("/nonexistent/rapidapi_config.json"))
        itinerary = {
            "price": None, "currency": "CNY", "total_duration": 210, "stops": 0,
            "legs": [{"airline_code": "BK", "airline": "Okay Airways",
                      "flight_number": "BK 3037", "airplane": "Boeing 737",
                      "overnight": False,
                      "departure_airport": {"code": "INC", "name": "Yinchuan Hedong",
                                            "time": "2026-10-27 11:30"},
                      "arrival_airport": {"code": "SZX", "name": "Shenzhen Bao'an",
                                          "time": "2026-10-27 15:00"}}],
        }
        row = source._to_offer(itinerary)
        self.assertIsNotNone(row, "有航段无报价的行程必须保留")
        self.assertIsNone(row["price"])
        self.assertIsNone(row["total_estimate"])
        self.assertIsNone(row["fee_breakdown"], "无价不得生成含税口径明细")
        self.assertEqual(row["price_note"], NO_PRICE_NOTE)
        self.assertEqual(row["flight_no"], "BK3037")
        self.assertEqual(row["depart_time"], "11:30")
        self.assertIn("价格未披露", row["note"])

    def test_rapidapi_still_drops_itinerary_without_legs(self):
        from sources.rapidapi_sky import RapidapiFlightSource
        source = RapidapiFlightSource(config_path=Path("/nonexistent/rapidapi_config.json"))
        self.assertIsNone(source._to_offer({"price": 500, "legs": []}))

    def test_rapidapi_empty_note_reports_raw_count(self):
        """上游有行程但全被丢弃时，说明里要给出条数，而不是含糊的"没返回"。"""
        import tempfile
        from sources.rapidapi_sky import RapidapiFlightSource
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "rapidapi_config.json"
            cfg.write_text(json.dumps({"api_key": "TESTKEY"}), encoding="utf-8")
            source = RapidapiFlightSource(config_path=cfg)
            source._cache_dir.mkdir(exist_ok=True)
            source._cache_write("offers_INC_SZX_2026-10-27.json",
                                {"expire_at": 0, "rows": [], "body_status": 200,
                                 "raw_itineraries": 2, "no_price_count": 0})
            note = source.empty_note("INC", "SZX", dt.date(2026, 10, 27))
        self.assertIn("2 条行程", note)


if __name__ == "__main__":
    unittest.main()
