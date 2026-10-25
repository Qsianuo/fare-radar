"""Travelpayouts（Aviasales 联盟）价格日历适配器——**只出参考价，不参与比价**（v1.12.0，ISSUE-021）。

定位说明（重要）：
- 本渠道**不产出比价行**（`search()` 恒返回空、`availability()` 恒不可用），只提供一个
  "整月每日最低参考价"的旁路能力，用于回答"这个月哪天飞最便宜"。
- 数据是 Aviasales 的**搜索缓存**（用户搜过的航线才有），实测国内覆盖极不均衡：
  SHA→CTU 36 天、HRB→SYX 143 天，但 INC→CAN / XMN→PEK / LHW→SZX 等为 0 天。
  因此**有则显示、无则整块隐藏**，绝不填补、绝不推算。
- 只有「日期 + 最低价 + 航司 + 航班号」，**没有起降时刻/机场/舱位/机型**，所以不能当报价用；
  界面必须标注"参考价，非可售报价"。

费用：免费（联盟 token），无按次计费；结果缓存 12 小时避免无谓请求。
鉴权：`token` 查询参数，存 `travelpayouts_config.json`（已 gitignore，不入库）。
"""

import datetime as dt
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from .base import Source, offline_mode, OFFLINE_REASON

CONFIG_NAME = "travelpayouts_config.json"
BASE = "https://api.travelpayouts.com/v1/prices/calendar"
CALENDAR_CACHE_TTL = 12 * 3600
TIMEOUT = 20

_NO_TOKEN_REASON = ("未配置 token——在 https://www.travelpayouts.com/ 免费注册联盟账号后，"
                    "于个人资料页复制 API token 填入 src/sources/travelpayouts_config.json")
_NOT_OFFER_REASON = "参考价日历渠道：只提供整月最低参考价，不产出可比价报价"


class TravelpayoutsCalendarSource(Source):
    """整月价格日历（参考价，非可售）。不产出比价行。"""

    source_id = "travelpayouts"
    offer_channel = False
    display_name = "Aviasales·价格日历"
    tagline = ("整月每日最低参考价（Aviasales 搜索缓存，免费），只回答「哪天最便宜」，"
               "无时刻/舱位，不参与比价")
    entry_url = "https://www.travelpayouts.com/"
    entry_note = "Travelpayouts 联盟平台（免费注册，token 自配）"

    def __init__(self, config_path=None):
        base = Path(__file__).resolve().parent
        self._config_path = Path(config_path) if config_path else base / CONFIG_NAME
        self._cache_dir = self._config_path.parent / ".travelpayouts_cache"
        self._config = self._read_config()

    # ---------- 配置与状态 ----------

    def _read_config(self):
        try:
            data = json.loads(self._config_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    @property
    def has_token(self):
        return bool(str(self._config.get("token") or "").strip())

    @property
    def status(self):
        if offline_mode() or not self.has_token:
            return "not_available"
        return "active"

    @property
    def status_reason(self):
        if offline_mode():
            return OFFLINE_REASON
        if not self.has_token:
            return _NO_TOKEN_REASON
        return _NOT_OFFER_REASON       # 已配置也不产出比价行，如实说明用途

    def availability(self, origin, destination, date):
        """本渠道永远不提供当日报价（只给整月参考价）。"""
        if offline_mode():
            return False, OFFLINE_REASON
        if not self.has_token:
            return False, _NO_TOKEN_REASON
        return False, _NOT_OFFER_REASON

    def search(self, origin, destination, date):
        return []                       # 不产出比价行（契约要求，非占位）

    # ---------- 缓存 ----------

    def _cache_read(self, name):
        try:
            return json.loads((self._cache_dir / name).read_text(encoding="utf-8"))
        except Exception:
            return None

    def _cache_write(self, name, payload):
        self._cache_dir.mkdir(exist_ok=True)
        (self._cache_dir / name).write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    # ---------- 价格日历 ----------

    def calendar(self, origin, destination, month):
        """返回该月每日最低参考价列表（按日期升序）；无数据返回 []。

        month 形如 "2026-11"。零编造：上游没给的日期不补、不插值。
        """
        if offline_mode() or not self.has_token:
            return []
        key = f"calendar_{origin}_{destination}_{month}.json"
        cached = self._cache_read(key)
        if cached and time.time() < float(cached.get("expire_at", 0)):
            return self._in_month(cached.get("days") or [], month)

        params = urllib.parse.urlencode({
            "origin": origin, "destination": destination,
            "depart_date": month, "currency": "cny",
            "token": self._config["token"]})
        request = urllib.request.Request(
            f"{BASE}?{params}",
            headers={"Accept": "application/json",
                     "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) FareRadar/1.12"})
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as resp:
                payload = json.loads(resp.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"Travelpayouts 接口返回 HTTP {exc.code}") from None
        except Exception as exc:
            raise RuntimeError(f"Travelpayouts 请求失败：{type(exc).__name__}") from None

        if not payload.get("success", False):
            raise RuntimeError(f"Travelpayouts 返回失败：{payload.get('error') or '未说明'}")

        days = []
        for day, item in (payload.get("data") or {}).items():
            if not isinstance(item, dict):
                continue
            price = item.get("price")
            if not isinstance(price, (int, float)) or price <= 0:
                continue                # 无价/脏价丢弃
            try:
                dt.date.fromisoformat(day)
            except ValueError:
                continue
            days.append({"date": day,
                         "price": round(price),
                         "airline": str(item.get("airline") or ""),
                         "flight_no": (str(item.get("airline") or "")
                                       + str(item.get("flight_number") or "")).strip()})
        days = self._in_month(days, month)
        days.sort(key=lambda x: x["date"])
        self._cache_write(key, {"expire_at": time.time() + CALENDAR_CACHE_TTL, "days": days})
        return days

    @staticmethod
    def _in_month(days, month):
        """上游 calendar 返回约半年窗口，必须按请求月份裁剪，否则"整月"名不副实。"""
        return [d for d in days if str(d.get("date", "")).startswith(month)]
