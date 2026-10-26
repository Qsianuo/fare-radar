# -*- coding: utf-8 -*-
"""Duffel 适配器单测（v1.16.0，ISSUE-025）——**零真实请求**。

本模块全程离线：映射测试喂 `tests/fixtures/duffel_offer_request_sample.json`
（按官方文档响应结构构造的形态样本，**不是真实响应**——本迭代尚无 live token，
联调后需按实测回填），网络路径一律用假 opener/mock 替换。

重点覆盖三条红线：
1. `duffel_test_`（测试模式=沙盒航司 Duffel Airways 虚构数据）**绝不发请求**、绝不出行；
2. 非 CNY 报价**不做汇率折算**（不替用户编价），行保留但价格位标未披露且不参与比价；
3. `FARE_RADAR_OFFLINE=1`（子进程场景）同样短路。
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sources import duffel  # noqa: E402

# 与其余 5 个模块一致：本进程内所有联网源指向不存在的配置（零真实请求）
import sources.amadeus as _am  # noqa: E402
import sources.juhe as _ju  # noqa: E402
import sources.rapidapi_sky as _rp  # noqa: E402
import sources.travelpayouts as _tp  # noqa: E402
import sources.variflight as _vf  # noqa: E402

_ABSENT = "_test_offline_absent.json"
for _module in (_am, _ju, _rp, _tp, _vf):
    _module.CONFIG_NAME = _ABSENT
duffel.CONFIG_NAME = _ABSENT

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "duffel_offer_request_sample.json"
LIVE = "duffel_live_" + "x" * 32          # 形态正确即视为 live（不会真的发请求）
TEST = "duffel_test_" + "x" * 32


def _payload():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _offer(offer_id):
    for item in _payload()["data"]["offers"]:
        if item["id"] == offer_id:
            return item
    raise AssertionError(f"样本里没有 {offer_id}")


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def read(self):
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeOpener:
    """记录请求；可返回固定 payload 或抛指定异常。"""

    def __init__(self, payload=None, error=None):
        self.requests = []
        self._payload = payload if payload is not None else {}
        self._error = error

    def open(self, request, timeout=None):
        self.requests.append(request)
        if self._error is not None:
            raise self._error
        return _FakeResponse(self._payload)


class _TmpSourceCase(unittest.TestCase):
    """每个用例独立临时目录，避免缓存/计数器互相污染。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.cfg = Path(self._tmp.name) / "duffel_config.json"

    def tearDown(self):
        self._tmp.cleanup()

    def _source(self, token=LIVE, **extra):
        payload = {"access_token": token}
        payload.update(extra)
        self.cfg.write_text(json.dumps(payload), encoding="utf-8")
        return duffel.DuffelSource(config_path=self.cfg)

    def _cache_file(self, name):
        return self.cfg.parent / ".duffel_cache" / name


class StatusGateTest(_TmpSourceCase):
    """闸门：只有 live token 才允许联网；测试模式/未知/无 token 一律 not_available。"""

    def test_no_config_is_not_available(self):
        source = duffel.DuffelSource(config_path=self.cfg)   # 文件不存在
        self.assertEqual(source.status, "not_available")
        self.assertEqual(source.mode, "none")
        self.assertIn("未配置访问令牌", source.status_reason)

    def test_live_token_is_active(self):
        source = self._source()
        self.assertEqual(source.mode, "live")
        self.assertEqual(source.status, "active")
        self.assertEqual(source.status_reason, "")
        self.assertTrue(source.availability("PVG", "LHR", _date())[0])

    def test_test_mode_token_is_rejected_with_reason(self):
        """测试模式=虚构数据：必须显式拒绝，并说明是 Duffel Airways 沙盒。"""
        source = self._source(token=TEST)
        self.assertEqual(source.mode, "test")
        self.assertEqual(source.status, "not_available")
        reason = source.status_reason
        self.assertIn("测试模式", reason)
        self.assertIn("Duffel Airways", reason)
        self.assertIn("虚构", reason)

    def test_test_mode_token_never_sends_request(self):
        """硬闸门：测试 token 下 search 必须直接返回 []，不得触达网络层。"""
        source = self._source(token=TEST)
        opener = _FakeOpener(payload=_payload())
        with mock.patch.object(source, "_opener", return_value=opener):
            self.assertEqual(source.search("PVG", "LHR", _date()), [])
        self.assertEqual(opener.requests, [], "测试模式 token 不得发起任何请求")

    def test_unknown_token_shape_is_rejected(self):
        source = self._source(token="sk-whatever")
        self.assertEqual(source.mode, "unknown")
        self.assertEqual(source.status, "not_available")
        self.assertIn("无法识别", source.status_reason)

    def test_offline_env_short_circuits_even_with_live_token(self):
        source = self._source()
        with mock.patch.dict(os.environ, {"FARE_RADAR_OFFLINE": "1"}):
            self.assertEqual(source.status, "not_available")
            self.assertIn("离线", source.status_reason)
            available, reason = source.availability("PVG", "LHR", _date())
            self.assertFalse(available)
            opener = _FakeOpener(payload=_payload())
            with mock.patch.object(source, "_opener", return_value=opener):
                self.assertEqual(source.search("PVG", "LHR", _date()), [])
            self.assertEqual(opener.requests, [])


class RequestShapeTest(_TmpSourceCase):
    """请求形态按官方文档：POST + Duffel-Version v2 + data.slices/passengers。"""

    def test_request_matches_documented_shape(self):
        source = self._source()
        opener = _FakeOpener(payload=_payload())
        with mock.patch.object(source, "_opener", return_value=opener):
            source.search("PVG", "LHR", _date())
        self.assertEqual(len(opener.requests), 1)
        request = opener.requests[0]
        self.assertEqual(request.get_method(), "POST")
        self.assertIn("return_offers=true", request.full_url)
        self.assertIn("supplier_timeout=20000", request.full_url)
        headers = {k.lower(): v for k, v in request.headers.items()}
        self.assertEqual(headers.get("duffel-version"), "v2")
        self.assertEqual(headers.get("authorization"), f"Bearer {LIVE}")
        self.assertEqual(headers.get("content-type"), "application/json")
        body = json.loads(request.data.decode("utf-8"))
        self.assertEqual(body["data"]["passengers"], [{"type": "adult"}])
        self.assertEqual(body["data"]["cabin_class"], "economy")
        slice_ = body["data"]["slices"][0]
        self.assertEqual(slice_["origin"], "PVG")
        self.assertEqual(slice_["destination"], "LHR")
        self.assertEqual(slice_["departure_date"], _date().isoformat())


class MappingTest(_TmpSourceCase):
    def test_direct_cny_offer_maps_completely(self):
        row = self._source()._to_offer(_offer("off_0000A7XyZ0001"))
        self.assertEqual(row["source_id"], "duffel")
        self.assertEqual(row["product"], "直飞｜东航")
        self.assertEqual(row["flight_no"], "MU551")
        self.assertEqual(row["carrier_name"], "东航")
        self.assertEqual(row["depart_time"], "13:20")
        self.assertEqual(row["arrive_time"], "18:05")
        self.assertFalse(row["cross_day"])
        self.assertEqual(row["duration_text"], "12小时45分")
        self.assertEqual(row["dep_airport"], "上海浦东国际机场（PVG）")
        self.assertEqual(row["arr_airport"], "伦敦希思罗机场（LHR）")
        self.assertEqual(row["price"], 4321)
        self.assertTrue(row["tax_included"])
        self.assertEqual(row["total_estimate"], 4321)
        self.assertIsNone(row["price_note"])

    def test_foreign_currency_is_not_converted(self):
        """GBP 报价不得折算成人民币（折算=替用户编价），价格位保留为空。"""
        row = self._source()._to_offer(_offer("off_0000A7XyZ0002"))
        self.assertIsNone(row["price"])
        self.assertIsNone(row["total_estimate"])
        self.assertIsNone(row["tax_included"])
        self.assertIn("GBP 512.30", row["price_note"])
        self.assertIn("非人民币", row["price_note"])
        self.assertIn("不参与比价", row["price_note"])

    def test_connecting_legs_count_and_cross_day(self):
        source = self._source()
        one_stop = source._to_offer(_offer("off_0000A7XyZ0002"))
        self.assertEqual(one_stop["product"], "中转｜1 段｜国泰")
        self.assertEqual(one_stop["flight_no"], "CX365/BA28")
        self.assertTrue(one_stop["cross_day"])
        self.assertEqual(one_stop["duration_text"], "16小时30分")

        two_stops = source._to_offer(_offer("off_0000A7XyZ0003"))
        self.assertEqual(two_stops["product"], "中转｜2 段｜南航")
        self.assertEqual(two_stops["flight_no"], "CZ3548/CZ307/KL1009")
        self.assertTrue(two_stops["cross_day"])
        self.assertEqual(two_stops["price"], 3980)

    def test_offer_without_slices_is_dropped(self):
        self.assertIsNone(self._source()._to_offer(_offer("off_0000A7XyZ0004")))

    def test_search_skips_malformed_offer(self):
        source = self._source()
        opener = _FakeOpener(payload=_payload())
        with mock.patch.object(source, "_opener", return_value=opener):
            rows = source.search("PVG", "LHR", _date())
        self.assertEqual(len(rows), 3)

    def test_carrier_falls_back_to_upstream_name_then_code(self):
        source = self._source()
        self.assertEqual(source._carrier_name("BA", "British Airways"), "英航")
        self.assertEqual(source._carrier_name("ZZ", "Duffel Airways"), "Duffel Airways")
        self.assertEqual(source._carrier_name("ZZ", ""), "ZZ")

    def test_duration_parsing_variants(self):
        parse = duffel.DuffelSource._duration_text
        self.assertEqual(parse("PT2H26M"), "2小时26分")
        self.assertEqual(parse("PT45M"), "45分")
        self.assertEqual(parse("PT8H"), "8小时00分")
        self.assertEqual(parse(""), "未披露")
        self.assertEqual(parse("P1D"), "未披露")
        self.assertEqual(parse("PTXX"), "未披露")

    def test_unknown_airport_falls_back_to_upstream_name(self):
        source = self._source()
        node = {"iata_code": "QQQ", "name": "Somewhere Airport"}
        self.assertEqual(source._airport(node), "Somewhere Airport（QQQ）")
        self.assertEqual(source._airport({"iata_code": "CAN"}), "广州白云国际机场")


class CacheAndLimitTest(_TmpSourceCase):
    def test_second_search_hits_cache(self):
        source = self._source()
        opener = _FakeOpener(payload=_payload())
        with mock.patch.object(source, "_opener", return_value=opener):
            first = source.search("PVG", "LHR", _date())
            second = source.search("PVG", "LHR", _date())
        self.assertEqual(len(opener.requests), 1, "同航线同日期的第二次查询应命中缓存")
        self.assertEqual(first, second)

    def test_daily_cap_blocks_before_request(self):
        source = self._source()
        self._cache_file("day_counter.json").parent.mkdir(parents=True, exist_ok=True)
        self._cache_file("day_counter.json").write_text(json.dumps(
            {"date": _today(), "calls": duffel.DAILY_CALL_CAP}), encoding="utf-8")
        opener = _FakeOpener(payload=_payload())
        with mock.patch.object(source, "_opener", return_value=opener):
            with self.assertRaises(RuntimeError) as ctx:
                source.search("PVG", "LHR", _date())
        self.assertIn("上限", str(ctx.exception))
        self.assertEqual(opener.requests, [])

    def test_empty_result_cached_and_explained(self):
        source = self._source()
        opener = _FakeOpener(payload={"data": {"offers": []}})
        with mock.patch.object(source, "_opener", return_value=opener):
            self.assertEqual(source.search("INC", "CTU", _date()), [])
        note = source.empty_note("INC", "CTU", _date())
        self.assertIn("无报价", note)
        self.assertIn("国内线", note)

    def test_empty_note_is_none_when_rows_exist(self):
        source = self._source()
        opener = _FakeOpener(payload=_payload())
        with mock.patch.object(source, "_opener", return_value=opener):
            source.search("PVG", "LHR", _date())
        self.assertIsNone(source.empty_note("PVG", "LHR", _date()))

    def test_http_error_messages_are_actionable(self):
        text = duffel.DuffelSource._http_error_text
        self.assertIn("token 无效", text(401))
        self.assertIn("token 无效", text(403))
        self.assertIn("429", text(429))
        self.assertIn("三字码", text(422))
        self.assertIn("500", text(500))


class UpstreamLiveModeGateTest(_TmpSourceCase):
    """第二道闸门（v1.16.1）：响应顶层 live_mode=false 时整批不采用。

    前缀闸门只看 token 形状；若上游改前缀规则或账号被降级，这条响应级校验兜底。
    依据：2026-10-26 三条航线实测响应顶层均带 live_mode 字段（test 模式为 false）。
    """

    def test_upstream_live_mode_false_blocks_all_rows(self):
        payload = _payload()
        payload["data"]["live_mode"] = False
        source = self._source()
        opener = _FakeOpener(payload=payload)
        with mock.patch.object(source, "_opener", return_value=opener):
            self.assertEqual(source.search("PVG", "LHR", _date()), [])
        self.assertEqual(len(opener.requests), 1, "已发出请求才谈得上拦截响应")
        note = source.empty_note("PVG", "LHR", _date())
        self.assertIn("live_mode=false", note)

    def test_upstream_live_mode_true_keeps_rows(self):
        payload = _payload()
        payload["data"]["live_mode"] = True
        source = self._source()
        with mock.patch.object(source, "_opener", return_value=_FakeOpener(payload=payload)):
            self.assertTrue(source.search("PVG", "LHR", _date()))


class SortingTest(_TmpSourceCase):
    """上游返回顺序不可依赖：截前 12 条之前必须自己排序（v1.16.1）。

    依据：实测 PVG→LHR 82 条虽近似按价升序，但官方未承诺顺序；
    一旦乱序，直接取前 12 条会把真正便宜的行截掉。
    """

    def _payload_with_shuffled_prices(self):
        payload = _payload()
        base = _offer("off_0000A7XyZ0001")
        offers = []
        for i in range(15):
            item = json.loads(json.dumps(base))
            item["id"] = f"off_sort_{i:02d}"
            item["total_amount"] = f"{5000 - i * 100}.00"   # i=14 → 3600，最便宜的在最后
            item["total_currency"] = "CNY"
            offers.append(item)
        payload["data"]["offers"] = offers
        return payload

    def test_cheapest_offer_survives_truncation(self):
        source = self._source()
        opener = _FakeOpener(payload=self._payload_with_shuffled_prices())
        with mock.patch.object(source, "_opener", return_value=opener):
            rows = source.search("PVG", "LHR", _date())
        prices = [r["price"] for r in rows if r["price"] is not None]
        self.assertTrue(prices, "应产出可比较的行")
        self.assertEqual(min(prices), 3600, "最便宜的报价不得被截断丢掉")
        self.assertLessEqual(len(rows), 6, "展示条数仍受上限约束")


class RegistryTest(unittest.TestCase):
    def test_source_is_registered(self):
        from sources import all_sources
        hits = [s for s in all_sources() if s.source_id == "duffel"]
        self.assertEqual(len(hits), 1, "Duffel 应且只应注册一次")

    def test_registered_source_is_inactive_offline(self):
        from sources import all_sources
        source = next(s for s in all_sources() if s.source_id == "duffel")
        self.assertEqual(source.status, "not_available")


def _date():
    import datetime as dt
    return dt.date(2026, 12, 20)


def _today():
    import datetime as dt
    return dt.date.today().isoformat()


if __name__ == "__main__":
    unittest.main()
