"""城市注册表——25 城唯一事实来源（PRD v1.3 第 4 节）。

网页下拉（/api/cities）、CLI 校验与提示、定价引擎统一读取本模块；
新增城市只改 _CITIES 一处，顺序即展示顺序。
"""

import math

# (三字码, 中文名, 纬度, 经度)
_CITIES = (
    ("PEK", "北京", 39.9, 116.4),
    ("SHA", "上海", 31.2, 121.4),
    ("CAN", "广州", 23.4, 113.3),
    ("SZX", "深圳", 22.6, 114.0),
    ("CTU", "成都", 30.6, 104.1),
    ("CKG", "重庆", 29.6, 106.5),
    ("HGH", "杭州", 30.2, 120.2),
    ("WUH", "武汉", 30.8, 114.2),
    ("XIY", "西安", 34.4, 108.9),
    ("NKG", "南京", 32.1, 118.8),
    ("HKG", "香港", 22.3, 114.2),
    ("KIX", "大阪关西", 34.4, 135.2),
    ("BKK", "曼谷", 13.7, 100.5),
    ("XMN", "厦门", 24.5, 118.1),
    ("TAO", "青岛", 36.1, 120.4),
    ("KMG", "昆明", 25.0, 102.7),
    ("SYX", "三亚", 18.3, 109.5),
    ("HRB", "哈尔滨", 45.6, 126.6),
    ("SHE", "沈阳", 41.8, 123.4),
    ("CGO", "郑州", 34.7, 113.7),
    ("TSN", "天津", 39.1, 117.2),
    ("KWE", "贵阳", 26.6, 106.7),
    ("CSX", "长沙", 28.2, 113.0),
    ("NRT", "东京成田", 35.8, 140.4),
    ("ICN", "首尔仁川", 37.5, 126.5),
)
CITIES = {code: {"name": name, "lat": lat, "lon": lon} for code, name, lat, lon in _CITIES}

_EARTH_RADIUS_KM = 6371


def city_name(code):
    """三字码 → 中文城市名；不在注册表返回 None。"""
    entry = CITIES.get(code)
    return entry["name"] if entry else None


def all_cities():
    """全部城市（注册表顺序），供下拉与 CLI 提示使用。"""
    return [{"code": code, "name": entry["name"]} for code, entry in CITIES.items()]


def distance_km(origin, destination):
    """两城大圆距离（km，向上取整到整数）；任一城市码不在注册表返回 None。"""
    a, b = CITIES.get(origin), CITIES.get(destination)
    if not a or not b:
        return None
    lat1, lon1 = math.radians(a["lat"]), math.radians(a["lon"])
    lat2, lon2 = math.radians(b["lat"]), math.radians(b["lon"])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return int(round(2 * _EARTH_RADIUS_KM * math.asin(math.sqrt(h))))
