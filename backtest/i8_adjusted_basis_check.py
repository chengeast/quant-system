# -*- coding: utf-8 -*-
"""I-8 · 后复权基准固定性黄金验证（数据工程师纪律）

区分后复权（基准固定）vs 前复权（基准漂移）的黄金标准：
同一历史交易日的复权因子，在【不同查询终点】下必须恒定。
若因子随查询终点变化 → 前复权特征（历史价被整体重标定）→ 禁止用于回测。

只读联网 baostock 免费源，有限请求（3 股 × 2 终点 = 6 次因子查询）。
"""
from __future__ import annotations
import sys, json, time
from pathlib import Path
from datetime import datetime, timezone
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backtest"))
import baostock as bs

F = "date,open,high,low,close,preclose,volume,amount,adjustflag,turn,tradestatus"
# (symbol, 探测历史日, 终点1, 终点2)  —— 探测日取各股确有 bar 的交易日
CASES = [
    ("sh.600000", "2000-01-10", "2015-12-31", "2026-11-13"),
    ("sh.600519", "2005-06-15", "2015-12-31", "2026-11-13"),
    ("sz.000001", "2005-06-15", "2015-12-31", "2026-11-13"),
]

def factor_at(sym, end, probe):
    adj, raw = {}, {}
    rs = bs.query_history_k_data_plus(sym, F, start_date="1998-09-15", end_date=end,
                                      frequency="d", adjustflag="1")
    while rs.error_code == "0" and rs.next():
        r = rs.get_row_data(); adj[r[0]] = float(r[4])
    rs2 = bs.query_history_k_data_plus(sym, F, start_date="1998-09-15", end_date=end,
                                       frequency="d", adjustflag="3")
    while rs2.error_code == "0" and rs2.next():
        r = rs2.get_row_data(); raw[r[0]] = float(r[4])
    probe_dash = probe
    if probe_dash in adj and probe_dash in raw and raw[probe_dash] > 0:
        return round(adj[probe_dash] / raw[probe_dash], 6)
    # 探测日无 bar → 取不晚于探测日的最近交易日
    days = sorted(set(adj) & set(raw))
    cand = [d for d in days if d <= probe_dash and raw[d] > 0]
    if not cand:
        return None
    d = cand[-1]
    return round(adj[d] / raw[d], 6), d


def main():
    t0 = time.perf_counter()
    bs.login()
    results = []
    req = 0
    for sym, probe, e1, e2 in CASES:
        a = factor_at(sym, e1, probe); req += 2
        b = factor_at(sym, e2, probe); req += 2
        fa = a[0] if isinstance(a, tuple) else a
        fb = b[0] if isinstance(b, tuple) else b
        da = a[1] if isinstance(a, tuple) else probe
        db = b[1] if isinstance(b, tuple) else probe
        stable = (fa is not None and fb is not None and abs(fa - fb) < 1e-4)
        results.append({"symbol": sym, "probe_date": probe,
                        "actual_date_end1": da, "actual_date_end2": db,
                        "factor_end_2015": fa, "factor_end_2026": fb,
                        "stable": bool(stable),
                        "verdict": "PASS" if stable else "CHECK"})
        print(f"[basis] {sym} @{da}/{db}: f2015={fa} f2026={fb} stable={stable}", flush=True)
    bs.logout()
    all_pass = all(r["verdict"] == "PASS" for r in results)
    out = {
        "batch": "I-8", "role": "data_engineer",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "baostock", "test": "后复权基准固定性黄金验证（同历史日跨查询终点因子恒定）",
        "method": "对同一 symbol、同一历史交易日，分别用 end=2015-12-31 与 end=2026-11-13 查询 adjustflag=1/3，比较因子 adj/raw；恒定=后复权基准固定，漂移=前复权特征",
        "results": results, "requests_estimate": req,
        "all_pass": all_pass,
        "verdict": "PASS（后复权基准固定、不漂移，可用于回测）" if all_pass else "CHECK",
        "elapsed_sec": round(time.perf_counter() - t0, 1),
        "redlines": "免费免key源、有限请求、未写data/cache、未脑补因子",
    }
    p = ROOT / "backtest" / "output" / "i8_adjusted_basis_check.json"
    p.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n=== {out['verdict']} === saved {p}", flush=True)
    return 0 if all_pass else 3

if __name__ == "__main__":
    sys.exit(main())
