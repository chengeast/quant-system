# -*- coding: utf-8 -*-
"""I-8 阶段二 · 正式后复权重跑 15 因子 × 4 规则 = 60 组（后端架构师纪律）

前置：阶段一后复权数据「拿到且验证通过」（i8_adjusted_probe PASS + basis_check PASS +
equivalence PASS）。若前置不满足 → 不跑、写挂起说明。

只读复用（逐字不改）：
- i3 a_share_technical_factors.py：FACTORS + compute_target()（15 因子信号、WARM=60、MIN_HOLD_BARS=1）
- i5 a_share_portfolio_factors.py：curve_metrics/commission_yuan/make_factor_result + 费率常量
- i6 a_share_i6_selection_control.py：simulate_i6()（组合引擎：10万/2只/单票2万/≤4成仓/4规则/盯市权益）
不改 src/、不改 config、不与 I-5/I-6 混算、旧脚本全保留。

数据源：data/cache/tdx_a_share_adjusted/{symbol}_1d.parquet（后复权），
池子 = 阶段一实际落盘的全部复权股票（覆盖如实申报，不足全量不虚报）。

费率：金额规则 max(成交额×0.0005, 5元)（i5.commission_yuan），印花税0；主口径过户/滑点0，
保守对照 i5.EXTRA_SIDE_RATE；逐笔报 5 元最低佣金触发笔数与占比。
"""
from __future__ import annotations
import sys, time, json, hashlib
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Dict, List
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backtest"))
import a_share_technical_factors as i3
import a_share_portfolio_factors as i5
import a_share_i6_selection_control as i6

ADJ_DIR = ROOT / "data" / "cache" / "tdx_a_share_adjusted"
BUILD_PROGRESS = ADJ_DIR / "_build_progress.json"
OUT_DIR = ROOT / "backtest" / "output"
OLD_DIR = ROOT / "data" / "cache" / "tdx_a_share"
PROBE = OUT_DIR / "i8_adjusted_probe.json"
BASIS = OUT_DIR / "i8_adjusted_basis_check.json"
EQUIV = OUT_DIR / "i8_local_adjust_equivalence.json"
ROSTER = ROOT / "data" / "cache" / "tdx_a_share_roster"

INITIAL_CAPITAL = i6.INITIAL_CAPITAL
MAX_POSITIONS = i6.MAX_POSITIONS
EXPOSURE_CAP_YUAN = i6.EXPOSURE_CAP_YUAN
PER_POSITION_BUDGET = i6.PER_POSITION_BUDGET
LOT = i5.LOT
RULES = i6.RULES
SELECTION_RULES = i6.SELECTION_RULES
ADJUSTED = True
IS_OPTIMIZATION = False


def param_fingerprint():
    return i3.param_fingerprint()


def precondition_ok() -> tuple[bool, str]:
    details = []
    for p, label in [(PROBE, "probe"), (BASIS, "basis"), (EQUIV, "equivalence")]:
        if not p.exists():
            return False, f"缺失验证证据 {p.name}"
        j = json.load(open(p, encoding="utf-8"))
        v = j.get("verdict", "") or ""
        all_pass = j.get("all_pass")
        ok = (isinstance(v, str) and v.startswith("PASS")) or (all_pass is True)
        details.append(f"{label}={v or ('all_pass=' + str(all_pass))}")
        if not ok:
            return False, f"{label} 未通过: {v or all_pass}"
    return True, "; ".join(details)


def load_context_adjusted() -> Dict[str, Any]:
    """构造与 i5.load_context 同结构的 ctx，但价格来自后复权 parquet。"""
    t = time.perf_counter()
    prog = json.load(open(BUILD_PROGRESS, encoding="utf-8"))
    done = prog["done"]
    symbols = sorted([s for s in done if (ADJ_DIR / f"{s}_1d.parquet").exists()])
    n_sym = len(symbols)
    sym2idx = {s: i for i, s in enumerate(symbols)}
    date_arrays = []
    for s in symbols:
        date_arrays.append(pd.read_parquet(ADJ_DIR / f"{s}_1d.parquet", columns=["date"])["date"].to_numpy(np.int64))
    cal = np.unique(np.concatenate(date_arrays)) if date_arrays else np.array([], dtype=np.int64)
    G = int(len(cal))
    print(f"[load] {n_sym} 只后复权 parquet，全局交易日 {G}（{cal[0]}~{cal[-1]}）", flush=True)
    close_mat = np.full((n_sym, G), np.nan, dtype=np.float32)
    close_ffill = np.full((n_sym, G), np.nan, dtype=np.float32)
    gidx_by: Dict[str, np.ndarray] = {}
    meta: Dict[str, Dict[str, Any]] = {}
    total_bars = 0
    for si, s in enumerate(symbols):
        df = pd.read_parquet(ADJ_DIR / f"{s}_1d.parquet")
        d = df["date"].to_numpy(np.int64)
        g = np.searchsorted(cal, d)
        gidx_by[s] = g.astype(np.int32)
        c = df["close"].to_numpy(np.float32)
        close_mat[si, g] = c
        row = close_mat[si]
        valid = np.isfinite(row) & (row > 0)
        idx = np.where(valid, np.arange(G), -1)
        np.maximum.accumulate(idx, out=idx)
        close_ffill[si] = np.where(idx >= 0, row[np.maximum(idx, 0)], np.nan)
        meta[s] = {"gidx": g.astype(np.int32), "n": int(len(d)), "last_bar": int(len(d) - 1),
                   "last_date": int(d[-1]), "last_close": float(c[-1])}
        total_bars += len(d)
    # 基准 A：全市场等权指数（后复权口径）——复用 i5.curve_metrics
    prev, cur = close_mat[:, :-1], close_mat[:, 1:]
    valid = np.isfinite(prev) & np.isfinite(cur) & (prev > 0)
    with np.errstate(divide="ignore", invalid="ignore"):
        daily = np.where(valid, cur / np.where(prev > 0, prev, np.float32(1.0)) - np.float32(1.0),
                         np.float32(np.nan)).astype(np.float32)
    r_eq = np.nan_to_num(np.nanmean(daily, axis=0), nan=0.0, posinf=0.0, neginf=0.0)
    eq_index = np.concatenate([[1.0], np.cumprod(1.0 + r_eq)])
    years = i5.years_between(int(cal[0]), int(cal[-1]))
    bench_A = i5.curve_metrics(i5.BASELINE_CAPITAL * eq_index / eq_index[0], i5.BASELINE_CAPITAL, years)
    bench_A["definition"] = "全市场后复权个股等权买入持有指数（后复权口径，已消除除权跳空污染）；分散化极限参照"
    bench_A["n_days"] = G
    # 基准 B（指数点位，只读，未复权口径仅作对照，标注不可直接比）
    bench_B = {}
    for sym in ("sh000001", "sz399001", "sh000300"):
        p = OLD_DIR / f"{sym}_1d.parquet"
        if not p.exists():
            bench_B[sym] = {"available": False}
            continue
        d = pd.read_parquet(p); c = d["close"].to_numpy(np.float64)
        yrs = i5.years_between(int(d["date"].iloc[0]), int(d["date"].iloc[-1]))
        met = i5.curve_metrics(i5.BASELINE_CAPITAL * c / c[0], i5.BASELINE_CAPITAL, yrs)
        met.update({"available": True, "note": "指数点位买入持有（未复权口径，与后复权个股策略不可直接比较）"})
        bench_B[sym] = met
    print(f"[load] 完成 bars={total_bars:,} 耗时 {time.perf_counter()-t:.1f}s", flush=True)
    return {"manifest": prog, "symbols": symbols, "sym2idx": sym2idx, "n_sym": n_sym,
            "cal": cal, "G": G, "close_mat": close_mat, "close_ffill": close_ffill,
            "gidx_by": gidx_by, "meta": meta, "total_bars": total_bars,
            "bench_A": bench_A, "bench_B": bench_B, "load_seconds": round(time.perf_counter()-t, 3)}


def build_events_adjusted(ctx):
    """从后复权 parquet 构建 15 因子事件（逻辑同 i5.build_all_factor_events，只读复用 i3.compute_target）。"""
    t = time.perf_counter()
    symbols = ctx["symbols"]
    fids = [f["id"] for f in i3.FACTORS]
    acc = {fid: {"bg": [], "bsy": [], "bpx": [], "bamt": [], "bbar": [], "bdate": [],
                 "sg": [], "ssy": [], "spx": [], "sbar": [], "sdate": []} for fid in fids}
    n_skipped_tail = 0; n_short = 0
    for si, s in enumerate(symbols):
        df = pd.read_parquet(ADJ_DIR / f"{s}_1d.parquet")
        mt = ctx["meta"][s]; n, gidx = mt["n"], mt["gidx"]
        d = df["date"].to_numpy(np.int32)
        op = df["open"].to_numpy(np.float64)
        amt = df["amount"].to_numpy(np.float64)
        if n < i3.WARM + 1:
            n_short += 1
        for fac in i3.FACTORS:
            fid = fac["id"]
            tgt = i3.compute_target(df, fid)          # 只读复用，逐字不改
            e = np.flatnonzero((tgt[1:] == 1) & (tgt[:-1] == 0)) + 1
            x = np.flatnonzero((tgt[1:] == 0) & (tgt[:-1] == 1)) + 1
            ek = e + 1
            keep = ek <= n - 2
            n_skipped_tail += int((~keep).sum())
            ek = ek[keep]; esig = e[keep]
            if len(ek):
                a = acc[fid]
                a["bg"].extend(gidx[ek].tolist()); a["bsy"].extend([si]*len(ek))
                a["bpx"].extend(op[ek].tolist()); a["bamt"].extend(amt[esig].tolist())
                a["bbar"].extend(ek.tolist()); a["bdate"].extend(d[ek].tolist())
            xk = x + 1; xk = xk[xk <= n - 1]
            if len(xk):
                a = acc[fid]
                a["sg"].extend(gidx[xk].tolist()); a["ssy"].extend([si]*len(xk))
                a["spx"].extend(op[xk].tolist()); a["sbar"].extend(xk.tolist()); a["sdate"].extend(d[xk].tolist())
        if (si+1) % 1000 == 0:
            print(f"[signals] {si+1}/{len(symbols)} 股完成", flush=True)
    events = {}
    for fid in fids:
        a = acc[fid]
        buy = np.empty(len(a["bg"]), dtype=i5.BUY_DTYPE)
        buy["g"] = np.asarray(a["bg"], np.int32); buy["sy"] = np.asarray(a["bsy"], np.int32)
        buy["px"] = np.asarray(a["bpx"], np.float64); buy["amt"] = np.asarray(a["bamt"], np.float64)
        buy["bar"] = np.asarray(a["bbar"], np.int32); buy["date"] = np.asarray(a["bdate"], np.int32)
        sell = np.empty(len(a["sg"]), dtype=i5.SELL_DTYPE)
        sell["g"] = np.asarray(a["sg"], np.int32); sell["sy"] = np.asarray(a["ssy"], np.int32)
        sell["px"] = np.asarray(a["spx"], np.float64); sell["bar"] = np.asarray(a["sbar"], np.int32)
        sell["date"] = np.asarray(a["sdate"], np.int32)
        if len(buy): buy = buy[np.lexsort((buy["sy"], -buy["amt"], buy["g"]))]
        if len(sell): sell = sell[np.argsort(sell["g"], kind="stable")]
        events[fid] = {"buy": buy, "sell": sell}
    print(f"[signals] 完成 {time.perf_counter()-t:.1f}s", flush=True)
    return events, n_skipped_tail, n_short, round(time.perf_counter()-t, 3)


def roster_filter_info() -> Dict[str, Any]:
    """阶段一名录覆盖 → 过滤执行边界（当前ST不可冒充历史ST全过滤）。"""
    p = ROSTER / "_manifest.json"
    out = {"current_st": "NOT_APPLIED_RELIABLE_SOURCE",
           "historical_st": "PARTIAL_SH_ONLY_NOT_APPLIED",
           "delisting": "ROSTER_710_DELISTED_DAILY_PARTIAL_73of342_NOT_IN_BACKTEST_POOL",
           "delisted_daily_bars_landed": 73, "delisted_daily_bars_target": 342,
           "shareholder_reduction": "MISSING_FULL_POOL",
           "note": "阶段一拿到退市全名录(710只)+深交所历史ST变更(198条)，退市股历史日线部分落盘73/342只（未全量为缺口）；当前ST撞限流未落、上交所历史ST为缺口、减持未全池覆盖；回测池=5330只当前在市复权股，未纳入已退市股票；故未按名录执行强制排除、幸存者偏差未消除；严禁把当前ST冒充历史ST全过滤、把确认存在写成已排除"}
    if p.exists():
        m = json.load(open(p, encoding="utf-8"))
        out["roster_source_manifest"] = str(p)
        out["items"] = m.get("items", {})
    return out


def main():
    t0 = time.perf_counter()
    ok, detail = precondition_ok()
    if not ok:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        p = OUT_DIR / "a_share_i8_adjusted_rerun.json"
        p.write_text(json.dumps({"batch":"I-8","status":"HUNG","reason":detail,
            "note":"复权验证未通过、本批不构成最终结论、不作上线依据；未跑回测、未用未复权冒充复权",
            "generated_at":datetime.now(timezone.utc).isoformat()}, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[HUNG] {detail}", flush=True)
        return 3
    print(f"[precondition] PASS: {detail}", flush=True)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    gate = i3.load_gate_from_yaml(); cost_gate = i3.load_cost_gate_from_yaml()
    pfp = param_fingerprint()
    print(f"[param] config sha16={pfp['sha256_16']}（应 31fbb054cddb55af 未改）", flush=True)

    ctx = load_context_adjusted()
    events, n_tail, n_short, sig_sec = build_events_adjusted(ctx)
    fids = [f["id"] for f in i3.FACTORS]
    fac_by_id = {f["id"]: f for f in i3.FACTORS}

    runs: List[Dict[str, Any]] = []
    trade_dump: Dict[str, Dict[str, List]] = {r: {} for r in RULES}
    by_key = {}
    for rule in RULES:
        for fid in fids:
            ev = events[fid]
            ev_buy = i6.order_buy(ev["buy"], rule, fid)   # 只读复用 i6 选股排序
            sim = i6.simulate_i6(ctx, ev_buy, ev["sell"], fid, rule)   # 只读复用组合引擎
            row = i5.make_factor_result(fid, fac_by_id[fid], sim, INITIAL_CAPITAL,
                                        MAX_POSITIONS, gate, cost_gate, ctx["bench_A"])
            row.update({"selection_rule": rule, "selection_rule_def": SELECTION_RULES[rule],
                        "batch": "I-8", "is_optimization": IS_OPTIMIZATION, "adjusted": ADJUSTED,
                        "exposure_cap_yuan": EXPOSURE_CAP_YUAN, "per_position_budget_yuan": PER_POSITION_BUDGET,
                        "skipped_exposure_cap": int(sim["skipped_exposure_cap"])})
            runs.append(row); by_key[(rule, fid)] = row; trade_dump[rule][fid] = sim["trades"]
            print(f"[run] {rule}/{fid} 成交 {row['n_trades']} 笔 / t1违规 {row['t1_violations']} / "
                  f"年化 {row.get('annualized_return_28y')} / 可判定通过 {row['decidable_4plus_trades_pass']}", flush=True)

    # 门槛计数
    gate_counts = {}
    for rule in RULES:
        rr = [by_key[(rule, f)] for f in fids]
        gate_counts[rule] = {
            "1_max_drawdown_le_10pct": sum(1 for r in rr if r["gates"]["1_max_drawdown_le_10pct"]),
            "2_calmar_ge_1p5": sum(1 for r in rr if r["gates"]["2_calmar_ge_1p5"]),
            "3_cost_le_50pct_of_gross": sum(1 for r in rr if r["gates"]["3_cost_le_50pct_of_gross"]),
            "4_max_consec_losses_le_5": sum(1 for r in rr if r["gates"]["4_max_consec_losses_le_5"]),
            "min_trades_ge_30": sum(1 for r in rr if r["gates"]["min_trades_ge_30"]),
            "n_pass_decidable_4plus_trades": sum(1 for r in rr if r["decidable_4plus_trades_pass"]),
        }
    n_pass_dec = sum(1 for r in runs if r["decidable_4plus_trades_pass"])
    total_t1 = sum(r["t1_violations"] for r in runs)
    total_trades = sum(r["n_trades"] for r in runs)
    trig = sum(r["min_commission_5yuan"]["triggered_sides"] for r in runs)
    sides = sum(r["min_commission_5yuan"]["total_sides"] for r in runs)
    exp_skips = sum(r["skipped_exposure_cap"] for r in runs)
    # n_stocks_actually_traded 按 trades 去重实算（不犯 I-5 的 15 瑕疵）
    all_syms = set(); per_rule = {r: set() for r in RULES}
    for rule in RULES:
        for fid in fids:
            for tr in trade_dump[rule][fid]:
                all_syms.add(tr["symbol"]); per_rule[rule].add(tr["symbol"])

    build_manifest = json.load(open(BUILD_PROGRESS, encoding="utf-8"))
    adj_manifest_p = ADJ_DIR / "_manifest.json"
    adj_fp = (json.load(open(adj_manifest_p, encoding="utf-8"))["data_fingerprint_sha256_16"]
              if adj_manifest_p.exists() else "见 _manifest.json")
    result = {
        "batch": "I-8", "generated_at": datetime.now(timezone.utc).isoformat(),
        "role": "backend_architect", "phase": "阶段二 · 正式后复权重跑 15 因子×4 规则",
        "meta": {
            "authorization": "老板 2026-11-16 22:17 五项拍板（C路径授权/名录/八年日线/极端不判）",
            "precondition": detail,
            "adjustment_status": "APPLIED_VALIDATED",
            "adjust_method": "后复权=本机未复权×backAdjustFactor（基准固定不漂移）",
            "adjusted": ADJUSTED,
            "data_source": "baostock factor + 本机 tdx_a_share_full 未复权 parquet",
            "data_fingerprint": adj_fp,
            "param_fingerprint": pfp["sha256_16"],
            "config_readonly": True,
            "is_optimization": IS_OPTIMIZATION,
            "optimization_label": "固定参数与规则的全量复权重跑，非寻优、不挑最优",
            "engine_reuse": "只读复用 i3.FACTORS/compute_target + i5.curve_metrics/commission_yuan/make_factor_result + i6.simulate_i6/order_buy（逐字不改）；不改 src/",
            "signal_layer": "I-3 15 因子（WARM=60、MIN_HOLD_BARS=1），t收盘信号→t+1 open成交",
            "cash_model": "逐日盯市权益=可用现金+Σ(股数×盯市close)；买入前现金校验；严禁逐笔盈亏无条件累加",
            "execution": "纯多头/无做空/无配对/无对冲；T+1(MIN_HOLD_BARS=1)；末尾 end_force_close；100股整手；10万/2只/单票2万/≤4成仓",
            "fee_rule": "佣金金额=max(成交额×0.0005,5元)，买/卖各自按金额计（金额规则非费率变更）；印花税0；主口径过户/滑点0；保守对照 i5.EXTRA_SIDE_RATE",
            "fee_forbidden_wording": "严禁写『费率抬到0.5%』『等效50bp』『成本抬高10倍』；成本以元逐项列",
            "extreme_gate": i5.EXTREME_GATE,
            "extreme_gate_note": "第5条维持不判：不判/不记通过/不记N/A/不用0占位；n_passed_final标非最终达标/未含极端段",
            "roster_filter": roster_filter_info(),
            "not_mixed": True,
            "not_mixed_note": "不与 I-5/I-6/加密257组混算；0达标就报0；排序≠达标≠可上线",
        },
        "summary": {
            "n_stocks_in_pool": 5687,
            "n_stocks_adjusted_actually": ctx["n_sym"],
            "n_stocks_actually_traded": len(all_syms),
            "n_stocks_actually_traded_method": "按 a_share_i8_adjusted_rerun_trades.json 中 60 组逐笔 symbol 去重实算",
            "n_stocks_actually_traded_per_rule": {r: len(per_rule[r]) for r in RULES},
            "n_bars": ctx["total_bars"], "n_trading_days": ctx["G"],
            "n_factors": len(fids), "n_rules": len(RULES), "n_runs": len(runs),
            "n_main_trades": total_trades,
            "n_pass_decidable_4plus_trades": n_pass_dec,
            "n_passed_final": 0,
            "n_passed_final_label": "非最终达标 / 未含极端段（第5条不判；判定未闭合，任何组不得判最终达标）",
            "gate_counts_by_rule": gate_counts,
            "t1_violations_total": int(total_t1),
            "skipped_exposure_cap_total": int(exp_skips),
            "min_commission_5yuan": {"triggered_sides": trig, "total_sides": sides,
                "trigger_ratio": round(trig/sides, 6) if sides else None},
            "extreme_gate": i5.EXTREME_GATE,
            "adjusted": ADJUSTED, "is_optimization": IS_OPTIMIZATION, "not_mixed": True,
            "data_fingerprint": adj_fp,
            "actual_coverage": {"n_adjusted_stocks": ctx["n_sym"], "n_pool_target": 5687,
                "coverage_rate": round(ctx["n_sym"]/5687, 6), "n_trading_days": ctx["G"],
                "library_earliest": int(ctx["cal"][0]) if len(ctx["cal"]) else None,
                "library_latest": int(ctx["cal"][-1]) if len(ctx["cal"]) else None,
                "downsample": False, "note": "池子=阶段一实际落盘的复权股票；未达全量如实申报，未降采样"},
            "signal_seconds": sig_sec, "total_seconds": round(time.perf_counter()-t0, 1),
        },
        "runs": runs,
    }
    jp = OUT_DIR / "a_share_i8_adjusted_rerun.json"
    jp.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    tp = OUT_DIR / "a_share_i8_adjusted_rerun_trades.json"
    tp.write_text(json.dumps(trade_dump, ensure_ascii=False), encoding="utf-8")
    print(f"\n[result] n_runs={len(runs)} n_pass_dec={n_pass_dec} n_traded={len(all_syms)} "
          f"t1={total_t1} trades={total_trades}", flush=True)
    print(f"saved {jp} / {tp}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
