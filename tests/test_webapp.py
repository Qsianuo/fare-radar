# -*- coding: utf-8 -*-
"""FareRadar Web 版 API 层单测（v1.4 契约）。

webapp 的业务规则全部复用 fareradar.py 与 sources（规则唯一来源），此处只测
翻译层：参数校验到 400、alerts 损坏到 409、search 载荷契约（航班 6 要素/费用
明细/到手价/channel_count/card_hints）、deals 两类结构、trend 到手价口径、
watch 设置/列表/检查/删除往返与已下线城市标注。
运行：python -m unittest discover tests -v（与其他用例一起跑）
"""

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import fareradar as fr  # noqa: E402
import webapp  # noqa: E402
from sources import all_sources, cities, pricing  # noqa: E402

PINNED_MINUTE = 29_500_000  # 价格随分钟演示波动：涉价断言先固定查询时刻
NEW_LINK = "https://v.douyin.com/USSyX47SP7s/"


class WebApiTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self._old_alerts = fr.ALERTS_FILE
        self.addCleanup(setattr, fr, "ALERTS_FILE", self._old_alerts)
        fr.ALERTS_FILE = Path(tmp.name) / "alerts.json"

    def test_search_payload_contract(self):
        with pricing.fixed_minute(PINNED_MINUTE):
            status, payload = webapp._api_search(
                {"from": "SHA", "to": "CTU", "date": "2026-11-20"})
            offers, _failed, _unavailable = fr.collect(all_sources(), "SHA", "CTU",
                                                       fr.parse_date("2026-11-20"))
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["offers"], "SHA-CTU 应有报价")
        lowest = payload["offers"][0]
        self.assertTrue(lowest["is_lowest"], "首行应为最低价行")
        self.assertEqual(lowest["total_estimate"],
                         fr.total_price(fr.lowest_eligible(offers, fr.parse_date("2026-11-20"))),
                         "网页最低到手价与 CLI 规则一致（引擎+演示波动）")
        self.assertEqual(payload["price_basis"], "到手价（票面+机建+燃油）")
        self.assertIn("到手价", payload["notice"], "notice 更新到手价口径文案")
        for offer in payload["offers"]:
            self.assertGreaterEqual(offer["total_estimate"], offer["price"])
            self.assertTrue(offer["fee_breakdown"])
            self.assertIsInstance(offer["risk_flags"], list)

    def test_search_rows_carry_flight_six_elements(self):
        _, payload = webapp._api_search({"from": "SHA", "to": "PEK", "date": "2026-11-20"})
        from sources import airlines
        schedule = airlines.route_flights("SHA", "PEK")
        schedule_nos = {f["flight_no"] for f in schedule}
        flight_rows = [o for o in payload["offers"] if o.get("flight_no") in schedule_nos]
        self.assertEqual({o["flight_no"] for o in flight_rows}, schedule_nos,
                         "正班 3 班全部出现在结果中（含跨渠道合并行）")
        self.assertEqual(len(flight_rows), 3)
        for offer in flight_rows:
            self.assertRegex(offer["depart_time"], r"^\d{2}:\d{2}$")
            self.assertRegex(offer["arrive_time"], r"^\d{2}:\d{2}$")
            self.assertIn("小时", offer["duration_text"])
            self.assertTrue(offer["dep_airport"].endswith("机场"))
            self.assertTrue(offer["arr_airport"].endswith("机场"))
            self.assertTrue(offer["flight_no"])
            self.assertTrue(offer["carrier_code"] and offer["carrier_name"])

    def test_search_channel_count_and_note(self):
        _, payload = webapp._api_search({"from": "SHA", "to": "CTU", "date": "2026-11-20"})
        merged = [o for o in payload["offers"] if o["channel_count"] > 1]
        self.assertTrue(merged, "应有跨渠道同航班号合并行")
        for offer in merged:
            self.assertEqual(offer["channel_count"], 2)
            self.assertIn("另有 1 个渠道有售", offer["note"])

    def test_search_param_errors_are_400(self):
        cases = [
            {"to": "PEK", "date": "2026-11-20"},                       # 缺出发
            {"from": "SHA", "to": "SHA", "date": "2026-11-20"},        # 同城
            {"from": "SHA", "to": "PEK", "date": "2026-02-30"},        # 非法日期
        ]
        for params in cases:
            status, payload = webapp._api_search(params)
            self.assertEqual(status, 400, params)
            self.assertFalse(payload["ok"])

    def test_search_retired_city_hint(self):
        status, payload = webapp._api_search({"from": "SHA", "to": "KIX", "date": "2026-11-20"})
        self.assertEqual(status, 200)
        self.assertIn("城市已下线", payload["empty_hint"])
        self.assertIn("大阪关西", payload["empty_hint"])

    def test_card_hints_block(self):
        _, payload = webapp._api_search({"from": "SHA", "to": "CTU", "date": "2026-11-20"})
        hints = payload["card_hints"]
        self.assertTrue(hints, "SHA-CTU 命中全航线卡与秋季单次卡")
        ids = {c["card_id"] for c in hints}
        self.assertTrue({"DY-101", "DY-103", "DY-104", "DY-105", "DY-106"} <= ids)
        for card in hints:
            self.assertIn("card_id", card) and self.assertIn("validity_text", card)
            self.assertIn("refund", card) and self.assertIn("entry_url", card)
        undisclosed = [c for c in hints if not c["refund_disclosed"]]
        self.assertTrue(all("退改规则未披露" in c["refund"] for c in undisclosed))
        self.assertNotIn("douyin_groupbuy", {o["source_id"] for o in payload["offers"]},
                         "卡提示独立于比价行（groupbuy 不出 offer）")

    def test_watch_roundtrip(self):
        status, payload = webapp._api_watch_set(
            {"from": "SHA", "to": "PEK", "price": "700"})
        self.assertEqual((status, payload["ok"]), (200, True))
        _, listed = webapp._api_watch_list({})
        self.assertEqual(len(listed["alerts"]), 1)
        self.assertEqual(listed["alerts"][0]["threshold"], 700)
        status, payload = webapp._api_watch_check({})
        self.assertEqual(status, 200)
        # 引擎定价下 SHA→PEK 明日可比最低到手价恒低于 700
        self.assertEqual(payload["lines"][0]["status"], "hit")
        webapp._api_watch_set({"from": "SHA", "to": "PEK", "price": "1"})
        _, payload = webapp._api_watch_check({})
        self.assertEqual(payload["lines"][0]["status"], "wait", "底价 1 元必不命中")
        status, payload = webapp._api_watch_remove({"from": "SHA", "to": "PEK"})
        self.assertEqual((status, payload["ok"]), (200, True))
        _, listed = webapp._api_watch_list({})
        self.assertEqual(listed["alerts"], [])

    def test_watch_list_marks_retired_city(self):
        webapp._api_watch_set({"from": "SHA", "to": "HKG", "price": "800"})
        _, listed = webapp._api_watch_list({})
        item = listed["alerts"][0]
        self.assertTrue(item["retired"])
        self.assertIn("城市已下线", item["status_note"])
        self.assertIsNone(item["current_lowest"])
        _, payload = webapp._api_watch_check({})
        self.assertEqual(payload["lines"][0]["status"], "retired")

    def test_watch_rejects_bad_input_and_corrupted_file(self):
        status, _ = webapp._api_watch_set({"from": "SHA", "to": "PEK", "price": "0"})
        self.assertEqual(status, 400, "底价必须为正整数")
        fr.ALERTS_FILE.write_text("{oops", encoding="utf-8")
        for api in (webapp._api_watch_set, webapp._api_watch_remove,
                    webapp._api_watch_list, webapp._api_watch_check):
            status, payload = api({"from": "SHA", "to": "PEK", "price": "700"})
            self.assertEqual(status, 409, api.__name__)
            self.assertIn("数据未被改动", payload["error"], "损坏时必须承诺不覆盖（ISSUE-002 规则）")


class V14ContractTest(unittest.TestCase):
    """v1.4：城市全集 / 覆盖模型 / 卡池 / 渠道入口的 API 契约（ISSUE-011）。"""

    def test_cities_endpoint_50_domestic_with_airport(self):
        status, payload = webapp._api_cities({})
        self.assertEqual((status, payload["ok"]), (200, True))
        self.assertEqual(payload["count"], 50)
        self.assertEqual(len(payload["cities"]), 50)
        self.assertEqual(payload["cities"][0],
                         {"code": "PEK", "name": "北京", "airport": "北京首都国际机场"})
        for city in payload["cities"]:
            self.assertTrue(city["code"] and city["name"] and city["airport"])
        codes = {c["code"] for c in payload["cities"]}
        self.assertFalse(codes & {"HKG", "KIX", "NRT", "ICN", "BKK"}, "零国际城市")

    def test_search_reports_unavailable_and_card_channels(self):
        status, payload = webapp._api_search({"from": "CGO", "to": "XIY", "date": "2026-10-26"})
        self.assertEqual(status, 200)
        self.assertNotIn("douyin_miniapp", {o["source_id"] for o in payload["offers"]},
                         "非春秋基地航线不得出现小程序报价（不编造）")
        unavailable = {u["source_id"]: u for u in payload["unavailable_sources"]}
        self.assertIn("douyin_miniapp", unavailable)
        self.assertIn("春秋基地", unavailable["douyin_miniapp"]["reason"])
        self.assertIn("douyin_groupbuy", unavailable, "卡类渠道以无销售语义出现在覆盖清单")
        self.assertTrue(all(u.get("reason") for u in unavailable.values()))
        self.assertEqual(payload["failed"], [])
        for offer in payload["offers"]:
            self.assertIn("entry_url", offer, "报价行携带渠道入口")
            self.assertIn("entry_note", offer)

    def test_sources_carry_new_location_link(self):
        _, sources = webapp._api_sources({})
        by_id = {s["id"]: s for s in sources["sources"]}
        self.assertEqual(by_id["douyin_miniapp"]["entry_url"], NEW_LINK)
        self.assertEqual(by_id["douyin_groupbuy"]["entry_url"], NEW_LINK)
        self.assertEqual(by_id["airline"]["entry_url"], "", "多家航司允许无单一入口")

    def test_deals_payload_two_sections(self):
        _, deals = webapp._api_deals({"from": "SHA", "to": "JJN"})
        self.assertTrue(deals["ok"])
        self.assertIn("[演示数据]", deals["notice"])
        specials = deals["special_fares"]
        self.assertEqual(len(specials), 1, "SHA-JJN 仅 10-27 一条特价票")
        self.assertEqual((specials[0]["date"], specials[0]["price"]), ("2026-10-27", 179))
        self.assertEqual(specials[0]["destination_display"], "泉州")
        self.assertIn("不含机建燃油费", specials[0]["label"])
        cards = deals["cards"]
        self.assertEqual(len(cards), 6, "卡池 6 张")
        for card in cards:
            self.assertIn("entry_url", card)
            self.assertIn("entry_note", card)
            self.assertIn("refund", card)
        self.assertTrue(any(c["card_id"] == "DY-103" and c["stock"] == "售罄" for c in cards))
        self.assertTrue(any("退改规则未披露" in c["refund"] for c in cards))
        _, all_deals = webapp._api_deals({})
        self.assertEqual(len(all_deals["special_fares"]), 21, "不过滤时 21 条特价票全量")

    def test_trend_uses_total_price_basis(self):
        params = {"from": "SHA", "to": "CTU", "date": "2026-11-20"}
        with pricing.fixed_minute(PINNED_MINUTE):
            status, payload = webapp._api_trend(dict(params))
            _, again = webapp._api_trend(dict(params))
        self.assertEqual((status, payload["ok"]), (200, True))
        self.assertEqual(len(payload["points"]), 24, "近 24 小时逐小时点集")
        self.assertEqual(payload["points"][0]["hours_ago"], 23)
        self.assertEqual(payload["points"][-1]["hours_ago"], 0)
        prices = [p["price"] for p in payload["points"]]
        self.assertTrue(all(p > 0 for p in prices))
        self.assertEqual(payload["current"], prices[-1])
        self.assertEqual(payload["day_low"], min(prices))
        self.assertEqual(payload["day_high"], max(prices))
        self.assertEqual(payload["price_basis"], "到手价（票面+机建+燃油）")
        self.assertEqual(payload, again, "同一分钟内曲线重放必须逐字节一致（确定性）")
        self.assertIn("到手价", payload["notice"])
        self.assertNotIn("行情", payload["notice"])

    def test_trend_rejects_bad_input(self):
        for params in ({"from": "SHA", "to": "SHA", "date": "2026-11-20"},
                       {"from": "SHA", "to": "HKG", "date": "2026-11-20"},
                       {"from": "SHA", "to": "CTU"}):
            status, payload = webapp._api_trend(dict(params))
            self.assertEqual(status, 400, params)
            self.assertFalse(payload["ok"])

    def test_search_deterministic_same_minute(self):
        params = {"from": "SHA", "to": "PEK", "date": "2026-11-20"}
        with pricing.fixed_minute(PINNED_MINUTE):
            _, first = webapp._api_search(dict(params))
            _, second = webapp._api_search(dict(params))
        self.assertEqual(first, second, "同输入同分钟载荷完全一致（无随机）")


if __name__ == "__main__":
    unittest.main()
