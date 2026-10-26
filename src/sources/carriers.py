"""真实航司中文简称表（IATA 二字码 → 简称）。

与 `airlines.AIRLINES` 的区别（**不要混用**）：
- `airlines.AIRLINES` 是**演示/生成器**的 6 家名册，扩充它会改变 `_pick_carrier`
  的候选池，进而改变演示航班号与承运人分配——那是确定性快照，不能动。
- 本表只做"码 → 中文简称"的翻译，供真实接口适配器展示用，不参与任何生成逻辑。

为什么需要本表：上游（聚合数据、飞常准）返回的 `airlineName` 常是**机器翻译**，
例如 BK → "Okay航空公司"（奥凯航空）、G5 → "中国快运航空公司"（华夏航空，
China Express 被逐词直译）。直接用会把错译展示给用户，故中文名一律以本地表为准，
上游只作最后兜底且需清洗。

来源：IATA 二字码公开资料，人工校对；只收国内常用航司，未收录的留空（不猜）。
"""

# 二字码 → 简称（与 variflight 历史表一致，另补 CA/MU/CZ/HU/9C/HO 等常见码）
CN_NAMES = {
    # 三大航与主要干线
    "CA": "国航", "MU": "东航", "CZ": "南航", "HU": "海航", "HO": "吉祥",
    "9C": "春秋", "3U": "川航", "MF": "厦航", "ZH": "深航", "SC": "山航",
    # 中小航司
    "BK": "奥凯", "G5": "华夏", "KY": "昆明", "PN": "西部", "DZ": "东海",
    "GJ": "长龙", "EU": "成都", "TV": "西藏", "KN": "中联航", "GS": "天津",
    "JD": "首都", "NS": "河北", "A6": "湖南", "QW": "青岛", "DR": "瑞丽",
    "GT": "桂林", "AQ": "九元", "CN": "大新华", "Y8": "金鹏", "8L": "祥鹏",
    "UQ": "乌鲁木齐", "FU": "福州", "FM": "上航", "GX": "北部湾", "RY": "江西",
}


def cn_name(code):
    """二字码 → 中文简称；未收录返回 None（调用方决定留空还是用码）。"""
    return CN_NAMES.get(str(code or "").strip().upper())


def display_name(code, upstream_name=None):
    """展示用承运人名：本地表 > 已清洗的上游名 > 二字码。

    上游名清洗规则（只做明显安全的处理，不猜译名）：
    - 去掉首尾空白与"航空公司/航空/公司"后缀；
    - 含 ASCII 字母或"快运/Okay"等直译痕迹 → 判为机翻，弃用（宁可显示码）。
    """
    local = cn_name(code)
    if local:
        return local
    name = str(upstream_name or "").strip()
    if not name:
        return str(code or "").strip()
    cleaned = (name.replace("航空公司", "").replace("航空", "")
                   .replace("公司", "").strip())
    if not cleaned or any(ch.isascii() and ch.isalpha() for ch in cleaned):
        return str(code or "").strip()      # 机翻痕迹 → 退回二字码
    return cleaned
