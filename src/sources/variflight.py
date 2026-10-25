"""飞常准 VariFlight Aviation MCP 适配器（v1.11.0 骨架，ISSUE-019）。

为什么选它：Google Flights（v1.9 起）对国内航线覆盖不足——实测 INC→CTU / INC→PEK /
INC→WUH 均无数据；飞常准官方开放平台覆盖国内 99.99% 航班，且**机票价格覆盖国内全部
航司与舱位**，是本项目唯一"官方 + 给票价 + 有免费额度"的国内数据源。

接入方式：MCP over HTTP（streamable HTTP，JSON-RPC 2.0），端点
`https://ai.variflight.com/servers/aviation/mcp`，鉴权用请求头 `X-API-Key`。

计费（官方口径）：1 积分 = ¥0.01，5–50 积分/次；`initialize`、`tools/list`、
`getTodayDate` **免费**，调用失败不扣费；新用户送 ¥50 体验额度。

本项目的调用纪律（比 RapidAPI 更保守——这是真扣钱的接口）：
- 按**最坏单价 50 积分/次**做预算，日上限 500 积分（¥5）、月上限 4500 积分（¥45），
  到线硬停、绝不自动充值（ISSUE-019 裁定）；
- 结果缓存 6 小时、空结果 30 分钟（docs/03 第 9 节通用约定）；
- 未配 Key 或 schema 未校准时 status=not_available，不出数据行、不编造。

⚠ 当前状态：**工具 schema 待校准**。飞常准未公开 tools 列表与参数名，需用 API Key 执行
一次 `tools/list`（免费）后，把结果填进 PRICING_TOOL_CALL 与 `_to_offers()`，再置
SCHEMA_CALIBRATED = True。在此之前 search() 不发起任何联网调用。
"""

import datetime as dt
import json
import time
import urllib.error
import urllib.request
from pathlib import Path

from .base import Source

CONFIG_NAME = "variflight_config.json"
DEFAULT_ENDPOINT = "https://ai.variflight.com/servers/aviation/mcp"

POINT_UNIT_CNY = 0.01          # 1 积分 = 0.01 元
POINT_PER_CALL_WORST = 50      # 官方区间 5–50，按最坏值做预算
DAILY_POINT_CAP = 500          # ¥5/日（约 10 次最坏单价调用）
MONTHLY_POINT_CAP = 4500       # ¥45/月（体验额度 ¥50 留 10% 余量）
OFFER_CACHE_TTL = 6 * 3600
EMPTY_CACHE_TTL = 30 * 60
TIMEOUT = 45

# ---- 待校准区（拿到 API Key 后执行 probe_tools() 补齐） ----
SCHEMA_CALIBRATED = False
PRICING_TOOL_NAME = None       # 例如 "search_flight_itinerary_and_pricing"
PRICING_TOOL_ARGS = None       # 参数名映射，例如 {"from": "departure", "to": "arrival", "date": "date"}

_NO_KEY_REASON = ("未配置 API Key——在 https://ai.variflight.com/keys 创建后填入 "
                  "src/sources/variflight_config.json（已 gitignore，不入库）")
_NO_SCHEMA_REASON = ("工具 schema 待校准：配好 Key 后执行一次 `python -m sources.variflight` "
                     "（tools/list 免费，不消耗积分）再补齐映射；此期间不发起任何查询")


def _rpc(method, params, api_key, endpoint=DEFAULT_ENDPOINT, req_id=1):
    """发一条 JSON-RPC 2.0 请求；兼容 application/json 与 text/event-stream 两种响应。"""
    body = json.dumps({"jsonrpc": "2.0", "id": req_id, "method": method,
                       "params": params or {}}).encode("utf-8")
    request = urllib.request.Request(
        endpoint, data=body, method="POST",
        headers={"Content-Type": "application/json",
                 "Accept": "application/json, text/event-stream",
                 "X-API-Key": api_key,
                 "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) FareRadar/1.11"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as resp:
            raw = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace")[:200]
        except Exception:
            pass
        if exc.code == 401:
            raise RuntimeError("飞常准接口 401：API Key 无效或未携带（不重试）") from None
        if exc.code == 402:
            raise RuntimeError("飞常准接口 402：额度不足，渠道已暂停（不自动充值）") from None
        if exc.code == 429:
            raise RuntimeError("飞常准接口 429：超出限速/配额，渠道已暂停（不重试）") from None
        raise RuntimeError(f"飞常准接口 HTTP {exc.code}" + (f"：{detail}" if detail else "")) from None
    # streamable HTTP 可能返回 SSE：取最后一条 data: 负载
    if raw.lstrip().startswith("event:") or "\ndata:" in raw or raw.startswith("data:"):
        payload = None
        for line in raw.splitlines():
            if line.startswith("data:"):
                payload = line[5:].strip()
        if payload is None:
            raise RuntimeError("飞常准接口返回空 SSE 流")
        raw = payload
    parsed = json.loads(raw)
    if parsed.get("error"):
        message = str((parsed.get("error") or {}).get("message") or parsed["error"])
        raise RuntimeError(f"飞常准接口返回错误：{message}")
    return parsed


class VariflightSource(Source):
    """真实接口票源：飞常准 Aviation MCP（国内航班与票价）。"""

    source_id = "variflight"
    display_name = "飞常准·Aviation MCP"
    tagline = ("国内航班与机票价格（飞常准官方开放平台，覆盖国内全部航司与舱位），"
               "按积分计费、内置日/月积分硬停上限")
    entry_url = "https://ai.variflight.com/keys"
    entry_note = "飞常准 AI 开放平台（注册送 ¥50 体验额度，Key 自配）"

    def __init__(self, config_path=None):
        base = Path(__file__).resolve().parent
        self._config_path = Path(config_path) if config_path else base / CONFIG_NAME
        # 缓存目录跟随配置文件所在目录：默认落在 src/sources/，测试注入临时配置时自动隔离，
        # 避免用例之间互相污染积分计数（积分是真金白银，必须严格隔离）。
        self._cache_dir = self._config_path.parent / ".variflight_cache"
        self._config = self._read_config()

    # ---------- 配置与状态 ----------

    def _read_config(self):
        try:
            data = json.loads(self._config_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    @property
    def has_key(self):
        return bool(str(self._config.get("api_key") or "").strip())

    @property
    def status(self):
        if not self.has_key:
            return "not_available"
        if not SCHEMA_CALIBRATED:
            return "not_available"
        return "active"

    @property
    def status_reason(self):
        if not self.has_key:
            return _NO_KEY_REASON
        if not SCHEMA_CALIBRATED:
            return _NO_SCHEMA_REASON
        return ""

    def availability(self, origin, destination, date):
        if self.status != "active":
            return False, self.status_reason
        return True, ""

    # ---------- 缓存与积分护栏 ----------

    def _cache_read(self, name):
        try:
            return json.loads((self._cache_dir / name).read_text(encoding="utf-8"))
        except Exception:
            return None

    def _cache_write(self, name, payload):
        self._cache_dir.mkdir(exist_ok=True)
        (self._cache_dir / name).write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    def _points_guard(self):
        """调用前检查日/月积分预算；按最坏单价预扣，到线硬停（绝不自动充值）。"""
        today = dt.date.today().isoformat()
        month = today[:7]
        day = self._cache_read("day_points.json") or {"date": today, "points": 0}
        if day.get("date") != today:
            day = {"date": today, "points": 0}
        mon = self._cache_read("month_points.json") or {"month": month, "points": 0}
        if mon.get("month") != month:
            mon = {"month": month, "points": 0}
        if mon["points"] + POINT_PER_CALL_WORST > MONTHLY_POINT_CAP:
            raise RuntimeError(
                f"飞常准当月积分预算已用尽（上限 {MONTHLY_POINT_CAP} 积分 ≈ "
                f"¥{MONTHLY_POINT_CAP * POINT_UNIT_CNY:.0f}），本月不再请求；缓存仍可用。"
                "按纪律硬停，不自动充值")
        if day["points"] + POINT_PER_CALL_WORST > DAILY_POINT_CAP:
            raise RuntimeError(
                f"飞常准当日积分预算已用尽（上限 {DAILY_POINT_CAP} 积分 ≈ "
                f"¥{DAILY_POINT_CAP * POINT_UNIT_CNY:.0f}），今日不再请求；明日恢复")
        day["points"] += POINT_PER_CALL_WORST
        mon["points"] += POINT_PER_CALL_WORST
        self._cache_write("day_points.json", day)
        self._cache_write("month_points.json", mon)

    def points_used(self):
        today = dt.date.today().isoformat()
        day = self._cache_read("day_points.json") or {}
        mon = self._cache_read("month_points.json") or {}
        return {"day": day.get("points", 0) if day.get("date") == today else 0,
                "month": mon.get("points", 0) if mon.get("month") == today[:7] else 0,
                "day_cap": DAILY_POINT_CAP, "month_cap": MONTHLY_POINT_CAP}

    # ---------- 查询 ----------

    def search(self, origin, destination, date):
        available, _ = self.availability(origin, destination, date)
        if not available:
            return []
        key = f"offers_{origin}_{destination}_{date.isoformat()}.json"
        cached = self._cache_read(key)
        if cached and time.time() < float(cached.get("expire_at", 0)):
            return cached["rows"]

        self._points_guard()
        parsed = _rpc("tools/call",
                      {"name": PRICING_TOOL_NAME,
                       "arguments": self._build_args(origin, destination, date)},
                      self._config["api_key"])
        rows, empty_note = self._to_offers(parsed)
        ttl = OFFER_CACHE_TTL if rows else EMPTY_CACHE_TTL
        self._cache_write(key, {"expire_at": time.time() + ttl, "rows": rows,
                                "empty_note": empty_note})
        return rows

    def _build_args(self, origin, destination, date):
        if not SCHEMA_CALIBRATED or not PRICING_TOOL_ARGS:
            raise RuntimeError("飞常准工具参数未校准：无法构造请求（不猜参数名）")
        mapping = PRICING_TOOL_ARGS
        return {mapping["from"]: origin,
                mapping["to"]: destination,
                mapping["date"]: date.isoformat()}

    def _to_offers(self, parsed):
        """把上游返回映射为比价行。

        ⚠ schema 未校准前不猜测字段名：返回空结果并给出明确说明，绝不编造航班。
        """
        if not SCHEMA_CALIBRATED:
            return [], "飞常准工具返回结构未校准（未执行 tools/list），本次不出数据"
        return [], "飞常准返回结构中未找到可映射的行程（待校准映射表）"

    def empty_note(self, origin, destination, date):
        cached = self._cache_read(f"offers_{origin}_{destination}_{date.isoformat()}.json")
        if not cached or cached.get("rows"):
            return None
        return cached.get("empty_note")


def probe_tools(config_path=None):
    """配好 Key 后跑一次：initialize + tools/list（均免费，不消耗积分）。

    用法：python -m sources.variflight     （在 src 目录下）
    输出用于补齐 PRICING_TOOL_NAME / PRICING_TOOL_ARGS / _to_offers。
    """
    source = VariflightSource(config_path)
    if not source.has_key:
        print(_NO_KEY_REASON)
        return 1
    key = source._config["api_key"]
    endpoint = str(source._config.get("endpoint") or DEFAULT_ENDPOINT)
    init = _rpc("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                               "clientInfo": {"name": "FareRadar", "version": "1.11"}},
                key, endpoint, req_id=1)
    print("=== initialize ===")
    print(json.dumps(init.get("result", init), ensure_ascii=False, indent=1)[:1200])
    tools = _rpc("tools/list", {}, key, endpoint, req_id=2)
    print("=== tools/list ===")
    print(json.dumps(tools.get("result", tools), ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(probe_tools())
