"""Duffel Flights 适配器（v1.16.0，ISSUE-025）——国际线第二真源。

背景：Amadeus Self-Service 于 2026-02 停止新注册、2026-07-17 整体下线
（现只保留 Enterprise 合同制，需 IATA/ARC 资质），国际线因此少了一个候选。
Duffel 是同类自助平台中目前仍开放注册的一家：注册即得 token，Flights API
覆盖 300+ 航司（NDC / GDS / LCC），REST + JSON，搜索不收费、下单才结算。

**双闸门（零编造红线，ISSUE-025 定案）**
Duffel 测试模式（token 前缀 `duffel_test_`）返回的是沙盒航司 **Duffel Airways**
（IATA `ZZ`）的虚构航班与虚构价格，官方文档原话是
"you won't see realistic flight schedules or prices"。
本工具禁止展示未经证实的价格，故测试 token 一律 `not_available` 且**不发起任何
网络请求**；只有 `duffel_live_`（需完成账号激活与 KYC）才算真正接入。
FARE_RADAR_OFFLINE=1 时同样短路（测试零真实请求，红线第 1 条）。

接口事实（2026-10-26 按官方文档核对，非照抄二手教程）：
- `POST https://api.duffel.com/air/offer_requests?return_offers=true`
  必带头：`Authorization: Bearer <token>`、`Duffel-Version: v2`、
          `Content-Type: application/json`、`Accept: application/json`
  body：`{"data": {"slices": [{"origin","destination","departure_date"}],
                   "passengers": [{"type": "adult"}], "cabin_class": "economy"}}`
- **请求侧没有货币参数**，货币由响应 `offers[].total_currency` 决定。
  故非 CNY 报价**不做汇率折算**（折算等于替用户编一个价），
  该行保留展示但价格位标"未披露"，且不参与排序与最低价竞争（v1.14 口径）。

调用纪律：
- 同 (航线, 日期) 结果缓存 15 分钟（Duffel 报价本身约 20 分钟失效，缓存不宜更长）；
- 空结果缓存 30 分钟；
- 当日自我上限 200 次（搜索不收费，此限额只防死循环，不属于计费护栏）；
- 单次请求超时 25 秒（`supplier_timeout=20000` 由 Duffel 侧控上游航司）。
"""

import datetime as dt
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from .base import Source, offline_mode, OFFLINE_REASON
from . import carriers, cities

CONFIG_NAME = "duffel_config.json"
ENDPOINT = "https://api.duffel.com/air/offer_requests"
API_VERSION = "v2"
SUPPLIER_TIMEOUT_MS = 20000   # 每家航司最长等待（毫秒），Duffel 侧控
TIMEOUT = 25                  # 本机请求超时（秒）
DAILY_CALL_CAP = 200          # 当日自我上限（搜索不收费，仅防失控）
OFFER_CACHE_TTL = 15 * 60
EMPTY_CACHE_TTL = 30 * 60

_NOTE_NO_KEY = ("未配置访问令牌——在 https://duffel.com 免费注册（搜索不收费）后，"
                "于 Dashboard → Developers → Access tokens 创建 **Live** token，"
                "填入 src/sources/duffel_config.json（已 gitignore，不入库）")

_NOTE_TEST_TOKEN = ("检测到 Duffel **测试模式** token（duffel_test_）：该模式的报价以沙盒航司"
                    " Duffel Airways（IATA ZZ）的虚构数据为主，也会混入真实航司（CA/MU/BA…）"
                    "但价格同样是沙盒值、不可订；本工具不展示虚构价格，故不接入、"
                    "不发起任何请求——请改用账号激活后创建的 live token（duffel_live_）")

# 第二道闸门（v1.16.1）：token 前缀闸门只能拦住 duffel_test_ 开头的凭据。
# 若上游改了前缀规则、或账号在 live 模式下被降级，响应顶层的 live_mode=false
# 仍能识破——这是实测响应里确实存在的字段（2026-10-26 三条航线均为 false）。
_NOTE_UPSTREAM_TEST = ("Duffel 响应标记 live_mode=false（测试环境数据）：即便 token 形如 live，"
                       "本渠道也只采用 live 报价，故不展示——请确认账号已通过激活审核")

_NOTE_UNKNOWN_TOKEN = ("访问令牌格式无法识别：Duffel token 必须以 duffel_live_（真实）"
                       "或 duffel_test_（虚构沙盒，本工具不使用）开头")

# 机场中文名补充表（**仅本适配器展示用**）。
# 收入国内 50 城表之外的常见机场（国际线为主，另含浦东/大兴/天府三个未进 PRD 表的国内枢纽）。
# 为什么不放进 cities.py：那里的 _CITIES 是 PRD 定义的 50 个国内城市唯一事实来源，
# 同时也是网页下拉、CLI 校验与定价引擎的输入域，往里塞国际机场会改变可查航线范围。
# 本表只做"三字码 → 中文机场名"的翻译，未收录时退回上游英文全称（不猜）。
_AIRPORT_ZH_EXTRA = {
    # 东亚 / 东南亚 / 南亚
    "PVG": "上海浦东国际机场", "PKX": "北京大兴国际机场", "TFU": "成都天府国际机场",
    "HKG": "香港国际机场", "MFM": "澳门国际机场", "TPE": "桃园国际机场",
    "KHH": "高雄国际机场", "NRT": "成田国际机场", "HND": "羽田机场",
    "KIX": "关西国际机场", "NGO": "中部国际机场", "FUK": "福冈机场",
    "CTS": "新千岁机场", "OKA": "那霸机场", "ICN": "仁川国际机场",
    "GMP": "金浦国际机场", "PUS": "金海国际机场", "CJU": "济州国际机场",
    "SIN": "樟宜机场", "BKK": "素万那普机场", "DMK": "廊曼机场",
    "KUL": "吉隆坡国际机场", "MNL": "尼诺伊·阿基诺国际机场", "SGN": "新山一国际机场",
    "HAN": "内排国际机场", "DPS": "巴厘岛国际机场", "CGK": "苏加诺-哈达国际机场",
    "HKT": "普吉国际机场", "DEL": "英迪拉·甘地国际机场", "BOM": "孟买国际机场",
    # 中东 / 非洲
    "DXB": "迪拜国际机场", "AUH": "扎耶德国际机场", "DOH": "哈马德国际机场",
    "IST": "伊斯坦布尔机场", "CAI": "开罗国际机场", "JNB": "约翰内斯堡国际机场",
    # 欧洲
    "LHR": "伦敦希思罗机场", "LGW": "伦敦盖特威克机场", "CDG": "戴高乐机场",
    "ORY": "奥利机场", "FRA": "法兰克福机场", "MUC": "慕尼黑机场",
    "AMS": "阿姆斯特丹史基浦机场", "MAD": "马德里巴拉哈斯机场", "BCN": "巴塞罗那机场",
    "FCO": "罗马菲乌米奇诺机场", "MXP": "米兰马尔彭萨机场", "ZRH": "苏黎世机场",
    "VIE": "维也纳国际机场", "BRU": "布鲁塞尔机场", "CPH": "哥本哈根机场",
    "ARN": "斯德哥尔摩阿兰达机场", "OSL": "奥斯陆加勒穆恩机场", "HEL": "赫尔辛基万塔机场",
    "LIS": "里斯本机场", "ATH": "雅典国际机场", "SVO": "谢列梅捷沃国际机场",
    "DUB": "都柏林机场", "MAN": "曼彻斯特机场",
    # 北美 / 大洋洲
    "JFK": "纽约肯尼迪国际机场", "EWR": "纽瓦克自由国际机场", "LAX": "洛杉矶国际机场",
    "SFO": "旧金山国际机场", "SEA": "西雅图-塔科马国际机场", "ORD": "芝加哥奥黑尔国际机场",
    "BOS": "波士顿洛根国际机场", "IAD": "华盛顿杜勒斯国际机场", "ATL": "亚特兰大国际机场",
    "DFW": "达拉斯-沃思堡国际机场", "MIA": "迈阿密国际机场", "HNL": "檀香山国际机场",
    "YVR": "温哥华国际机场", "YYZ": "多伦多皮尔逊国际机场", "SYD": "悉尼金斯福德·史密斯机场",
    "MEL": "墨尔本机场", "BNE": "布里斯班机场", "PER": "珀斯机场", "AKL": "奥克兰机场",
}


class DuffelSource(Source):
    """真实接口票源：Duffel Flights（国际线为主，含税总价，CNY 报价才参与比价）。"""

    source_id = "duffel"
    display_name = "Duffel 真实接口"
    tagline = ("国际线真实报价（300+ 航司，NDC/GDS/LCC）；含税总价，"
               "非人民币报价不做折算、不参与比价；需 live token（测试模式为虚构数据，不接入）")
    entry_url = "https://duffel.com"
    entry_note = "Duffel 开发者平台（注册后用 live token 启用本渠道，测试模式不接入）"

    def __init__(self, config_path=None):
        self._config_path = (Path(config_path) if config_path
                             else Path(__file__).parent / CONFIG_NAME)
        self._cache_dir = self._config_path.parent / ".duffel_cache"
        self._config = self._read_config()

    # ---------- 配置与状态 ----------

    def _read_config(self):
        try:
            data = json.loads(self._config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    @property
    def token(self):
        return str(self._config.get("access_token") or "").strip()

    @property
    def mode(self):
        """token 形态：live / test / unknown / none（决定是否允许联网）。"""
        if not self.token:
            return "none"
        if self.token.startswith("duffel_live_"):
            return "live"
        if self.token.startswith("duffel_test_"):
            return "test"
        return "unknown"

    @property
    def status(self):
        if offline_mode():
            return "not_available"
        return "active" if self.mode == "live" else "not_available"

    @property
    def status_reason(self):
        if offline_mode():
            return OFFLINE_REASON
        return {"live": "", "test": _NOTE_TEST_TOKEN,
                "none": _NOTE_NO_KEY, "unknown": _NOTE_UNKNOWN_TOKEN}[self.mode]

    def availability(self, origin, destination, date):
        """双闸门：离线 / 非 live token 一律不可用，且 search 不会发请求。"""
        if offline_mode():
            return False, OFFLINE_REASON
        if self.mode != "live":
            return False, self.status_reason
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
        """当日调用计数（搜索不收费，仅防失控）：跨日重置，达上限抛错不重试。"""
        today = dt.date.today().isoformat()
        counter = self._cache_read("day_counter.json") or {"date": today, "calls": 0}
        if counter.get("date") != today:
            counter = {"date": today, "calls": 0}
        if counter["calls"] >= DAILY_CALL_CAP:
            raise RuntimeError(f"Duffel 当日自我上限 {DAILY_CALL_CAP} 次已到，"
                               "今日不再请求（缓存仍可用，明日自动恢复）")
        counter["calls"] += 1
        self._cache_write("day_counter.json", counter)

    # ---------- HTTP ----------

    def _opener(self):
        proxy = str(self._config.get("proxy") or "").strip()
        if proxy:
            return urllib.request.build_opener(
                urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
        return urllib.request.build_opener()

    def _fetch_offers(self, origin, destination, date):
        self._daily_guard()
        query = urllib.parse.urlencode({"return_offers": "true",
                                        "supplier_timeout": SUPPLIER_TIMEOUT_MS})
        body = json.dumps({"data": {
            "slices": [{"origin": origin, "destination": destination,
                        "departure_date": date.isoformat()}],
            "passengers": [{"type": "adult"}],
            "cabin_class": "economy"}}, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            f"{ENDPOINT}?{query}", data=body, method="POST",
            headers={"Authorization": f"Bearer {self.token}",
                     "Duffel-Version": API_VERSION,
                     "Content-Type": "application/json",
                     "Accept": "application/json",
                     "User-Agent": "fareradar/1.16 (+https://github.com/Qsianuo/fare-radar)"})
        try:
            with self._opener().open(request, timeout=TIMEOUT) as resp:
                return json.loads(resp.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as exc:
            raise RuntimeError(self._http_error_text(exc.code)) from None
        except Exception as exc:
            raise RuntimeError(f"Duffel 请求失败：{type(exc).__name__}"
                               "（网络/代理问题，已记录；请勿立即重发）") from None

    @staticmethod
    def _http_error_text(code):
        if code in (401, 403):
            return (f"Duffel 返回 HTTP {code}：token 无效或无该接口权限——"
                    "请到 Dashboard → Developers → Access tokens 重新创建 live token")
        if code == 429:
            return "Duffel 返回 HTTP 429（频率/额度超限），本渠道已暂停——核对配额，不要重试"
        if code == 422:
            return ("Duffel 返回 HTTP 422：请求参数不被接受"
                    "（多为 IATA 三字码不合法或该航线不支持）")
        return f"Duffel 接口返回 HTTP {code}"

    # ---------- 映射 ----------

    @staticmethod
    def _hhmm(iso):
        text = str(iso or "")
        return text[11:16] if len(text) >= 16 else "未披露"

    @staticmethod
    def _duration_text(iso):
        """PT2H26M → 2小时26分；解析失败返回"未披露"（不猜）。"""
        text = str(iso or "").strip().upper()
        if not text.startswith("PT") or not any(c in text for c in "HM"):
            return "未披露"
        body = text[2:]
        hours = minutes = 0
        try:
            if "H" in body:
                hours_part, body = body.split("H", 1)
                hours = int(hours_part or 0)
            if "M" in body:
                minutes = int(body.split("M", 1)[0] or 0)
        except (ValueError, TypeError):
            return "未披露"
        if hours < 0 or not 0 <= minutes < 60:
            return "未披露"
        return f"{hours}小时{minutes:02d}分" if hours else f"{minutes}分"

    @staticmethod
    def _carrier_name(code, upstream_name):
        """展示用承运人名：本地中文表 > 上游英文原名 > 二字码。

        不复用 carriers.display_name：它把含 ASCII 字母的名字一律判为"机翻"丢弃，
        而 Duffel 给的是航司**官方英文名**（如 British Airways），丢掉不划算。
        """
        local = carriers.cn_name(code)
        if local:
            return local
        name = str(upstream_name or "").strip()
        return name or str(code or "").strip()

    def _airport(self, node):
        iata = str((node or {}).get("iata_code") or "").strip().upper()
        known = cities.airport_name(iata)          # 国内 50 城（PRD 唯一事实来源）
        if known:
            return known
        if iata in _AIRPORT_ZH_EXTRA:
            return f"{_AIRPORT_ZH_EXTRA[iata]}（{iata}）"
        upstream = str((node or {}).get("name") or "").strip()
        if upstream and iata:
            return f"{upstream}（{iata}）"
        return upstream or iata or "未披露"

    def _to_offer(self, item):
        """Duffel 一个 offer → 比价行。缺航段/缺起降时刻则丢弃。"""
        slices = item.get("slices") or []
        if not isinstance(slices, list) or not slices:
            return None
        segments = slices[0].get("segments") or []
        if not isinstance(segments, list) or not segments:
            return None
        first, last = segments[0] or {}, segments[-1] or {}
        dep_at = str(first.get("departing_at") or "")
        arr_at = str(last.get("arriving_at") or "")
        if not dep_at or not arr_at:
            return None

        marketing = first.get("marketing_carrier") or {}
        code = str(marketing.get("iata_code")
                   or (first.get("operating_carrier") or {}).get("iata_code") or "").strip().upper()
        numbers = []
        for seg in segments:
            carrier = seg.get("marketing_carrier") or {}
            seg_code = str(carrier.get("iata_code") or "").strip().upper()
            number = str(seg.get("marketing_carrier_flight_number") or "").strip()
            if seg_code and number:
                numbers.append(f"{seg_code}{number}")
        flight_no = "|".join(numbers).replace("|", "/") if numbers else "未披露"
        carrier = self._carrier_name(code, marketing.get("name"))

        legs = len(segments)
        product = ("直飞" if legs == 1 else f"中转｜{legs - 1} 段") + "｜" + carrier

        # 货币：请求侧无法指定，非 CNY 一律不折算（折算即编价）
        currency = str(item.get("total_currency") or "").strip().upper()
        amount = None
        try:
            amount = float(item.get("total_amount"))
        except (TypeError, ValueError):
            amount = None
        has_cny = currency == "CNY" and amount is not None and amount > 0
        price = round(amount) if has_cny else None

        if has_cny:
            price_note = None
            note = ("真实接口·Duffel（含税总价，未拆分机建/燃油；"
                    "报价约 20 分钟内有效，下单以航司渠道为准）")
        elif amount is not None and currency:
            price_note = (f"上游报价 {currency} {amount:,.2f}，非人民币"
                          "（不做汇率折算，故不参与比价）")
            note = "真实接口·Duffel·报价非人民币，仅展示航班与外币价格，不参与比价"
        else:
            price_note = "上游未返回可用报价"
            note = "真实接口·Duffel·报价缺失（上游该航段无可用价格）"

        return {
            "source_id": self.source_id,
            "source_name": self.display_name,
            "product": product,
            "flight_no": flight_no,
            "carrier_code": code,
            "carrier_name": carrier,
            "depart_time": self._hhmm(dep_at),
            "arrive_time": self._hhmm(arr_at),
            "cross_day": arr_at[:10] != dep_at[:10],
            "duration_text": self._duration_text(
                (slices[0] or {}).get("duration")
                or (first.get("duration") if legs == 1 else "")),
            "dep_airport": self._airport(first.get("origin")),
            "arr_airport": self._airport(last.get("destination")),
            "price": price,
            "tax_included": True if has_cny else None,
            "is_deal": False,
            "total_estimate": price,
            "price_note": price_note,
            "note": note,
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

        payload = self._fetch_offers(origin, destination, date)
        data = payload.get("data") if isinstance(payload, dict) else None
        offers = (data or {}).get("offers") if isinstance(data, dict) else None
        offers = offers if isinstance(offers, list) else []

        # 第二道闸门：上游自报 live_mode=false（测试环境数据）一律不采用。
        if isinstance(data, dict) and data.get("live_mode") is False:
            self._cache_write(key, {"expire_at": time.time() + EMPTY_CACHE_TTL,
                                    "rows": [], "raw_offers": len(offers),
                                    "upstream_live_mode": False})
            return []

        # 上游返回顺序不保证按价格升序（实测 PVG→LHR 82 条近似升序，但不可依赖）。
        # 人民币优先、同币种按总价升序后再截取，避免把便宜的行截掉。
        def _sort_key(item):
            cur = str(item.get("total_currency") or "").strip().upper()
            try:
                amount = float(item.get("total_amount"))
            except (TypeError, ValueError):
                amount = float("inf")
            return (0 if cur == "CNY" else 1, amount)

        offers = sorted([o for o in offers if isinstance(o, dict)], key=_sort_key)

        rows, foreign = [], []
        for item in offers[:12]:
            if not isinstance(item, dict):
                continue
            row = self._to_offer(item)
            if not row:
                continue
            cur = str(item.get("total_currency") or "").strip().upper()
            if row["price"] is None and cur:
                foreign.append(cur)
            rows.append(row)
        rows = rows[:6]
        ttl = OFFER_CACHE_TTL if rows else EMPTY_CACHE_TTL
        self._cache_write(key, {"expire_at": time.time() + ttl, "rows": rows,
                                "raw_offers": len(offers),
                                "foreign_currencies": sorted(set(foreign))})
        return rows

    def empty_note(self, origin, destination, date):
        """空结果原因（只读缓存，不回源）。有报价或原因未知返回 None。"""
        cached = self._cache_read(f"offers_{origin}_{destination}_{date.isoformat()}.json")
        if not cached or cached.get("rows"):
            return None
        if cached.get("upstream_live_mode") is False:
            return _NOTE_UPSTREAM_TEST
        raw = cached.get("raw_offers")
        if isinstance(raw, int) and raw > 0:
            return (f"Duffel 返回 {raw} 条报价但均缺少航段或起降时刻，无法成行展示")
        return ("Duffel 查询成功但该航线该日期无报价——Duffel 主覆盖国际线，"
                "多数国内线不在其分销范围")
