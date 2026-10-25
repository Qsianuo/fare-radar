# -*- coding: utf-8 -*-
"""FareRadar v1.4 特性单测（v1.5 口径改写，ISSUE-011/012）。

覆盖：航司名册与枢纽规则、航班时刻不变式（时段覆盖/不跨日/5 分钟粒度）、
税费与到手价（分档边界 800km）、跨渠道去重不变式、21 条固定特价票逐字校验
与仅当日可售（v1.5：不再编造航班号/时刻）、次卡卡池（命中规则/未披露退改/
售罄/不计入比价）、已下线城市收藏兼容、CLI 表格 CJK 对齐。
运行：python -m unittest discover tests -v
"""

import datetime as dt
import io
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import fareradar as fr  # noqa: E402

# 测试一律离线：网络类真实接口票源视为未配置 Key（零真实请求）。
# 注：unittest discover 以顶层模块导入，tests/__init__.py 不执行，故逐模块注入。
import sources.amadeus as _am, sources.rapidapi_sky as _rp  # noqa: E402
_am.CONFIG_NAME = _rp.CONFIG_NAME = "_test_offline_absent.json"

import webapp  # noqa: E402
from sources import airlines, all_sources, cities, fees, pricing  # noqa: E402
from sources.douyin_groupbuy import CARDS, DouyinGroupbuySource  # noqa: E402
from sources.douyin_miniapp import _SPECIAL_FARES, DouyinMiniappSource  # noqa: E402

PINNED_MINUTE = 29_500_000
SPECIAL_DATE_FMT = "%Y-%m-%d"
SNAPSHOT_DATE = dt.date(2026, 11, 1)


def _pinned(date=SNAPSHOT_DATE):
    """旁路 parse_date 的"不早于今天"校验（快照日期可能成为过去）。"""
    return mock.patch.object(fr, "parse_date", lambda raw: date)


def _minutes(hhmm):
    return int(hhmm[:2]) * 60 + int(hhmm[3:])


class AirlinesRegistryTest(unittest.TestCase):
    """PRD v1.4 第 6 节：6 航司名册与枢纽。"""

    ROSTER = {"CA": ("国航", ("PEK",)), "MU": ("东航", ("SHA",)), "CZ": ("南航", ("CAN",)),
              "HU": ("海航", ("PEK", "HAK")), "9C": ("春秋", ("SHA", "SHE", "SZX")),
              "HO": ("吉祥", ("SHA", "HGH"))}

    def test_roster_matches_prd(self):
        self.assertEqual(set(airlines.AIRLINES), set(self.ROSTER))
        for code, (name, hubs) in self.ROSTER.items():
            self.assertEqual(airlines.AIRLINES[code]["name"], name)
            self.assertEqual(tuple(airlines.AIRLINES[code]["hubs"]), hubs)

    def test_carrier_name_lookup(self):
        self.assertEqual(airlines.carrier_name("9C"), "春秋")
        self.assertIsNone(airlines.carrier_name("XX"))


class HubCarrierRuleTest(unittest.TestCase):
    """承运人分配：枢纽归属优先，通达航线全名册确定性分布。"""

    def test_hub_route_uses_hub_carriers_only(self):
        # SHA 出发优先 MU/9C/HO（三端枢纽并集）；CA/CZ 不应执飞纯 SHA 系航线
        for origin, destination in (("SHA", "CTU"), ("SHE", "SHA"), ("SZX", "HGH")):
            flights = airlines.route_flights(origin, destination)
            pool = {c for c in airlines.AIRLINES
                    if origin in airlines.AIRLINES[c]["hubs"]
                    or destination in airlines.AIRLINES[c]["hubs"]}
            for flight in flights:
                self.assertIn(flight["carrier_code"], pool)

    def test_non_hub_route_distributes_full_roster(self):
        # 济南-南昌两端均非任何航司枢纽 → 全名册确定性分布
        flights = airlines.route_flights("TNA", "NNG")
        for flight in flights:
            self.assertIn(flight["carrier_code"], airlines.AIRLINES)
        again = airlines.route_flights("TNA", "NNG")
        self.assertEqual([f["flight_no"] for f in flights], [f["flight_no"] for f in again],
                         "承运人分配确定性（哈希决定，无随机）")

    def test_all_six_carriers_appear_across_network(self):
        seen = set()
        for origin, destination in (("SHA", "CTU"), ("SHA", "PEK"), ("PEK", "CAN"),
                                    ("CAN", "SHA"), ("HAK", "SHA"), ("SHE", "SZX")):
            for flight in airlines.route_flights(origin, destination):
                seen.add(flight["carrier_code"])
        self.assertTrue(seen <= set(airlines.AIRLINES))
        self.assertLessEqual({"CA", "MU", "CZ", "HU", "9C", "HO"} - seen, set(),
                             f"6 航司均应在网络上执飞，实际 {seen}")


class ScheduleInvariantTest(unittest.TestCase):
    """PRD v1.4 第 5 节：时刻与机场字段的硬不变式。"""

    SAMPLE_ROUTES = (("SHA", "CTU"), ("SHA", "PEK"), ("PEK", "CAN"), ("SHA", "OHE"),
                     ("HRB", "SYX"), ("TNA", "NNG"), ("SHA", "HTN"), ("KMG", "URC"),
                     ("SHA", "LXA"), ("HAK", "SHA"))

    def test_period_coverage_per_route(self):
        for origin, destination in self.SAMPLE_ROUTES:
            flights = airlines.route_flights(origin, destination)
            self.assertGreaterEqual(len(flights), 3)
            periods = [f["period"] for f in flights]
            self.assertEqual(set(periods), {"morning", "noon", "evening"},
                             f"{origin}-{destination} 早/午/晚各 ≥1 班")

    def test_time_fields_deterministic_and_consistent(self):
        for origin, destination in self.SAMPLE_ROUTES:
            flights = airlines.route_flights(origin, destination)
            again = airlines.route_flights(origin, destination)
            self.assertEqual(flights, again, "同航线两次生成完全一致（确定性）")
            for flight in flights:
                dep, arr = _minutes(flight["depart_time"]), _minutes(flight["arrive_time"])
                self.assertEqual(dep % 5, 0, "起飞时刻 5 分钟粒度")
                self.assertEqual(arr % 5, 0, "到达时刻 5 分钟粒度")
                self.assertEqual(arr, dep + flight["duration_minutes"],
                                 "到达时间 = 起飞 + 航程时长")
                self.assertLessEqual(arr, 23 * 60 + 59, "不允许跨日到达")
                window = {"morning": (360, 719), "noon": (720, 1079), "evening": (1080, 1439)}[flight["period"]]
                self.assertTrue(window[0] <= dep <= window[1], "起飞落在所属时段窗口内")

    def test_duration_from_distance(self):
        # SHA-NKG ≈ 266km → 时长 ≈ 35 + 266/780*60 ≈ 55.5 → 60 分钟
        self.assertEqual(airlines.duration_minutes("SHA", "NKG"), 60)
        self.assertEqual(airlines.duration_minutes("SHA", "HKG"), None)  # 已下线城市
        self.assertEqual(airlines.duration_minutes("SHA", "SHA"), None)  # 同城

    def test_airports_are_registry_full_names(self):
        flights = airlines.route_flights("SHA", "JJN")
        for flight in flights:
            self.assertEqual(flight["dep_airport"], cities.airport_name("SHA"))
            self.assertEqual(flight["arr_airport"], cities.airport_name("JJN"))
            self.assertEqual(flight["dep_airport"], "上海虹桥国际机场")
            self.assertEqual(flight["arr_airport"], "泉州晋江国际机场")

    def test_flight_no_format_and_route_uniqueness(self):
        for origin, destination in self.SAMPLE_ROUTES:
            flights = airlines.route_flights(origin, destination)
            numbers = [f["flight_no"] for f in flights]
            for number, flight in zip(numbers, flights):
                self.assertRegex(number, rf"^{flight['carrier_code']}\d{{4}}$",
                                 "航班号 = 承运人二字码 + 4 位数字")
            self.assertEqual(len(set(numbers)), len(numbers), "同航线内航班号唯一（同渠道班次不重号）")

    def test_airline_source_retired_no_rows(self):
        # v1.5（ISSUE-012）：引擎编造正班退场——航司直连不再出任何比价行
        airline = [s for s in all_sources() if s.source_id == "airline"][0]
        offers, _f, _u = fr.collect([airline], "SHA", "CTU", dt.date(2026, 11, 20))
        self.assertEqual(offers, [])
        available, reason = airline.availability("SHA", "CTU", dt.date(2026, 11, 20))
        self.assertFalse(available)
        self.assertEqual(reason, "暂无合规公开接口，未接入")

    def test_carrier_tier_semantics(self):
        # 9C 低成本不含税、其余含税（口径明细由 fee_breakdown 承载）
        self.assertFalse(airlines.AIRLINES["9C"]["tax_included"])
        for code in ("CA", "MU", "CZ", "HU", "HO"):
            self.assertTrue(airlines.AIRLINES[code]["tax_included"])


class FeeEngineTest(unittest.TestCase):
    """PRD v1.4 第 8 节：机建 50；燃油 ≤800km 20 / >800km 40（边界含 800）。"""

    def test_fuel_fee_boundary(self):
        self.assertEqual(fees.fuel_fee(799), 20)
        self.assertEqual(fees.fuel_fee(800), 20, "分档边界：≤800km 含 800")
        self.assertEqual(fees.fuel_fee(801), 40)
        self.assertIsNone(fees.fuel_fee(None))

    def test_total_estimate_semantics(self):
        self.assertEqual(fees.total_estimate(500, True, 266), 500, "已含报价到手价=票面价")
        self.assertEqual(fees.total_estimate(500, False, 266), 570, "不含税短程：+50+20")
        self.assertEqual(fees.total_estimate(500, False, 1067), 590, "不含税长程：+50+40")

    def test_included_desc_two_forms_and_special(self):
        self.assertEqual(fees.included_desc(True, 20), "已含机建+燃油")
        self.assertEqual(fees.included_desc(False, 20), "另加机建 ¥50 + 燃油 ¥20")
        self.assertEqual(fees.included_desc(False, 40), "另加机建 ¥50 + 燃油 ¥40")
        self.assertEqual(fees.included_desc(False, 20, special=True), "不含机建燃油费")

    def test_fee_breakdown_shape(self):
        fb = fees.fee_breakdown(179, False, 752, "不含机建燃油费")
        self.assertEqual(fb, {"airport_fee": 50, "fuel_fee": 20, "tax_included": False,
                              "included_desc": "不含机建燃油费"})

    def test_engine_applies_distance_tiers(self):
        # 上海-泉州 ≈ 750km → 燃油 20；上海-三亚 ≈ 1900km → 燃油 40（特价票行走费用引擎）
        for (origin, destination), fuel, date in ((("SHA", "JJN"), 20, dt.date(2026, 10, 27)),
                                                  (("SHA", "SYX"), 40, dt.date(2026, 10, 28))):
            offers, _f, _u = fr.collect(all_sources(), origin, destination, date)
            rows = [o for o in offers if not o.get("is_snapshot")]
            self.assertTrue(rows, f"{origin}-{destination} 特价票行存在")
            for offer in rows:
                self.assertEqual(offer["fee_breakdown"]["fuel_fee"], fuel)


class DedupTest(unittest.TestCase):
    """PRD v1.4 第 6 节：去重键=出发日期+航班号，只做跨渠道去重。

    v1.5（ISSUE-012）：比价行仅来自实测快照单渠道，线上不再产生跨渠道重复；
    去重逻辑保留（未来多真实源接入即用），以单元级锁定。
    """

    def test_ota_channel_retired(self):
        ota = [s for s in all_sources() if s.source_id == "ota"][0]
        available, reason = ota.availability("SHA", "CTU", dt.date(2026, 11, 20))
        self.assertFalse(available)
        self.assertEqual(reason, "暂无合规公开接口，未接入")
        self.assertEqual(ota.search("SHA", "CTU", dt.date(2026, 11, 20)), [])

    def test_cross_channel_merge_keeps_lowest_total(self):
        # 跨渠道合并单元锁定：同航班号两渠道行合并为 1 行，保留到手最低价
        date = dt.date(2026, 11, 20)
        rows = [{"source_id": "airline", "source_name": "航司直连", "price": 900,
                 "total_estimate": 900, "product": "MU0001", "flight_no": "MU0001"},
                {"source_id": "ota", "source_name": "OTA 比价", "price": 850,
                 "total_estimate": 850, "product": "特惠代理票·MU0001", "flight_no": "MU0001"}]
        merged = fr.dedup_offers(rows, date)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["channel_count"], 2)
        self.assertEqual(set(merged[0]["channel_names"]), {"OTA 比价", "航司直连"})
        self.assertEqual(merged[0]["price"], 850, "合并保留到手最低价")
        self.assertIn("另有 1 个渠道有售", fr.search_note(merged[0], date))

    def test_same_channel_flights_never_merged(self):
        # 单元不变式：同渠道两行（不同航班号）去重后各自成行
        date = dt.date(2026, 11, 20)
        rows = [{"source_id": "airline", "source_name": "航司直连", "price": 500,
                 "total_estimate": 500, "product": "MU0001", "flight_no": "MU0001"},
                {"source_id": "airline", "source_name": "航司直连", "price": 600,
                 "total_estimate": 600, "product": "MU0002", "flight_no": "MU0002"}]
        self.assertEqual(len(fr.dedup_offers(rows, date)), 2)
        # 集成不变式：快照行同渠道多班次，去重后航班号并集不变
        offers, _f, _u = fr.collect(all_sources(), "INC", "CAN", dt.date(2026, 11, 1))
        self.assertTrue(offers)
        self.assertTrue(all(o.get("channel_count", 1) == 1 for o in offers),
                        "快照单渠道：无跨渠道合并行")

    def test_rows_without_flight_no_never_deduped(self):
        date = dt.date(2026, 11, 20)
        rows = [{"source_id": "a", "source_name": "A", "price": 100,
                 "total_estimate": 100, "product": "券A"},
                {"source_id": "b", "source_name": "B", "price": 100,
                 "total_estimate": 100, "product": "券B"}]
        self.assertEqual(len(fr.dedup_offers(rows, date)), 2, "无航班号的行不参与去重")

    def test_special_fare_exempt_from_dedup(self):
        offers, _f, _u = fr.collect(all_sources(), "SHA", "JJN", dt.date(2026, 10, 27))
        special = [o for o in offers if o.get("is_special_fare")]
        self.assertEqual(len(special), 1)
        self.assertTrue(special[0].get("no_dedup"))
        self.assertNotIn("channel_count", special[0], "特价票不参与跨渠道去重")
        self.assertIsNone(special[0]["flight_no"], "特价票卡面未展示航班号（不编造）")


class SpecialFareTest(unittest.TestCase):
    """PRD v1.4 第 7.1 节：21 条固定特价票逐字校验 + 仅固定日期可售。"""

    # (日期, 目的码, 票面价)——与 PRD 表逐字一致
    TABLE = (
        ("2026-10-26", "LJG", 359), ("2026-10-27", "JJN", 179), ("2026-10-27", "CKG", 189),
        ("2026-10-27", "CAN", 219), ("2026-10-27", "CGD", 329), ("2026-10-27", "JIQ", 339),
        ("2026-10-27", "JHG", 349), ("2026-10-28", "URC", 419), ("2026-10-28", "SYX", 609),
        ("2026-10-29", "SHE", 189), ("2026-10-29", "KMG", 259), ("2026-10-29", "OHE", 569),
        ("2026-10-29", "HTN", 1049), ("2026-11-02", "ZYI", 229), ("2026-11-04", "ZQZ", 219),
        ("2026-11-04", "SZX", 229), ("2026-11-04", "KWE", 309), ("2026-11-06", "AAT", 429),
        ("2026-11-07", "MIG", 369), ("2026-11-16", "DLU", 389), ("2026-11-21", "XIY", 249),
    )

    def test_source_data_matches_prd_verbatim(self):
        self.assertEqual(len(_SPECIAL_FARES), 21, "21 条逐字入库，不许增删")
        converted = tuple(
            (dt.date(2026, int(mmdd[:2]), int(mmdd[3:])).isoformat(), dest, price)
            for mmdd, dest, price in _SPECIAL_FARES)
        self.assertEqual(converted, self.TABLE)

    def test_each_entry_sellable_only_on_fixed_date(self):
        miniapp = DouyinMiniappSource()
        for date_text, dest, price in self.TABLE:
            date = dt.date.fromisoformat(date_text)
            offers = miniapp.search("SHA", dest, date)
            specials = [o for o in offers if o.get("is_special_fare")]
            self.assertEqual(len(specials), 1, f"{date_text} SHA-{dest} 应恰有 1 条特价票")
            row = specials[0]
            self.assertEqual(row["price"], price, f"{date_text} SHA-{dest} 票面价逐字一致")
            self.assertFalse(row["tax_included"])
            self.assertEqual(row["carrier_code"], "9C")
            self.assertIn("特价票·固定航班日期·不含机建燃油费", row["note"])
            self.assertIn("省 ¥41", row["note"])
            self.assertIn(f"限 {date_text} 出发", row["note"])
            # 非固定日期不出现该特价（前后各一天）
            for offset in (-1, 1):
                other = date + dt.timedelta(days=offset)
                if other < dt.date.today():
                    continue
                off = miniapp.search("SHA", dest, other)
                self.assertFalse([o for o in off if o.get("is_special_fare")],
                                 f"{dest} 特价票在 {other} 不可售")

    def test_flight_fields_not_fabricated_and_deterministic(self):
        miniapp = DouyinMiniappSource()
        for index, (date_text, dest, _price) in enumerate(self.TABLE):
            date = dt.date.fromisoformat(date_text)
            first = miniapp.search("SHA", dest, date)[0]
            second = miniapp.search("SHA", dest, date)[0]
            self.assertEqual(first, second,
                             "同航线同日期任何入口查询一致（确定性锁定）")
            # v1.5（ISSUE-012）：卡面未展示航班号/时刻——出 None，禁止编造填充
            self.assertIsNone(first.get("flight_no"), f"{dest} 特价票不得编造航班号")
            self.assertIsNone(first.get("depart_time"), f"{dest} 特价票不得编造时刻")
            self.assertEqual(first["carrier_code"], "9C")
            self.assertEqual(first["carrier_name"], "春秋")

    def test_qianjiang_card_face_verbatim(self):
        catalog = fr.special_fare_catalog("SHA", "JIQ")
        self.assertEqual(catalog[0]["destination_display"], "黔江(重庆)",
                         "特价票卡面照抄评审人'黔江(重庆)'原口径")
        self.assertEqual(cities.city_name("JIQ"), "黔江", "城市名保持'黔江'")

    def test_catalog_has_21_and_verbatim_fields(self):
        catalog = fr.special_fare_catalog()
        self.assertEqual(len(catalog), 21)
        for item in catalog:
            self.assertEqual(item["origin"], "SHA")
            self.assertTrue(item["date"] and item["price"], "日期与票价逐字保留")
            self.assertEqual(item["carrier_code"], "9C")
            self.assertTrue(item["destination_display"])
            self.assertIsNone(item.get("flight_no"), "目录不携带编造航班号")
            self.assertIsNone(item.get("depart_time"), "目录不携带编造时刻")

    def test_special_fare_enters_ranking_with_total_price(self):
        # 特价票是具体航班票：计入比价行，到手价 = 票面 + 机建 + 燃油
        date = dt.date(2026, 10, 27)
        offers, _f, _u = fr.collect(all_sources(), "SHA", "JJN", date)
        special = [o for o in offers if o.get("is_special_fare")][0]
        km = cities.distance_km("SHA", "JJN")
        self.assertEqual(special["total_estimate"],
                         special["price"] + fees.AIRPORT_FEE + fees.fuel_fee(km))
        self.assertEqual(special["fee_breakdown"]["included_desc"], "不含机建燃油费")
        self.assertTrue(fr.eligible_for_lowest(special, date), "特价票参与到手价竞争")

    def test_special_fare_is_lowest_on_its_date(self):
        offers, _f, _u = fr.collect(all_sources(), "SHA", "JJN", dt.date(2026, 10, 27))
        offers = fr.rank_offers(offers, dt.date(2026, 10, 27))
        self.assertTrue(offers[0].get("is_special_fare"), "¥179 特价票应为该日到手最低价")


class CardsTest(unittest.TestCase):
    """PRD v1.4 第 7.2 节：6 张卡面逐字、命中规则、未披露退改、售罄、不入比价。"""

    FACES = (
        ("DY-101", "省钱卡单人版365天", 79), ("DY-102", "199 秋冬机票单次卡·上海/兰州/重庆/潮汕/厦门/哈尔滨", 199),
        ("DY-103", "199 秋季机票单次卡·石家庄/兰州/大连/重庆/潮汕/上海/西安", 199),
        ("DY-104", "秋冬 MAX 版尊卡", 299), ("DY-105", "秋冬 MAX PLUS 版", 239),
        ("DY-106", "秋冬 MAX 国内版", 149),
    )

    def test_card_faces_verbatim(self):
        cards = DouyinGroupbuySource().deals()
        self.assertEqual([(c["card_id"], c["name"], c["price"]) for c in cards], list(self.FACES))
        joined = "\n".join(c["name"] for c in cards)
        self.assertIn("潮汕", joined, "注册表外城市照抄保留，不许'修正'")

    def test_honest_disclosure_assets(self):
        cards = DouyinGroupbuySource().deals()
        undisclosed = [c["card_id"] for c in cards if not c.get("refund_change_rules")]
        self.assertEqual(sorted(undisclosed), ["DY-101", "DY-104", "DY-105", "DY-106"],
                         "省钱卡+三张权益卡保留'退改规则未披露'资产（DY-005 迁移）")
        sold_out = [c["card_id"] for c in cards if c["stock"] == "售罄"]
        self.assertEqual(sold_out, ["DY-103"], "1 张确定性售罄卡（DY-004 场景延续）")

    def test_cards_are_not_offer_rows(self):
        date = dt.date(2026, 11, 20)
        offers, _f, _u = fr.collect(all_sources(), "SHA", "CTU", date)
        self.assertFalse([o for o in offers if o["source_id"] == "douyin_groupbuy"],
                         "次卡/省钱卡不计入比价行")

    def test_single_card_hit_rules(self):
        source = DouyinGroupbuySource()
        # 秋季单次卡（DY-103）：两端命中城市 + 出发日期在有效期内
        self.assertTrue(any(c["card_id"] == "DY-103"
                            for c in source.cards_for("SHA", "DLC", dt.date(2026, 11, 1))))
        self.assertFalse(any(c["card_id"] == "DY-103"
                             for c in source.cards_for("TNA", "NNG", dt.date(2026, 11, 1))),
                         "两端都不在卡面城市 → 不命中")
        self.assertFalse(any(c["card_id"] == "DY-103"
                             for c in source.cards_for("SHA", "DLC", dt.date(2026, 12, 15))),
                         "出发日期超出有效期 → 不命中（ISSUE-003 可出行窗口延续）")
        # 秋冬单次卡（DY-102）只在 12 月命中
        self.assertTrue(any(c["card_id"] == "DY-102"
                            for c in source.cards_for("HRB", "KMG", dt.date(2026, 12, 5))))
        self.assertFalse(any(c["card_id"] == "DY-102"
                             for c in source.cards_for("HRB", "KMG", dt.date(2026, 11, 20))))

    def test_entitlement_cards_hit_all_routes(self):
        source = DouyinGroupbuySource()
        hints = source.cards_for("TNA", "NNG", dt.date(2026, 11, 20))
        ids = {c["card_id"] for c in hints}
        self.assertTrue({"DY-101", "DY-104", "DY-105", "DY-106"} <= ids, "全航线命中卡恒命中")
        self.assertNotIn("DY-102", ids) and self.assertNotIn("DY-103", ids)

    def test_cli_card_hints_block_before_rows(self):
        buf = io.StringIO()
        with _pinned(), redirect_stdout(buf):
            fr.main(["search", "--from", "INC", "--to", "CAN", "--date", "2026-11-01"])
        out = buf.getvalue()
        self.assertIn("[卡提示]", out, "CLI 在比价行前输出卡提示块")
        self.assertIn("不计入比价", out)
        self.assertIn("退改规则未披露", out)
        self.assertLess(out.index("[卡提示]"), out.index("到手价 "), "卡提示块位于比价行之前")

    def test_no_card_block_when_no_hit(self):
        buf = io.StringIO()
        with pricing.fixed_minute(PINNED_MINUTE), redirect_stdout(buf):
            fr.main(["search", "--from", "TNA", "--to", "NNG", "--date", "2026-12-20"])
        out = buf.getvalue()
        self.assertNotIn("199 秋冬", out, "12 月卡不命中 11 月前查询/非卡面城市航线")
        self.assertIn("[卡提示]", out)  # 全航线命中卡仍在

    def test_deals_shows_special_fares_and_cards(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = fr.main(["deals"])
        out = buf.getvalue()
        self.assertEqual(code, 0)
        self.assertIn("固定特价票（21 条", out)
        self.assertIn("上海→黔江(重庆)", out)
        self.assertIn("未披露（以下单页为准）", out, "特价票卡面未展示航班号/时刻——诚实渲染")
        self.assertIn("卡池（6 张", out, "6 张团购卡（直播闪购引擎卡已退场）")
        self.assertIn("退改规则未披露", out)
        self.assertIn("[真实商品]", out.splitlines()[0])

    def test_card_price_fixed_no_wiggle(self):
        first = DouyinGroupbuySource().deals()
        with pricing.fixed_minute(PINNED_MINUTE + 600):
            second = DouyinGroupbuySource().deals()
        self.assertEqual([c["price"] for c in first], [c["price"] for c in second],
                         "卡为固定价商品，不叠加分钟级演示波动")


class RetiredCityTest(unittest.TestCase):
    """PRD v1.4 第 4 节⑥：存量 alerts 引用已下线城市 → 显式标注、不静默删除、不再触发查询。"""

    ALERTS = [
        {"origin": "SHA", "destination": "HKG", "threshold": 800, "created_at": "2026-09-01"},
        {"origin": "SHA", "destination": "CTU", "threshold": 9000, "created_at": "2026-09-02"},
    ]

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self._old = fr.ALERTS_FILE
        self.addCleanup(setattr, fr, "ALERTS_FILE", self._old)
        fr.ALERTS_FILE = Path(tmp.name) / "alerts.json"
        fr.save_alerts([dict(a) for a in self.ALERTS], fr.ALERTS_FILE)

    def test_watch_list_marks_retired_city(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = fr.main(["watch", "--list"])
        out = buf.getvalue()
        self.assertEqual(code, 0)
        self.assertIn("城市已下线（v1.4 城市全集收缩）：香港", out)
        self.assertIn("SHA → HKG", out, "不静默删除存量收藏")

    def test_watch_check_skips_retired_without_query(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = fr.main(["watch"])
        out = buf.getvalue()
        self.assertEqual(code, 0)
        self.assertIn("城市已下线", out)
        self.assertIn("不参与比价与提醒", out)
        self.assertIn("暂无报价数据", out, "SHA→CTU 无快照数据，显式披露（不编数）")
        self.assertEqual(out.count("[低价提醒]"), 0, "无数据航线不得触发提醒")

    def test_web_watch_list_carries_retired_flag(self):
        _, payload = webapp._api_watch_list({})
        by_dest = {i["destination"]: i for i in payload["alerts"]}
        self.assertTrue(by_dest["HKG"]["retired"])
        self.assertIn("城市已下线", by_dest["HKG"]["status_note"])
        self.assertIsNone(by_dest["HKG"]["current_lowest"])
        self.assertFalse(by_dest["HKG"]["below_threshold"])
        self.assertFalse(by_dest["CTU"]["retired"])
        self.assertIsNone(by_dest["CTU"]["current_lowest"],
                          "快照未收录航线：无当前最低价（不编数）")

    def test_web_watch_check_reports_retired_line(self):
        _, payload = webapp._api_watch_check({})
        retired = [l for l in payload["lines"] if l["status"] == "retired"]
        self.assertEqual(len(retired), 1)
        self.assertIn("城市已下线", retired[0]["text"])

    def test_data_structure_unchanged(self):
        alerts = fr.load_alerts()
        self.assertEqual(alerts, self.ALERTS, "判定口径切换到手价，alerts.json 数据结构不变")


class CJKTableTest(unittest.TestCase):
    """CLI 表格 CJK 对齐：全角按 2 列计，各列起点逐行一致。"""

    HEADERS = ("到手价", "票面价", "票源", "航班/套餐", "时间", "机场", "税费", "备注")
    ROWS = [
        ("¥459", "¥459", "抖音小程序·春秋航空", "特价票 上海→黔江(重庆)",
         "07:35→09:45", "上海虹桥国际机场→黔江武陵山机场", "不含机建燃油费", "备注A"),
        ("¥830", "¥830", "航司直连", "MU3168", "08:30→11:15",
         "上海虹桥国际机场→成都双流国际机场", "已含机建+燃油", "备注B"),
        ("¥895", "¥895", "OTA 比价", "特惠代理票·HO7990", "15:30→18:15",
         "上海虹桥国际机场→成都双流国际机场", "已含机建+燃油", "另有 1 个渠道有售"),
    ]

    def test_table_columns_aligned_with_cjk(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            fr.print_table(self.HEADERS, self.ROWS)
        lines = buf.getvalue().splitlines()
        widths = [max(fr.display_width(h), max(fr.display_width(r[i]) for r in self.ROWS))
                  for i, h in enumerate(self.HEADERS)]
        rendered = [fr.display_width(ln) for ln in lines]
        self.assertEqual(rendered[1], max(rendered),
                         "分隔线与最宽行（含窄表头/宽内容列）对齐")
        for row, ln in zip(self.ROWS, lines[2:]):
            expected = "  ".join(fr.pad(c, w) for c, w in zip(row, widths)).rstrip()
            self.assertEqual(ln, expected,
                             "每格按显示宽度补齐（全角按 2 列）→ 各列逐行对齐")


if __name__ == "__main__":
    unittest.main()
