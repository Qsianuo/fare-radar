"""春秋航空特价票数据库（v1.13，ISSUE-022）：截图结构化入库 + 券匹配折算定价。

为什么单独一层（与 douyin_miniapp / douyin_groupbuy 的分工）：
- douyin_miniapp 卖的是"21 条固定日期特价票"，是比价行；
- douyin_groupbuy 卖的是"卡面商品"，只出 card_hints；
- 本模块解决第三种需求：**价格不由机票接口给，而由优惠券规则算**。
  春秋抖音小程序的"209 元秋季 2 次卡"与"购省钱卡专享价"两张截图给的是
  「券规则 + 适用航线/价目」，不给航班号也不给时刻。因此这里提供两级接口：
    1. available_flights()：只回答"这条航线这一天有没有券可用"，
       **按设计不返回任何价格字段**（调用方可断言 price_* 不存在）；
    2. coupon_quote()：把券匹配上来，按券规则折算单价（209÷2=104.5 元/次）。

数据铁律（沿用 snapshot_pack 的口径，零编造）：
- 航线名、票价、省额、日期、税费口径全部逐字来自截图（src/data/spring_seed.json），
  不做任何推算补全；截图未给的字段一律 NULL，由上层显示"未披露"；
- 截图不含航班号、起降时刻、舱位，因此本层**不产生** flight_no / depart_time；
  一条记录的含义是"一条可售商品"，不是"一班航班"，输出里显式说明；
- 城市 IATA 码是查询辅助索引（截图只给中文名），未核实的城市留空，只支持中文名查。

数据库：src/data/spring_aviation.db（SQLite，标准库 sqlite3，无第三方依赖）。
首次访问自动按 seed 建库；`python src/spring_ingest.py --rebuild` 可重建。
"""

import datetime as dt
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from . import cities

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
DEFAULT_DB = DATA_DIR / "spring_aviation.db"
SEED_FILE = DATA_DIR / "spring_seed.json"

CARRIER_CODE = "9C"
CARRIER_NAME = "春秋航空"

# 税费口径 → 展示文案（比价必须同口径，见 _pick_best 的说明）
BASIS_TAX_INCLUDED = "含税费等"
BASIS_TAX_EXCLUDED = "票面价（不含机建燃油）"
BASIS_UNDISCLOSED = "税费口径未披露"

# 城市名 → IATA（辅助索引；仅收录可确认的，未确认留空只支持中文名）
_NAME_TO_CODE = {
    "上海": "SHA", "北京": "PEK", "广州": "CAN", "深圳": "SZX", "成都": "CTU",
    "重庆": "CKG", "杭州": "HGH", "武汉": "WUH", "西安": "XIY", "南京": "NKG",
    "厦门": "XMN", "青岛": "TAO", "昆明": "KMG", "三亚": "SYX", "哈尔滨": "HRB",
    "沈阳": "SHE", "郑州": "CGO", "天津": "TSN", "贵阳": "KWE", "长沙": "CSX",
    "泉州(晋江)": "JJN", "张家口": "ZQZ", "遵义": "ZYI", "常德": "CGD",
    "黔江(重庆)": "JIQ", "西双版纳": "JHG", "丽江": "LJG", "绵阳": "MIG",
    "大理": "DLU", "乌鲁木齐": "URC", "阿勒泰": "AAT", "漠河": "OHE",
    "和田": "HTN", "石家庄": "SJW", "太原": "TYN", "呼和浩特": "HET",
    "长春": "CGQ", "福州": "FOC", "南昌": "KHN", "兰州": "LHW", "银川": "INC",
    # 以下为春秋航线常用、但不在 50 城注册表内的城市
    "大连": "DLC", "常州": "CZX", "盐城": "YNZ", "东营": "DOY", "恩施": "ENH",
    "扬州(泰州)": "YTY", "海拉尔": "HLD", "菏泽": "HZA", "衡阳": "HNY",
    "霍林郭勒": "HUO", "通辽": "TGO", "十堰(武当山)": "WDS", "嘉峪关": "JGN",
    "揭阳(潮汕)": "SWA", "井冈山": "JGS", "克拉玛依": "KRY", "南阳": "NNY",
    "连云港": "LYG", "乌海": "WUA", "于田": "YTW", "威海": "WEH", "宁波": "NGB",
    # 境外/港澳台（台北、高雄按官方口径书写）
    "台北(中国台湾)": "TPE", "高雄(中国台湾)": "KHH", "澳门": "MFM", "香港": "HKG",
    "首尔": "ICN", "釜山": "PUS", "济州岛": "CJU", "胡志明": "SGN", "清州": "CJJ",
    "新加坡": "SIN", "吉隆坡": "KUL", "清迈": "CNX", "曼谷": "BKK", "普吉": "HKT",
    "乌兰巴托": "UBN",
}
_CODE_TO_NAME = {code: name for name, code in _NAME_TO_CODE.items()}

# 截图口径的城市别名（乘客常用写法 → 截图写法）
_CITY_ALIAS = {
    "揭阳": "揭阳(潮汕)", "潮汕": "揭阳(潮汕)", "汕头": "揭阳(潮汕)",
    "扬州": "扬州(泰州)", "泰州": "扬州(泰州)",
    "十堰": "十堰(武当山)", "武当山": "十堰(武当山)",
    "泉州": "泉州(晋江)", "晋江": "泉州(晋江)",
    "黔江": "黔江(重庆)", "台北": "台北(中国台湾)", "高雄": "高雄(中国台湾)",
    "呼伦贝尔": "海拉尔", "济州": "济州岛", "胡志明市": "胡志明",
}

SCHEMA = """
CREATE TABLE ingest_batch (
    batch_id      TEXT PRIMARY KEY,
    source_kind   TEXT NOT NULL,
    source_note   TEXT NOT NULL,
    captured_at   TEXT NOT NULL,
    image_count   INTEGER NOT NULL,
    fare_year     INTEGER NOT NULL,
    ingested_at   TEXT NOT NULL,
    coupon_count  INTEGER NOT NULL,
    route_count   INTEGER NOT NULL,
    offer_count   INTEGER NOT NULL
);
CREATE TABLE coupon (
    coupon_id     TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    face_name     TEXT,
    issuer        TEXT,
    channel       TEXT,
    price_cny     REAL,            -- 卡价；NULL = 截图未披露（省钱卡卡费）
    trips         INTEGER,         -- 可兑次数；NULL = 非次卡
    per_trip_cny  REAL,            -- 折算单价 = price_cny / trips（入库时算好并落库）
    tax_included  INTEGER,         -- 1 含税费 / 0 不含 / NULL 未披露
    valid_from    TEXT,            -- NULL = 截图未披露
    valid_until   TEXT,
    match_rule    TEXT NOT NULL,   -- route_list（按适用航线）| listed_fare（按价目表）
    note          TEXT,
    batch_id      TEXT NOT NULL
);
CREATE TABLE coupon_route (
    coupon_id     TEXT NOT NULL,
    dep_city      TEXT NOT NULL,
    arr_city      TEXT NOT NULL,
    dep_code      TEXT,
    arr_code      TEXT,
    PRIMARY KEY (coupon_id, dep_city, arr_city)
);
CREATE TABLE fare_offer (
    offer_id      TEXT PRIMARY KEY,
    coupon_id     TEXT NOT NULL,
    dep_city      TEXT NOT NULL,
    arr_city      TEXT NOT NULL,
    dep_code      TEXT,
    arr_code      TEXT,
    travel_date   TEXT NOT NULL,   -- YYYY-MM-DD（截图 MM-DD + 批次年份）
    price_cny     REAL NOT NULL,   -- 卡面"¥xxx 起"
    saved_cny     REAL,            -- 卡面"省¥xxx"
    tax_included  INTEGER,         -- "含税费等"=1 / "不含机建燃油费"=0
    batch_id      TEXT NOT NULL,
    note          TEXT
);
CREATE INDEX idx_offer_route ON fare_offer(dep_city, arr_city, travel_date);
"""


# ---------- 城市解析 ----------

def resolve_city(raw):
    """把 from/to 输入解析成截图口径的城市名；无法识别返回 None。

    支持三种写法：三字码（SHA）、城市名（上海）、常用别名（揭阳/潮汕）。
    """
    text = str(raw or "").strip()
    if not text:
        return None
    if text in _CITY_ALIAS:
        return _CITY_ALIAS[text]
    if text in _NAME_TO_CODE:
        return text
    code = text.upper()
    if len(code) == 3 and code.isalpha():
        if code in _CODE_TO_NAME:
            return _CODE_TO_NAME[code]
        return cities.city_name(code)          # 50 城注册表兜底；未收录返回 None
    return text                                # 中文城市名原样保留，是否收录由查库结果决定


def city_code(name):
    """城市名 → 三字码（未收录返回 None，不猜）。"""
    return _NAME_TO_CODE.get(name)


def coverage(db_path=None):
    """当前库的收录范围（未命中时的提示文案用）。"""
    with _db(db_path) as conn:
        coupons = conn.execute("SELECT COUNT(*) FROM coupon").fetchone()[0]
        routes = conn.execute("SELECT COUNT(*) FROM coupon_route").fetchone()[0]
        offers = conn.execute("SELECT COUNT(*), MIN(travel_date), MAX(travel_date) "
                              "FROM fare_offer").fetchone()
        batch = conn.execute("SELECT batch_id, source_kind, captured_at "
                             "FROM ingest_batch ORDER BY ingested_at DESC LIMIT 1").fetchone()
    return {
        "batch_id": batch[0] if batch else None,
        "source_kind": batch[1] if batch else None,
        "captured_at": batch[2] if batch else None,
        "coupon_count": coupons,
        "route_count": routes,
        "offer_count": offers[0],
        "offer_date_min": offers[1],
        "offer_date_max": offers[2],
    }


# ---------- 建库 / 入库 ----------

def _connect(db_path=None):
    path = Path(db_path) if db_path else DEFAULT_DB
    if not path.exists():
        ingest(db_path=path)      # 首次访问自动按 seed 建库
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def _db(db_path=None):
    """只读查询用的连接上下文：查询结束即关闭，避免长驻 Web 进程句柄泄漏。"""
    conn = _connect(db_path)
    try:
        yield conn
    finally:
        conn.close()


def ingest(seed_path=None, db_path=None, rebuild=True):
    """把 seed JSON 灌进 SQLite；rebuild=True 时整表重建（幂等，可反复执行）。

    返回入库摘要 dict（供 CLI/文档引用）；任何一条 seed 缺字段都直接抛错，
    不允许"缺字段就跳过"式的静默丢数据。
    """
    seed_path = Path(seed_path) if seed_path else SEED_FILE
    db_path = Path(db_path) if db_path else DEFAULT_DB
    seed = json.loads(seed_path.read_text(encoding="utf-8"))
    batch = seed["batch"]
    year = int(batch["fare_year"])

    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    try:
        if rebuild:
            for table in ("fare_offer", "coupon_route", "coupon", "ingest_batch"):
                conn.execute(f"DROP TABLE IF EXISTS {table}")
        conn.executescript(SCHEMA)

        coupon_rows, route_rows, offer_rows = [], [], []
        for coupon in seed["coupons"]:
            price = coupon.get("price_cny")
            trips = coupon.get("trips")
            per_trip = round(price / trips, 2) if price is not None and trips else None
            coupon_rows.append((
                coupon["coupon_id"], coupon["name"], coupon.get("face_name"),
                coupon.get("issuer"), coupon.get("channel"), price, trips, per_trip,
                _tax_flag(coupon.get("tax_included")), coupon.get("valid_from"),
                coupon.get("valid_until"), coupon["match_rule"], coupon.get("note"),
                batch["batch_id"],
            ))
            for pair in coupon.get("routes", []):
                dep, arr = pair.split("|")
                route_rows.append((coupon["coupon_id"], dep.strip(), arr.strip(),
                                   city_code(dep.strip()), city_code(arr.strip())))

        for group in seed.get("offer_groups", []):
            dep = group["dep"].strip()
            for index, offer in enumerate(group["offers"]):
                month, day = offer["date"].split("-")
                travel_date = dt.date(year, int(month), int(day)).isoformat()
                offer_rows.append((
                    f"{group['coupon_id']}-{index + 1:03d}", group["coupon_id"],
                    dep, offer["dest"].strip(), city_code(dep), city_code(offer["dest"].strip()),
                    travel_date, float(offer["price_cny"]),
                    None if offer.get("saved_cny") is None else float(offer["saved_cny"]),
                    _tax_flag(offer.get("tax_included")), batch["batch_id"], group.get("note"),
                ))

        conn.executemany(_insert_sql("coupon", coupon_rows), coupon_rows)
        conn.executemany(_insert_sql("coupon_route", route_rows), route_rows)
        conn.executemany(_insert_sql("fare_offer", offer_rows), offer_rows)
        conn.execute(
            "INSERT INTO ingest_batch VALUES (?,?,?,?,?,?,?,?,?,?)",
            (batch["batch_id"], batch["source_kind"], batch["source_note"],
             batch["captured_at"], int(batch["image_count"]), year,
             dt.datetime.now().isoformat(timespec="seconds"),
             len(coupon_rows), len(route_rows), len(offer_rows)))
        conn.commit()
    finally:
        conn.close()
    return {"db": str(db_path), "batch_id": batch["batch_id"],
            "coupons": len(coupon_rows), "routes": len(route_rows),
            "offers": len(offer_rows)}


def _tax_flag(value):
    return None if value is None else (1 if value else 0)


def _insert_sql(table, rows):
    """按行宽生成占位符（列数与占位符数不一致是本模块唯一容易写错的地方）。"""
    width = len(rows[0]) if rows else 0
    return f"INSERT INTO {table} VALUES ({','.join('?' * width)})"


def _basis(tax_included):
    if tax_included is None:
        return BASIS_UNDISCLOSED
    return BASIS_TAX_INCLUDED if tax_included else BASIS_TAX_EXCLUDED


# ---------- 查询：可用商品（不含价格） ----------

def available_flights(dep_raw, arr_raw, date_raw=None, db_path=None):
    """查春秋可用航班/商品——**按设计不返回任何价格字段**。

    返回的每条记录含义是"一条可售商品"（航线 + 可选日期 + 适用券），
    不是"一班航班"：截图不含航班号与起降时刻，flight_no/depart_time/
    arrive_time 恒为 None，且用 flight_info_disclosed=False 显式声明，
    禁止上层用生成值填充（零编造红线）。

    报价请交给 coupon_quote()——两级接口分离是本次需求的核心约束。
    """
    dep = resolve_city(dep_raw)
    arr = resolve_city(arr_raw)
    date = _parse_date(date_raw)
    if dep is None or arr is None:
        return _fail(f"城市无法识别：{dep_raw} → {arr_raw}（支持三字码或中文城市名）")
    if dep == arr:
        return _fail("出发城市与到达城市不能相同")

    with _db(db_path) as conn:
        coupons = {row["coupon_id"]: dict(row) for row in conn.execute("SELECT * FROM coupon")}
        flights = []

        # ① route_list 券（次卡）：航线在适用清单内即"可兑"，卡面未给有效期 → 不按日期过滤
        for row in conn.execute(
                "SELECT * FROM coupon_route WHERE dep_city=? AND arr_city=?", (dep, arr)):
            coupon = coupons[row["coupon_id"]]
            flights.append(_flight_item(dep, arr, None, coupon, "onsale_route_only",
                                        matched_by="券适用航线"))

        # ② listed_fare 券（省钱卡专享价）：价目表逐条带日期，有日期则精确匹配
        sql = "SELECT * FROM fare_offer WHERE dep_city=? AND arr_city=?"
        args = [dep, arr]
        if date is not None:
            sql += " AND travel_date=?"
            args.append(date.isoformat())
        for row in conn.execute(sql + " ORDER BY travel_date", args):
            coupon = coupons[row["coupon_id"]]
            flights.append(_flight_item(dep, arr, row["travel_date"], coupon,
                                        "onsale_date_exact", matched_by="价目表同日专享价",
                                        tax_included=row["tax_included"], offer_id=row["offer_id"]))

        flights.sort(key=lambda f: (f["date"] or "9999-12-31", f["coupon_id"]))

    cover = coverage(db_path)
    return {
        "ok": True,
        "query": {"dep_input": str(dep_raw), "arr_input": str(arr_raw),
                  "dep_city": dep, "arr_city": arr,
                  "dep_code": city_code(dep), "arr_code": city_code(arr),
                  "date": date.isoformat() if date else None},
        "carrier": {"code": CARRIER_CODE, "name": CARRIER_NAME},
        "price_returned": False,
        "price_note": "本接口按设计不返回价格：价格由优惠券规则折算，请调用 coupon_quote()。",
        "flights": flights,
        "matched_coupon_ids": sorted({f["coupon_id"] for f in flights}),
        "miss_hint": None if flights else (
            f"该航线/日期在春秋券库中无可用商品：券适用航线共 {cover['route_count']} 条、"
            f"价目表 {cover['offer_count']} 条（{cover['offer_date_min']} ~ "
            f"{cover['offer_date_max']}）。航线是否收录与日期是否在价目表内需分别核对。"),
        "hint": ("未命中时：该航线可能不在券适用清单内，或该日期价目表未收录；"
                 "券有效期/税费口径未披露的一律按「未披露」展示，不推算。"),
        "coverage": cover,
    }


def _flight_item(dep, arr, date, coupon, sale_status, matched_by,
                 tax_included=None, offer_id=None):
    """一条可售商品记录：绝不含价格字段（price/saved/total 一律不出现）。"""
    return {
        "item_id": offer_id or f"{coupon['coupon_id']}::{dep}::{arr}",
        "coupon_id": coupon["coupon_id"],
        "coupon_name": coupon["name"],
        "product": f"春秋航空·{coupon['name']}",
        "route_text": f"{dep} → {arr}",
        "dep_city": dep, "arr_city": arr,
        "dep_code": city_code(dep), "arr_code": city_code(arr),
        "date": date,
        "sale_status": sale_status,
        "sale_status_text": ("同日专享价在售" if sale_status == "onsale_date_exact"
                             else "券适用航线可兑"),
        "matched_by": matched_by,
        # 截图未披露 → None + 显式声明，禁止任何生成值
        "flight_no": None, "depart_time": None, "arrive_time": None,
        "flight_info_disclosed": False,
        "flight_info_note": "截图未披露航班号与起降时刻，以下单页为准",
        "fare_disclosed": False,
        "tax_included": tax_included,
        "source": f"{coupon['channel'] or '抖音小程序'}截图｜{coupon['batch_id']}",
    }


# ---------- 查询：券匹配 + 折算 ----------

def coupon_quote(dep_raw, arr_raw, date_raw=None, db_path=None):
    """把券匹配到航线上并折算单价（这是本需求里唯一的定价入口）。

    折算规则（与券的 match_rule 一一对应，不做任何跨券叠加）：
    - route_list 券（209 元秋季 2 次卡）：单价 = 卡价 ÷ 次数 = 209 ÷ 2 = 104.5 元/次；
      券不绑日期，因此任何日期都返回同一单价，同时标注"有效期未披露"。
    - listed_fare 券（省钱卡）：单价 = 价目表当日专享价；无日期则不给单价，
      改列候选日期与各自价格（避免把不同日期的价混成一个数）。
    """
    dep = resolve_city(dep_raw)
    arr = resolve_city(arr_raw)
    date = _parse_date(date_raw)
    if dep is None or arr is None:
        return _fail(f"城市无法识别：{dep_raw} → {arr_raw}（支持三字码或中文城市名）")
    if dep == arr:
        return _fail("出发城市与到达城市不能相同")

    with _db(db_path) as conn:
        coupons = {row["coupon_id"]: dict(row) for row in conn.execute("SELECT * FROM coupon")}
        quotes = []

        # ① 次卡：航线命中适用清单 → 卡价 ÷ 次数
        for row in conn.execute(
                "SELECT coupon_id FROM coupon_route WHERE dep_city=? AND arr_city=?", (dep, arr)):
            coupon = coupons[row["coupon_id"]]
            quotes.append(_route_list_quote(conn, coupon, dep, arr, date))

        # ② 省钱卡：按价目表当日专享价成交
        rows = conn.execute("SELECT * FROM fare_offer WHERE dep_city=? AND arr_city=? "
                            "ORDER BY travel_date", (dep, arr)).fetchall()
        if rows:
            if date is None:
                quotes.append(_listed_fare_need_date(coupons[rows[0]["coupon_id"]], dep, arr, rows))
            else:
                exact = [r for r in rows if r["travel_date"] == date.isoformat()]
                if exact:
                    quotes.append(_listed_fare_quote(coupons[exact[0]["coupon_id"]],
                                                     dep, arr, exact[0]))
                else:
                    quotes.append(_listed_fare_no_date(coupons[rows[0]["coupon_id"]],
                                                       dep, arr, date, rows))
        known = [r for r in rows if date is not None and r["travel_date"] == date.isoformat()]

    quotes.sort(key=lambda q: (q["unit_price_cny"] is None, q["unit_price_cny"] or 0))
    best, best_reason = _pick_best(quotes)
    groups = _basis_groups(quotes)
    cover = coverage(db_path)
    return {
        "ok": True,
        "query": {"dep_input": str(dep_raw), "arr_input": str(arr_raw),
                  "dep_city": dep, "arr_city": arr,
                  "dep_code": city_code(dep), "arr_code": city_code(arr),
                  "date": date.isoformat() if date else None},
        "carrier": {"code": CARRIER_CODE, "name": CARRIER_NAME},
        "quotes": quotes,
        "best_unit_price_cny": best.get("unit_price_cny") if best else None,
        "best_coupon_id": best.get("coupon_id") if best else None,
        "best_reason": best_reason,
        "basis_groups": groups,
        "cross_basis_note": _cross_basis_note(groups),
        "same_day_reference": ([{"offer_id": r["offer_id"], "price_cny": r["price_cny"],
                                 "saved_cny": r["saved_cny"],
                                 "tax_included": r["tax_included"]} for r in known]
                               if quotes and known else []),
        "notice": ("价格口径：单价为「元/人次/单程」票面折算值；一名旅客一次单程只能用一张券，"
                   "本工具不做券叠加。税费口径未披露时，单价不含机建燃油，到手价需另加税费。"),
        "miss_hint": None if quotes else (
            f"该航线在春秋券库中无记录：券适用航线 {cover['route_count']} 条、"
            f"价目表 {cover['offer_count']} 条（{cover['offer_date_min']} ~ "
            f"{cover['offer_date_max']}）。可先用 spring_flights 核实航线是否收录。"),
        "coverage": cover,
    }


def _route_list_quote(conn, coupon, dep, arr, date):
    """次卡折算：单价 = 卡价 ÷ 次数（与航线原价无关，这正是次卡的定价规则）。"""
    unit = coupon["per_trip_cny"]
    trips = coupon["trips"] or 1
    warnings = ["有效期未披露（卡面未标注），可兑日期以下单页为准"]
    if coupon["tax_included"] is None:
        warnings.append("税费口径未披露：折算单价仅代表票面，机建/燃油需另计")
    if trips > 1:
        warnings.append(f"折算单价 = 卡价 ÷ {trips} 次；需一次买整张卡，不可拆成散票购买")
    return {
        "coupon_id": coupon["coupon_id"],
        "coupon_name": coupon["name"],
        "match_rule": coupon["match_rule"],
        "matched_by": "券适用航线",
        "unit_price_cny": unit,
        "unit": "元/人次/单程",
        "price_basis": _basis(coupon["tax_included"]),
        "tax_included": coupon["tax_included"],
        "formula": (f"卡价 ¥{_num(coupon['price_cny'])} ÷ {trips} 次 = "
                    f"¥{_num(unit)}/次"),
        "coupon_price_cny": coupon["price_cny"],
        "trips": trips,
        "validity": _validity_text(coupon),
        "warnings": warnings,
        "note": coupon.get("note") or "",
    }


def _listed_fare_quote(coupon, dep, arr, row):
    price = float(row["price_cny"])
    saved = row["saved_cny"]
    warnings = []
    if coupon["price_cny"] is None:
        warnings.append("卡费未披露（截图未展示省钱卡售价），本单价为持卡成交价，不含卡费摊销")
    if saved:
        warnings.append(f"卡面标「省 ¥{_num(saved)}」为相对原价的立减额，非本单价的一部分")
    return {
        "coupon_id": coupon["coupon_id"],
        "coupon_name": coupon["name"],
        "match_rule": coupon["match_rule"],
        "matched_by": f"价目表同日专享价（{row['travel_date']}）",
        "unit_price_cny": price,
        "unit": "元/人次/单程",
        "price_basis": _basis(row["tax_included"]),
        "tax_included": row["tax_included"],
        "formula": (f"持卡按专享价 ¥{_num(price)} 成交"
                    + (f"（卡面标省 ¥{_num(saved)}）" if saved else "")),
        "coupon_price_cny": coupon["price_cny"],
        "trips": None,
        "validity": f"仅 {row['travel_date']} 出发（价目表收录日期）",
        "saved_cny": saved,
        "warnings": warnings,
        "note": coupon.get("note") or "",
    }


def _listed_fare_need_date(coupon, dep, arr, rows):
    """未给日期：不给单一单价，改列候选日期（避免把不同日期的价格压成一个数）。"""
    return {
        "coupon_id": coupon["coupon_id"],
        "coupon_name": coupon["name"],
        "match_rule": coupon["match_rule"],
        "matched_by": "价目表航线命中（未指定日期）",
        "unit_price_cny": None,
        "unit": "元/人次/单程",
        "price_basis": BASIS_UNDISCLOSED,
        "tax_included": None,
        "formula": "需指定出发日期后才能给出专享价（不同日期价格不同）",
        "coupon_price_cny": coupon["price_cny"],
        "trips": None,
        "validity": f"价目表收录 {len(rows)} 个出发日期",
        "candidate_dates": [{"date": r["travel_date"], "price_cny": r["price_cny"],
                             "saved_cny": r["saved_cny"],
                             "price_basis": _basis(r["tax_included"])} for r in rows],
        "warnings": ["缺少出发日期：专享价按日期逐条定价，不做跨日期平均"],
        "note": coupon.get("note") or "",
    }


def _listed_fare_no_date(coupon, dep, arr, date, rows):
    """给了日期但价目表该日无专享价：显式说明，绝不外推到邻近日期。"""
    return {
        "coupon_id": coupon["coupon_id"],
        "coupon_name": coupon["name"],
        "match_rule": coupon["match_rule"],
        "matched_by": "价目表同日专享价（未命中）",
        "unit_price_cny": None,
        "unit": "元/人次/单程",
        "price_basis": BASIS_UNDISCLOSED,
        "tax_included": None,
        "formula": f"{date.isoformat()} 无专享价记录，不做邻近日期外推",
        "coupon_price_cny": coupon["price_cny"],
        "trips": None,
        "validity": f"价目表收录日期：{'、'.join(r['travel_date'] for r in rows)}",
        "candidate_dates": [{"date": r["travel_date"], "price_cny": r["price_cny"],
                             "saved_cny": r["saved_cny"],
                             "price_basis": _basis(r["tax_included"])} for r in rows],
        "warnings": [f"该券在 {date.isoformat()} 无专享价，价格未披露"],
        "note": coupon.get("note") or "",
    }


def _pick_best(quotes):
    """只在税费口径一致时才给"最低单价"，混口径一律不排名。

    理由：含税费的 ¥280 与不含机建燃油的 ¥130 直接比大小会误导用户，
    宁可回 None + 原因，也不给一个口径错位的"最低价"。
    """
    priced = [q for q in quotes if q.get("unit_price_cny") is not None]
    if not priced:
        return None, "无可用折算价（缺少日期，或该航线不在券适用范围内）"
    bases = {q["price_basis"] for q in priced}
    if len(bases) > 1:
        return None, ("命中券的税费口径不一致（" + "、".join(sorted(bases)) +
                      "），跨口径比价会误导，请按口径分别参考，不自动排名")
    if all(q["price_basis"] == BASIS_UNDISCLOSED for q in priced):
        return min(priced, key=lambda q: q["unit_price_cny"]), (
            "同为未披露口径，仅按票面折算值排序，实际到手价需另加税费")
    best = min(priced, key=lambda q: q["unit_price_cny"])
    return best, f"同口径（{best['price_basis']}）下最低"


def _basis_groups(quotes):
    """按税费口径分组，给每组最低单价——口径不同就不硬排名（展示逻辑核心）。

    含税费的 ¥280 与不含机建燃油的 ¥130 放一张榜上排名，用户会照着低价下单然后
    发现还要加税；卡面未标注口径的（次卡）更是连是否含税都不可知。因此把"排名"
    降级为"分口径各给最低"，把可比性判断交回给用户。
    """
    groups = {}
    for quote in quotes:
        if quote.get("unit_price_cny") is None:
            continue
        group = groups.setdefault(quote["price_basis"], {"basis": quote["price_basis"],
                                                         "items": []})
        group["items"].append({"coupon_id": quote["coupon_id"],
                               "coupon_name": quote["coupon_name"],
                               "unit_price_cny": quote["unit_price_cny"]})
    result = []
    for group in groups.values():
        lowest = min(group["items"], key=lambda item: item["unit_price_cny"])
        result.append({"basis": group["basis"], "count": len(group["items"]),
                       "lowest_unit_cny": lowest["unit_price_cny"],
                       "lowest_coupon_id": lowest["coupon_id"],
                       "lowest_coupon_name": lowest["coupon_name"],
                       "items": group["items"]})
    result.sort(key=lambda group: group["lowest_unit_cny"])
    return result


def _cross_basis_note(groups):
    """多口径时的显式提示：给出各组最低额，但不宣布"哪个更便宜"。"""
    if len(groups) < 2:
        return None
    listed = "；".join(f"{g['basis']} ¥{_num(g['lowest_unit_cny'])}"
                      f"（{g['lowest_coupon_name']}）" for g in groups)
    return (f"命中券分属 {len(groups)} 种税费口径：{listed}。口径不同不能直接比大小——"
            "其中「税费口径未披露」的券需先向下单页确认是否含机建燃油，再与含税/不含税价比。")


def _validity_text(coupon):
    if not coupon.get("valid_from") and not coupon.get("valid_until"):
        return "未披露（卡面未标注有效期）"
    return f"{coupon.get('valid_from') or '未披露'} ~ {coupon.get('valid_until') or '未披露'}"


def _num(value):
    """金额展示：整数不带小数尾巴，104.5 保留一位；None → 未披露。"""
    if value is None:
        return "未披露"
    number = float(value)
    return str(int(number)) if number == int(number) else f"{number:.2f}".rstrip("0").rstrip(".")


def _parse_date(raw):
    if raw in (None, ""):
        return None
    text = str(raw).strip()
    try:
        return dt.datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        raise ValueError(f"日期需为 YYYY-MM-DD 格式，收到：{raw}")


def _fail(message):
    return {"ok": False, "error": message}
