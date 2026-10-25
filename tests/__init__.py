# -*- coding: utf-8 -*-
"""测试包初始化：强制网络类票源在测试中离线（零真实请求——调用纪律红线）。

无论本地是否存在真实 Key 配置，测试运行时 Amadeus/RapidAPI 两个适配器一律
视为"未配置 Key"（status=not_available、不出数据、不发请求），保证测试
确定性、离线、快速。专属单测（test_amadeus/test_rapidapi_sky）用显式
config_path 注入，不受影响。
"""

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import sources.amadeus as _amadeus  # noqa: E402
import sources.rapidapi_sky as _rapidapi  # noqa: E402

_amadeus.CONFIG_NAME = "_test_offline_absent.json"
_rapidapi.CONFIG_NAME = "_test_offline_absent.json"
