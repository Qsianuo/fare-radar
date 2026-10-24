# -*- coding: utf-8 -*-
"""FareRadar Web 版 API 层单测。

webapp 的业务规则全部复用 fareradar.py（已有 35 例覆盖），此处只测翻译层：
参数校验到 400、alerts 损坏到 409、search 载荷契约（准入式最低价标记）、
watch 设置/列表/检查/删除往返。
运行：python -m unittest discover tests -v（与 CLI 用例一起跑）
"""

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import fareradar as fr  # noqa: E402
import webapp  # noqa: E402
from sources import all_sources, pricing  # noqa: E402

PINNED_MINUTE = 29_500_000  # 价格随分钟演示波动：涉价断言先固定查询时刻


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
            offers, _failed, _unavailable = fr.collect(all_sources(), "SHA", "CTU", fr.parse_date("2026-11-20"))
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["offers"], "SHA-CTU 应有报价")
        lowest = payload["offers"][0]
        self.assertTrue(lowest["is_lowest"], "首行应为最低价行")
        self.assertEqual(lowest["price"],
                         fr.lowest_eligible(offers, fr.parse_date("2026-11-20"))["price"],
                         "网页最低价与 CLI 规则一致（引擎+演示波动）")
        self.assertTrue(430 <= lowest["price"] <= 490, "SHA-CTU 最低价应在精选券 459±波动带内")
        ineligible = [o for o in payload["offers"] if o["tax_label"] == "不含税"]
        self.assertTrue(ineligible)
        self.assertFalse(any(o["is_lowest"] for o in ineligible),
                         "不含税行不得竞争最低价（ISSUE-001 规则网页同样生效）")

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

    def test_watch_roundtrip(self):
        status, payload = webapp._api_watch_set(
            {"from": "SHA", "to": "PEK", "price": "700"})
        self.assertEqual((status, payload["ok"]), (200, True))
        _, listed = webapp._api_watch_list({})
        self.assertEqual(len(listed["alerts"]), 1)
        self.assertEqual(listed["alerts"][0]["threshold"], 700)
        status, payload = webapp._api_watch_check({})
        self.assertEqual(status, 200)
        # 引擎定价下 SHA→PEK 明日可比最低价恒低于 700（五源准入后取最小）
        self.assertEqual(payload["lines"][0]["status"], "hit")
        webapp._api_watch_set({"from": "SHA", "to": "PEK", "price": "1"})
        _, payload = webapp._api_watch_check({})
        self.assertEqual(payload["lines"][0]["status"], "wait", "底价 1 元必不命中")
        status, payload = webapp._api_watch_remove({"from": "SHA", "to": "PEK"})
        self.assertEqual((status, payload["ok"]), (200, True))
        _, listed = webapp._api_watch_list({})
        self.assertEqual(listed["alerts"], [])

    def test_watch_rejects_bad_input_and_corrupted_file(self):
        status, _ = webapp._api_watch_set({"from": "SHA", "to": "PEK", "price": "0"})
        self.assertEqual(status, 400, "底价必须为正整数")
        fr.ALERTS_FILE.write_text("{oops", encoding="utf-8")
        for api in (webapp._api_watch_set, webapp._api_watch_remove,
                    webapp._api_watch_list, webapp._api_watch_check):
            status, payload = api({"from": "SHA", "to": "PEK", "price": "700"})
            self.assertEqual(status, 409, api.__name__)
            self.assertIn("数据未被改动", payload["error"], "损坏时必须承诺不覆盖（ISSUE-002 规则）")


class V131CoverageContractTest(unittest.TestCase):
    """v1.3.1：票源覆盖模型与渠道入口的 API 契约（ISSUE-010）。"""

    def test_search_payload_reports_unavailable_sources(self):
        status, payload = webapp._api_search({"from": "CGO", "to": "XIY", "date": "2026-10-26"})
        self.assertEqual(status, 200)
        self.assertNotIn("douyin_miniapp", {o["source_id"] for o in payload["offers"]},
                         "非春秋基地航线不得出现小程序报价（不编造）")
        unavailable = {u["source_id"]: u for u in payload["unavailable_sources"]}
        self.assertIn("douyin_miniapp", unavailable)
        self.assertEqual(unavailable["douyin_miniapp"]["name"], "抖音小程序·春秋航空")
        self.assertIn("春秋基地", unavailable["douyin_miniapp"]["reason"])
        self.assertTrue(all(u.get("reason") for u in unavailable.values()))
        # 与 failed（异常降级）语义区分：本次无异常，failed 应为空
        self.assertEqual(payload["failed"], [])
        for offer in payload["offers"]:
            self.assertIn("entry_url", offer, "报价行携带渠道入口")
            self.assertIn("entry_note", offer)

    def test_sources_and_deals_carry_entry_url(self):
        _, sources = webapp._api_sources({})
        by_id = {s["id"]: s for s in sources["sources"]}
        self.assertEqual(by_id["douyin_miniapp"]["entry_url"],
                         "https://www.iesdouyin.com/share/microapp/"
                         "?token=QjdDMUQzOTlCQkU2NzZqc2I2YTdt&share_channel=copy")
        self.assertEqual(by_id["douyin_groupbuy"]["entry_url"],
                         "https://v.douyin.com/dIviP77Fqow/")
        self.assertEqual(by_id["airline"]["entry_url"], "", "多家航司允许无单一入口")
        _, deals = webapp._api_deals({"from": "SHA", "to": "CTU"})
        self.assertTrue(deals["deals"])
        for card in deals["deals"]:
            self.assertIn("entry_url", card)
            self.assertIn("entry_note", card)
        self.assertTrue(any(c["entry_url"].startswith("https://v.douyin.com/")
                            for c in deals["deals"]), "团购卡应带地点页入口")


if __name__ == "__main__":
    unittest.main()
