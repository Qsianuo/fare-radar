"""RapidAPI「Google Flights Search & Prices」真实接口适配器（v1.9.0）。

数据链路：RapidAPI（订阅制免费档 100 次/月）→ Google Flights 真实报价。
认证：请求头 X-RapidAPI-Key（配置于 rapidapi_config.json，已 gitignore）。
查询：IATA 三字码直查（/v1/flights/search?departure_id&arrival_id&outbound_date），
无需城市映射，任意 50 城组合均可按需获取真实航班。

调用纪律（内置，使用者无需操心）：
- 同 (航线, 日期) 结果缓存 6 小时（价格日级波动，长缓存保护配额）；
- 当日调用上限 40 次 + 当月上限 90 次（月配额 100 的保护线）；
- 单次超时 45 秒（首查偏慢）；429 即停中文提示，不自动重试。

诚实披露：价格为 Google Flights 含税口径（未拆分机建/燃油明细）；上游波动时
渠道如实降级，不编造；无价格行程丢弃。
"""

import datetime as dt
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from .base import Source, offline_mode, OFFLINE_REASON
from . import airlines

CONFIG_NAME = "rapidapi_config.json"
DAILY_CALL_CAP = 40           # 当日上限（100 次/月额度保护）
MONTHLY_CALL_CAP = 90         # 当月上限（给失败重试留 10 次余量）
OFFER_CACHE_TTL = 6 * 3600    # 同航线同日期结果缓存 6 小时
EMPTY_CACHE_TTL = 30 * 60     # 空结果只缓存 30 分钟：上游无数据/瞬时故障不该锁死 6 小时
TIMEOUT = 45
DEFAULT_HOST = "google-flights-search-prices.p.rapidapi.com"
SEARCH_PATH = "/v1/flights/search"

_NO_KEY_REASON = ("未配置 API Key——在 rapidapi.com 订阅 Google Flights Search 免费档后，"
                  "于开发者后台 default-application 复制 X-RapidAPI-Key 到 rapidapi_config.json")


class RapidapiFlightSource(Source):
    """真实接口票源：RapidAPI · Google Flights Search & Prices。"""

    source_id = "rapidapi_sky"
    display_name = "RapidAPI·Google Flights"
    tagline = ("Google Flights 真实报价（RapidAPI 订阅，免费档配额有限），"
               "任意城市按需查询；价格为含税口径，结果缓存 6 小时")
    entry_url = "https://rapidapi.com/letsscrape/api/google-flights-search-prices"
    entry_note = "RapidAPI·Google Flights Search（订阅制，Key 自配）"

    def __init__(self, config_path=None):
        self._config_path = (Path(config_path) if config_path
                             else Path(__file__).parent / CONFIG_NAME)
        self._cache_dir = self._config_path.parent / ".rapidapi_cache"
        self._config = self._read_config()

    def _read_config(self):
        try:
            data = json.loads(self._config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    @property
    def has_key(self):
        return bool(self._config.get("api_key"))

    @property
    def status(self):
        if offline_mode():
            return "not_available"
        return "active" if self.has_key else "not_available"

    @property
    def status_reason(self):
        if offline_mode():
            return OFFLINE_REASON
        return "" if self.has_key else _NO_KEY_REASON

    def availability(self, origin, destination, date):
        if offline_mode():
            return False, OFFLINE_REASON
        if not self.has_key:
            return False, _NO_KEY_REASON
        return True, None

    # ---------- 缓存与限额 ----------

    def _cache_read(self, name):
        try:
            return json.loads((self._cache_dir / name).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def _cache_write(self, name, payload):
        self._cache_dir.mkdir(exist_ok=True)
        (self._cache_dir / name).write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    def _quota_guard(self):
        today = dt.date.today().isoformat()
        month = today[:7]
        day_counter = self._cache_read("day_counter.json") or {"date": today, "calls": 0}
        if day_counter.get("date") != today:
            day_counter = {"date": today, "calls": 0}
        month_counter = self._cache_read("month_counter.json") or {"month": month, "calls": 0}
        if month_counter.get("month") != month:
            month_counter = {"month": month, "calls": 0}
        if month_counter["calls"] >= MONTHLY_CALL_CAP:
            raise RuntimeError(f"RapidAPI 当月调用已达自我上限 {MONTHLY_CALL_CAP} 次"
                               "（月配额 100 的保护线），本月不再请求；缓存仍可用")
        if day_counter["calls"] >= DAILY_CALL_CAP:
            raise RuntimeError(f"RapidAPI 当日调用已达自我上限 {DAILY_CALL_CAP} 次，"
                               "今日不再请求；缓存仍可用，明日恢复")
        day_counter["calls"] += 1
        month_counter["calls"] += 1
        self._cache_write("day_counter.json", day_counter)
        self._cache_write("month_counter.json", month_counter)

    # ---------- HTTP ----------

    def _fetch(self, origin, destination, date):
        self._quota_guard()
        host = self._config.get("host", DEFAULT_HOST)
        query = urllib.parse.urlencode({
            "departure_id": origin, "arrival_id": destination,
            "outbound_date": date.isoformat(), "currency": "CNY"})
        request = urllib.request.Request(
            f"https://{host}{SEARCH_PATH}?{query}",
            headers={"X-RapidAPI-Key": self._config["api_key"],
                     "X-RapidAPI-Host": host,
                     # 前置 WAF 拦截默认 Python UA（实测 403），声明可溯源客户端
                     "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) FareRadar/1.9"})
        proxy = str(self._config.get("proxy") or "").strip()
        opener = (urllib.request.build_opener(urllib.request.ProxyHandler(
            {"http": proxy, "https": proxy})) if proxy
            else urllib.request.build_opener())
        try:
            with opener.open(request, timeout=TIMEOUT) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                raise RuntimeError("RapidAPI 返回 429（超出配额/限速），渠道已暂停——"
                                   "请到后台核对订阅配额，不要重试") from None
            raise RuntimeError(f"RapidAPI 接口返回 HTTP {exc.code}") from None
        # 上游可能用 HTTP 200 + 响应体 status 表达错误（实测 204 = provider 无数据）。
        # 不校验就会把配额/限速错误静默当成"无报价"，掩盖真实原因。
        body_status = body.get("status")
        if body_status is not None and body_status != 200:
            desc = str(body.get("description") or "").strip()
            if body_status == 204:
                return body          # 语义明确的"上游无此航线数据"，由调用方记录
            if body_status == 429:
                raise RuntimeError("RapidAPI 返回 429（超出配额/限速），渠道已暂停——"
                                   "请到后台核对订阅配额，不要重试") from None
            raise RuntimeError(f"RapidAPI 接口返回 status {body_status}"
                               + (f"：{desc}" if desc else "")) from None
        return body

    # ---------- 映射 ----------

    @staticmethod
    def _hhmm(iso_time):
        return (iso_time or "")[11:16] or "未披露"

    def _to_offer(self, itinerary):
        legs = itinerary.get("legs") or []
        if not legs:
            return None
        price = itinerary.get("price")
        if not isinstance(price, (int, float)):
            return None  # 无价格不编造
        price = round(price)
        first, last = legs[0], legs[-1]
        flight_no = "/".join(
            str(leg.get("flight_number", "")).replace(" ", "") for leg in legs)
        code = first.get("airline_code", "") or "XX"
        carrier = airlines.carrier_name(code) or str(first.get("airline") or code)
        dep = first.get("departure_airport") or {}
        arr = last.get("arrival_airport") or {}
        total_min = itinerary.get("total_duration")
        stops = itinerary.get("stops", len(legs) - 1)
        return {
            "source_id": self.source_id, "source_name": self.display_name,
            "product": ("直飞" if stops == 0 else f"中转{stops}站")
                       + "｜" + carrier
                       + (f"｜{first.get('airplane')}" if first.get("airplane") else ""),
            "flight_no": flight_no,
            "carrier_code": code,
            "carrier_name": carrier,
            "depart_time": self._hhmm(dep.get("time")),
            "arrive_time": self._hhmm(arr.get("time")),
            "cross_day": bool(first.get("overnight")) or (
                self._hhmm(arr.get("time")) < self._hhmm(dep.get("time"))
                and len(str(arr.get("time", ""))) > 10),
            "duration_text": (f"{total_min // 60}小时{total_min % 60:02d}分"
                              if isinstance(total_min, int) else "未披露"),
            # 机场名用上游真实英文全称（比本地注册表更权威）
            "dep_airport": f"{dep.get('name', '未披露')}（{dep.get('code', '')}）",
            "arr_airport": f"{arr.get('name', '未披露')}（{arr.get('code', '')}）",
            "price": price, "tax_included": True, "is_deal": False,
            "fee_breakdown": {"airport_fee": None, "fuel_fee": None, "tax_included": True,
                              "included_desc": "Google Flights 含税总价（未拆分机建/燃油）"},
            "total_estimate": price,
            "note": "真实接口·Google Flights（价格缓存 6 小时，下单以官方渠道为准）",
        }

    def _to_insights(self, raw):
        """price_insights → 精简结构。零编造：无可用字段即返回 None，不猜。"""
        if not isinstance(raw, dict):
            return None
        lowest = raw.get("lowest_price")
        lowest = round(lowest) if isinstance(lowest, (int, float)) else None
        rng = raw.get("typical_price_range")
        rng = ([round(x) for x in rng]
               if isinstance(rng, list) and len(rng) == 2
               and all(isinstance(x, (int, float)) for x in rng) else None)
        level = raw.get("price_level")
        level = level.strip() if isinstance(level, str) and level.strip() else None
        history = [{"date": h["date"], "price": round(h["price"])}
                   for h in (raw.get("price_history") or [])
                   if isinstance(h, dict) and isinstance(h.get("price"), (int, float))
                   and h.get("date")]
        if lowest is None and rng is None:
            return None          # 连最低价和区间都没有，就没有可展示的内容
        return {
            "source_id": self.source_id,
            "source_name": self.display_name,
            "currency": str(raw.get("currency") or "CNY"),
            "lowest_price": lowest,
            "price_level": level,
            "typical_price_range": rng,
            "history": history,
            "basis": "Google Flights 含税总价（未拆分机建/燃油），与本渠道到手价同口径",
            "history_note": "上游为抽样价位（非逐日真实报价），仅作趋势参考",
        }

    def search(self, origin, destination, date):
        available, _ = self.availability(origin, destination, date)
        if not available:
            return []
        key = f"offers_{origin}_{destination}_{date.isoformat()}.json"
        cached = self._cache_read(key)
        if cached and time.time() < float(cached.get("expire_at", 0)):
            return cached["rows"]
        payload = self._fetch(origin, destination, date)
        body_status = payload.get("status")
        data = payload.get("data") or {}
        rows = []
        for itinerary in ((data.get("best_flights") or []) + (data.get("other_flights") or []))[:8]:
            row = self._to_offer(itinerary)
            if row:
                rows.append(row)
        ttl = OFFER_CACHE_TTL if rows else EMPTY_CACHE_TTL
        self._cache_write(key, {"expire_at": time.time() + ttl,
                                "rows": rows,
                                "body_status": body_status,
                                "insights": self._to_insights(data.get("price_insights"))})
        return rows

    def empty_note(self, origin, destination, date):
        """空结果的原因说明（上游无此航线数据 / 其他异常状态）。

        只读缓存，不回源；有报价或原因未知时返回 None——不替上游猜原因。
        """
        cached = self._cache_read(f"offers_{origin}_{destination}_{date.isoformat()}.json")
        if not cached or cached.get("rows"):
            return None
        status = cached.get("body_status")
        if status == 204:
            return "上游 Google Flights 无该航线数据（status 204：No content）"
        if status == 200:
            # 与 204 语义不同：查询本身成功，只是没返回可售行程
            # （航线真实存在但上游无可售报价，或该日期尚无排班）
            return "上游查询成功但未返回可售行程（可能有航班但无可售报价，或该日期尚无排班）"
        if status not in (None, 200):
            return f"上游返回 status {status}"
        return None

    def insights(self, origin, destination, date):
        """价格洞察：只读缓存，不回源（零额外配额；缓存缺失即 None）。"""
        cached = self._cache_read(f"offers_{origin}_{destination}_{date.isoformat()}.json")
        if not cached:
            return None
        found = cached.get("insights")     # v1.9 及更早的缓存没有该字段 → None
        return found if isinstance(found, dict) else None
