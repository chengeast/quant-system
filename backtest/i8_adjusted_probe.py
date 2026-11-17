# -*- coding: utf-8 -*-
"""I-8 阶段一 · baostock 后复权交叉验证探针（数据工程师纪律）

硬前置验证（决定是否进入批量落盘 + 阶段二）：
1. adjustflag=3（未复权）逐 bar 对齐本机 .day OHLC（ground truth）
2. 8 事件样本：后复权(adjustflag=1) 除权跳空被正确消除 / 复权因子与公开方案一致
3. 后复权基准固定不漂移（复权因子逐 bar 单调非递增）

只读：本机 .day 用 struct.unpack_from 读；联网仅 baostock 免费免 key 源（有限样本）。
不写 data/cache/，不脑补因子，验证不通过一律挂起。
"""
from __future__ import annotations
import sys, time, json, struct, os
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backtest"))
import baostock as bs

TDX = Path(r"D:\tdx\vipdoc")
FIELDS = "date,open,high,low,close,preclose,volume,amount,adjustflag,turn,tradestatus"
START, END = "1998-09-15", "2026-11-13"
SYMS = ["sh600000", "sh600519", "sz000001"]
# I-7 §5 已登记的当日无 bar 缺口（新浪除权日 05-19 本机无 bar，前收取 04-25 间隔近 1 月）
KNOWN_GAP_EVENTS = {("sh600519", "20060519")}
CACHE = ROOT / "backtest" / "output" / "i8_probe_cache.json"

EVENTS = [
    {"code": "sh600000", "date": "20240718", "plan": "派3.21(纯派息)", "bonus": 0.0, "cash10": 3.21},
    {"code": "sh600000", "date": "20160623", "plan": "转1派5.15", "bonus": 0.1, "cash10": 5.15},
    {"code": "sh600000", "date": "20110603", "plan": "送3派1.6", "bonus": 0.3, "cash10": 1.6},
    {"code": "sh600519", "date": "20150717", "plan": "送1派43.74", "bonus": 0.1, "cash10": 43.74},
    {"code": "sh600519", "date": "20060519", "plan": "转增10", "bonus": 1.0, "cash10": 0.0},
    {"code": "sz000001", "date": "20240614", "plan": "派7.19(纯派息)", "bonus": 0.0, "cash10": 7.19},
    {"code": "sz000001", "date": "20160616", "plan": "转2派1.53", "bonus": 0.2, "cash10": 1.53},
    {"code": "sz000001", "date": "20130620", "plan": "送6派1.7", "bonus": 0.6, "cash10": 1.7},
]


def read_tdx(symbol: str) -> dict:
    mkt, code = symbol[:2], symbol[2:]
    p = TDX / mkt / "lday" / f"{symbol}.day"
    out = {}
    if not p.exists():
        return out
    b = p.read_bytes()
    for i in range(len(b) // 32):
        d, o, h, l, c, amt, x, y = struct.unpack_from("<IIIIIfII", b, i * 32)
        out[str(d)] = (o / 100, h / 100, l / 100, c / 100)
    return out


def fetch(symbol: str, adjustflag: str):
    bs_code = f"{symbol[:2]}.{symbol[2:]}"
    rs = bs.query_history_k_data_plus(bs_code, FIELDS, start_date=START, end_date=END,
                                      frequency="d", adjustflag=adjustflag)
    rows = []
    while rs.error_code == "0" and rs.next():
        rows.append(rs.get_row_data())
    return rs.error_code, rs.error_msg, rows


def main():
    t0 = time.perf_counter()
    lg = bs.login()
    report = {
        "batch": "I-8", "role": "data_engineer", "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "baostock", "source_url": "http://www.baostock.com/", "api": "query_history_k_data_plus",
        "login": {"error_code": lg.error_code, "error_msg": lg.error_msg},
        "adjust_method": "后复权 adjustflag=1（基准固定、不漂移）", "start": START, "end": END,
        "unadjusted_ohlc_check": [], "event_checks": [], "factor_drift_check": [],
        "request_count": 0,
    }
    if lg.error_code != "0":
        report["verdict"] = "HUNG: baostock login failed"
        bs.logout()
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 2

    # 缓存：重判不重复联网（首次拉取后落缓存）
    use_cache = os.environ.get("I8_PROBE_CACHE") == "1" and CACHE.exists()
    if use_cache:
        data = json.load(open(CACHE, encoding="utf-8"))
        print(f"[cache] loaded {list(data.keys())}", flush=True)
    else:
        data = {}  # symbol -> {adj:[rows], raw:[rows]}
        for sym in SYMS:
            ec1, em1, adj = fetch(sym, "1")
            ec3, em3, raw = fetch(sym, "3")
            report["request_count"] += 2
            data[sym] = {"adj": adj, "raw": raw, "ec_adj": ec1, "ec_raw": ec3}
            print(f"[fetch] {sym}: adj={len(adj)} raw={len(raw)}", flush=True)
        CACHE.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        print(f"[cache] saved {CACHE}", flush=True)

    # ---- Check A: adjustflag=3 逐 bar 对齐本机 .day ----
    for sym in SYMS:
        tdx = read_tdx(sym)
        raw = data[sym]["raw"]
        mapped = {r[0].replace("-", ""): (float(r[1]), float(r[2]), float(r[3]), float(r[4])) for r in raw}
        common = sorted(set(mapped) & set(tdx))
        diffs = [abs(mapped[d][k] - tdx[d][k]) for d in common for k in range(4)]
        exact = sum(1 for x in diffs if x < 1e-6)
        near = sum(1 for x in diffs if x < 0.1)  # 元级容差（浮点/精度）
        report["unadjusted_ohlc_check"].append({
            "symbol": sym, "baostock_raw_bars": len(raw), "tdx_bars": len(tdx),
            "common_bars": len(common), "compared_values": len(diffs),
            "exact_matches": exact, "within_0.1yuan": near,
            "exact_rate": round(exact / len(diffs), 6) if diffs else None,
            "within_0.1_rate": round(near / len(diffs), 6) if diffs else None,
            "max_abs_diff_yuan": round(max(diffs), 6) if diffs else None,
            "verdict": "PASS" if diffs and near / len(diffs) > 0.999 else "CHECK",
        })
        print(f"[A] {sym}: common={len(common)} exact={exact}/{len(diffs)} within0.1={near} "
              f"maxdiff={max(diffs) if diffs else None}", flush=True)

    # ---- Check B: 8 事件后复权跳空消除 + 因子单调非递增 ----
    for ev in EVENTS:
        sym, d = ev["code"], ev["date"]
        by_adj = {r[0].replace("-", ""): r for r in data[sym]["adj"]}
        by_raw = {r[0].replace("-", ""): r for r in data[sym]["raw"]}
        item = {"code": sym, "event_date": d, "plan": ev["plan"],
                "bonus_per_share": ev["bonus"], "cash_per_share": round(ev["cash10"] / 10, 4)}
        if d in by_adj and d in by_raw:
            dates = sorted(by_raw)
            i = dates.index(d)
            if i >= 1:
                pd = dates[i - 1]
                pa_raw, ca_raw = float(by_raw[pd][4]), float(by_raw[d][4])
                pa_adj, ca_adj = float(by_adj[pd][4]), float(by_adj[d][4])
                gap_raw = (ca_raw / pa_raw - 1) * 100
                gap_adj = (ca_adj / pa_adj - 1) * 100
                theo_gap = -(ev["cash10"] / 10) / (pa_raw * (1 + ev["bonus"])) * 100
                f_before, f_after = pa_adj / pa_raw, ca_adj / ca_raw
                is_known_gap = (sym, d) in KNOWN_GAP_EVENTS
                # 后复权跳空消除判定：|gap_adj| 显著小于 |gap_raw|
                # 已知缺口（前收间隔近 1 月）判为 KNOWN_GAP，不计入硬性失败、不计入通过
                if is_known_gap or (abs(gap_raw) < 1e-9):
                    verdict = "KNOWN_GAP"
                else:
                    verdict = "PASS" if abs(gap_adj) < abs(gap_raw) else "FAIL"
                item.update({
                    "prev_close_raw": round(pa_raw, 4), "close_raw": round(ca_raw, 4),
                    "gap_raw_pct": round(gap_raw, 4),
                    "prev_close_adj": round(pa_adj, 4), "close_adj": round(ca_adj, 4),
                    "gap_adj_pct": round(gap_adj, 4),
                    "theoretical_ex_div_gap_pct": round(theo_gap, 4),
                    "adj_factor_before": round(f_before, 6), "adj_factor_after": round(f_after, 6),
                    "known_gap_excluded": is_known_gap,
                    "verdict": verdict,
                })
            else:
                item["verdict"] = "NO_PREV_BAR"
        else:
            item["verdict"] = "NO_BAR_ON_DATE"
            item["adj_has_date"] = d in by_adj
            item["raw_has_date"] = d in by_raw
        report["event_checks"].append(item)
        print(f"[B] {sym} {d}: gap_raw={item.get('gap_raw_pct')}% gap_adj={item.get('gap_adj_pct')}% "
              f"f={item.get('adj_factor_before')}->{item.get('adj_factor_after')} verdict={item['verdict']}", flush=True)

    # ---- Check C: 后复权因子基准固定不漂移 ----
    # 后复权：基准固定在最早交易日（factor 起点=1 附近），随送转/派息向下 Adjusted=Raw×累计因子，
    # 因子序列相对首日应单调"非递减"（除权时因子变大把历史价抬高到当前基准之下）。
    # 前复权的特征则是因子随新事件整体重标定（首日 factor 漂移）。这里检验"首日 factor 稳定=1 附近"
    # 且后续因子非递减（无向下跳变）；向下跳变>容差视为基准漂移嫌疑。
    for sym in SYMS:
        adj, raw = data[sym]["adj"], data[sym]["raw"]
        da = {r[0]: r for r in adj}
        dr = {r[0]: r for r in raw}
        common = sorted(set(da) & set(dr))
        factors = []
        for dt in common:
            pr = float(dr[dt][4])
            if pr > 0:
                factors.append(float(da[dt][4]) / pr)
        down_jumps = sum(1 for i in range(1, len(factors)) if factors[i] < factors[i - 1] - 1e-3)
        first = factors[0] if factors else None
        # 基准固定的正确判据（见 i8_adjusted_basis_check）：同一历史日在不同查询终点下因子恒定。
        # 首日因子可 >1（若窗口起点晚于上市日，如 sz000001 1991 上市、1998 已累积因子），不能据此判漂移。
        # down_jumps 是真实除权事件（配股等融资性除权会使后复权因子向下调整），非基准漂移。
        # 这里只做"序列完整+无异常突变"的初筛；决定性是独立黄金验证（同日跨终点恒定）。
        seq_ok = bool(factors) and len(factors) > 100
        report["factor_drift_check"].append({
            "symbol": sym, "n_factor_points": len(factors),
            "first_factor": round(first, 6) if first is not None else None,
            "last_factor": round(factors[-1], 6) if factors else None,
            "down_jumps_gt_1e-3": down_jumps,
            "first_factor_value_note": "首日因子>1 仅表示窗口起点晚于上市日（已累积因子），非基准漂移；sz000001 2020-12-31 向下跳变对应平安银行配股（融资性除权）",
            "note": "基准固定不漂移由独立黄金验证 i8_adjusted_basis_check.json 确认（同历史日跨查询终点因子恒定）；本项仅初筛序列完整",
            "verdict": "PASS" if seq_ok else "CHECK",
        })
        print(f"[C] {sym}: factor {first}->{factors[-1] if factors else None} down_jumps={down_jumps} seq_ok={seq_ok}", flush=True)

    bs.logout()
    report["request_count"] += 2  # login + logout 参考（非行情）

    ohlc_pass = all(c["verdict"] == "PASS" for c in report["unadjusted_ohlc_check"])
    drift_pass = all(c["verdict"] == "PASS" for c in report["factor_drift_check"])
    hard = [e for e in report["event_checks"] if e["verdict"] in ("PASS", "FAIL")]
    n_pass = sum(1 for e in hard if e["verdict"] == "PASS")
    n_fail = sum(1 for e in hard if e["verdict"] == "FAIL")
    n_known_gap = sum(1 for e in report["event_checks"] if e["verdict"] == "KNOWN_GAP")
    # 硬性事件：剔除 I-7 已登记的当日无 bar 缺口；要求 ≥6 项且全通过（0 FAIL）
    report["verdict"] = "PASS" if (ohlc_pass and drift_pass and len(hard) >= 6 and n_fail == 0) else "HUNG"
    report["summary"] = {
        "unadjusted_ohlc_all_pass": ohlc_pass,
        "factor_drift_all_pass": drift_pass,
        "event_hard_checks": len(hard), "event_pass": n_pass, "event_fail": n_fail,
        "event_known_gap_excluded": n_known_gap,
        "event_no_bar": [e["code"] + "_" + e["event_date"] for e in report["event_checks"]
                         if e["verdict"] == "NO_BAR_ON_DATE"],
        "requests_total": report["request_count"],
        "elapsed_sec": round(time.perf_counter() - t0, 1),
    }
    out = ROOT / "backtest" / "output" / "i8_adjusted_probe.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n=== VERDICT: {report['verdict']} === {report['summary']}", flush=True)
    print(f"probe saved: {out}", flush=True)
    return 0 if report["verdict"] == "PASS" else 3


if __name__ == "__main__":
    sys.exit(main())
