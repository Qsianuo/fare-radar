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


class WebApiTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self._old_alerts = fr.ALERTS_FILE
        self.addCleanup(setattr, fr, "ALERTS_FILE", self._old_alerts)
        fr.ALERTS_FILE = Path(tmp.name) / "alerts.json"

    def test_search_payload_contract(self):
        status, payload = webapp._api_search(
            {"from": "SHA", "to": "CTU", "date": "2026-11-20"})
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["offers"], "SHA-CTU 应有报价")
        lowest = payload["offers"][0]
        self.assertTrue(lowest["is_lowest"], "首行应为最低价行")
        self.assertEqual(lowest["price"], 459, "准入规则与 CLI 一致：含税 ¥459 券拿最低价")
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
        self.assertEqual(payload["lines"][0]["status"], "wait")
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


if __name__ == "__main__":
    unittest.main()
