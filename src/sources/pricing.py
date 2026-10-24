"""确定性定价引擎（v1.3）：注册表内任意航线出价 + 分钟级演示波动。

规则唯一来源：CLI 与网页的同一查询经过同一批适配器与同一批函数，
同一分钟内同航线同票源价格必然一致（PRD v1.3 第 7 节，专门单测锁定）。
确定性硬约束：不使用 random；全部因子来自城市注册表、出发日期与查询分钟。
"""

import contextlib
import datetime as dt
import hashlib
import math
import threading

from . import cities

# 演示波动：幅度 ±3.5%，sin 相位由查询键决定，周期≈2π×7≈44 分钟——分钟间可见小幅变化
_WAVE_AMPLITUDE = 0.035
_WAVE_PERIOD_MINUTES = 7
_WEEKDAY_SURCHARGE = (0, 40, 20, 10, 90, 150, 110)  # 周一..周日，周五/周末偏高

_LOCAL = threading.local()  # 线程局部固定分钟：ThreadingHTTPServer 下各请求互不干扰


def stable_hash(text):
    """跨进程稳定的确定性哈希（内置 hash() 有随机盐，不可用于定价）。"""
    return int(hashlib.md5(text.encode("utf-8")).hexdigest(), 16)


def current_minute():
    """当前分钟桶（同分钟内任何入口一致）；被 fixed_minute 覆盖时返回固定值。"""
    fixed = getattr(_LOCAL, "fixed", None)
    return fixed if fixed is not None else int(dt.datetime.now().timestamp() // 60)


@contextlib.contextmanager
def fixed_minute(minute):
    """把当前线程的"查询时刻"固定到指定分钟桶——24h 曲线重放与单测专用。

    支持嵌套：退出时恢复进入前的值，不会破坏外层上下文。
    """
    previous = getattr(_LOCAL, "fixed", None)
    _LOCAL.fixed = minute
    try:
        yield
    finally:
        _LOCAL.fixed = previous


def route_base(origin, destination):
    """航线基础价：大圆距离线性定价（远贵近廉）；未知城市或同城返回 None。"""
    if origin == destination:
        return None
    km = cities.distance_km(origin, destination)
    return None if km is None else int(round(180 + km * 0.42))


def weekday_surcharge(date):
    return _WEEKDAY_SURCHARGE[date.weekday()]


def wiggle(key, minute=None):
    """演示波动因子（1±3.5%，sin 相位由 key 决定）。

    quote() 引擎报价与固定价商品（精选券）统一叠加同一套波动，
    保证"演示波动"作用于全部报价（PRD v1.3 第 7 节）。
    """
    minute = current_minute() if minute is None else minute
    phase = (stable_hash(key) % 628) / 100.0
    return 1 + _WAVE_AMPLITUDE * math.sin(minute / _WAVE_PERIOD_MINUTES + phase)


def quote(origin, destination, date, *, key, tier=1.0, minute=None):
    """确定性报价：基础价 × 票源档位 + 星期因子，叠加分钟级演示波动（±3.5%）。

    key 参与波动相位（同票源同航线的不同产品波动不同）；minute 供重放与测试固定。
    未知城市/同城返回 None；结果恒为正整数，含税口径由适配器标注、不受波动影响。
    """
    assert isinstance(date, dt.date), "quote 需要日期对象，防适配器误传字符串"
    base = route_base(origin, destination)
    if base is None:
        return None
    minute = current_minute() if minute is None else minute
    return max(1, int(round((base * tier + weekday_surcharge(date)) * wiggle(key, minute))))
