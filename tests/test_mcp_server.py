# -*- coding: utf-8 -*-
"""MCP Server 离线测试：协议生命周期 + 四工具 + 子进程 stdio 集成（零真实请求）。

网络类票源（amadeus/rapidapi_sky）在测试中一律视为未配置 Key（离线红线）。
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

# 测试一律离线：网络票源视为未配置（同其他测试模块的注入方式）
import sources.amadeus as _am, sources.rapidapi_sky as _rp  # noqa: E402
import sources.variflight as _vf  # noqa: E402
import sources.travelpayouts as _tp  # noqa: E402
_am.CONFIG_NAME = _rp.CONFIG_NAME = _vf.CONFIG_NAME = "_test_offline_absent.json"
_tp.CONFIG_NAME = "_test_offline_absent.json"

import mcp_server  # noqa: E402

SNAP_ARGS = {"from": "INC", "to": "CAN", "date": "2026-11-01"}


def _call(name, arguments):
    resp = mcp_server.handle_message(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
         "params": {"name": name, "arguments": arguments}})
    assert resp is not None
    return resp["result"]["content"][0]["text"], resp["result"].get("isError", False)


class ProtocolLifecycleTest(unittest.TestCase):
    def test_initialize(self):
        resp = mcp_server.handle_message(
            {"jsonrpc": "2.0", "id": 7, "method": "initialize", "params": {}})
        self.assertEqual(resp["id"], 7)
        result = resp["result"]
        self.assertEqual(result["serverInfo"]["name"], "fareradar")
        self.assertIn("tools", result["capabilities"])
        self.assertTrue(result["instructions"])

    def test_initialized_notification_no_response(self):
        self.assertIsNone(mcp_server.handle_message(
            {"jsonrpc": "2.0", "method": "notifications/initialized"}))

    def test_ping(self):
        resp = mcp_server.handle_message({"jsonrpc": "2.0", "id": "x", "method": "ping"})
        self.assertEqual(resp["result"], {})

    def test_tools_list_contract(self):
        resp = mcp_server.handle_message(
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        tools = {t["name"]: t for t in resp["result"]["tools"]}
        self.assertEqual(set(tools), {"search_flights", "list_deals", "list_sources", "favorites"})
        for t in tools.values():
            self.assertIn("description", t)
            self.assertEqual(t["inputSchema"]["type"], "object")
        self.assertEqual(tools["favorites"]["inputSchema"]["properties"]["action"]["enum"],
                         ["list", "set", "remove", "check"])

    def test_unknown_method_and_tool(self):
        resp = mcp_server.handle_message({"jsonrpc": "2.0", "id": 3, "method": "no/such"})
        self.assertEqual(resp["error"]["code"], -32601)
        resp = mcp_server.handle_message(
            {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
             "params": {"name": "nope", "arguments": {}}})
        self.assertEqual(resp["error"]["code"], -32602)


class ToolsTest(unittest.TestCase):
    def test_search_snapshot_route(self):
        text, is_error = _call("search_flights", SNAP_ARGS)
        self.assertFalse(is_error)
        self.assertIn("MF8753", text)
        self.assertIn("07:40", text)
        self.assertIn("◀最低价", text)
        self.assertIn("不能用于实际购票", text)  # 诚实声明必在

    def test_search_uncovered_route_disclosure(self):
        text, _ = _call("search_flights", {"from": "SHA", "to": "PEK", "date": "2026-11-05"})
        self.assertIn("无销售", text)  # 未收录渠道如实披露

    def test_list_deals(self):
        text, _ = _call("list_deals", {})
        self.assertIn("¥", text)
        self.assertIn("特价票", text)

    def test_list_sources_honesty(self):
        text, _ = _call("list_sources", {})
        self.assertIn("✅ snapshot_pack", text)
        self.assertIn("⛔", text)

    def test_favorites_roundtrip(self):
        original = mcp_server.fr.ALERTS_FILE
        with tempfile.TemporaryDirectory() as tmp:
            mcp_server.fr.ALERTS_FILE = Path(tmp) / "alerts.json"
            try:
                text, _ = _call("favorites", {"action": "set", "from": "SHA", "to": "CTU"})
                self.assertIn("已", text)
                text, _ = _call("favorites", {"action": "list"})
                self.assertIn("SHA→CTU", text)
                text, _ = _call("favorites", {"action": "check"})
                self.assertIn("检查", text)
                text, _ = _call("favorites", {"action": "remove", "from": "SHA", "to": "CTU"})
                self.assertIn("删除", text)
            finally:
                mcp_server.fr.ALERTS_FILE = original  # 恢复共享状态，避免污染其他用例


class StdioIntegrationTest(unittest.TestCase):
    """子进程拉起真实 server，走完整 NDJSON 协议往返（纯本地）。"""

    def test_stdio_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = dict(os.environ, FARE_RADAR_ALERTS=str(Path(tmp) / "alerts.json"),
                       FARE_RADAR_OFFLINE="1",   # 子进程拿不到 monkeypatch，必须显式传
                       PYTHONIOENCODING="utf-8")
            proc = subprocess.run(
                [sys.executable, str(ROOT / "src" / "mcp_server.py")],
                input="\n".join([
                    json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}),
                    json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
                    json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
                    json.dumps({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                                "params": {"name": "search_flights", "arguments": SNAP_ARGS}}),
                ]),
                capture_output=True, text=True, encoding="utf-8", timeout=60, env=env)
            self.assertEqual(proc.returncode, 0, proc.stderr[-400:])
            lines = [json.loads(l) for l in proc.stdout.strip().splitlines() if l.strip()]
            by_id = {m["id"]: m for m in lines}
            self.assertEqual(by_id[1]["result"]["serverInfo"]["name"], "fareradar")
            self.assertEqual(len(by_id[2]["result"]["tools"]), 4)
            call_text = by_id[3]["result"]["content"][0]["text"]
            self.assertIn("MF8753", call_text)
            self.assertIn("不能用于实际购票", call_text)


if __name__ == "__main__":
    unittest.main()
