# -*- coding: utf-8 -*-
"""测试包初始化：强制网络类票源在测试中离线（零真实请求——调用纪律红线）。

无论本地是否存在真实 Key 配置，测试运行时所有联网适配器一律视为"未配置 Key"
（status=not_available、不出数据、不发请求），保证测试确定性、离线、快速。
专属单测（test_amadeus / test_rapidapi_sky / test_variflight / test_juhe）用显式
config_path 注入，不受影响。

**本文件是新增联网适配器的必改点**：`all_sources()` 每加一个按次/按量计费的源，
就在这里补一行 CONFIG_NAME 重定向。历史上这条纪律漏了 4 次（v1.7 / v1.9 / v1.10 /
v1.11 子进程），其中一次真实扣费（ISSUE-020）。v1.15 加聚合数据（按次计费，
试用仅 3 次）时第 6 处同步落实。
"""

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import sources.amadeus as _amadeus  # noqa: E402
import sources.duffel as _duffel  # noqa: E402
import sources.juhe as _juhe  # noqa: E402
import sources.rapidapi_sky as _rapidapi  # noqa: E402
import sources.travelpayouts as _travelpayouts  # noqa: E402
import sources.variflight as _variflight  # noqa: E402

_OFFLINE_ABSENT = "_test_offline_absent.json"

_amadeus.CONFIG_NAME = _OFFLINE_ABSENT
_rapidapi.CONFIG_NAME = _OFFLINE_ABSENT
_variflight.CONFIG_NAME = _OFFLINE_ABSENT
_travelpayouts.CONFIG_NAME = _OFFLINE_ABSENT
_juhe.CONFIG_NAME = _OFFLINE_ABSENT
_duffel.CONFIG_NAME = _OFFLINE_ABSENT
