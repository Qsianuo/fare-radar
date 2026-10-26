# -*- coding: utf-8 -*-
"""离线守卫：任何联网票源在测试进程里都不得处于"可发请求"状态。

为什么要单独一个测试文件：`python -m unittest discover tests` 时 top_level_dir
默认就是 `tests`，模块按 `test_xxx` 顶层导入，**`tests/__init__.py` 不会被执行**。
所以"只改 __init__.py"的注入在 discover 下是无效的——2026-10-26 加聚合数据源时
就因此真实联网并按次计费扣了额度（红线第 5 次复发）。

本文件做两件事：
1. 与各测试模块一样在**模块级**重定向所有联网源的配置路径（自身自足）；
2. 断言重定向确实生效——新增联网源若忘了注入，这里会红并指名道姓，
   而不是等到某个业务断言莫名其妙失败、最后才发现"其实是联网了"。
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import sources.amadeus as _amadeus  # noqa: E402
import sources.duffel as _duffel  # noqa: E402
import sources.juhe as _juhe  # noqa: E402
import sources.rapidapi_sky as _rapidapi  # noqa: E402
import sources.travelpayouts as _travelpayouts  # noqa: E402
import sources.variflight as _variflight  # noqa: E402

OFFLINE_ABSENT = "_test_offline_absent.json"

# 联网源清单：**每新增一个联网适配器，必须在这里登记**（否则下面会红）
NETWORK_MODULES = {
    "amadeus": _amadeus,
    "rapidapi_sky": _rapidapi,
    "variflight": _variflight,
    "travelpayouts": _travelpayouts,
    "juhe": _juhe,
    "duffel": _duffel,
}

for _module in NETWORK_MODULES.values():
    _module.CONFIG_NAME = OFFLINE_ABSENT


class OfflineInjectionTest(unittest.TestCase):
    def test_every_network_source_points_to_absent_config(self):
        missing = [name for name, module in NETWORK_MODULES.items()
                   if getattr(module, "CONFIG_NAME", None) != OFFLINE_ABSENT]
        self.assertEqual(missing, [],
                         "联网源未注入离线配置——测试会真实联网："
                         + ", ".join(missing))

    def test_no_network_source_is_active_in_tests(self):
        from sources import all_sources
        active = [s.source_id for s in all_sources()
                  if s.source_id in {"rapidapi_sky", "variflight", "juhe_flight",
                                     "amadeus", "duffel"}
                  and s.status == "active"]
        self.assertEqual(active, [], "测试进程中不得有可发请求的联网源")

    def test_all_sources_covers_every_registered_network_module(self):
        """清单与注册表对齐：注册表里出现新的真实接口源而这里没登记 → 报警。"""
        from sources import all_sources
        registered = {s.source_id for s in all_sources()}
        known = {"snapshot_pack", "rapidapi_sky", "variflight", "juhe_flight",
                 "travelpayouts", "amadeus", "duffel", "airline", "ota",
                 "douyin_groupbuy", "douyin_miniapp", "douyin_live"}
        unknown = registered - known
        self.assertEqual(unknown, set(),
                         "发现未登记的新票源，请先判断是否联网并在此处与本文件登记："
                         + ", ".join(sorted(unknown)))


if __name__ == "__main__":
    unittest.main()
