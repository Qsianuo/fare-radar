# -*- coding: utf-8 -*-
"""FareRadar v1.13 春秋券库单测（ISSUE-022）。

需求三条验收线，逐条对应测试：
① 截图结构化入库：101 条券适用航线 / 52 条日期价目 / 2 张券，且可重复入库不重复；
② 航班查询**不返回价格**：递归扫描返回体，除白名单键外不得出现任何 price 语义字段，
   也不得出现「¥」符号；航班号/时刻必须为 None（截图未披露，禁止编造）；
③ 券匹配与折算：209 元 2 次卡 → 104.5 元/次；省钱卡 → 按当日专享价；
   跨税费口径不自动排名；日期不匹配不外推到邻近日期。

运行：python -m unittest discover tests -v
"""

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from sources import spring_store  # noqa: E402

# 零真实请求说明：券库是本地 SQLite，本模块不发起任何联网调用，因此**不设**
# FARE_RADAR_OFFLINE——该变量是进程级开关，在测试模块里全局设置会连带把
# rapidapi/variflight 的适配器单测打成 not_available（ISSUE-019 踩过的坑）。

# 递归扫描时允许出现的"非价格"键（语义是声明，不是数值）
_PRICE_KEY_ALLOWLIST = {"price_returned", "price_note", "price_basis"}


class _DbCase(unittest.TestCase):
    """每个用例一份独立临时库，避免测试污染 src/data 下的生产库。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = str(Path(self._tmp.name) / "spring_test.db")
        spring_store.ingest(db_path=self.db)

    def tearDown(self):
        self._tmp.cleanup()


class IngestTest(_DbCase):
    def test_counts_verbatim_from_screenshots(self):
        cover = spring_store.coverage(self.db)
        self.assertEqual(cover["coupon_count"], 2)
        self.assertEqual(cover["route_count"], 101)     # 3 张航线表逐行转录
        self.assertEqual(cover["offer_count"], 52)      # 4 张价目表去重后
        self.assertEqual(cover["offer_date_min"], "2026-10-27")
        self.assertEqual(cover["offer_date_max"], "2026-11-24")

    def test_rebuild_is_idempotent(self):
        spring_store.ingest(db_path=self.db)            # 第二次整表重建
        cover = spring_store.coverage(self.db)
        self.assertEqual((cover["route_count"], cover["offer_count"]), (101, 52))

    def test_per_trip_price_is_104_5(self):
        """209 ÷ 2 = 104.5：用户需求里点名的折算值，必须落在库里而非运行期临时算。"""
        import sqlite3
        conn = sqlite3.connect(self.db)
        per_trip = conn.execute("SELECT per_trip_cny FROM coupon "
                                "WHERE coupon_id='SP-9C-209AUT2'").fetchone()[0]
        conn.close()
        self.assertAlmostEqual(per_trip, 104.5, places=2)

    def test_undisclosed_fields_stay_null(self):
        """卡面没写的（有效期/税费口径/省钱卡卡费）一律 NULL，不许补默认值。"""
        import sqlite3
        conn = sqlite3.connect(self.db)
        row = conn.execute("SELECT price_cny, tax_included, valid_from, valid_until "
                           "FROM coupon WHERE coupon_id='SP-9C-SAVECARD'").fetchone()
        conn.close()
        self.assertEqual(row, (None, None, None, None))


class NoPriceLeakTest(_DbCase):
    """② 航班查询不得返回价格——本需求的硬约束，用递归扫描守住。"""

    def _walk(self, node, path="root"):
        if isinstance(node, dict):
            for key, value in node.items():
                yield from self._walk(value, f"{path}.{key}")
            yield path, node
        elif isinstance(node, list):
            for index, value in enumerate(node):
                yield from self._walk(value, f"{path}[{index}]")

    def test_no_price_field_anywhere(self):
        payload = spring_store.available_flights("SHA", "通辽", "2026-11-04", self.db)
        self.assertTrue(payload["ok"])
        for path, node in self._walk(payload):
            if isinstance(node, dict):
                for key in node:
                    lowered = key.lower()
                    if "price" in lowered or "amount" in lowered or "fare_cny" in lowered:
                        self.assertIn(key, _PRICE_KEY_ALLOWLIST,
                                      f"{path} 出现价格字段 {key}")
            if isinstance(node, str):
                self.assertNotIn("¥", node, f"{path} 出现金额符号")

    def test_ads_declares_no_price(self):
        payload = spring_store.available_flights("SHA", "大连", "2026-11-09", self.db)
        self.assertFalse(payload["price_returned"])
        self.assertIn("coupon_quote", payload["price_note"])

    def test_flight_number_and_time_never_fabricated(self):
        payload = spring_store.available_flights("SHA", "大连", "2026-11-09", self.db)
        self.assertTrue(payload["flights"])
        for item in payload["flights"]:
            self.assertIsNone(item["flight_no"])
            self.assertIsNone(item["depart_time"])
            self.assertIsNone(item["arrive_time"])
            self.assertFalse(item["flight_info_disclosed"])


class CouponMatchTest(_DbCase):
    def test_route_list_card_hits_and_converts(self):
        """209 元 2 次卡：航线命中即 104.5 元/次，与航线原价无关。"""
        quote = spring_store.coupon_quote("SHA", "通辽", "2026-11-04", self.db)
        card = next(q for q in quote["quotes"] if q["coupon_id"] == "SP-9C-209AUT2")
        self.assertAlmostEqual(card["unit_price_cny"], 104.5, places=2)
        self.assertIn("209", card["formula"])
        self.assertIn("2 次", card["formula"])
        self.assertEqual(card["unit"], "元/人次/单程")

    def test_card_not_applicable_outside_route_list(self):
        """上海→石家庄不在 2 次卡 101 条适用范围里，只有省钱卡命中。"""
        quote = spring_store.coupon_quote("SHA", "石家庄", "2026-11-04", self.db)
        ids = [q["coupon_id"] for q in quote["quotes"]]
        self.assertIn("SP-9C-SAVECARD", ids)
        self.assertNotIn("SP-9C-209AUT2", ids)

    def test_listed_fare_needs_exact_date(self):
        """省钱卡专享价按日期定价：日期不符不外推出一个价。"""
        quote = spring_store.coupon_quote("SHA", "通辽", "2026-11-05", self.db)
        saving = next(q for q in quote["quotes"] if q["coupon_id"] == "SP-9C-SAVECARD")
        self.assertIsNone(saving["unit_price_cny"])
        self.assertTrue(saving["candidate_dates"])
        self.assertIn("邻近日期外推", saving["formula"])

    def test_listed_fare_exact_date_price(self):
        quote = spring_store.coupon_quote("上海", "大连", "2026-11-09", self.db)
        saving = next(q for q in quote["quotes"] if q["coupon_id"] == "SP-9C-SAVECARD")
        self.assertAlmostEqual(saving["unit_price_cny"], 130.0, places=2)
        self.assertEqual(saving["price_basis"], spring_store.BASIS_TAX_EXCLUDED)

    def test_no_date_returns_candidates_not_average(self):
        quote = spring_store.coupon_quote("SHA", "大连", None, self.db)
        saving = next(q for q in quote["quotes"] if q["coupon_id"] == "SP-9C-SAVECARD")
        self.assertIsNone(saving["unit_price_cny"])
        self.assertIn("不做跨日期平均", "".join(saving["warnings"]))

    def test_cross_basis_not_ranked(self):
        """未披露口径 vs 不含税口径 → 不给单一"最低价"，改为分口径 + 提示。"""
        quote = spring_store.coupon_quote("SHA", "通辽", "2026-11-04", self.db)
        self.assertIsNone(quote["best_unit_price_cny"])
        self.assertEqual(len(quote["basis_groups"]), 2)
        self.assertTrue(quote["cross_basis_note"])
        self.assertIn("不能直接比大小", quote["cross_basis_note"])

    def test_same_basis_is_ranked(self):
        quote = spring_store.coupon_quote("SHA", "台北", "2026-11-16", self.db)
        self.assertAlmostEqual(quote["best_unit_price_cny"], 439.0, places=2)
        self.assertEqual(quote["best_coupon_id"], "SP-9C-SAVECARD")

    def test_uncovered_route_explains_instead_of_empty_price(self):
        quote = spring_store.coupon_quote("INC", "CAN", "2026-11-01", self.db)
        self.assertTrue(quote["ok"])
        self.assertEqual(quote["quotes"], [])
        self.assertIsNone(quote["best_unit_price_cny"])
        self.assertIn("101", quote["miss_hint"])


class CityResolveTest(_DbCase):
    def test_alias_and_code_both_work(self):
        self.assertEqual(spring_store.resolve_city("SWA"), "揭阳(潮汕)")
        self.assertEqual(spring_store.resolve_city("揭阳"), "揭阳(潮汕)")
        self.assertEqual(spring_store.resolve_city("潮汕"), "揭阳(潮汕)")
        self.assertEqual(spring_store.resolve_city("SHA"), "上海")
        self.assertEqual(spring_store.resolve_city("上海"), "上海")

    def test_unknown_city_rejected(self):
        payload = spring_store.available_flights("XXX", "上海", "2026-11-04", self.db)
        self.assertFalse(payload["ok"])
        self.assertIn("无法识别", payload["error"])

    def test_same_city_rejected(self):
        payload = spring_store.coupon_quote("上海", "SHA", "2026-11-04", self.db)
        self.assertFalse(payload["ok"])

    def test_bad_date_format_rejected(self):
        with self.assertRaises(ValueError):
            spring_store.coupon_quote("SHA", "通辽", "11-04", self.db)


class EntryParityTest(unittest.TestCase):
    """三入口同源：Web API 与 MCP 工具复用同一套 spring_store 规则。"""

    @classmethod
    def setUpClass(cls):
        import webapp  # noqa: E402
        import mcp_server  # noqa: E402
        cls.webapp = webapp
        cls.mcp = mcp_server

    def test_web_api_price_split(self):
        status, payload = self.webapp._api_spring_flights(
            {"from": "SHA", "to": "通辽", "date": "2026-11-04"})
        self.assertEqual(status, 200)
        self.assertFalse(payload["price_returned"])
        status, quote = self.webapp._api_spring_quote(
            {"from": "SHA", "to": "通辽", "date": "2026-11-04"})
        self.assertEqual(status, 200)
        self.assertAlmostEqual(quote["quotes"][0]["unit_price_cny"], 104.5, places=2)

    def test_web_api_rejects_missing_param(self):
        status, payload = self.webapp._api_spring_quote({"from": "SHA"})
        self.assertEqual(status, 400)
        self.assertIn("to", payload["error"])

    def test_mcp_tools_declared(self):
        names = [t["name"] for t in self.mcp._TOOLS]
        self.assertIn("spring_flights", names)
        self.assertIn("spring_coupon_quote", names)
        flights_tool = next(t for t in self.mcp._TOOLS if t["name"] == "spring_flights")
        self.assertIn("不返回任何价格", flights_tool["description"])

    def test_mcp_flights_text_has_no_amount(self):
        text = self.mcp._fmt_spring_flights(
            {"from": "SHA", "to": "通辽", "date": "2026-11-04"})
        self.assertNotIn("¥", text)
        self.assertIn("不返回价格", text)

    def test_mcp_quote_text_has_104_5(self):
        text = self.mcp._fmt_spring_quote(
            {"from": "SHA", "to": "通辽", "date": "2026-11-04"})
        self.assertIn("¥104.5", text)
        self.assertIn("不自动排名", text)


if __name__ == "__main__":
    unittest.main()
