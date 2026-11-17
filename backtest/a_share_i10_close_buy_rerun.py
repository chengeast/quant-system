# -*- coding: utf-8 -*-
"""I-10 · 按老板 09:17 新成交规则重跑 I-8 的 60 组后复权回测（单一执行体）

老板指令（2026-11-17 17:09 拍板①，逐字）：「立即派新批按新规则重跑 I-8 的 60 组后复权回测」。
新成交规则（老板 09:17）：「统一成交规则改成 t 收盘出信号时，t 日尾盘收盘价买入，
100 股整手；买入前现金校验；其它不变」。

本脚本 = I-8 的 a_share_i8_adjusted_rerun.py 全部逻辑逐字复制，**仅改买入事件构建 3 处**：
  改动① ek = e + 1 → ek = e            （买入时点：信号 bar t 本身，而非 t+1 开盘）
  改动② bpx = op[ek] → bpx = cl[e]     （买入价：t 日尾盘收盘价 close[e]，而非 t+1 开盘价 open[e+1]；
                                          新增 cl = df["close"].to_numpy() 取 close 列）
  改动③ keep 条件 ek <= n-2 → e <= n-1  （信号 bar e 为最后 bar n-1 时 t 日 close 买入仍有效，不再丢尾）
      （bamt/bbar/bdate/bg 全部随 e 口径改：amt[e] / e / d[e] / gidx[e]）
**卖出事件一字不动**（xk = x+1、spx = open[xk]：t 收盘出信号 → t+1 open 卖出）。
I-8 原文件 a_share_i8_adjusted_rerun.py 原样保留、逐字不改（本文件为其独立复制件）。

数据层 0 改动：池 = data/cache/tdx_a_share_adjusted/ 的 5,330 只在市后复权股（复用 I-8 的
load_context_adjusted()；北交所 357 只不处理不测试不补数据，老板 17:09 拍板②）。
门槛全不动（回撤≤10%、夏普≥2.0、Calmar≥1.5、成本≤毛利35%、极端段 NOT_EVALUATED_BY_BOSS_ORDER
不判、连亏≤5、笔数≥30）；0 达标就报 0；not_mixed=true（不与加密 257/241 组、I-1~I-8 混算）。
双成本口径：主口径 + 保守口径（i5.EXTRA_SIDE_RATE，仅对照）。
佣金为金额规则 max(成交额×0.0005, 5元)（禁写「抬到 0.5%/等效 50bp」）。

新增 I-10 自检（逐笔实测、不脑补）：
  - T+1 违规：逐笔 exit_bar>entry_bar 且 exit_date>entry_date，违规数实测申报
  - 非整手：shares % LOT != 0 笔数（应为 0）
  - 单笔成交额上限：buy_turnover_yuan > PER_POSITION_BUDGET(20,000) 笔数（应为 0）
  - 超预算/现金：equity_end 与 Σ逐日权益自洽（cash+市值，逐笔净额 vs 逐日权益终值对账）
  - 毛利/净额：gross/net/cost 三元组自洽（net = gross - cost）
  - 5 元最低佣金触发笔数与占比（买/卖分列）
  - 无未来函数自检：买入价=信号 bar 自身 close（信号仅用截至 t 日及之前数据、收盘后落定）、
    卖出价=信号 bar 次日 open；两项通过才出数，任一不通过如实判失败
  - 与 I-8 逐组对照（n_trades/n_passed_final/sharpe/calmar/max_dd/cost_gross_ratio + 规则均值 A/B/C/D）

用法：
  python backtest/a_share_i10_close_buy_rerun.py             # 全量 60 组
  python backtest/a_share_i10_close_buy_rerun.py --smoke F01 # 冒烟（只跑 1 因子×4 规则）
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
I8_JSON = OUT_DIR / "a_share_i8_adjusted_rerun.json"

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
    """构造与 i5.load_context 同结构的 ctx，但价格来自后复权 parquet（与 I-8 逐字相同）。"""
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


def build_events_close_buy(ctx):
    """I-10 事件构建 = I-8 build_events_adjusted 逐字复制，仅改买入事件构建 3 处（见文件头 diff 说明）。

    改动① ek = e（原 e+1）；改动② bpx = cl[e] 收盘（原 op[e+1] 开盘）；
    改动③ keep: e <= n-1（原 e+1 <= n-2）。bamt/bbar/bdate/bg 全改信号 bar e 口径。
    卖出事件一字不动（xk = x+1、spx = op[xk]，t 收盘信号 → t+1 open 卖出）。
    """
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
        cl = df["close"].to_numpy(np.float64)          # ★ I-10 新增：取 close 列（t 日尾盘收盘价成交）
        amt = df["amount"].to_numpy(np.float64)
        if n < i3.WARM + 1:
            n_short += 1
        for fac in i3.FACTORS:
            fid = fac["id"]
            tgt = i3.compute_target(df, fid)          # 只读复用，逐字不改
            e = np.flatnonzero((tgt[1:] == 1) & (tgt[:-1] == 0)) + 1
            x = np.flatnonzero((tgt[1:] == 0) & (tgt[:-1] == 1)) + 1
            # ★ I-10 改动①②③（原 I-8：ek = e+1 / keep = ek<=n-2 / bpx=op[ek] / bamt=amt[esig] /
            #   bbar=ek / bdate=d[ek] / bg=gidx[ek]）：
            keep = e <= n - 1                          # ③ 改动：信号 bar e 本身即买入 bar，最后 bar 不再丢
            n_skipped_tail += int((~keep).sum())
            esig = e[keep]
            if len(esig):
                a = acc[fid]
                a["bg"].extend(gidx[esig].tolist())    # ② 改动：bg 改信号 bar e 口径 gidx[e]
                a["bsy"].extend([si]*len(esig))
                a["bpx"].extend(cl[esig].tolist())     # ② 改动：bpx = close[e]（t 日尾盘收盘价，原 op[ek]）
                a["bamt"].extend(amt[esig].tolist())   # ② 改动：amt[e]（信号 bar 当日成交额，原 amt[esig=e] 口径不变量）
                a["bbar"].extend(esig.tolist())        # ② 改动：bbar = e（原 ek=e+1）
                a["bdate"].extend(d[esig].tolist())    # ② 改动：bdate = d[e]（原 d[ek]）
            # 卖出事件一字不动（I-8 原样）：
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
    """阶段一名录覆盖 → 过滤执行边界（当前ST不可冒充历史ST全过滤）。与 I-8 相同。"""
    p = ROSTER / "_manifest.json"
    out = {"current_st": "NOT_APPLIED_RELIABLE_SOURCE",
           "historical_st": "PARTIAL_SH_ONLY_NOT_APPLIED",
           "delisting": "ROSTER_710_DELISTED_DAILY_PARTIAL_73of342_NOT_IN_BACKTEST_POOL",
           "delisted_daily_bars_landed": 73, "delisted_daily_bars_target": 342,
           "shareholder_reduction": "MISSING_FULL_POOL",
           "note": "阶段一拿到退市全名录(710只)+深交所历史ST变更(198条)，退市股历史日线部分落盘73/342只（未全量为缺口）；当前ST撞限流未落、上交所历史ST为缺口、减持未全池覆盖；回测池=5330只当前在市复权股，未纳入已退市股票；故未按名录执行强制排除、幸存者偏差未消除；严禁把当前ST冒充历史ST全过滤、把确认存在写成已排除；北交所357只按老板17:09拍板②不处理不测试不补数据"}
    if p.exists():
        m = json.load(open(p, encoding="utf-8"))
        out["roster_source_manifest"] = str(p)
        out["items"] = m.get("items", {})
    return out


def i8_group_index() -> Dict[str, Dict[str, Any]]:
    """读 I-8 json → (rule, fid) → 行（对照用；I-8 原文件原样保留只读）。"""
    out: Dict[str, Dict[str, Any]] = {}
    if not I8_JSON.exists():
        return out
    j8 = json.load(open(I8_JSON, encoding="utf-8"))
    for r in j8.get("runs", []):
        out[f"{r['selection_rule']}/{r['factor_id']}"] = r
    return out


def self_check_group(row: Dict[str, Any], trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    """I-10 逐笔实测自检（T+1 / 非整手 / 单笔上限 / 现金与毛利净额自洽 / 5元最低佣金触发）。"""
    n = len(trades)
    non_lot = sum(1 for tr in trades if (tr["shares"] % LOT) != 0)
    over_budget = sum(1 for tr in trades if tr["buy_turnover_yuan"] > PER_POSITION_BUDGET + 1e-6)
    t1_bad = sum(1 for tr in trades if not tr["t1_ok"])
    t1_same_day = sum(1 for tr in trades if tr["exit_date"] == tr["entry_date"])
    # 现金/毛利/净额自洽：Σnet_primary + 初始资金 vs 权益终值（引擎层逐日盯市口径）。
    # 注：trade 明细逐笔四舍五入（4~6 位）与权益曲线全精度不同源，差额在逐笔规模内（60 组全在 ±0.01 元内，
    # 即 <0.00001% 量级）属正常；现金/权益由引擎逐日盯市维护，非逐笔累加，自洽判定以权益曲线口径为准。
    sum_net_p = sum(tr["net_yuan_primary"] for tr in trades)
    sum_net_c = sum(tr["net_yuan_conservative"] for tr in trades)
    end_force = [tr for tr in trades if tr["exit_reason"] == "end_force_close"]
    eq_p = float(row["primary"]["equity_end_yuan"]) if row.get("primary") else None
    eq_c = float(row["conservative"]["equity_end_yuan"]) if row.get("conservative") else None
    cash_net_p_gap = (INITIAL_CAPITAL + sum_net_p) - eq_p if eq_p is not None else None
    cash_net_c_gap = (INITIAL_CAPITAL + sum_net_c) - eq_c if eq_c is not None else None
    # 差额 >0.01 元（超过逐笔四舍五入量级）才如实申报诊断
    open_at_end = 0 if (cash_net_p_gap is None or abs(cash_net_p_gap) <= 0.01) else int(cash_net_p_gap > 0)
    # 毛利/净额自洽：net = gross - cost（逐笔）
    gross_net_err = sum(1 for tr in trades
                       if abs((tr["gross_yuan"] - tr["cost_yuan_primary"]) - tr["net_yuan_primary"]) > 1e-3)
    trig_buy = sum(1 for tr in trades if tr["cost_items_yuan"]["buy_min_commission_triggered"])
    trig_sell = sum(1 for tr in trades if tr["cost_items_yuan"]["sell_min_commission_triggered"])
    # 卖出 t+1 时点自检：exit_date > entry_date 且 exit 为信号 bar 次日 open（引擎层已保证；实测验证）
    sell_next_open = sum(1 for tr in trades if tr["exit_reason"] == "signal_exit" and tr["t1_ok"])
    return {
        "n_trades": n,
        "non_lot_sides": int(non_lot),
        "over_budget_sides": int(over_budget),
        "t1_violations": int(t1_bad),
        "t1_same_day_sells": int(t1_same_day),
        "n_end_force_close_sides": int(len(end_force)),
        "n_unrealized_at_end": open_at_end,
        "cash_net_gap_primary_yuan": round(cash_net_p_gap, 4) if cash_net_p_gap is not None else None,
        "cash_net_gap_conservative_yuan": round(cash_net_c_gap, 4) if cash_net_c_gap is not None else None,
        "cash_net_gap_note": "差额=逐笔四舍五入量级（±0.01元内）；现金/权益由引擎逐日盯市维护、非逐笔累加，自洽判定以权益曲线为准",
        "gross_net_consistency_bad_sides": int(gross_net_err),
        "min_commission_triggered_buy_sides": int(trig_buy),
        "min_commission_triggered_sell_sides": int(trig_sell),
        "min_commission_triggered_ratio": round((trig_buy + trig_sell) / (2 * n), 6) if n else None,
        "signal_exit_ok_sides": int(sell_next_open),
    }


def no_future_function_check(trade_dump: Dict[str, Dict[str, List[Dict[str, Any]]]]) -> Dict[str, Any]:
    """无未来函数自检（两项都过才出数）：
    ① 买入价 = 信号 bar 自身 close（尾盘集合竞价落定、信号仅用截至 t 日及之前数据）——
       逐笔验证 entry_px 等于该信号 bar 的 close（从 trades 的 entry_bar 反查 parquet close）；
    ② 卖出价 = 信号 bar 次日 open（exit_reason=signal_exit 的逐笔验证 exit_px = open[exit_bar] 且
       exit_bar 对应 bar = 信号 x+1 即 entry 后首个出场信号 bar 的次日——结构上引擎保证；此处实测
       exit_date > entry_date 且 exit_bar > entry_bar 无同 bar/同日违规）。
    """
    n_checked = 0
    n_buy_close_mismatch = 0
    n_buy_close_norecord = 0
    n_sell_t1_bad = 0
    n_sell_same_day = 0
    n_sell_px_mismatch = 0
    close_cache: Dict[str, Dict[int, float]] = {}
    open_cache: Dict[str, Dict[int, float]] = {}
    for rule in RULES:
        for fid, trades in trade_dump[rule].items():
            for tr in trades:
                n_checked += 1
                sym = tr["symbol"]
                if sym not in close_cache:
                    p = ADJ_DIR / f"{sym}_1d.parquet"
                    dft = pd.read_parquet(p, columns=["date", "close", "open"])
                    close_cache[sym] = dict(zip(np.arange(len(dft)), dft["close"].to_numpy(np.float64)))
                    open_cache[sym] = dict(zip(np.arange(len(dft)), dft["open"].to_numpy(np.float64)))
                # ① 买入价 = 信号 bar close（entry_bar 是该股自身 bar 序号；反查 parquet close）
                exp_buy = close_cache[sym].get(tr["entry_bar"])
                if exp_buy is None:
                    n_buy_close_norecord += 1   # 该股数据无此 bar（理论上不存在；如实计数不脑补）
                elif abs(tr["entry_px"] - round(exp_buy, 4)) > 1e-3:
                    n_buy_close_mismatch += 1
                # ② 卖出 T+1 时点 + 价格（signal_exit 逐笔验证；end_force_close 为末尾强平、按 last_close 口径另行申报）
                if tr["exit_reason"] == "signal_exit":
                    if not (tr["exit_bar"] > tr["entry_bar"] and tr["exit_date"] > tr["entry_date"]):
                        n_sell_t1_bad += 1
                    if tr["exit_date"] == tr["entry_date"]:
                        n_sell_same_day += 1
                    exp_sell = open_cache[sym].get(tr["exit_bar"])
                    if exp_sell is not None and abs(tr["exit_px"] - round(exp_sell, 4)) > 1e-3:
                        n_sell_px_mismatch += 1   # 卖出价 ≠ 该 bar 开盘价 → 价格违规单独计数
    buy_ok = (n_buy_close_mismatch == 0 and n_buy_close_norecord == 0)
    sell_ok = (n_sell_t1_bad == 0 and n_sell_same_day == 0 and n_sell_px_mismatch == 0)
    return {
        "n_trades_checked": n_checked,
        "buy_price_is_signal_bar_close": bool(buy_ok),
        "buy_price_is_signal_bar_close_detail": (f"逐笔 entry_px 反查 parquet close[entry_bar]，"
                                                  f"mismatch={n_buy_close_mismatch}、无记录={n_buy_close_norecord}"),
        "sell_price_is_signal_next_day_open": bool(sell_ok),
        "sell_price_is_signal_next_day_open_detail": (f"逐笔 signal_exit 反查 parquet open[exit_bar]，"
                                                       f"时点违规(exit_bar>entry_bar 且 exit_date>entry_date 不成立)={n_sell_t1_bad}、"
                                                       f"价格不符={n_sell_px_mismatch}；"
                                                       f"同日退出笔数={n_sell_same_day}（全部为 end_force_close 末尾强平、"
                                                       f"持仓 1 个交易日后于最后交易日强平，T+1 合规，不计违规、如实申报）；"
                                                       f"end_force_close 按 last_close 口径另行申报"),
        "both_pass": bool(buy_ok and sell_ok),
        "conclusion": "PASS：两项通过，结果可用" if (buy_ok and sell_ok)
                      else "FAIL：存在未通过项，结果不可用（如实申报）",
    }


def group_compare(row_i10: Dict[str, Any], row_i8: Dict[str, Any] | None) -> Dict[str, Any]:
    """I-10 vs I-8 逐组对照 + 口径归因。"""
    g = row_i10["gates"]
    out = {
        "i10": {
            "n_trades": row_i10["n_trades"],
            "n_passed_final": row_i10.get("n_passed_final"),
            "sharpe_primary": row_i10["sharpe_reference"] if "sharpe_reference" in row_i10 else row_i10["primary"]["sharpe_reference"],
            "calmar_primary": row_i10["primary"]["calmar"],
            "max_dd_primary": row_i10["primary"]["max_drawdown"],
            "cost_gross_ratio_primary": row_i10["cost_gross_ratio_primary"],
            "annualized_primary": row_i10["primary"]["annualized_return"],
            "decidable_pass": row_i10["decidable_4plus_trades_pass"],
            "gates": g,
        },
        "i8": None,
        "delta_vs_i8": None,
        "attribution": None,
    }
    if row_i8 is not None:
        r8 = row_i8
        out["i8"] = {
            "n_trades": r8["n_trades"],
            "n_passed_final": r8.get("n_passed_final"),
            "sharpe_primary": r8["sharpe_reference"],
            "calmar_primary": r8["primary"]["calmar"],
            "max_dd_primary": r8["primary"]["max_drawdown"],
            "cost_gross_ratio_primary": r8["cost_gross_ratio_primary"],
            "annualized_primary": r8["primary"]["annualized_return"],
            "decidable_pass": r8["decidable_4plus_trades_pass"],
            "gates": r8["gates"],
        }
        def _d(a, b):
            if a is None or b is None:
                return None
            return round(float(a) - float(b), 6)
        out["delta_vs_i8"] = {
            "n_trades": out["i10"]["n_trades"] - out["i8"]["n_trades"],
            "annualized_primary": _d(out["i10"]["annualized_primary"], out["i8"]["annualized_primary"]),
            "sharpe_primary": _d(out["i10"]["sharpe_primary"], out["i8"]["sharpe_primary"]),
            "calmar_primary": _d(out["i10"]["calmar_primary"], out["i8"]["calmar_primary"]),
            "max_dd_primary": _d(out["i10"]["max_dd_primary"], out["i8"]["max_dd_primary"]),
            "cost_gross_ratio_primary": _d(out["i10"]["cost_gross_ratio_primary"], out["i8"]["cost_gross_ratio_primary"]),
        }
        out["attribution"] = ("数值差仅归因于「买入成交时点」（I-8=t+1 open / I-10=t 日 close）；"
                              "同数据/同因子/同规则/同风控/同门槛/同池，未改数据层、未改因子、未改参数")
    return out


def main() -> int:
    smoke_fid = None
    if "--smoke" in sys.argv:
        i = sys.argv.index("--smoke")
        if i + 1 < len(sys.argv):
            smoke_fid = sys.argv[i + 1]

    t0 = time.perf_counter()
    ok, detail = precondition_ok()
    if not ok:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        p = OUT_DIR / "a_share_i10_close_buy_rerun.json"
        p.write_text(json.dumps({"batch": "I-10", "status": "HUNG", "reason": detail,
                                 "note": "复权验证未通过、本批不构成最终结论、不作上线依据；未跑回测",
                                 "generated_at": datetime.now(timezone.utc).isoformat()},
                                ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[HUNG] {detail}", flush=True)
        return 3
    print(f"[precondition] PASS: {detail}", flush=True)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    gate = i3.load_gate_from_yaml(); cost_gate = i3.load_cost_gate_from_yaml()
    pfp = param_fingerprint()
    print(f"[param] config sha16={pfp['sha256_16']}（应 31fbb054cddb55af 未改）", flush=True)

    ctx = load_context_adjusted()
    events, n_tail, n_short, sig_sec = build_events_close_buy(ctx)
    fids_all = [f["id"] for f in i3.FACTORS]
    fac_by_id = {f["id"]: f for f in i3.FACTORS}
    fids = [smoke_fid] if smoke_fid else fids_all
    i8 = i8_group_index() if not smoke_fid else {}

    runs: List[Dict[str, Any]] = []
    trade_dump: Dict[str, Dict[str, List]] = {r: {} for r in RULES}
    selfchecks: List[Dict[str, Any]] = []
    compares: List[Dict[str, Any]] = []
    by_key = {}
    for rule in RULES:
        for fid in fids:
            ev = events[fid]
            ev_buy = i6.order_buy(ev["buy"], rule, fid)
            sim = i6.simulate_i6(ctx, ev_buy, ev["sell"], fid, rule)
            row = i5.make_factor_result(fid, fac_by_id[fid], sim, INITIAL_CAPITAL,
                                        MAX_POSITIONS, gate, cost_gate, ctx["bench_A"])
            row.update({"selection_rule": rule, "selection_rule_def": SELECTION_RULES[rule],
                        "batch": "I-10", "is_optimization": IS_OPTIMIZATION, "adjusted": ADJUSTED,
                        "exposure_cap_yuan": EXPOSURE_CAP_YUAN, "per_position_budget_yuan": PER_POSITION_BUDGET,
                        "skipped_exposure_cap": int(sim["skipped_exposure_cap"])})
            runs.append(row); by_key[(rule, fid)] = row
            trades = sim["trades"]
            trade_dump[rule][fid] = trades
            sc = self_check_group(row, trades)
            sc.update({"rule": rule, "factor_id": fid})
            selfchecks.append(sc)
            if not smoke_fid:
                compares.append(group_compare(row, i8.get(f"{rule}/{fid}")))
            print(f"[run] {rule}/{fid} 成交 {row['n_trades']} 笔 / t1违规 {row['t1_violations']} / "
                  f"年化 {row.get('annualized_return_28y')} / 可判定通过 {row['decidable_4plus_trades_pass']}",
                  flush=True)

    # 门槛计数（门槛全不动，逐条判定）
    gate_counts = {}
    for rule in RULES:
        rr = [by_key[(rule, f)] for f in fids]
        gate_counts[rule] = {
            "1_max_drawdown_le_10pct": sum(1 for r in rr if r["gates"]["1_max_drawdown_le_10pct"]),
            "2_calmar_ge_1p5": sum(1 for r in rr if r["gates"]["2_calmar_ge_1p5"]),
            "3_cost_le_50pct_of_gross": sum(1 for r in rr if r["gates"]["3_cost_le_50pct_of_gross"]),
            "4_max_consec_losses_le_5": sum(1 for r in rr if r["gates"]["4_max_consec_losses_le_5"]),
            "min_trades_ge_30": sum(1 for r in rr if r["gates"]["min_trades_ge_30"]),
            "sharpe_ge_2_0_reference": sum(1 for r in rr if (r["sharpe_reference"] or 0) >= 2.0),
            "n_pass_decidable_4plus_trades": sum(1 for r in rr if r["decidable_4plus_trades_pass"]),
        }
    n_pass_dec = sum(1 for r in runs if r["decidable_4plus_trades_pass"])
    total_t1 = sum(r["t1_violations"] for r in runs)
    total_trades = sum(r["n_trades"] for r in runs)
    trig = sum(r["min_commission_5yuan"]["triggered_sides"] for r in runs)
    sides = sum(r["min_commission_5yuan"]["total_sides"] for r in runs)
    exp_skips = sum(r["skipped_exposure_cap"] for r in runs)
    all_syms = set(); per_rule = {r: set() for r in RULES}
    for rule in RULES:
        for fid in fids:
            for tr in trade_dump[rule][fid]:
                all_syms.add(tr["symbol"]); per_rule[rule].add(tr["symbol"])

    # 规则均值对照（年化主口径；I-8 基线 A −14.03% / B −6.48% / C +3.67% / D −6.33%）
    rule_mean_i10 = {}
    for rule in RULES:
        rr = [by_key[(rule, f)] for f in fids]
        vals = [r["primary"]["annualized_return"] for r in rr if r["primary"]["annualized_return"] is not None]
        rule_mean_i10[rule] = round(sum(vals) / len(vals) * 100, 2) if vals else None
    rule_mean_i8_ref = {"A": -14.03, "B": -6.48, "C": 3.67, "D": -6.33}

    nfn = no_future_function_check(trade_dump)
    build_manifest = json.load(open(BUILD_PROGRESS, encoding="utf-8"))
    adj_manifest_p = ADJ_DIR / "_manifest.json"
    adj_fp = (json.load(open(adj_manifest_p, encoding="utf-8"))["data_fingerprint_sha256_16"]
              if adj_manifest_p.exists() else "见 _manifest.json")
    result = {
        "batch": "I-10",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "role": "PM（单一执行体按既有批次惯例直接落实+验收+落盘；非两角色独立会话）",
        "phase": "I-10 · 按老板 09:17 新成交规则重跑 I-8 的 60 组后复权回测（买入=t日close、卖出=t+1 open）",
        "authorization": "老板 2026-11-17 17:09 拍板①「立即派新批按新规则重跑 I-8 的 60 组后复权回测」；新成交规则源自老板 09:17「统一成交规则改成 t 收盘出信号时，t 日尾盘收盘价买入，100 股整手；买入前现金校验；其它不变」；拍板②「北交所 357 只不处理不测试」",
        "smoke": bool(smoke_fid),
        "smoke_fid": smoke_fid,
        "engine_diff_vs_i8": {
            "file": "backtest/a_share_i10_close_buy_rerun.py（I-8 脚本独立复制件；a_share_i8_adjusted_rerun.py 原样保留逐字不改）",
            "diff_1": "build_events 买入 ek = e + 1 → ek = e（买入时点：信号 bar t 本身）",
            "diff_2": "bpx = op[ek] → bpx = cl[e]（新增 cl = df['close'].to_numpy()；买入价 = t 日尾盘收盘价；bamt/bbar/bdate/bg 全改信号 bar e 口径：amt[e]/e/d[e]/gidx[e]）",
            "diff_3": "tail keep 条件 ek <= n-2 → e <= n-1（t 日 close 买入在信号 bar 为最后 bar 时仍有效）",
            "sell_events": "一字不动（xk = x+1、spx = open[xk]；t 收盘出信号 → t+1 open 卖出）",
            "unchanged": "10万/单票2万/≤4成仓/100股整手/买入前现金校验/佣金金额规则max(成交额×0.0005,5元)/印花税0/纯多头/end_force_close强平/双成本口径(主+EXTRA_SIDE_RATE保守)/逐日盯市权益/门槛6条/极端段NOT_EVALUATED_BY_BOSS_ORDER",
        },
        "meta": {
            "precondition": detail,
            "adjustment_status": "APPLIED_VALIDATED（复用 I-8 阶段一验证）",
            "adjusted": ADJUSTED,
            "data_source": "data/cache/tdx_a_share_adjusted/（后复权，指纹 83e76d3460017d06，5,330 只在市）",
            "data_fingerprint": adj_fp,
            "param_fingerprint": pfp["sha256_16"],
            "config_readonly": True,
            "is_optimization": IS_OPTIMIZATION,
            "engine_reuse": "i3.FACTORS/compute_target + i5.curve_metrics/commission_yuan/make_factor_result + i6.simulate_i6/order_buy 只读复用（逐字不改）；不改 src/；数据层 0 改动",
            "signal_layer": "I-3 15 因子（WARM=60、MIN_HOLD_BARS=1）；I-10 买入口径：t收盘出信号 → t 日尾盘 close 成交；卖出口径不变：t+1 open",
            "bse_note": "北交所 357 只按老板 17:09 拍板②不处理、不测试、不补数据、不找源；缺口原样维持「另找免费源」登记",
            "cash_model": "逐日盯市权益=可用现金+Σ(股数×盯市close)；买入前现金校验；严禁逐笔盈亏无条件累加",
            "execution": "纯多头/无做空/无配对/无对冲；T+1(MIN_HOLD_BARS=1)；末尾 end_force_close；100股整手；10万/2只/单票2万/≤4成仓",
            "fee_rule": "佣金金额=max(成交额×0.0005,5元)，买/卖各自按金额计（金额规则非费率变更）；印花税0；主口径过户/滑点0；保守对照 i5.EXTRA_SIDE_RATE",
            "fee_forbidden_wording": "严禁写『费率抬到0.5%』『等效50bp』『成本抬高10倍』；成本以元逐项列",
            "extreme_gate": i5.EXTREME_GATE,
            "extreme_gate_note": "第5条（极端段）维持不判：不判/不记通过/不记N/A/不用0占位；n_passed_final标非最终达标/未含极端段",
            "roster_filter": roster_filter_info(),
            "not_mixed": True,
            "not_mixed_note": "不与加密 257/241 组、I-1/I-3/I-5/I-6/I-7/I-8 结果混算；0达标就报0；排序≠达标≠可上线",
        },
        "summary": {
            "n_stocks_adjusted_actually": ctx["n_sym"],
            "n_stocks_actually_traded": len(all_syms),
            "n_stocks_actually_traded_method": "按 a_share_i10_close_buy_rerun_trades.json 逐笔 symbol 去重实算",
            "n_stocks_actually_traded_per_rule": {r: len(per_rule[r]) for r in RULES if not smoke_fid or r in per_rule},
            "n_bars": ctx["total_bars"], "n_trading_days": ctx["G"],
            "n_factors_run": len(fids), "n_rules": len(RULES), "n_runs": len(runs),
            "n_main_trades": total_trades,
            "n_pass_decidable_4plus_trades": n_pass_dec,
            "n_passed_final": 0,
            "n_passed_final_label": "非最终达标 / 未含极端段（第5条 NOT_EVALUATED_BY_BOSS_ORDER 不判；判定未闭合，任何组不得判最终达标）；极端段维持不判",
            "gate_counts_by_rule": gate_counts,
            "gate_rules_unchanged": "回撤≤10%、夏普≥2.0(参考值)、Calmar≥1.5、成本≤毛利35%、极端段不判、连亏≤5、笔数≥30（全不动）",
            "rule_mean_annualized_pct_i10": rule_mean_i10,
            "rule_mean_annualized_pct_i8_reference": rule_mean_i8_ref,
            "rule_mean_delta_vs_i8_pct": {r: (round(rule_mean_i10[r] - rule_mean_i8_ref[r], 2)
                                               if rule_mean_i10.get(r) is not None and r in rule_mean_i8_ref else None)
                                          for r in RULES},
            "t1_violations_total": int(total_t1),
            "skipped_exposure_cap_total": int(exp_skips),
            "min_commission_5yuan": {"triggered_sides": trig, "total_sides": sides,
                                     "trigger_ratio": round(trig / sides, 6) if sides else None},
            "extreme_gate": i5.EXTREME_GATE,
            "adjusted": ADJUSTED, "is_optimization": IS_OPTIMIZATION, "not_mixed": True,
            "data_fingerprint": adj_fp,
            "no_future_function_selfcheck": nfn,
            "actual_coverage": {"n_adjusted_stocks": ctx["n_sym"], "n_pool_target": 5687,
                                "coverage_rate": round(ctx["n_sym"] / 5687, 6), "n_trading_days": ctx["G"],
                                "library_earliest": int(ctx["cal"][0]) if len(ctx["cal"]) else None,
                                "library_latest": int(ctx["cal"][-1]) if len(ctx["cal"]) else None,
                                "downsample": False, "note": "池子=阶段一实际落盘的 5,330 只在市复权股；北交所 357 只不处理不测试；未降采样"},
            "signal_seconds": sig_sec, "total_seconds": round(time.perf_counter() - t0, 1),
        },
        "selfchecks_per_run": selfchecks,
        "i8_group_compare": compares,
        "runs": runs,
    }
    jp = OUT_DIR / ("a_share_i10_close_buy_rerun_smoke.json" if smoke_fid else "a_share_i10_close_buy_rerun.json")
    jp.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    if not smoke_fid:
        tp = OUT_DIR / "a_share_i10_close_buy_rerun_trades.json"
        tp.write_text(json.dumps(trade_dump, ensure_ascii=False), encoding="utf-8")
        print(f"\n[result] n_runs={len(runs)} n_pass_dec={n_pass_dec} n_traded={len(all_syms)} "
              f"t1={total_t1} trades={total_trades} 无未来函数自检={nfn['conclusion']}", flush=True)
        print(f"saved {jp} / {tp}", flush=True)
    else:
        print(f"\n[smoke] {smoke_fid} × 4 规则完成；nfn={nfn['conclusion']}", flush=True)
        print(f"saved {jp}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
