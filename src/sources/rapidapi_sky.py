"""RapidAPI「Flights Scraper Sky」真实接口适配器（ISSUE-013 后续，v1.7.0）。

数据链路：RapidAPI（订阅制，免费档 100 次/月）→ 上游 Skyscanner 抓取。
认证：请求头 X-RapidAPI-Key（配置于 rapidapi_config.json，已 gitignore）。

调用纪律（与 amadeus.py 同框架，使用者无需操心）：
- 同 (航线, 日期) 查询结果缓存 30 分钟；城市 entityId 映射永久缓存；
- 当日 API 调用自我上限 60 次（免费档月配额 100 次的 ~2/3 保护线）；
- 单次请求超时 15 秒；429/上游 503 即时上抛中文说明，不自动重试。

诚实披露：上游为抓取型数据源，可能 503 波动或字段缺失——缺失字段按
"未披露"兜底，上游不可用时渠道降级（failed），不编造。
"""

import datetime as dt
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from .base import Source
from . import airlines, cities

CONFIG_NAME = "rapidapi_config.json"
DAILY_CALL_CAP = 60           # 当日自我上限（保护免费档月配额 100 次）
OFFER_CACHE_TTL = 30 * 60     # 同航线同日期结果缓存 30 分钟
TIMEOUT = 15
BASE = "https://{host}{path}"

_NO_KEY_REASON = ("未配置 API Key——在 rapidapi.com 订阅 Flights Scraper Sky 免费档后，"
                  "于开发者后台 default-application 复制 X-RapidAPI-Key 到 rapidapi_config.json")


class RapidapiFlightSource(Source):
    """真实接口票源：RapidAPI Flights Scraper Sky（上游 Skyscanner）。"""

    source_id = "rapidapi_sky"
    display_name = "RapidAPI 实时比价"
    tagline = ("RapidAPI 订阅接口（上游 Skyscanner），真实报价；免费档配额有限，"
               "上游可能波动，不可用时如实降级")
    entry_url = "https://rapidapi.com/ntd119/api/flights-sky"
    entry_note = "RapidAPI·Flights Scraper Sky（订阅制，Key 自配）"

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
        return "active" if self.has_key else "not_available"

    @property
    def status_reason(self):
        return "" if self.has_key else _NO_KEY_REASON

    def availability(self, origin, destination, date):
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

    def _daily_guard(self):
        today = dt.date.today().isoformat()
        counter = self._cache_read("day_counter.json") or {"date": today, "calls": 0}
        if counter.get("date") != today:
            counter = {"date": today, "calls": 0}
        if counter["calls"] >= DAILY_CALL_CAP:
            raise RuntimeError(f"RapidAPI 当日调用已达自我上限 {DAILY_CALL_CAP} 次"
                               "（保护免费档月配额），今日不再请求；缓存仍可用")
        counter["calls"] += 1
        self._cache_write("day_counter.json", counter)

    # ---------- HTTP ----------

    def _get(self, path, params):
        self._daily_guard()
        host = self._config.get("host", "flights-sky.p.rapidapi.com")
        query = urllib.parse.urlencode({k: v for k, v in params.items() if v})
        request = urllib.request.Request(
            BASE.format(host=host, path=path) + (f"?{query}" if query else ""),
            headers={"X-RapidAPI-Key": self._config["api_key"],
                     "X-RapidAPI-Host": host})
        proxy = str(self._config.get("proxy") or "").strip()
        opener = (urllib.request.build_opener(urllib.request.ProxyHandler(
            {"http": proxy, "https": proxy})) if proxy
            else urllib.request.build_opener())
        try:
            with opener.open(request, timeout=TIMEOUT) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                raise RuntimeError("RapidAPI 返回 429（超出配额/限速），渠道已暂停——"
                                   "请到后台核对订阅配额，不要重试") from None
            raise RuntimeError(f"RapidAPI 接口返回 HTTP {exc.code}") from None

    # ---------- 城市映射（IATA → Skyscanner entityId） ----------

    def _entity_id(self, iata):
        """auto-complete 查询并永久缓存 IATA→entityId（Skyscanner 体系）。"""
        cached = self._cache_read("entities.json") or {}
        if iata in cached:
            return cached[iata]
        payload = self._get("/web/flights/auto-complete", {"query": iata})
        for item in (payload.get("data") or []):
            if item.get("iata") == iata or (item.get("navigation") or {}) \
                    .get("entityId", "").endswith(iata):
                cached[iata] = item["navigation"]["entityId"]
                self._cache_write("entities.json", cached)
                return cached[iata]
        # 兜底：取第一条 entityId
        data = payload.get("data") or []
        if data and (data[0].get("navigation") or {}).get("entityId"):
            cached[iata] = data[0]["navigation"]["entityId"]
            self._cache_write("entities.json", cached)
            return cached[iata]
        raise RuntimeError(f"RapidAPI 未找到城市 {iata} 的 entityId（上游可能波动）")

    # ---------- 映射与查询 ----------

    @staticmethod
    def _iso_hhmm(value):
        try:
            return value[11:16]
        except (TypeError, IndexError):
            return "未披露"

    def _to_offer(self, itinerary, leg):
        segments = leg.get("segments") or [{}]
        first, last = segments[0], segments[-1]
        flight_no = "|".join(
            f"{s.get('marketingCarrier', {}).get('alternateId', '') or '?'}"
            f"{s.get('flightNumber', '') or '??'}" for s in segments)
        carrier_code = first.get("marketingCarrier", {}).get("alternateId", "") or "XX"
        price_items = itinerary.get("pricingOptions") or [{}]
        price = price_items[0].get("price", {}).get("amount")
        price = round(float(price)) if price is not None else None
        if price is None:
            return None  # 无价格的行程不编造，直接丢弃
        dep_airport = first.get("origin", {}).get("displayCode", "") or ""
        arr_airport = last.get("destination", {}).get("displayCode", "") or ""
        dep_time = (first.get("departure") or "")[:16]
        arr_time = (last.get("arrival") or "")[:16]
        duration = leg.get("durationInMinutes")
        return {
            "source_id": self.source_id, "source_name": self.display_name,
            "product": ("直飞" if len(segments) == 1 else f"中转｜{len(segments) - 1} 段")
                       + "｜" + (airlines.carrier_name(carrier_code)),
            "flight_no": flight_no,
            "carrier_code": carrier_code,
            "carrier_name": airlines.carrier_name(carrier_code),
            "depart_time": self._iso_hhmm(dep_time),
            "arrive_time": self._iso_hhmm(arr_time),
            "cross_day": bool(dep_time and arr_time and arr_time[:10] > dep_time[:10]),
            "duration_text": (f"{int(duration) // 60}小时{int(duration) % 60:02d}分"
                              if isinstance(duration, (int, float)) else "未披露"),
            "dep_airport": cities.airport_name(dep_airport) if dep_airport else "未披露",
            "arr_airport": cities.airport_name(arr_airport) if arr_airport else "未披露",
            "price": price, "tax_included": True, "is_deal": False,
            "fee_breakdown": {"airport_fee": None, "fuel_fee": None, "tax_included": True,
                              "included_desc": "上游报价（Skyscanner 口径，未拆分机建/燃油）"},
            "total_estimate": price,
            "note": "真实接口·RapidAPI（上游 Skyscanner，价格币种/口径以下单页为准）",
        }

    def search(self, origin, destination, date):
        available, _ = self.availability(origin, destination, date)
        if not available:
            return []
        key = f"offers_{origin}_{destination}_{date.isoformat()}.json"
        cached = self._cache_read(key)
        if cached and time.time() < float(cached.get("expire_at", 0)):
            return cached["rows"]
        payload = self._get("/web/flights/search-one-way", {
            "fromEntityId": self._entity_id(origin),
            "toEntityId": self._entity_id(destination),
            "departDate": date.isoformat(), "currency": "CNY",
            "market": "zh-CN", "locale": "zh-CN"})
        rows = []
        for itinerary in (payload.get("data", {}).get("itineraries") or [])[:8]:
            leg = ((itinerary.get("legs") or [{}])[0])
            row = self._to_offer(itinerary, leg)
            if row:
                rows.append(row)
        self._cache_write(key, {"expire_at": time.time() + OFFER_CACHE_TTL, "rows": rows})
        return rows
