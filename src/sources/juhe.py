"""聚合数据「航班机票查询」适配器（v1.15.0，ISSUE-024）。

定位：**国内航班 + 缓存票价**，补 RapidAPI 在国内线的两个缺口——
① 国内覆盖差（INC→CTU 类航线返回 status 204 无数据）；
② 有航班但 price 为 null（如 INC→SZX 2026-10-27 的 BK3037）。
实测同一条 BK3037：Google Flights 无价，聚合数据给 ¥918。

接口（2026-10-26 实测校准，非照抄文档）：
- `GET https://apis.juhe.cn/flight/query`
  参数 `departure` / `arrival` / `departureDate` / `key`（**不是** `start/end/date`，
  `/jipiao/query` 实测返回 404 error_code=10022，文档里那个写法不成立）；
- 返回 `{error_code, reason, result:{orderid, flightInfo:[...]}}`，`error_code == 0` 为成功；
- `flightInfo[]` 字段：airline / airlineName / flightNo / isCodeShare / equipment /
  departure(Name/Date/Time) / arrival(Name/Date/Time) / duration / transferNum / ticketPrice。

两个实测坑（写死在映射里）：
1. **`transferNum` 是航段数不是中转次数**：33 条样本里 transferNum=1 的 7 条全是直飞
   （flightNo 无分隔），transferNum=2 的 26 条 flightNo 均形如 `G54338 | PN6229`。
   若当"中转次数"用，直飞航班会被标成"中转1次"。
2. **`airlineName` 是机器翻译**：BK → "Okay航空公司"、G5 → "中国快运航空公司"
   （China Express 逐词直译）。中文名一律走 `carriers.cn_name()`，上游名清洗后兜底。

调用纪律（与 rapidapi_sky 同构，按次计费更严）：
- 同 (航线, 日期) 结果缓存 6 小时；空结果只缓存 30 分钟；
- 当日硬停 20 次、当月硬停 60 次（¥99/450 次档 ≈ 0.22 元/次，月上限约 13 元），
  **绝不自动充值**，触顶即抛中文原因、渠道降级、缓存仍可用；
- 单次超时 60 秒（实测正常 3.2s，但该接口偶发 25s+ 无响应，短超时会误判为失败）。

诚实披露：`ticketPrice` 是否含机建/燃油**上游未明示**，故 `tax_included=False`
（按票面价处理，由引擎用全站口径补费用明细，与飞常准同口径），note 里写清楚。
无价航班按 v1.14 口径**保留展示**（price=None + price_note），不填数、不参与排序。
"""

import datetime as dt
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from .base import Source, offline_mode, OFFLINE_REASON, NO_PRICE_NOTE
from . import carriers

CONFIG_NAME = "juhe_config.json"
ENDPOINT = "https://apis.juhe.cn/flight/query"

DAILY_CALL_CAP = 20           # 当日硬停（按次计费，先严后松）
MONTHLY_CALL_CAP = 60         # 当月硬停（≈13 元/月上限）
OFFER_CACHE_TTL = 6 * 3600
EMPTY_CACHE_TTL = 30 * 60
TIMEOUT = 60

_NO_KEY_REASON = (
    "未配置 API Key——在 https://www.juhe.cn/ 注册并申请「航班机票查询」后，"
    "于「我的 API」复制 AppKey 填入 src/sources/juhe_config.json（已 gitignore，不入库）")

# 上游错误码 → 中文原因（实测 10001/10022；10012 来自官方文档，未实测）
_ERROR_TEXT = {
    10001: "Key 无效（错误的请求 KEY）",
    10002: "Key 已被停用或无权调用该接口",
    10012: "调用次数已用尽（试用/套餐额度耗尽）",
    10022: "接口地址不存在（endpoint 写错）",
}


class JuheFlightSource(Source):
    """真实接口票源：聚合数据 · 航班机票查询（国内航班 + 缓存票价）。"""

    source_id = "juhe_flight"
    display_name = "聚合数据·航班机票"
    tagline = ("国内航班 + 缓存票价（按次计费，试用额度极小），"
               "补 Google Flights 在国内线的覆盖与缺价；价格为票面价，是否含税未明示")
    entry_url = "https://www.juhe.cn/"
    entry_note = "聚合数据开放平台（按次计费，Key 自配、日/月双硬停）"

    def __init__(self, config_path=None):
        self._config_path = (Path(config_path) if config_path
                             else Path(__file__).parent / CONFIG_NAME)
        self._cache_dir = self._config_path.parent / ".juhe_cache"
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
        return bool(str(self._config.get("api_key") or "").strip())

    @property
    def status(self):
        if offline_mode() or not self.has_key:
            return "not_available"
        return "active"

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
        """日/月双硬停。计费型接口：先扣后发，绝不自动充值（ISSUE-019 纪律）。"""
        today = dt.date.today().isoformat()
        month = today[:7]
        day_counter = self._cache_read("day_counter.json") or {"date": today, "calls": 0}
        if day_counter.get("date") != today:
            day_counter = {"date": today, "calls": 0}
        month_counter = self._cache_read("month_counter.json") or {"month": month, "calls": 0}
        if month_counter.get("month") != month:
            month_counter = {"month": month, "calls": 0}
        if month_counter["calls"] >= MONTHLY_CALL_CAP:
            raise RuntimeError(
                f"聚合数据当月调用已达自我上限 {MONTHLY_CALL_CAP} 次（按次计费保护线），"
                "本月不再请求；缓存仍可用。需要更多额度请自行到后台调整套餐——本程序不会自动充值")
        if day_counter["calls"] >= DAILY_CALL_CAP:
            raise RuntimeError(
                f"聚合数据当日调用已达自我上限 {DAILY_CALL_CAP} 次，今日不再请求；"
                "缓存仍可用，明日自动恢复")
        day_counter["calls"] += 1
        month_counter["calls"] += 1
        self._cache_write("day_counter.json", day_counter)
        self._cache_write("month_counter.json", month_counter)

    def quota_state(self):
        """当前自我限额用量（只读，供 /api/sources 与 CLI 展示）。"""
        today = dt.date.today().isoformat()
        day_counter = self._cache_read("day_counter.json") or {}
        month_counter = self._cache_read("month_counter.json") or {}
        used_today = (day_counter.get("calls", 0)
                      if day_counter.get("date") == today else 0)
        used_month = (month_counter.get("calls", 0)
                      if month_counter.get("month") == today[:7] else 0)
        return {"day_used": used_today, "day_cap": DAILY_CALL_CAP,
                "month_used": used_month, "month_cap": MONTHLY_CALL_CAP}

    # ---------- HTTP ----------

    def _fetch(self, origin, destination, date):
        self._quota_guard()
        query = urllib.parse.urlencode({
            "departure": origin, "arrival": destination,
            "departureDate": date.isoformat(), "key": self._config["api_key"]})
        request = urllib.request.Request(
            f"{ENDPOINT}?{query}",
            headers={"Accept": "application/json",
                     "User-Agent": "fareradar/1.15 (+https://github.com/Qsianuo/fare-radar)"})
        proxy = str(self._config.get("proxy") or "").strip()
        opener = (urllib.request.build_opener(urllib.request.ProxyHandler(
            {"http": proxy, "https": proxy})) if proxy
            else urllib.request.build_opener())
        try:
            with opener.open(request, timeout=TIMEOUT) as resp:
                payload = json.loads(resp.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"聚合数据接口返回 HTTP {exc.code}") from None
        except Exception as exc:
            raise RuntimeError(f"聚合数据请求失败：{type(exc).__name__}（该接口偶发慢响应，"
                               "已记录，建议稍后重试而非立即重发）") from None

        code = payload.get("error_code")
        if code not in (0, "0", None):
            reason = _ERROR_TEXT.get(code) or str(payload.get("reason") or "未说明")
            raise RuntimeError(f"聚合数据返回错误 {code}：{reason}")
        return payload

    # ---------- 映射 ----------

    @staticmethod
    def _duration_text(raw):
        """上游 "3h30m" → "3小时30分"；格式不认识就"未披露"（不猜）。"""
        text = str(raw or "").strip().lower()
        if not text or "h" not in text:
            return "未披露"
        try:
            hours_part, minutes_part = text.split("h", 1)
            hours = int(hours_part)
            minutes = int(minutes_part.replace("m", "").strip() or 0)
        except (ValueError, TypeError):
            return "未披露"
        if hours < 0 or not 0 <= minutes < 60:
            return "未披露"
        return f"{hours}小时{minutes:02d}分"

    def _to_offer(self, flight):
        """上游一条 flightInfo → 比价行。缺起降时刻则丢弃（连哪一班都说不清）。"""
        dep_time = str(flight.get("departureTime") or "").strip()
        arr_time = str(flight.get("arrivalTime") or "").strip()
        flight_no = str(flight.get("flightNo") or "").replace(" ", "")
        if not dep_time or not arr_time or not flight_no:
            return None

        code = str(flight.get("airline") or "").strip().upper()
        carrier = carriers.display_name(code, flight.get("airlineName"))
        # 航段数（transferNum 是段数不是中转次数；>=2 表示联程，无中转城市信息）
        try:
            legs = int(flight.get("transferNum") or 1)
        except (TypeError, ValueError):
            legs = 1
        legs = max(1, legs)

        raw_price = flight.get("ticketPrice")
        has_price = (isinstance(raw_price, (int, float))
                     and not isinstance(raw_price, bool) and raw_price > 0)
        price = round(raw_price) if has_price else None

        equipment = str(flight.get("equipment") or "").strip()
        product = ("直飞" if legs == 1 else f"{legs} 段联程（中转城市未披露）") + "｜" + carrier
        if equipment:
            product += f"｜{equipment}"

        dep_ap = f"{str(flight.get('departureName') or '未披露').strip()}（{flight.get('departure') or ''}）"
        arr_ap = f"{str(flight.get('arrivalName') or '未披露').strip()}（{flight.get('arrival') or ''}）"

        return {
            "source_id": self.source_id,
            "source_name": self.display_name,
            "product": product,
            "flight_no": flight_no.replace("|", "/"),
            "carrier_code": code,
            "carrier_name": carrier,
            "depart_time": dep_time,
            "arrive_time": arr_time,
            # 用日期字段判跨天，比"到达时刻 < 起飞时刻"可靠（长航程同刻也可能误判）
            "cross_day": (str(flight.get("arrivalDate") or "")
                          != str(flight.get("departureDate") or "")),
            "duration_text": self._duration_text(flight.get("duration")),
            "dep_airport": dep_ap,
            "arr_airport": arr_ap,
            "price": price,
            # 上游未明示是否含机建燃油 → 按票面价处理，费用明细交给引擎统一补
            "tax_included": False if has_price else None,
            "is_deal": False,
            "total_estimate": None,          # 由引擎 collect 按全站口径补
            "price_note": None if has_price else NO_PRICE_NOTE,
            "note": ("真实接口·聚合数据（缓存票价，非秒级实时；票面是否含税上游未明示，"
                     "到手价按未含机建燃油估算，下单以官方渠道为准）" if has_price
                     else "真实接口·聚合数据·价格未披露（上游该日期未返回票价，"
                          "航班信息真实，购票前请到官方渠道核实价格）"),
        }

    # ---------- 查询 ----------

    def search(self, origin, destination, date):
        available, _ = self.availability(origin, destination, date)
        if not available:
            return []
        key = f"offers_{origin}_{destination}_{date.isoformat()}.json"
        cached = self._cache_read(key)
        if cached and time.time() < float(cached.get("expire_at", 0)):
            return cached["rows"]

        payload = self._fetch(origin, destination, date)
        result = payload.get("result") or {}
        info = result.get("flightInfo") if isinstance(result, dict) else None
        info = info if isinstance(info, list) else []
        rows = []
        for flight in info:
            if not isinstance(flight, dict):
                continue
            row = self._to_offer(flight)
            if row:
                rows.append(row)
        quoted = sum(1 for r in rows if r.get("price") is not None)
        ttl = OFFER_CACHE_TTL if rows else EMPTY_CACHE_TTL
        self._cache_write(key, {"expire_at": time.time() + ttl,
                                "rows": rows,
                                "raw_flights": len(info),
                                "no_price_count": len(rows) - quoted,
                                "orderid": str(result.get("orderid") or "")})
        return rows

    def empty_note(self, origin, destination, date):
        """空结果原因（只读缓存，不回源）。有报价或原因未知返回 None。"""
        cached = self._cache_read(f"offers_{origin}_{destination}_{date.isoformat()}.json")
        if not cached or cached.get("rows"):
            return None
        raw = cached.get("raw_flights")
        if isinstance(raw, int) and raw > 0:
            return (f"上游返回 {raw} 条航班但均缺少航班号或起降时刻，无法成行展示")
        return "上游查询成功但该航线该日期无航班记录（聚合数据未收录该航线或当日无排班）"
