# -*- coding: utf-8 -*-
"""FareRadar Web 版 API 层单测（v1.5 契约，ISSUE-012）。

webapp 的业务规则全部复用 fareradar.py 与 sources（规则唯一来源），此处只测
翻译层：参数校验到 400、alerts 损坏到 409、search 载荷契约（航班号一等字段/
费用明细/到手价/sources_status 渠道状态/card_hints）、deals 两类结构、
nearby 临近日期最低价条（替代原 trend 演示走势）、watch 设置/列表/检查/删除
往返与已下线城市标注。运行：python -m unittest discover tests -v
"""

import datetime as dt
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import fareradar as fr  # noqa: E402
import webapp  # noqa: E402
from sources import all_sources, cities, pricing  # noqa: E402

PINNED_MINUTE = 29_500_000  # 价格随分钟演示波动：涉价断言先固定查询时刻
NEW_LINK = "https://v.douyin.com/USSyX47SP7s/"
SNAPSHOT_DATE = dt.date(2026, 11, 1)


def _pinned(date=SNAPSHOT_DATE):
    """旁路 parse_date 的"不早于今天"校验（快照日期可能成为过去）。"""
    return mock.patch.object(fr, "parse_date", lambda raw: date)


class _LowestStubSource:
    """恒定提供一条到手 ¥800 行的存根票源：watch 命中/等待流程验证。"""

    source_id = "stub_lowest"
    display_name = "存根票源(测试)"
    entry_url = ""
    entry_note = ""

    def availability(self, origin, destination, date):
        return True, None

    def search(self, origin, destination, date):
        return [{"source_id": self.source_id, "source_name": self.display_name,
                 "product": "存根行", "price": 800, "tax_included": True,
                 "is_deal": False}]


class WebApiTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self._old_alerts = fr.ALERTS_FILE
        self.addCleanup(setattr, fr, "ALERTS_FILE", self._old_alerts)
        fr.ALERTS_FILE = Path(tmp.name) / "alerts.json"

    def test_search_payload_contract(self):
        status, payload = webapp._api_search({"from": "INC", "to": "CAN", "date": "2026-11-01"})
        offers, _failed, _unavailable = fr.collect(all_sources(), "INC", "CAN", SNAPSHOT_DATE)
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["offers"], "INC-CAN 快照日期应有报价")
        lowest = payload["offers"][0]
        self.assertTrue(lowest["is_lowest"], "首行应为最低价行")
        self.assertEqual(lowest["total_estimate"],
                         fr.total_price(fr.lowest_eligible(offers, SNAPSHOT_DATE)),
                         "网页最低到手价与 CLI 规则一致")
        self.assertEqual(payload["price_basis"], "到手价（票面+机建+燃油）")
        self.assertIn("到手价", payload["notice"], "notice 说明到手价口径")
        self.assertIn("[实测快照]", payload["notice"], "notice 首标签为实测快照")
        for offer in payload["offers"]:
            self.assertGreaterEqual(offer["total_estimate"], offer["price"])
            self.assertTrue(offer["fee_breakdown"])
            self.assertIsInstance(offer["risk_flags"], list)
            self.assertTrue(offer["is_snapshot"], "比价行均来自实测快照")
        statuses = {s["id"]: s for s in payload["sources_status"]}
        for sid in ("airline", "ota", "douyin_live"):
            self.assertEqual(statuses[sid]["status"], "not_available")
            self.assertEqual(statuses[sid]["reason"], "暂无合规公开接口，未接入")

    def test_search_rows_carry_flight_fields(self):
        _, payload = webapp._api_search({"from": "INC", "to": "CAN", "date": "2026-11-01"})
        rows = payload["offers"]
        self.assertEqual(len(rows), 8, "快照 5 直飞 + 3 中转")
        for offer in rows:
            self.assertRegex(offer["depart_time"], r"^\d{2}:\d{2}$")
            self.assertRegex(offer["arrive_time"], r"^\d{2}:\d{2}$")
            self.assertIn("小时", offer["duration_text"])
            self.assertTrue(offer["dep_airport"].startswith("银川河东国际机场"))
            self.assertTrue(offer["arr_airport"].startswith("广州白云国际机场"))
            self.assertTrue(offer["flight_no"])
            self.assertTrue(offer["carrier_name"])
        t2 = {o["flight_no"] for o in rows if o["arr_airport"] == "广州白云国际机场T2"}
        self.assertTrue({"MF8753", "GJ8258", "CZ3270"} <= t2, "航站楼逐字（T2）")
        t3 = {o["flight_no"] for o in rows if o["arr_airport"] == "广州白云国际机场T3"}
        self.assertEqual(t3, {"ZH8942"}, "航站楼逐字（T3）")

    def test_search_note_carries_snapshot_disclosure(self):
        _, payload = webapp._api_search({"from": "INC", "to": "CAN", "date": "2026-11-01"})
        gj = next(o for o in payload["offers"] if o["flight_no"] == "GJ8258")
        self.assertIn("另有南航共享航班售卖", gj["note"])
        self.assertIn("来源：航旅纵横", gj["note"])
        self.assertTrue(all(o["channel_count"] == 1 for o in payload["offers"]),
                        "快照单渠道：无跨渠道合并行（去重逻辑由单元测试锁定）")

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
        with _pinned(dt.date(2026, 11, 20)):
            status, payload = webapp._api_search({"from": "SHA", "to": "KIX", "date": "2026-11-20"})
        self.assertEqual(status, 200)
        self.assertIn("城市已下线", payload["empty_hint"])
        self.assertIn("大阪关西", payload["empty_hint"])

    def test_card_hints_block(self):
        with _pinned(dt.date(2026, 11, 20)):
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
        # 快照未收录 SHA→PEK：检查行显式报"暂无报价数据"（不编数）
        self.assertEqual(payload["lines"][0]["status"], "none")
        status, payload = webapp._api_watch_remove({"from": "SHA", "to": "PEK"})
        self.assertEqual((status, payload["ok"]), (200, True))
        _, listed = webapp._api_watch_list({})
        self.assertEqual(listed["alerts"], [])

    def test_watch_check_hit_and_wait_with_stub_source(self):
        with mock.patch.object(webapp, "all_sources", lambda: [_LowestStubSource()]):
            webapp._api_watch_set({"from": "SHA", "to": "PEK", "price": "900"})
            _, payload = webapp._api_watch_check({})
            self.assertEqual(payload["lines"][0]["status"], "hit")
            self.assertIn("可以出手了", payload["lines"][0]["text"])
            webapp._api_watch_set({"from": "SHA", "to": "PEK", "price": "1"})
            _, payload = webapp._api_watch_check({})
            self.assertEqual(payload["lines"][0]["status"], "wait", "底价 1 元必不命中")
        webapp._api_watch_remove({"from": "SHA", "to": "PEK"})

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
        with _pinned(dt.date(2026, 10, 26)):
            status, payload = webapp._api_search({"from": "CGO", "to": "XIY",
                                                  "date": "2026-10-26"})
        self.assertEqual(status, 200)
        self.assertEqual(payload["offers"], [], "非特价票航线不得出现小程序报价（不编造）")
        unavailable = {u["source_id"]: u for u in payload["unavailable_sources"]}
        self.assertIn("douyin_miniapp", unavailable)
        self.assertIn("固定特价票", unavailable["douyin_miniapp"]["reason"])
        self.assertIn("douyin_groupbuy", unavailable, "卡类渠道以无销售语义出现在覆盖清单")
        self.assertTrue(all(u.get("reason") for u in unavailable.values()))
        self.assertEqual(payload["failed"], [])
        self.assertIn("该航线暂无真实数据快照", payload["empty_hint"])

    def test_sources_carry_new_location_link(self):
        _, sources = webapp._api_sources({})
        by_id = {s["id"]: s for s in sources["sources"]}
        self.assertEqual(by_id["douyin_miniapp"]["entry_url"], NEW_LINK)
        self.assertEqual(by_id["douyin_groupbuy"]["entry_url"], NEW_LINK)
        self.assertEqual(by_id["airline"]["entry_url"], "", "多家航司允许无单一入口")
        self.assertEqual(by_id["airline"]["status"], "not_available", "未接入显式声明")
        self.assertEqual(by_id["airline"]["reason"], "暂无合规公开接口，未接入")
        self.assertEqual(by_id["snapshot_pack"]["status"], "active", "快照渠道在册")
        self.assertEqual(by_id["snapshot_pack"]["name"], "实测快照")

    def test_deals_payload_two_sections(self):
        _, deals = webapp._api_deals({"from": "SHA", "to": "JJN"})
        self.assertTrue(deals["ok"])
        self.assertIn("[真实商品]", deals["notice"])
        specials = deals["special_fares"]
        self.assertEqual(len(specials), 1, "SHA-JJN 仅 10-27 一条特价票")
        self.assertEqual((specials[0]["date"], specials[0]["price"]), ("2026-10-27", 179))
        self.assertEqual(specials[0]["destination_display"], "泉州")
        self.assertIsNone(specials[0]["flight_no"], "卡面未展示航班号（不编造）")
        self.assertIn("不含机建燃油费", specials[0]["label"])
        cards = deals["cards"]
        self.assertEqual(len(cards), 6, "卡池 6 张（直播闪购引擎卡已退场）")
        for card in cards:
            self.assertIn("entry_url", card)
            self.assertIn("entry_note", card)
            self.assertIn("refund", card)
        self.assertTrue(any(c["card_id"] == "DY-103" and c["stock"] == "售罄" for c in cards))
        self.assertTrue(any("退改规则未披露" in c["refund"] for c in cards))
        _, all_deals = webapp._api_deals({})
        self.assertEqual(len(all_deals["special_fares"]), 21, "不过滤时 21 条特价票全量")

    def test_nearby_bar_uses_snapshot_verbatim(self):
        params = {"from": "INC", "to": "CAN", "date": "2026-11-01"}
        with _pinned():
            status, payload = webapp._api_nearby(dict(params))
            _, again = webapp._api_nearby(dict(params))
        self.assertEqual((status, payload["ok"]), (200, True))
        self.assertTrue(payload["covered"])
        self.assertEqual([(p["date"], p["price"]) for p in payload["bar"]],
                         [("2026-10-30", 450), ("2026-10-31", 450),
                          ("2026-11-01", 500), ("2026-11-02", 490)],
                         "临近日期最低价条与快照逐字一致")
        self.assertEqual(payload["price_basis"], "裸价（不含机建+燃油）")
        self.assertIn("航旅纵横", payload["notice"])
        self.assertEqual(payload, again, "快照静态数据，重复请求完全一致")

    def test_nearby_uncovered_route_empty_with_hint(self):
        with _pinned(dt.date(2026, 11, 20)):
            status, payload = webapp._api_nearby({"from": "SHA", "to": "CTU",
                                                  "date": "2026-11-20"})
        self.assertEqual(status, 200)
        self.assertFalse(payload["covered"])
        self.assertEqual(payload["bar"], [])
        self.assertIn("暂无临近日期快照数据", payload["notice"])
        self.assertIn("银川(INC) → 广州(CAN) 2026-11-01", payload["notice"])

    def test_nearby_rejects_bad_input(self):
        for params in ({"from": "SHA", "to": "SHA", "date": "2026-11-20"},
                       {"from": "SHA", "to": "HKG", "date": "2026-11-20"},
                       {"from": "SHA", "to": "CTU"}):
            status, payload = webapp._api_nearby(dict(params))
            self.assertEqual(status, 400, params)
            self.assertFalse(payload["ok"])

    def test_search_deterministic_same_input(self):
        params = {"from": "INC", "to": "CAN", "date": "2026-11-01"}
        with pricing.fixed_minute(PINNED_MINUTE):
            _, first = webapp._api_search(dict(params))
        with pricing.fixed_minute(PINNED_MINUTE + 600):
            _, second = webapp._api_search(dict(params))
        self.assertEqual(first, second, "不同查询时刻载荷完全一致（快照静态，无随机）")


if __name__ == "__main__":
    unittest.main()
