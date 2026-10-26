#!/usr/bin/env python3
"""春秋特价票截图入库 CLI：python src/spring_ingest.py [--rebuild] [--db 路径] [--show]

把 src/data/spring_seed.json（7 张抖音小程序截图的人工转录结果）灌进
src/data/spring_aviation.db，并打印入库摘要。默认整表重建（幂等），
重复执行不会产生重复行。

--show 额外打印库内四张表的抽样，用于核对"录入真的落库了"。
"""

import argparse
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent
sys.path.insert(0, str(SRC))

from sources import spring_store  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8")
except AttributeError:
    pass


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="spring_ingest",
        description="春秋特价票截图 → SQLite 结构化入库（券、券适用航线、日期价目）。",
        epilog="示例：python src/spring_ingest.py --rebuild --show",
    )
    parser.add_argument("--seed", help="seed JSON 路径（默认 src/data/spring_seed.json）")
    parser.add_argument("--db", help="库文件路径（默认 src/data/spring_aviation.db）")
    parser.add_argument("--rebuild", action="store_true", default=True,
                        help="整表重建（默认开启，幂等）")
    parser.add_argument("--show", action="store_true", help="打印入库后抽样")
    args = parser.parse_args(argv)

    summary = spring_store.ingest(seed_path=args.seed, db_path=args.db,
                                 rebuild=args.rebuild)
    print(f"入库完成：{summary['batch_id']}")
    print(f"  库文件：{summary['db']}")
    print(f"  券 {summary['coupons']} 张 ｜ 券适用航线 {summary['routes']} 条 ｜ "
          f"日期价目 {summary['offers']} 条")

    if args.show:
        cover = spring_store.coverage(args.db)
        print(f"\n收录范围：价目日期 {cover['offer_date_min']} ~ {cover['offer_date_max']}"
              f" ｜ 来源：{cover['source_kind']}（{cover['captured_at']}）")
        demo = spring_store.coupon_quote("SHA", "通辽", "2026-11-04", args.db)
        print("\n抽样：上海 → 通辽 2026-11-04")
        for quote in demo.get("quotes") or []:
            print(f"  · {quote['coupon_name']}｜{quote['formula']}"
                  f"｜单价 ¥{quote['unit_price_cny']}（{quote['price_basis']}）")
        flights = spring_store.available_flights("SHA", "通辽", "2026-11-04", args.db)
        print(f"  可售商品 {len(flights.get('flights') or [])} 条，"
              f"price_returned={flights.get('price_returned')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
