"""命令行入口：按交班日 cutoff 复现报表。

用法：
    python3 -I scripts/run_report.py                 # 两个 policy cutoff 各出一套
    python3 -I scripts/run_report.py --asof cut1     # 只出第一个 cutoff
    python3 -I scripts/run_report.py --list-cutoffs

只读 data/，输出写入 reports/，文件名带 cutoff 时间戳，保证历史报表不被覆盖。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from harvest.dataio import load_sources
from harvest.evidence import policy_cutoffs
from harvest.reports import generate_all, REPORT_DIR


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="采摘工效与计件复核报表")
    parser.add_argument("--asof", choices=["cut1", "cut2"],
                        help="只复现指定交班日；缺省则两个 cutoff 都出")
    parser.add_argument("--list-cutoffs", action="store_true",
                        help="列出 policy.json 中的交班日")
    args = parser.parse_args(argv)

    data = load_sources()
    cutoffs = policy_cutoffs(data)
    if args.list_cutoffs:
        for i, asof in enumerate(cutoffs, start=1):
            print(f"cut{i}: {asof.cutoff.isoformat()}")
        return 0

    selected = cutoffs if args.asof is None else [cutoffs[int(args.asof[-1]) - 1]]
    REPORT_DIR.mkdir(exist_ok=True)
    for asof in selected:
        paths = generate_all(data, asof)
        print(f"[{asof.label}] 证据截止 {asof.cutoff.isoformat()}")
        for p in paths:
            print(f"  - {p.relative_to(Path.cwd())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
