"""Amadeus Self-Service 真实接口适配器（ISSUE-013，默认关闭）。

接入条件：在 https://developers.amadeus.com 自行注册开发者账号，复制
amadeus_config.example.json 为 amadeus_config.json 并填入 Key；可选填
proxy（如 http://127.0.0.1:7897）——本机代理为规则模式时需确保
*.amadeus.com 走代理。未配置 Key 时渠道状态为 not_available，不出数据行，
不假装接入（ISSUE-012 成功标准 ④）。

调用纪律（本文件内置，使用者无需操心）：
- Token 缓存至过期前 2 分钟（内存 + 文件），同 Key 不重复申请；
- 同 (航线, 日期) 查询结果缓存 30 分钟；
- 当日 API 调用（token+查询）自我上限 200 次，达到即拒绝并明确提示；
- 单次请求超时 10 秒；遇 429/额度类错误立即上抛说明，不自动重试。
覆盖说明：Amadeus 为国际 GDS，对中国国内航线覆盖有限——返回为空时如实
显示无报价，不编造。
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

CONFIG_NAME = "amadeus_config.json"
DAILY_CALL_CAP = 200          # 当日 API 调用自我上限（token 与查询各计 1 次）
OFFER_CACHE_TTL = 30 * 60     # 同航线同日期结果缓存 30 分钟
TOKEN_SKEW = 120              # Token 提前 2 分钟视为过期
TIMEOUT = 10                  # 单次请求超时（秒）
HOSTS = {"test": "test.api.amadeus.com", "prod": "api.amadeus.com"}

_NO_KEY_REASON = ("未配置 API Key——请在 developers.amadeus.com 自行注册（免费），"
                  "复制 src/sources/amadeus_config.example.json 为 amadeus_config.json 并填入")


class AmadeusSource(Source):
    """真实接口票源：数据来自 Amadeus 测试环境（可能为缓存数据，行内标注）。"""

    source_id = "amadeus"
    display_name = "Amadeus 真实接口"
    tagline = ("国际 GDS 开发者 API（Self-Service），真实报价；"
               "对中国国内航线覆盖有限，以实际返回为准；未配置 Key 时不接入")
    entry_url = "https://developers.amadeus.com"
    entry_note = "Amadeus 开发者平台（注册后自配 Key 启用本渠道）"

    def __init__(self, config_path=None):
        self._config_path = (Path(config_path) if config_path
                             else Path(__file__).parent / CONFIG_NAME)
        self._cache_dir = self._config_path.parent / ".amadeus_cache"
        self._config = self._read_config()

    # ---------- 配置与状态 ----------

    def _read_config(self):
        try:
            data = json.loads(self._config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    @property
    def has_key(self):
        return bool(self._config.get("client_id")) and bool(self._config.get("client_secret"))

    @property
    def status(self):  # 覆盖基类类属性：按是否配置 Key 动态报告
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
        """当日调用计数：跨日重置；达到上限抛错（渠道降级并提示，不重试）。"""
        today = dt.date.today().isoformat()
        counter = self._cache_read("day_counter.json") or {"date": today, "calls": 0}
        if counter.get("date") != today:
            counter = {"date": today, "calls": 0}
        if counter["calls"] >= DAILY_CALL_CAP:
            raise RuntimeError(f"Amadeus 当日调用已达自我上限 {DAILY_CALL_CAP} 次，"
                               "为控制风险今日不再请求（缓存仍可用，明日自动恢复）")
        counter["calls"] += 1
        self._cache_write("day_counter.json", counter)

    # ---------- HTTP ----------

    def _opener(self):
        proxy = str(self._config.get("proxy") or "").strip()
        if proxy:
            return urllib.request.build_opener(
                urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
        return urllib.request.build_opener()

    def _token(self):
        cached = self._cache_read("token.json")
        if cached and time.time() < float(cached.get("expire_at", 0)):
            return cached["access_token"]
        self._daily_guard()
        host = HOSTS.get(self._config.get("env", "test"), HOSTS["test"])
        body = urllib.parse.urlencode({
            "grant_type": "client_credentials",
            "client_id": self._config["client_id"],
            "client_secret": self._config["client_secret"]}).encode()
        request = urllib.request.Request(f"https://{host}/v1/security/oauth2/token",
                                         data=body, method="POST")
        with self._opener().open(request, timeout=TIMEOUT) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        token = {"access_token": payload["access_token"],
                 "expire_at": time.time() + int(payload.get("expires_in", 1700)) - TOKEN_SKEW}
        self._cache_write("token.json", token)
        return token["access_token"]

    def _fetch_offers(self, origin, destination, date):
        self._daily_guard()
        host = HOSTS.get(self._config.get("env", "test"), HOSTS["test"])
        query = urllib.parse.urlencode({
            "originLocationCode": origin, "destinationLocationCode": destination,
            "departureDate": date.isoformat(), "adults": 1,
            "currencyCode": "CNY", "max": 6})
        request = urllib.request.Request(
            f"https://{host}/v2/shopping/flight-offers?{query}",
            headers={"Authorization": f"Bearer {self._token()}"})
        try:
            with self._opener().open(request, timeout=TIMEOUT) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                raise RuntimeError("Amadeus 返回 429（调用频率/额度超限），本渠道已暂停——"
                                   "请到开发者控制台核对配额，不要重试") from None
            raise RuntimeError(f"Amadeus 接口返回 HTTP {exc.code}") from None

    # ---------- 映射与查询 ----------

    @staticmethod
    def _hhmm(iso):
        return iso[11:16] if len(iso) >= 16 else "未披露"

    @staticmethod
    def _duration_text(iso):
        """PT8H5M → 8小时05分；解析失败返回空串。"""
        text, hours, minutes = iso or "", "", ""
        if "H" in text:
            hours = text.split("PT")[-1].split("H")[0]
            tail = text.split("H")[-1]
            minutes = tail.rstrip("M") if tail.endswith("M") else ""
        elif "M" in text:
            minutes = text.split("PT")[-1].rstrip("M")
        parts = []
        if hours:
            parts.append(f"{int(hours)}小时")
        if minutes:
            parts.append(f"{int(minutes):02d}分")
        return "".join(parts)

    def _to_offer(self, item):
        segments = item["itineraries"][0]["segments"]
        first, last = segments[0], segments[-1]
        flight_no = "|".join(f"{s['carrierCode']}{s['number']}" for s in segments)
        dep_date, arr_date = first["departure"]["at"][:10], last["arrival"]["at"][:10]
        price = round(float(item["price"]["grandTotal"]))
        return {
            "source_id": self.source_id, "source_name": self.display_name,
            "product": ("直飞" if len(segments) == 1 else f"中转｜{len(segments) - 1} 段")
                       + "｜" + airlines.carrier_name(first["carrierCode"]),
            "flight_no": flight_no,
            "carrier_code": first["carrierCode"],
            "carrier_name": airlines.carrier_name(first["carrierCode"]),
            "depart_time": self._hhmm(first["departure"]["at"]),
            "arrive_time": self._hhmm(last["arrival"]["at"]),
            "cross_day": arr_date > dep_date,
            "duration_text": self._duration_text(item["itineraries"][0].get("duration", "")),
            "dep_airport": cities.airport_name(first["departure"]["iataCode"]),
            "arr_airport": cities.airport_name(last["arrival"]["iataCode"]),
            "price": price, "tax_included": True, "is_deal": False,
            "fee_breakdown": {"airport_fee": None, "fuel_fee": None, "tax_included": True,
                              "included_desc": "含税总价（Amadeus 口径，未拆分机建/燃油）"},
            "total_estimate": price,
            "note": "真实接口·Amadeus 测试环境（数据可能为缓存，购买以官方渠道为准）",
        }

    def search(self, origin, destination, date):
        available, _ = self.availability(origin, destination, date)
        if not available:
            return []
        key = f"offers_{origin}_{destination}_{date.isoformat()}.json"
        cached = self._cache_read(key)
        if cached and time.time() < float(cached.get("expire_at", 0)):
            return cached["rows"]
        payload = self._fetch_offers(origin, destination, date)
        rows = [self._to_offer(item) for item in payload.get("data", [])[:6]]
        self._cache_write(key, {"expire_at": time.time() + OFFER_CACHE_TTL, "rows": rows})
        return rows
