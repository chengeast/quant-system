# -*- coding: utf-8 -*-
"""I-5 阶段二 · 全市场个股组合选股分仓 + 真实仓位控制回测（后端架构师产出）

PM 承派 + 验收；按 agents/souls/backend_architect.md 纪律：模块边界清晰、接口可测试可回滚
（新增脚本、保留 backtest/ 下全部旧脚本不动、不改 src/、不改 config/）。

老板 2026-11-15 21:15 指令（逐字）：
「…整个A股市场有五千多支个股，我也不可能所有股票都买，而是在持仓金额允许范围内，
  选出符合条件的个股，再分仓持有，达到卖出条件再卖出。你们测试的样本太少了。
  仓位控制也没有做好，请重新安排修改、测试。再发准确的报告给我。」

============================================================================
方向改了（与 I-3/I-4 的根本区别，逐条可验证）
============================================================================
  - I-3/I-4：12 指数 + 3 个股，逐标的独立成交，逐笔盈亏**无条件累加到一条
    BASE_EQUITY=10000 固定曲线**，每笔 1000 元名义、无资金占用/并行持仓检查。
    → 老板点名「标的不对（指数点位当成交价）/ 样本太少 / 仓位控制没做好」。
  - 本批：**全市场 5687 只个股池** → 每交易日按因子筛候选 → **组合层面**在可用现金 /
    MAX_POSITIONS 约束下选股 → **分仓同时持有多只** → 各自达到出场信号才卖（T+1）。
    **逐日盯市权益曲线 = 可用现金 + 持仓市值**（严禁逐笔盈亏无条件累加到固定曲线）。
    **买入前校验现金 ≥ 成交额 + 费用**，同一笔资金不得重复占用；按 100 股整数倍取整，
    按 t+1 open 的**真实股价**成交（成交额 = 100 股整数倍 × 开盘价，真实量级）。

============================================================================
信号层（只读复用 I-3，逐字不改）
============================================================================
  import a_share_technical_factors as i3；只用 i3.FACTORS / i3.compute_target() /
  i3.WARM / i3.MIN_HOLD_BARS / i3.load_gate_from_yaml() / i3.load_cost_gate_from_yaml() /
  i3.param_fingerprint()；**不铺网格、不寻优、不改 src/、不改 config**。
  15 因子统一参数，进场/出场事件由 compute_target() 的 target 状态机 0→1 / 1→0 提取。

============================================================================
费率（老板 20:21/20:23 金额规则，原样引用、不改写）
============================================================================
  佣金金额 = max(成交额 × 0.0005, 5 元)，买 / 卖**各自独立按金额计**；
  **这是金额规则、不是费率变更** —— 严禁写「费率被抬到 0.5%」「等效 50bp」
  「成本抬高 10 倍」等换算；成本一律以「元」逐项列示。
  印花税 = 0（老板「先取消」）。过户费 / 滑点老板未指定 → 主口径 0，
  另出保守对照（加回过户 0.001%/边 + 滑点 5bp/边，按金额计）。
  不得说「A 股费率低一个数量级」；phase7 加密现货 0.1%/边 / roundtrip 28bp
  仅适用加密主线，绝不硬套 A 股。

============================================================================
沿用未撤回口径
============================================================================
  28 年全量（1998-09-15~2026-11-13，不裁窗口）、T+1（当日买不可当日卖）、
  纯多头 / 无做空 / 无配对 / 无对冲（老板 17:37/17:46）、
  先不判极端段（extreme_gate=NOT_EVALUATED_BY_BOSS_ORDER；判定 = 4 条硬门槛 + 笔数≥30；
  n_pass_decidable_4plus_trades 与 n_passed_final 严格区分，n_passed_final 标
  「非最终达标/未含极端段」，严禁把「先不判」写成放行）、
  config/strategy_params.yaml 一字不改、冻结门槛不因换数据而放宽。

============================================================================
产出
============================================================================
  backtest/output/a_share_portfolio_factors.json + _trades.json
  运行：python backtest/a_share_portfolio_factors.py
"""
from __future__ import annotations

import hashlib
import json
import math
import sys
import time
from array import array as pyarray
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))                 # 让 src.core 可导入
sys.path.insert(0, str(ROOT / "backtest"))   # 让 a_share_technical_factors 可导入
import a_share_technical_factors as i3        # noqa: E402  （只读复用，逐字不改）
from src.core.timeframe import resolve_timeframe  # noqa: E402 （只读复用 A 股 252 日）

DATA_DIR = ROOT / "data" / "cache" / "tdx_a_share_full"
OLD_DIR = ROOT / "data" / "cache" / "tdx_a_share"   # I-1 指数 parquet（基准 B 只读复用）
OUT_DIR = ROOT / "backtest" / "output"
MANIFEST = DATA_DIR / "_manifest.json"

# ---------------------------------------------------------------------------
# 组合参数（工程师设定，逐项写明理由；敏感性档为「参数敏感性对照，非参数寻优」）
# ---------------------------------------------------------------------------
INITIAL_CAPITAL_OPTIONS = [500_000.0, 1_000_000.0, 5_000_000.0]  # 50万 / 100万 / 500万
MAX_POSITIONS_OPTIONS = [5, 10, 20]                               # 最大同时持仓数
BASELINE_CAPITAL = 1_000_000.0      # 主档：100 万量级（A 股 100 股整手下每票 1/N 可覆盖多数股价）
BASELINE_MAX_POS = 10               # 主档：10 只分仓
LOT = 100                          # 100 股整数倍（A 股整手）

# 单票仓位上限：等权 1/N（按初始资金固定分 N 份，简单可复现，避免同一笔资金重复占用）
#   per_position_budget = INITIAL_CAPITAL / MAX_POSITIONS
# 选股排序规则（明确、可复现、无未来函数、不得隐式随机）：
#   同一交易日候选 > 可用仓位时，按【信号日成交额 amount 降序（流动性优先）】取前 N，
#   同额按【股票代码升序】tie-break。
#   理由：全市场选股时流动性是执行可行性的关键；成交额是信号产生当日（bar t 收盘）已知的
#   公开数据，不含未来函数；固定代码序保证完全确定性、无隐式随机、可复现。
#   进场事件为布尔跳变（0→1），无连续「信号强度」可定义，故不臆造强度分数。
SORT_RULE = "signal_day_amount_desc_then_symbol_asc"

# 费率（金额规则，单位：元）
COMM_RATE = 0.0005                 # 单向万分之 5（仅用于计算按比例佣金金额）
MIN_COMM_YUAN = 5.0               # 不足 5 元直接按 5 元这一笔金额收
STAMP_DUTY = 0.0                  # 老板「先取消印花税」
TRANSFER_PCT = 0.00001            # 保守对照：过户 0.001%/边（老板未指定）
SLIPPAGE_BPS = 5.0                # 保守对照：滑点 5bp/边（老板未指定）
EXTRA_SIDE_RATE = TRANSFER_PCT + SLIPPAGE_BPS / 10_000.0   # 0.00051/边（仅保守对照）

YEARS_28 = 28.16                  # 1998-09-15~2026-11-13 日历年数（报告年化口径）
MIN_TRADES = 30
EXTREME_GATE = "NOT_EVALUATED_BY_BOSS_ORDER"

BUY_DTYPE = np.dtype([("g", "<i4"), ("sy", "<i4"), ("px", "<f8"),
                      ("amt", "<f8"), ("bar", "<i4"), ("date", "<i4")])
SELL_DTYPE = np.dtype([("g", "<i4"), ("sy", "<i4"), ("px", "<f8"),
                       ("bar", "<i4"), ("date", "<i4")])


def commission_yuan(turnover: float):
    """单边佣金金额（元）→ (实收金额, 是否触发 5 元最低佣金, 按比例佣金金额)。金额规则。"""
    proportional = turnover * COMM_RATE
    amount = max(proportional, MIN_COMM_YUAN)
    return amount, proportional < MIN_COMM_YUAN, proportional


def sha16(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()[:16]


def years_between(d0: int, d1: int) -> float:
    a = date(int(str(d0)[:4]), int(str(d0)[4:6]), int(str(d0)[6:8]))
    b = date(int(str(d1)[:4]), int(str(d1)[4:6]), int(str(d1)[6:8]))
    return max((b - a).days / 365.25, 1e-9)


def curve_metrics(equity: np.ndarray, capital: float, years_ann: float) -> Dict[str, Any]:
    """逐日盯市权益曲线 → 累计 / 年化 / 最大回撤 / Calmar / 夏普(参考)。"""
    eq = np.asarray(equity, dtype=np.float64)
    total_return = float(eq[-1] / capital - 1.0)
    peak = np.maximum.accumulate(eq)
    denom = np.where(peak > 0, peak, 1.0)
    max_dd = float(np.max((peak - eq) / denom)) if np.all(peak > 0) else 1.0
    r = np.diff(eq) / np.where(eq[:-1] > 0, eq[:-1], 1.0)
    sharpe = float(np.mean(r) / np.std(r) * np.sqrt(252.0)) if len(r) > 1 and np.std(r) > 0 else 0.0
    annualized = (float((1.0 + total_return) ** (1.0 / years_ann) - 1.0)
                  if total_return > -1 else None)
    # Calmar 沿用 I-4「与冻结门槛可比口径」：年数 = len(equity)/252（A 股交易日/年）
    years_calmar = len(eq) / 252.0
    annual_calmar = (float((1.0 + total_return) ** (1.0 / years_calmar) - 1.0)
                     if years_calmar > 0 and total_return > -1 else 0.0)
    calmar = (float(annual_calmar / max_dd) if max_dd > 1e-12
              else (0.0 if annual_calmar <= 0 else None))
    return {
        "cumulative_return": round(total_return, 6),
        "annualized_return": (round(annualized, 6) if annualized is not None else None),
        "annualized_defined": bool(total_return > -1),
        "annualized_years": round(years_ann, 4),
        "annualized_formula": f"(1 + {round(total_return, 6)})^(1/{years_ann}) - 1",
        "max_drawdown": round(max_dd, 6),
        "calmar": (round(calmar, 4) if calmar is not None else None),
        "calmar_years_note": "Calmar 年数 = len(equity)/252（沿用 I-4 与冻结门槛可比口径）；报告年化用 28.16 年",
        "sharpe_reference": round(sharpe, 4),
        "equity_start_yuan": round(float(eq[0]), 2),
        "equity_end_yuan": round(float(eq[-1]), 2),
    }


def load_context() -> Dict[str, Any]:
    """Pass 0：只读加载全市场 parquet → 全局日历 / 盯市价矩阵 / 每股元数据 / 基准 A / 基准 B。"""
    t = time.perf_counter()
    m = json.loads(MANIFEST.read_text(encoding="utf-8"))
    symbols = [f["symbol"] for f in m["files"]]
    n_sym = len(symbols)
    sym2idx = {s: i for i, s in enumerate(symbols)}
    # 第一遍只读 date 列（全局日历），不常驻整个 DataFrame（内存安全）
    date_arrays: List[np.ndarray] = []
    for s in symbols:
        date_arrays.append(
            pd.read_parquet(DATA_DIR / f"{s}_1d.parquet", columns=["date"])["date"].to_numpy(np.int64))
    cal = np.unique(np.concatenate(date_arrays)) if date_arrays else np.array([], dtype=np.int64)
    G = int(len(cal))
    print(f"[load] {n_sym} 只个股 parquet，全局交易日 {G}（{cal[0]}~{cal[-1]}）", flush=True)

    # 盯市价矩阵（float32；NaN=该股当日无 bar）+ 前值填充矩阵
    close_mat = np.full((n_sym, G), np.nan, dtype=np.float32)
    close_ffill = np.full((n_sym, G), np.nan, dtype=np.float32)
    gidx_by: Dict[str, np.ndarray] = {}
    meta: Dict[str, Dict[str, Any]] = {}
    total_bars = 0
    for si, s in enumerate(symbols):
        df = pd.read_parquet(DATA_DIR / f"{s}_1d.parquet")   # 第二遍：填盯市价 + 元数据后丢弃
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
        meta[s] = {
            "gidx": g.astype(np.int32), "n": int(len(d)),
            "last_bar": int(len(d) - 1), "last_date": int(d[-1]), "last_close": float(c[-1]),
        }
        total_bars += len(d)

    # 基准 A：全市场个股等权买入持有指数（每日等权收益复合；分散化极限参照）
    prev = close_mat[:, :-1]              # float32 view，不复制（省内存）
    cur = close_mat[:, 1:]
    valid = np.isfinite(prev) & np.isfinite(cur) & (prev > 0)
    with np.errstate(divide="ignore", invalid="ignore"):
        daily = np.where(valid,
                         cur / np.where(prev > 0, prev, np.float32(1.0)) - np.float32(1.0),
                         np.float32(np.nan)).astype(np.float32)
    r_eq = np.nanmean(daily, axis=0)
    r_eq = np.nan_to_num(r_eq, nan=0.0, posinf=0.0, neginf=0.0)
    eq_index = np.concatenate([[1.0], np.cumprod(1.0 + r_eq)])
    bench_eq = BASELINE_CAPITAL * eq_index / eq_index[0]
    bench_A = curve_metrics(bench_eq, BASELINE_CAPITAL, YEARS_28)
    bench_A["definition"] = ("全市场 5687 只个股每日等权收益复合指数 × 初始资金（全程满仓持有、无杠杆）；"
                            "分散化极限参照，忽略 100 股整手/单票上限/资金约束上限，"
                            "与策略同初始资金但不等同于分仓约束；未复权，除权跳空同样污染。")
    bench_A["n_days"] = G

    # 基准 B：主要指数买入持有（只读 I-1 旧 parquet 的指数点位序列；不作成交标的）
    bench_B: Dict[str, Any] = {}
    for sym in ("sh000001", "sz399001", "sh000300"):
        p = OLD_DIR / f"{sym}_1d.parquet"
        if not p.exists():
            bench_B[sym] = {"available": False, "reason": "旧 parquet 不存在"}
            continue
        d = pd.read_parquet(p)
        c = d["close"].to_numpy(np.float64)
        d0, d1 = int(d["date"].iloc[0]), int(d["date"].iloc[-1])
        yrs = years_between(d0, d1)
        be = BASELINE_CAPITAL * c / c[0]
        met = curve_metrics(be, BASELINE_CAPITAL, yrs)
        met.update({"available": True, "start_date": str(d0), "end_date": str(d1),
                    "n_bars": int(len(c)), "years_actual": round(yrs, 4),
                    "note": "指数点位买入持有（仅作收益对照，不作个股成交标的）；年化按各自实际起止年数，"
                            "不套 28.16 年（短样本如实申报）"})
        bench_B[sym] = met

    print(f"[load] 完成，总 bars={total_bars:,}，耗时 {time.perf_counter()-t:.1f}s", flush=True)
    return {
        "manifest": m, "symbols": symbols, "sym2idx": sym2idx, "n_sym": n_sym,
        "cal": cal, "G": G, "close_mat": close_mat,
        "close_ffill": close_ffill, "gidx_by": gidx_by, "meta": meta,
        "total_bars": total_bars, "bench_A": bench_A, "bench_B": bench_B,
        "load_seconds": round(time.perf_counter() - t, 3),
    }


def build_all_factor_events(ctx: Dict[str, Any]):
    """单遍遍历个股，逐股调用 i3.compute_target（15 因子，逐字不改），
    按因子把进场/出场事件累积到紧凑 array（避免逐因子重复读盘、避免常驻大 target 矩阵）。"""
    t = time.perf_counter()
    symbols = ctx["symbols"]
    fids = [f["id"] for f in i3.FACTORS]
    acc = {fid: {
        "bg": pyarray("i"), "bsy": pyarray("i"), "bpx": pyarray("d"),
        "bamt": pyarray("d"), "bbar": pyarray("i"), "bdate": pyarray("i"),
        "sg": pyarray("i"), "ssy": pyarray("i"), "spx": pyarray("d"),
        "sbar": pyarray("i"), "sdate": pyarray("i"),
    } for fid in fids}
    n_skipped_tail_entry = 0
    n_short = 0
    for si, s in enumerate(symbols):
        df = pd.read_parquet(DATA_DIR / f"{s}_1d.parquet")   # 第三遍：算 15 因子信号（逐股释放）
        mt = ctx["meta"][s]
        n, gidx = mt["n"], mt["gidx"]
        d = df["date"].to_numpy(np.int32)
        op = df["open"].to_numpy(np.float64)
        amt = df["amount"].to_numpy(np.float64)
        if n < i3.WARM + 1:
            n_short += 1
        for fac in i3.FACTORS:
            fid = fac["id"]
            tgt = i3.compute_target(df, fid)          # 只读复用，逐字不改
            e = np.flatnonzero((tgt[1:] == 1) & (tgt[:-1] == 0)) + 1   # 进场事件 bar
            x = np.flatnonzero((tgt[1:] == 0) & (tgt[:-1] == 1)) + 1   # 出场事件 bar
            # 成交安排在该股「下一根 bar」（T+1，防未来函数）
            ek = e + 1
            keep = ek <= n - 2        # 留出最后一根 bar 供末尾强平，保证 exit_bar > entry_bar
            n_skipped_tail_entry += int((~keep).sum())
            ek = ek[keep]
            esig = e[keep]
            if len(ek):
                a = acc[fid]
                a["bg"].extend(gidx[ek].tolist())
                a["bsy"].extend([si] * len(ek))
                a["bpx"].extend(op[ek].tolist())
                a["bamt"].extend(amt[esig].tolist())   # 信号日成交额（无未来函数）
                a["bbar"].extend(ek.tolist())
                a["bdate"].extend(d[ek].tolist())
            xk = x + 1
            xk = xk[xk <= n - 1]
            if len(xk):
                a = acc[fid]
                a["sg"].extend(gidx[xk].tolist())
                a["ssy"].extend([si] * len(xk))
                a["spx"].extend(op[xk].tolist())
                a["sbar"].extend(xk.tolist())
                a["sdate"].extend(d[xk].tolist())
        if (si + 1) % 1000 == 0:
            print(f"[signals] {si+1}/{len(symbols)} 股 × 15 因子完成", flush=True)

    events: Dict[str, Dict[str, np.ndarray]] = {}
    for fid in fids:
        a = acc[fid]
        buy = np.empty(len(a["bg"]), dtype=BUY_DTYPE)
        buy["g"] = np.asarray(a["bg"], dtype=np.int32)
        buy["sy"] = np.asarray(a["bsy"], dtype=np.int32)
        buy["px"] = np.asarray(a["bpx"], dtype=np.float64)
        buy["amt"] = np.asarray(a["bamt"], dtype=np.float64)
        buy["bar"] = np.asarray(a["bbar"], dtype=np.int32)
        buy["date"] = np.asarray(a["bdate"], dtype=np.int32)
        sell = np.empty(len(a["sg"]), dtype=SELL_DTYPE)
        sell["g"] = np.asarray(a["sg"], dtype=np.int32)
        sell["sy"] = np.asarray(a["ssy"], dtype=np.int32)
        sell["px"] = np.asarray(a["spx"], dtype=np.float64)
        sell["bar"] = np.asarray(a["sbar"], dtype=np.int32)
        sell["date"] = np.asarray(a["sdate"], dtype=np.int32)
        # 排序：买入按 (g 升序, amt 降序, sy 升序)；卖出按 g 升序
        if len(buy):
            buy = buy[np.lexsort((buy["sy"], -buy["amt"], buy["g"]))]
        if len(sell):
            sell = sell[np.argsort(sell["g"], kind="stable")]
        events[fid] = {"buy": buy, "sell": sell}
    print(f"[signals] 15 因子事件累积完成，耗时 {time.perf_counter()-t:.1f}s；"
          f"末尾无法合规平仓而跳过的进场信号 {n_skipped_tail_entry} 个；"
          f"历史不足(<WARM+1)个股 {n_short} 只", flush=True)
    return events, n_skipped_tail_entry, n_short, round(time.perf_counter() - t, 3)


def simulate(ctx, ev_buy, ev_sell, fid: str, N: int, C: float, want_trades: bool):
    """组合引擎：逐交易日推进，维护可用现金 + 分仓持仓 + 逐日盯市权益曲线。

    一次模拟同时维护 primary / conservative 两套 cash & equity（成交时序由 primary 的
    现金校验决定；conservative 为「同成交时序、仅成本金额不同」的对照，如实标注）。
    """
    G = ctx["G"]
    symbols = ctx["symbols"]
    close_ffill = ctx["close_ffill"]
    meta = ctx["meta"]
    cal = ctx["cal"]

    cash_p = C
    cash_c = C
    positions: Dict[int, Dict[str, Any]] = {}
    equity_p = np.empty(G, dtype=np.float64)
    equity_c = np.empty(G, dtype=np.float64)
    pos_count = np.empty(G, dtype=np.int16)
    trades: List[Dict[str, Any]] = []
    t1_violations = 0
    skipped_insufficient_capital = 0
    skipped_parallel_cap = 0
    traded_syms = set()
    cash_util_sum = 0.0
    budget = C / N

    if len(ev_buy):
        bs = np.searchsorted(ev_buy["g"], np.arange(G), side="left")
        be = np.searchsorted(ev_buy["g"], np.arange(G), side="right")
    else:
        bs = be = np.zeros(G, dtype=np.int64)
    if len(ev_sell):
        ss = np.searchsorted(ev_sell["g"], np.arange(G), side="left")
        se = np.searchsorted(ev_sell["g"], np.arange(G), side="right")
    else:
        ss = se = np.zeros(G, dtype=np.int64)

    def do_sell(sy: int, px: float, bar: int, dt: int, g: int, reason: str):
        nonlocal cash_p, cash_c, t1_violations
        pos = positions.pop(sy, None)
        if pos is None:
            return
        sym = symbols[sy]
        if not (px > 0 and np.isfinite(px)):
            px = pos["entry_px"]
        sell_turnover = pos["shares"] * px
        comm_s, trig_s, prop_s = commission_yuan(sell_turnover)
        other_s_p = 0.0
        other_s_c = sell_turnover * EXTRA_SIDE_RATE
        cash_p += sell_turnover - comm_s - other_s_p
        cash_c += sell_turnover - comm_s - other_s_c
        buy_comm = pos["buy_comm"]
        buy_other_c = pos["buy_other_c"]
        cost_p = buy_comm + comm_s
        cost_c = buy_comm + comm_s + buy_other_c + other_s_c
        gross = sell_turnover - pos["buy_turnover"]
        net = gross - cost_p
        net_c = gross - cost_c
        t1ok = bool(bar > pos["entry_bar"] and dt > pos["entry_date"])
        if not t1ok:
            t1_violations += 1
        trades.append({
            "factor_id": fid, "symbol": sym,
            "entry_date": int(pos["entry_date"]), "exit_date": int(dt),
            "entry_bar": int(pos["entry_bar"]), "exit_bar": int(bar),
            "entry_g": int(pos["entry_g"]), "exit_g": int(g),
            "hold_trading_days": int(g - pos["entry_g"]),
            "entry_px": round(pos["entry_px"], 4), "exit_px": round(float(px), 4),
            "shares": int(pos["shares"]),
            "buy_turnover_yuan": round(pos["buy_turnover"], 4),
            "sell_turnover_yuan": round(sell_turnover, 4),
            "gross_yuan": round(gross, 4),
            "cost_items_yuan": {
                "buy_commission_yuan": round(buy_comm, 6),
                "sell_commission_yuan": round(comm_s, 6),
                "buy_commission_proportional_yuan": round(pos["buy_prop"], 6),
                "sell_commission_proportional_yuan": round(prop_s, 6),
                "buy_min_commission_triggered": bool(pos["buy_trig"]),
                "sell_min_commission_triggered": bool(trig_s),
                "transfer_fee_buy_yuan": 0.0, "transfer_fee_sell_yuan": 0.0,
                "slippage_buy_yuan": 0.0, "slippage_sell_yuan": 0.0,
                "stamp_duty_sell_yuan": STAMP_DUTY,
                "cost_roundtrip_yuan_primary": round(cost_p, 6),
                "cost_roundtrip_yuan_conservative": round(cost_c, 6),
            },
            "cost_yuan_primary": round(cost_p, 6),
            "cost_yuan_conservative": round(cost_c, 6),
            "net_yuan_primary": round(net, 6),
            "net_yuan_conservative": round(net_c, 6),
            "exit_reason": reason,
            "t1_ok": t1ok,
        })

    for g in range(G):
        # 1) 当日卖出执行（t 收盘出信号 → t+1 open 成交；先卖释放现金）
        for q in range(int(ss[g]), int(se[g])):
            e = ev_sell[q]
            do_sell(int(e["sy"]), float(e["px"]), int(e["bar"]), int(e["date"]), g, "signal_exit")

        # 2) 当日买入候选 → 选股排序（已在事件数组按 g/amt/sy 预排）→ 分仓
        slots = N - len(positions)
        if slots > 0:
            for q in range(int(bs[g]), int(be[g])):
                if slots <= 0:
                    skipped_parallel_cap += 1
                    break
                e = ev_buy[q]
                sy = int(e["sy"])
                if sy in positions:
                    continue
                px = float(e["px"])
                if not (px > 0 and np.isfinite(px)):
                    continue
                shares = int(budget / px / LOT) * LOT
                if shares < LOT:
                    skipped_insufficient_capital += 1
                    continue
                turnover = shares * px
                comm_b, trig_b, prop_b = commission_yuan(turnover)
                need_p = turnover + comm_b              # primary 现金校验（过户/滑点主口径 0）
                if need_p > cash_p + 1e-9:
                    # 现金不足 → 按可用现金反解手数，仍不足则跳过（如实计数）
                    afford = int((cash_p - MIN_COMM_YUAN) / (px * (1.0 + COMM_RATE)) / LOT) * LOT
                    shares = min(shares, max(afford, 0))
                    if shares < LOT:
                        skipped_insufficient_capital += 1
                        continue
                    turnover = shares * px
                    comm_b, trig_b, prop_b = commission_yuan(turnover)
                    need_p = turnover + comm_b
                    if need_p > cash_p + 1e-9:
                        skipped_insufficient_capital += 1
                        continue
                other_b_c = turnover * EXTRA_SIDE_RATE
                need_c = turnover + comm_b + other_b_c
                cash_p -= need_p
                cash_c -= need_c
                positions[sy] = {
                    "entry_date": int(e["date"]), "entry_bar": int(e["bar"]), "entry_g": g,
                    "entry_px": px, "shares": shares, "buy_turnover": turnover,
                    "buy_comm": comm_b, "buy_trig": bool(trig_b), "buy_prop": prop_b,
                    "buy_other_c": other_b_c,
                }
                traded_syms.add(sy)
                slots -= 1

        # 3) 末尾未平仓强平（最后交易日，按各股最后一根 close；如实计入 end_force_close）
        if g == G - 1 and positions:
            for sy in list(positions.keys()):
                mt = meta[symbols[sy]]
                do_sell(sy, mt["last_close"], mt["last_bar"], mt["last_date"], g, "end_force_close")

        # 4) 逐日盯市权益曲线 = 可用现金 + 持仓市值（严禁逐笔盈亏无条件累加）
        mv = 0.0
        for sy, pos in positions.items():
            p = close_ffill[sy, g]
            if np.isfinite(p):
                mv += pos["shares"] * float(p)
            else:
                mv += pos["shares"] * pos["entry_px"]
        equity_p[g] = cash_p + mv
        equity_c[g] = cash_c + mv
        pos_count[g] = len(positions)
        if equity_p[g] > 0:
            cash_util_sum += 1.0 - cash_p / equity_p[g] if cash_p > 0 else 1.0

    return {
        "equity_p": equity_p, "equity_c": equity_c, "pos_count": pos_count,
        "trades": trades, "t1_violations": t1_violations,
        "skipped_insufficient_capital": skipped_insufficient_capital,
        "skipped_parallel_cap": skipped_parallel_cap,
        "n_traded_syms": len(traded_syms), "cash_util_sum": cash_util_sum,
    }


def make_factor_result(fid: str, fac: Dict[str, str], sim, C: float, N: int,
                       gate: Dict[str, Any], cost_gate: Dict[str, Any],
                       bench_A: Dict[str, Any]) -> Dict[str, Any]:
    """逐因子申报：成交数/持仓数/持仓天数/现金利用率/gross-cost-net/年化/回撤/Calmar/
    夏普/连亏/胜率/4 门槛/笔数/t1/5 元触发；primary 为主口径，conservative 为对照。"""
    trades = sorted(sim["trades"], key=lambda t: (t["exit_g"], t["exit_date"], t["symbol"]))
    n_trades = len(trades)
    gross = float(sum(t["gross_yuan"] for t in trades))
    cost_p = float(sum(t["cost_yuan_primary"] for t in trades))
    cost_c = float(sum(t["cost_yuan_conservative"] for t in trades))
    net_p = gross - cost_p
    net_c = gross - cost_c
    cost_ratio_p = round(cost_p / gross, 6) if gross > 0 else None
    cost_ratio_c = round(cost_c / gross, 6) if gross > 0 else None
    cost_ok_p = bool(gross > 0 and cost_p <= cost_gate["max_cost_as_pct_of_gross"] * gross)
    cost_ok_c = bool(gross > 0 and cost_c <= cost_gate["max_cost_as_pct_of_gross"] * gross)

    met_p = curve_metrics(sim["equity_p"], C, YEARS_28)
    met_c = curve_metrics(sim["equity_c"], C, YEARS_28)

    # 连亏 / 胜率（按 primary net 逐笔）
    max_consec, cur = 0, 0
    for t in trades:
        cur = cur + 1 if t["net_yuan_primary"] <= 0 else 0
        max_consec = max(max_consec, cur)
    wins = sum(1 for t in trades if t["net_yuan_primary"] > 0)

    # 5 元最低佣金触发（按单边成交计；买/卖各自独立）
    trig_sides = 0
    for t in trades:
        d = t["cost_items_yuan"]
        if d.get("buy_min_commission_triggered") is True:
            trig_sides += 1
        if d.get("sell_min_commission_triggered") is True:
            trig_sides += 1
    total_sides = 2 * n_trades
    min_comm = {
        "triggered_sides": trig_sides, "total_sides": total_sides,
        "trigger_ratio": round(trig_sides / total_sides, 6) if total_sides else None,
        "note": "买入 / 卖出各自独立按金额判断：按比例佣金 < 5 元即触发 5 元最低佣金（金额规则）",
    }

    gates = {
        "1_max_drawdown_le_10pct": bool(met_p["max_drawdown"] <= gate["max_drawdown_pct"] / 100.0),
        "2_calmar_ge_1p5": bool(met_p["calmar"] is not None and met_p["calmar"] >= gate["min_calmar"]),
        "3_cost_le_50pct_of_gross": bool(cost_ok_p),
        "4_max_consec_losses_le_5": bool(max_consec <= gate["max_consecutive_losses"]),
        "min_trades_ge_30": bool(n_trades >= MIN_TRADES),
        "sharpe_hard_gate": "不设硬门槛（min_sharpe=null；夏普仅作参考值）",
    }
    decidable = all(gates[k] for k in (
        "1_max_drawdown_le_10pct", "2_calmar_ge_1p5",
        "3_cost_le_50pct_of_gross", "4_max_consec_losses_le_5", "min_trades_ge_30"))

    pc = sim["pos_count"]
    hold_days = [t["hold_trading_days"] for t in trades]
    ann = met_p["annualized_return"]
    b_ann = bench_A["annualized_return"]

    return {
        "factor_id": fid, "name": fac["name"], "category": fac["cat"],
        "fields": fac["fields"], "source_status": fac["source"],
        "cost_model_primary": "primary", "initial_capital_yuan": C, "max_positions": N,
        "n_trades": n_trades,
        "n_stocks_actually_traded": int(sim["n_traded_syms"]),
        "positions_peak": int(pc.max()) if len(pc) else 0,
        "positions_mean": round(float(pc.mean()), 4) if len(pc) else 0.0,
        "avg_hold_trading_days": round(float(np.mean(hold_days)), 4) if hold_days else None,
        "cash_utilization_mean": round(sim["cash_util_sum"] / len(pc), 6) if len(pc) else None,
        "gross_total_yuan": round(gross, 4),
        "cost_total_yuan_primary": round(cost_p, 4),
        "cost_total_yuan_conservative": round(cost_c, 4),
        "cost_gross_ratio_primary": cost_ratio_p,
        "cost_gross_ratio_conservative": cost_ratio_c,
        "cost_ok_primary": cost_ok_p, "cost_ok_conservative": cost_ok_c,
        "net_total_yuan_primary": round(net_p, 4),
        "net_total_yuan_conservative": round(net_c, 4),
        "primary": met_p,
        "conservative": met_c,
        "annualized_return_28y": ann,
        "annualized_defined": met_p["annualized_defined"],
        "sharpe_reference": met_p["sharpe_reference"],
        "max_consec_losses": max_consec,
        "win_rate": round(wins / n_trades, 4) if n_trades else None,
        "min_commission_5yuan": min_comm,
        "benchmark_equal_weight_annualized": b_ann,
        "vs_equal_weight": ("跑赢" if (ann is not None and b_ann is not None and ann > b_ann)
                            else ("跑输" if (ann is not None and b_ann is not None) else "不可比较（年化不可定义）")),
        "t1_violations": int(sim["t1_violations"]),
        "t1_ok": bool(sim["t1_violations"] == 0),
        "skipped_insufficient_capital": int(sim["skipped_insufficient_capital"]),
        "skipped_parallel_cap": int(sim["skipped_parallel_cap"]),
        "gates": gates,
        "decidable_4plus_trades_pass": bool(decidable),
        "passed_final": False,
        "passed_final_reason": "第 5 条极端段按老板 17:34 指令不判（NOT_EVALUATED_BY_BOSS_ORDER），"
                               "判定未闭合 → 非最终达标 / 未含极端段；且本批未复权、结果非可上线依据。",
        "extreme_gate": EXTREME_GATE,
        "adjusted": False,
        "equity_curve_label": "逐日盯市权益曲线 = 可用现金 + 持仓市值（严禁逐笔盈亏无条件累加到固定曲线）",
    }


def main() -> int:
    t0 = time.perf_counter()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    gate = i3.load_gate_from_yaml()
    cost_gate = i3.load_cost_gate_from_yaml()
    pfp = i3.param_fingerprint()
    tf = resolve_timeframe("1d", hours_per_day=4.0, trading_days_per_year=252.0)
    assert tf.bars_per_year == 252.0
    print(f"[gate] {gate} | cost_gate={cost_gate}", flush=True)
    print(f"[param] config sha256-16={pfp['sha256_16']}（应为 31fbb054cddb55af，一字未改）", flush=True)

    ctx = load_context()
    events, n_skipped_tail, n_short, sig_seconds = build_all_factor_events(ctx)
    t_signals = time.perf_counter()

    fids = [f["id"] for f in i3.FACTORS]
    fac_by_id = {f["id"]: f for f in i3.FACTORS}

    # ---- 主档（N=10, C=100 万）逐因子完整结果 + 全量逐笔 ----
    factor_rows: List[Dict[str, Any]] = []
    trade_dump: Dict[str, Any] = {}
    conservative_rows: List[Dict[str, Any]] = []
    for fid in fids:
        ev = events[fid]
        sim = simulate(ctx, ev["buy"], ev["sell"], fid, BASELINE_MAX_POS,
                       BASELINE_CAPITAL, want_trades=True)
        row = make_factor_result(fid, fac_by_id[fid], sim, BASELINE_CAPITAL,
                                 BASELINE_MAX_POS, gate, cost_gate, ctx["bench_A"])
        factor_rows.append(row)
        trade_dump[fid] = sim["trades"]
        conservative_rows.append({
            "factor_id": fid,
            "cost_yuan_primary": row["cost_total_yuan_primary"],
            "cost_yuan_conservative": row["cost_total_yuan_conservative"],
            "net_yuan_primary": row["net_total_yuan_primary"],
            "net_yuan_conservative": row["net_total_yuan_conservative"],
            "cost_ok_primary": row["cost_ok_primary"],
            "cost_ok_conservative": row["cost_ok_conservative"],
            "annualized_primary": row["primary"]["annualized_return"],
            "annualized_conservative": row["conservative"]["annualized_return"],
        })
        print(f"[main] {fid} 成交 {row['n_trades']} 笔 / t1违规 {row['t1_violations']} / "
              f"年化 {row['annualized_return_28y']} / 达标 {row['decidable_4plus_trades_pass']}", flush=True)

    # ---- 敏感性对照（MAX_POSITIONS 5/10/20 × 初始资金 50万/100万/500万，共 9 档；非寻优） ----
    sensitivity: List[Dict[str, Any]] = []
    for N in MAX_POSITIONS_OPTIONS:
        for C in INITIAL_CAPITAL_OPTIONS:
            for fid in fids:
                ev = events[fid]
                sim = simulate(ctx, ev["buy"], ev["sell"], fid, N, C, want_trades=False)
                row = make_factor_result(fid, fac_by_id[fid], sim, C, N, gate,
                                         cost_gate, ctx["bench_A"])
                sensitivity.append({
                    "factor_id": fid, "max_positions": N, "initial_capital_yuan": C,
                    "n_trades": row["n_trades"], "gross_total_yuan": row["gross_total_yuan"],
                    "cost_total_yuan_primary": row["cost_total_yuan_primary"],
                    "net_total_yuan_primary": row["net_total_yuan_primary"],
                    "annualized_return_28y": row["annualized_return_28y"],
                    "annualized_defined": row["annualized_defined"],
                    "cumulative_return_28y": row["primary"]["cumulative_return"],
                    "max_drawdown": row["primary"]["max_drawdown"],
                    "calmar": row["primary"]["calmar"],
                    "sharpe_reference": row["sharpe_reference"],
                    "max_consec_losses": row["max_consec_losses"],
                    "win_rate": row["win_rate"],
                    "positions_peak": row["positions_peak"],
                    "positions_mean": row["positions_mean"],
                    "cash_utilization_mean": row["cash_utilization_mean"],
                    "t1_violations": row["t1_violations"],
                    "decidable_4plus_trades_pass": row["decidable_4plus_trades_pass"],
                    "vs_equal_weight": row["vs_equal_weight"],
                })
            print(f"[sens] N={N} C={int(C)} 完成（15 因子）", flush=True)
    t_sim = time.perf_counter()

    # ---- 门槛计数 + 两个达标数区分 ----
    n_pass_decidable = sum(1 for r in factor_rows if r["decidable_4plus_trades_pass"])
    gate_counts = {
        "1_max_drawdown_le_10pct": sum(1 for r in factor_rows if r["gates"]["1_max_drawdown_le_10pct"]),
        "2_calmar_ge_1p5": sum(1 for r in factor_rows if r["gates"]["2_calmar_ge_1p5"]),
        "3_cost_le_50pct_of_gross": sum(1 for r in factor_rows if r["gates"]["3_cost_le_50pct_of_gross"]),
        "4_max_consec_losses_le_5": sum(1 for r in factor_rows if r["gates"]["4_max_consec_losses_le_5"]),
        "min_trades_ge_30": sum(1 for r in factor_rows if r["gates"]["min_trades_ge_30"]),
    }
    total_t1 = sum(r["t1_violations"] for r in factor_rows) + sum(s["t1_violations"] for s in sensitivity)
    total_trades_main = sum(r["n_trades"] for r in factor_rows)
    trig_total = sum(r["min_commission_5yuan"]["triggered_sides"] for r in factor_rows)
    sides_total = sum(r["min_commission_5yuan"]["total_sides"] for r in factor_rows)

    m = ctx["manifest"]
    result: Dict[str, Any] = {
        "batch": "I-5", "generated_at": datetime.now(timezone.utc).isoformat(),
        "role": "backend_architect", "phase": "阶段二 · 组合回测引擎",
        "meta": {
            "direction": "全市场个股池上的组合层面回测（选股/分仓/资金约束/达到卖出条件才卖），"
                         "不再是逐标的独立成交、盈亏无条件累加",
            "signal_layer": "只读复用 I-3 a_share_technical_factors.py 的 FACTORS / compute_target() "
                            "逐字不改（15 因子、WARM=60、MIN_HOLD_BARS=1）、不铺网格/不寻优/不改 src/",
            "initial_capital_options_yuan": INITIAL_CAPITAL_OPTIONS,
            "max_positions_options": MAX_POSITIONS_OPTIONS,
            "baseline": {"max_positions": BASELINE_MAX_POS, "initial_capital_yuan": BASELINE_CAPITAL,
                         "reason": "100 万量级 / 10 只分仓：A 股 100 股整手下每票 C/N=10 万可覆盖多数股价，"
                                   "且分仓数足以体现『选出符合条件个股、分仓持有多只』"},
            "per_position_cap": "等权 1/N：per_position_budget = INITIAL_CAPITAL / MAX_POSITIONS（固定分仓，"
                                "简单可复现，买入前校验现金 ≥ 成交额 + 费用，同一笔资金不得重复占用）",
            "sort_rule": SORT_RULE,
            "sort_rule_reason": "同一交易日候选数 > 可用仓位时按信号日成交额降序（流动性优先、无未来函数）"
                                "取前 N，同额按代码升序 tie-break；不隐式随机、不脑补信号强度",
            "cash_model": "逐日盯市权益曲线 = 可用现金 + 持仓市值；买入前现金校验；严禁逐笔盈亏无条件累加",
            "execution": "t 收盘出信号 → t+1 open 真实股价成交；100 股整数倍取整；"
                         "T+1（MIN_HOLD_BARS=1，逐笔 exit_date>entry_date、exit_bar>entry_bar）；"
                         "末尾未平仓 end_force_close 如实计入；纯多头/无做空/无配对/无对冲",
            "fee_rule": "佣金金额 = max(成交额 × 0.0005, 5 元)，买/卖各自独立按金额计（金额规则、非费率变更）；"
                        "印花税 0；主口径过户/滑点 0；保守对照加回过户 0.001%/边 + 滑点 5bp/边（按金额计）",
            "fee_forbidden_wording": "严禁写『费率被抬到 0.5%』『等效 50bp』『成本抬高 10 倍』；"
                                     "成本一律以『元』逐项列示；不得说『A 股费率低一个数量级』",
            "conservative_note": "保守对照与 primary 同成交时序（成交由 primary 现金校验决定），"
                                 "仅成本金额不同（加回过户/滑点按金额计）；如实标注、不写成独立重跑结论",
            "extreme_gate": EXTREME_GATE,
            "extreme_gate_note": "老板 17:34「先不判极端行情」，方向改后未撤回 → 沿用；"
                                 "判定 = 4 条硬门槛 + 笔数≥30；n_passed_final 标非最终达标/未含极端段",
            "config_readonly": True,
            "param_fingerprint": pfp["sha256_16"],
            "adjusted": False,
            "years_28": YEARS_28,
        },
        "summary": {
            "n_stocks_in_pool": m["included"]["total_by_code_prefix"],
            "n_stocks_actually_converted": m["coverage"]["n_stocks_actually_converted"],
            "n_stocks_actually_traded": sum(1 for r in factor_rows if r["n_stocks_actually_traded"] > 0),
            "n_stocks_actually_traded_note": "主档 15 因子中至少成交 1 笔的个股数（按因子去重见各因子 n_stocks_actually_traded）",
            "n_bars": ctx["total_bars"],
            "n_trading_days": ctx["G"],
            "n_factors": len(fids),
            "n_sensitivity_runs": len(sensitivity),
            "n_main_trades": total_trades_main,
            "n_pass_decidable_4plus_trades": n_pass_decidable,
            "n_passed_final": 0,
            "n_passed_final_label": "非最终达标 / 未含极端段（第 5 条按老板 17:34 指令不判）",
            "n_passed_final_reason": "极端段不判使判定未闭合，任何因子不能判『最终达标』；"
                                     "严禁把 n_pass_decidable_4plus_trades 冒充 n_passed_final、"
                                     "严禁把 n_passed_final 写成 >0。且本批未复权、结果非可上线依据。",
            "gate_counts_main": gate_counts,
            "t1_violations_total": int(total_t1),
            "min_commission_5yuan_main": {
                "triggered_sides": trig_total, "total_sides": sides_total,
                "trigger_ratio": round(trig_total / sides_total, 6) if sides_total else None,
            },
            "extreme_gate": EXTREME_GATE,
            "data_fingerprint": m["data_fingerprint_sha256_16"],
            "param_fingerprint": pfp["sha256_16"],
            "adjusted": False,
            "not_mixed": True,
            "not_mixed_note": "不与加密 257 组 / I-1 3 组 / I-4 结果混算；0 达标就报 0，排序 ≠ 达标 ≠ 可上线",
            "actual_coverage": {
                "n_stocks_scanned": m["coverage"]["n_stocks_in_pool_by_code_prefix"],
                "n_stocks_converted": m["coverage"]["n_stocks_actually_converted"],
                "n_stocks_excluded_data_quality": m["coverage"]["n_stocks_excluded_data_quality"],
                "n_stocks_insufficient_history": m["coverage"]["n_stocks_insufficient_history"],
                "total_bars": ctx["total_bars"],
                "n_trading_days_union": ctx["G"],
                "library_earliest": m["coverage"]["library_earliest"],
                "library_latest": m["coverage"]["library_latest"],
                "mean_gap_rate": m["coverage"]["mean_gap_rate_since_listing"],
                "downsample_or_batching": m["coverage"]["downsample_or_batching"],
                "signals_computed": "15 因子 × 全部实际转换个股（只读复用 compute_target，无降采样）",
                "tail_entry_signals_skipped": n_skipped_tail,
                "insufficient_history_stocks_in_signal_pass": n_short,
            },
            "runtime": {
                "load_seconds": ctx["load_seconds"],
                "signal_seconds": sig_seconds,
                "simulation_seconds": round(t_sim - t_signals, 3),
                "total_seconds": round(time.perf_counter() - t0, 3),
            },
            "assumptions": [
                "未复权（gbbq 非明文，无法可靠复权；个股技术信号受除权跳空污染，比指数严重得多）",
                "当前在市快照 → 幸存者偏差（🔴，无法用指数规避）",
                ".day 无 ST/停牌/涨跌停标记 → LOCKED 未接入、可能成交在不可成交日",
                "1990-12-19~1998-09-14 约 8 年日线本机缺失",
                "上市不足 WARM+1 根的新股不产生交易（逐只标记、如实计数）",
                "过户费/滑点主口径 0（老板未指定）；保守对照 0.001%/边 + 5bp/边为『待老板确认的假设』",
                "基准 A 为每日等权收益复合指数（每日再平衡口径），未复权污染同样存在",
                "保守对照与 primary 同成交时序，仅成本金额不同（不独立重跑资金约束）",
            ],
        },
        "factors": [{k: v for k, v in r.items()} for r in factor_rows],
        "conservative_comparison": {
            "note": "主档（N=10, C=100 万）primary vs conservative（加回过户 0.001%/边 + 滑点 5bp/边，"
                    "按金额计）逐因子对照；同成交时序，仅成本金额不同",
            "rows": conservative_rows,
        },
        "sensitivity": {
            "note": "MAX_POSITIONS 5/10/20 × 初始资金 50万/100万/500万 = 9 档 × 15 因子 = 135 组；"
                    "**参数敏感性对照，非参数寻优、不挑最优**",
            "is_optimization": False,
            "rows": sensitivity,
        },
        "benchmarks": {
            "equal_weight_all_stocks": ctx["bench_A"],
            "indices": ctx["bench_B"],
            "note": "年化 = (1+累计)^(1/年数) − 1；28 年累计金额不写成收益率或年化；"
                    "指数按各自实际起止年数年化，不套 28.16 年",
        },
        "gates": {
            "frozen": gate,
            "cost_gate": cost_gate,
            "min_sharpe": None,
            "sharpe_hard_gate": "不设硬门槛（min_sharpe=null；夏普仅作参考值）",
            "counts_main": gate_counts,
            "n_pass_decidable_4plus_trades": n_pass_decidable,
            "n_passed_final": 0,
            "extreme_gate": EXTREME_GATE,
            "forbidden_confirmed": {
                "no_pair": True, "no_short": True, "no_hedge": True, "no_spread": True,
                "no_zscore_spread": True, "no_hedge_ratio_h": True, "no_etf_short_leg": True,
                "no_index_as_trade_price": True, "no_unconditional_pnl_accumulation": True,
            },
        },
        "compliance": {
            "local_readonly": True, "no_client_launch": True, "no_network_quotes": True,
            "no_registry_change": True, "src_unchanged": True,
            "config_unchanged": True, "config_sha256_16": pfp["sha256_16"],
            "no_bat_gitignore_env_change": True, "no_git_commit": True,
            "no_gate_relax": True, "no_live_trading": True, "no_token_key": True,
            "no_paid_source": True, "no_api_key": True,
            "old_scripts_preserved": True, "old_parquet_preserved": True,
            "no_g6_3_g5_2_rush": True, "no_beautify": True,
            "single_execution_body": True,
            "single_execution_note": "本环境为单一执行体、无独立 subagent 会话，"
                                     "两阶段按 data_engineer / backend_architect 两份 soul 纪律分别落地，"
                                     "非两角色各自独立会话",
            "no_429": True, "no_429_note": "本批无联网 / 无 API 调用，未撞 429 限流，无需重试，如实登记",
        },
        "limits": m["limits_red"],
    }

    jp = OUT_DIR / "a_share_portfolio_factors.json"
    jp.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    tp = OUT_DIR / "a_share_portfolio_factors_trades.json"
    tp.write_text(json.dumps({
        "batch": "I-5",
        "note": "主档（MAX_POSITIONS=10, INITIAL_CAPITAL=1000000）15 因子 primary 全量逐笔；"
                "conservative 同成交时序仅成本金额不同（见主 json cost_items_yuan 双口径）、"
                "其它 8 档敏感性仅汇总、未重复落盘逐笔。",
        "primary": trade_dump,
    }, ensure_ascii=False, default=str), encoding="utf-8")

    print(f"\n[OUT] {jp}", flush=True)
    print(f"[OUT] {tp}", flush=True)
    print(f"[SUMMARY] n_stocks_in_pool={result['summary']['n_stocks_in_pool']} "
          f"n_bars={result['summary']['n_bars']} n_factors={result['summary']['n_factors']} "
          f"n_sensitivity_runs={result['summary']['n_sensitivity_runs']} "
          f"n_pass_decidable_4plus_trades={n_pass_decidable} n_passed_final=0（非最终达标）", flush=True)
    print(f"[T1] violations_total={total_t1} | [MIN5] {trig_total}/{sides_total} 边触发", flush=True)
    print(f"[BENCH] 等权年化={ctx['bench_A']['annualized_return']} | "
          f"sh000001年化={ctx['bench_B'].get('sh000001', {}).get('annualized_return')}", flush=True)
    print(f"[RUNTIME] {result['summary']['runtime']}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
