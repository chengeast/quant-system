"""第三轮专家辩论配套 · 参数扫描实测（phase5-p3-param-debate）

背景：P0-P3 工程缺陷修复后，程序逻辑已正确（逐笔 pnl 非 0、净值与逐笔自洽、
M8/M9 验收全绿），但 3 个保留币对 reg_rate 全线 <12%、成本吞毛利 10×。
用户判断"应再让 3 金融专家调整测试参数"。

本脚本不预设结论，按用户原则「以计算结果为准，不凭经验」用三层实测回答：

  A. 信号层网格：entry_sigma × exit_sigma × timeout 倍数 × 3 尺度 × 3 币对
     → reg_rate、进信号数（样本量）。向量化，秒级。
  B. 完整 M8 回测：对 A 层 reg_rate 最高且样本充足的组合验证
     净值 / 夏普 / 回撤 / 逐笔毛利 / 成本 / 成本毛利比。
  C. 结构性验证：
     - 仓位放大敏感性（验证成本/毛利比是否与仓位线性无关）
     - 升尺度 1h→4h→1d（成本-频率权衡方向：升尺度 or 降尺度）
     - 成本敏感性（费率需降到多少才可执行）

用法：
  python backtest/m8_param_sweep.py grid     # 只跑 A 层，产出 param_sweep_grid.csv
  python backtest/m8_param_sweep.py verify   # 跑 B/C 层，读 grid 结果
  python backtest/m8_param_sweep.py all      # 全部
"""
from __future__ import annotations

import sys
import time
import warnings
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
warnings.filterwarnings("ignore")

import numpy as np
import pandas as pd

from src.backtest.engine import Backtester, BacktestConfig
from src.core.timeframe import resolve_timeframe
from src.signal_layer.mean_reversion_signals import MeanReversionSignals

CACHE = ROOT / "data" / "cache"
OUT = ROOT / "backtest" / "output"
OUT.mkdir(parents=True, exist_ok=True)

PAIRS = ["XRP/DOGE", "SOL/DOGE", "BTC/ETH"]
SCALES = [1, 4, 24]                 # 聚合根数：1h / 4h / 1d
ENTRY_S = [2.5, 3.0, 3.5, 4.0, 5.0]
EXIT_S = [0.5, 1.0, 1.5]
TIMEOUT_F = [1.2, 2.0, 4.0, 8.0]
HL_RANGE = (368, 856)
BASE_EQUITY = 10_000.0
MIN_SAMPLES = 30                    # 冻结门槛：样本 >30 笔

_SIG_CACHE: dict = {}
_BM_CACHE: dict = {}


# ----------------------------------------------------------------------
# 数据
# ----------------------------------------------------------------------
def load_aligned(pair: str, scale: int) -> pd.DataFrame:
    """读 1h 双腿并按 scale 根聚合成更长尺度（ts 取组内首根，close 取末根）。"""
    a, b = pair.split("/")
    pa = (pd.read_parquet(CACHE / f"{a}_USDT_1h_410d.parquet")[["ts", "close"]]
          .rename(columns={"close": "close_a"}))
    pb = (pd.read_parquet(CACHE / f"{b}_USDT_1h_410d.parquet")[["ts", "close"]]
          .rename(columns={"close": "close_b"}))
    al = pa.merge(pb, on="ts").sort_values("ts").reset_index(drop=True)
    if scale > 1:
        n = len(al) // scale * scale
        al = al.iloc[:n].reset_index(drop=True)
        grp = np.arange(n) // scale
        al = (al.groupby(grp)
              .agg(ts=("ts", "first"),
                   close_a=("close_a", "last"),
                   close_b=("close_b", "last"))
              .reset_index(drop=True))
    return al


def load_benchmark(scale: int) -> pd.DataFrame:
    bm = pd.read_parquet(CACHE / "BTC_USDT_1h_410d.parquet")[["ts", "close"]]
    if scale > 1:
        n = len(bm) // scale * scale
        bm = bm.iloc[:n].reset_index(drop=True)
        grp = np.arange(n) // scale
        bm = (bm.groupby(grp)
              .agg(ts=("ts", "first"), close=("close", "last"))
              .reset_index(drop=True))
    return bm


def get_sig(pair: str, scale: int, hedge_window: int = 60) -> pd.DataFrame:
    key = (pair, scale, hedge_window)
    if key not in _SIG_CACHE:
        al = load_aligned(pair, scale)
        calc = MeanReversionSignals(hedge_window=hedge_window,
                                    entry_sigma=3.0, exit_sigma=0.5,
                                    hl_range=HL_RANGE)
        sig = calc.compute_signals(al, window=hedge_window)
        sig["ts_ms"] = sig["ts"].astype("int64") * 1000
        _SIG_CACHE[key] = sig
    return _SIG_CACHE[key]


def get_bm(scale: int) -> pd.DataFrame:
    if scale not in _BM_CACHE:
        _BM_CACHE[scale] = load_benchmark(scale)
    return _BM_CACHE[scale]


def scale_label(scale: int) -> str:
    return {1: "1h", 4: "4h", 24: "1d"}[scale]


# ----------------------------------------------------------------------
# A 层：信号层网格（向量化 next-exit 查表，O(n)/exit_sigma）
# ----------------------------------------------------------------------
def grid_one(pair: str, scale: int, hedge_window: int = 60) -> pd.DataFrame:
    sig = get_sig(pair, scale, hedge_window)
    z = sig["zscore"].to_numpy(float)
    st = sig["state"].to_numpy()
    hl = sig["half_life"].to_numpy(float)
    valid = np.isin(st, ["OK", "DEGRADED"]) & np.isfinite(z)
    hv = hl[np.isfinite(hl) & (hl > 0)]
    median_h = float(np.median(hv)) if len(hv) else 8.0
    bars_per_hour = resolve_timeframe(scale_label(scale)).bars_per_hour
    n = len(z)
    rows = []

    for exit_s in EXIT_S:
        band = (np.abs(z) <= exit_s) & np.isfinite(z)
        nxt = np.full(n, n, dtype=np.int64)
        pos = n
        for i in range(n - 1, -1, -1):
            nxt[i] = pos
            if band[i]:
                pos = i
        for entry_s in ENTRY_S:
            idx = np.flatnonzero(valid & (np.abs(z) >= entry_s))
            n_entries = int(len(idx))
            dist = nxt[idx] - idx
            for f in TIMEOUT_F:
                timeout_bars = max(3.0 * bars_per_hour, f * median_h)
                reg = int(np.sum(dist <= timeout_bars))
                div = n_entries - reg
                rows.append({
                    "pair": pair, "scale": scale_label(scale),
                    "hedge_window": hedge_window,
                    "entry_sigma": entry_s, "exit_sigma": exit_s,
                    "timeout_factor": f, "timeout_bars": round(timeout_bars, 2),
                    "median_h": round(median_h, 2),
                    "n_entries": n_entries, "reg": reg, "div": div,
                    "reg_rate": round(reg / n_entries, 4) if n_entries else None,
                    "sample_ok": n_entries >= MIN_SAMPLES,
                    "bars": n,
                })
    return pd.DataFrame(rows)


def run_grid() -> pd.DataFrame:
    out = []
    for scale in SCALES:
        for pair in PAIRS:
            t0 = time.time()
            df = grid_one(pair, scale)
            out.append(df)
            best = df[df["sample_ok"]].nlargest(1, "reg_rate")
            if len(best):
                r = best.iloc[0]
                print(f"[grid] {pair:9s} {scale_label(scale):2s} bars={r['bars']:5d} "
                      f"medianH={r['median_h']:6.2f} best reg_rate={r['reg_rate']*100:5.1f}% "
                      f"@ {r['entry_sigma']}σ/{r['exit_sigma']}σ/{r['timeout_factor']}H "
                      f"n={r['n_entries']} ({time.time()-t0:.1f}s)", flush=True)
            else:
                print(f"[grid] {pair:9s} {scale_label(scale):2s} 样本不足（无 n_entries>=30 组合）",
                      flush=True)
    res = pd.concat(out, ignore_index=True)
    path = OUT / "param_sweep_grid.csv"
    res.to_csv(path, index=False)
    print(f"-> {path}  ({len(res)} 组)", flush=True)
    return res


# ----------------------------------------------------------------------
# B 层：完整 M8 回测
# ----------------------------------------------------------------------
def run_bt(pair: str, scale: int, entry_s: float, exit_s: float,
           timeout_f: float, hedge_window: int = 60,
           leg_equity_pct: float = 0.05,
           taker_fee_pct: float = 0.001, slippage_bps: float = 4.0,
           tag: str = "") -> dict:
    a, b = pair.split("/")
    sig = get_sig(pair, scale, hedge_window)
    bm = get_bm(scale)
    cfg = BacktestConfig(
        # 读 1h parquet 后按 scale 根聚合成 4h/1d；回测周期与源数据周期分开声明
        interval=scale_label(scale),
        data_interval="1h",
        leg_a_file=f"{a}_USDT_1h_410d.parquet",
        leg_b_file=f"{b}_USDT_1h_410d.parquet",
        benchmark_file="BTC_USDT_1h_410d.parquet",
        hedge_window=hedge_window,
        entry_sigma=entry_s, exit_sigma=exit_s,
        stop_loss_sigma=2.0, timeout_hours=3.0,
        timeout_half_life_factor=timeout_f,
        leg_equity_pct=leg_equity_pct,
        max_single_exposure=max(0.05, leg_equity_pct),
        max_total_exposure=max(0.20, leg_equity_pct * 4),
        taker_fee_pct=taker_fee_pct, slippage_bps=slippage_bps,
        hl_range=HL_RANGE,
    )
    t0 = time.time()
    bt = Backtester(cfg)
    res = bt.run(sig=sig, benchmark=bm)
    m = res.metrics
    pnls = [t.get("pnl", 0.0) for t in res.trades]
    gross = float(np.sum(pnls))
    cost = float(res.cost_paid)
    n_trades = int(len(res.trades))
    rec = {
        "tag": tag, "pair": pair, "scale": scale_label(scale),
        "entry_sigma": entry_s, "exit_sigma": exit_s,
        "timeout_factor": timeout_f, "hedge_window": hedge_window,
        "leg_equity_pct": leg_equity_pct,
        "taker_fee_pct": taker_fee_pct, "slippage_bps": slippage_bps,
        "n_trades": n_trades,
        "gross_total": round(gross, 2),
        "cost_total": round(cost, 2),
        "cost_gross_ratio": round(cost / gross, 2) if gross > 0 else None,
        "gross_per_trade": round(gross / n_trades, 4) if n_trades else None,
        "cost_per_trade": round(cost / n_trades, 4) if n_trades else None,
        "net_peak": round(float(m.get("total_return", 0.0)), 6),
        "max_dd": round(float(m.get("max_drawdown", 0.0)), 6),
        "sharpe": round(float(m.get("sharpe", 0.0)), 4),
        "calmar": round(float(m.get("calmar", 0.0)), 4),
        "max_consec_losses": int(m.get("max_consecutive_losses", 0)),
        "cost_executable": bool(m.get("cost_executable", False)),
        "extreme_survived": bool(m.get("extreme_survived", False)),
        "seconds": round(time.time() - t0, 1),
    }
    print(f"[bt] {tag:22s} {pair:9s} {scale_label(scale):2s} "
          f"e={entry_s}/x={exit_s}/f={timeout_f} pct={leg_equity_pct} "
          f"trades={n_trades:4d} gross={gross:+9.2f} cost={cost:8.2f} "
          f"net={rec['net_peak']*100:+6.2f}% sharpe={rec['sharpe']:7.2f} "
          f"({rec['seconds']}s)", flush=True)
    return rec


def pick_combos(grid: pd.DataFrame, pair: str, scale: int,
                full: bool = True) -> list:
    """覆盖 3 位专家各自的参数假设，而不是只挑 reg_rate 数字最大的组合。

    reg_rate 在放宽 exit 带 / 拉长 timeout 后会饱和到 100%（指标作弊），
    因此必须让完整 M8 回测判定真实毛利与成本，不能只按 reg_rate 选参数。
    """
    combos = [(3.0, 0.5, 1.2, "frozen_baseline")]
    if full:
        combos += [
            (5.0, 0.5, 1.2, "high_entry_5s"),      # A：提高进场 σ，降频提单笔毛利
            (3.0, 1.5, 1.2, "wide_exit_1p5s"),    # B：放宽离场，压连亏
            (3.0, 0.5, 4.0, "long_hold_4H"),      # A：拉长持有，提回归率
            (3.0, 0.5, 8.0, "long_hold_8H"),
            (3.0, 0.5, 16.0, "long_hold_16H"),    # P4 止损修复后再向更长窗口探边界
            (4.0, 0.5, 8.0, "entry4s_longhold"),
            (5.0, 0.5, 8.0, "entry5s_longhold"),
        ]
    df = grid[(grid["pair"] == pair) & (grid["scale"] == scale_label(scale))
              & grid["sample_ok"] & grid["reg_rate"].notna()]
    top = df.nlargest(1, "reg_rate")
    if len(top):
        r = top.iloc[0]
        combos.append((float(r.entry_sigma), float(r.exit_sigma),
                       float(r.timeout_factor), "grid_top_reg"))
    seen, uniq = set(), []
    for c in combos:
        key = c[:3]
        if key not in seen:
            seen.add(key)
            uniq.append(c)
    return uniq


def run_verify() -> pd.DataFrame:
    grid_path = OUT / "param_sweep_grid.csv"
    if not grid_path.exists():
        print("缺少 grid 结果，先执行：python backtest/m8_param_sweep.py grid")
        return pd.DataFrame()
    grid = pd.read_csv(grid_path)
    rows = []

    # B1：主币对 XRP/DOGE × 3 尺度。1h 跑全假设集，4h/1d 跑基准 + grid 最优（省时）
    for scale in SCALES:
        for e, x, f, tag in pick_combos(grid, "XRP/DOGE", scale, full=(scale == 1)):
            rows.append(run_bt("XRP/DOGE", scale, e, x, f, tag=tag))

    # B2：最优 1h 组合推广到另外 2 个币对
    best1h = (grid[(grid["pair"] == "XRP/DOGE") & (grid["scale"] == "1h")
                   & grid["sample_ok"] & grid["reg_rate"].notna()]
              .nlargest(1, "reg_rate"))
    if len(best1h):
        r = best1h.iloc[0]
        for pair in ["SOL/DOGE", "BTC/ETH"]:
            rows.append(run_bt(pair, 1, float(r.entry_sigma), float(r.exit_sigma),
                               float(r.timeout_factor), tag="best_1h_other_pair"))

    # C1：仓位放大敏感性（验证成本/毛利比与仓位线性无关）
    for pct in [0.05, 0.10, 0.20, 0.40]:
        rows.append(run_bt("XRP/DOGE", 1, 3.0, 0.5, 1.2,
                           leg_equity_pct=pct, tag=f"position_pct_{pct}"))

    # C2：成本敏感性（费率降到多少才可执行）
    for fee, slip, t in [(0.001, 4.0, "cost_base"),
                         (0.0002, 3.0, "cost_vip"),
                         (0.00005, 3.0, "cost_near_zero")]:
        rows.append(run_bt("XRP/DOGE", 1, 3.0, 0.5, 1.2,
                           taker_fee_pct=fee, slippage_bps=slip, tag=t))

    res = pd.DataFrame(rows)
    path = OUT / "param_sweep_verify.csv"
    res.to_csv(path, index=False)
    print(f"-> {path}  ({len(res)} 组回测)", flush=True)
    return res


# ----------------------------------------------------------------------
# 汇总判定
# ----------------------------------------------------------------------
def summarize(verify: pd.DataFrame) -> None:
    if verify.empty:
        return
    print("\n" + "=" * 92)
    print("第三轮调参实测 · 门槛判定（成本≤毛利35% / 夏普≥2 / Calmar≥1.5 / 回撤≤10% / 连亏≤5 / 样本>30）")
    print("=" * 92)
    ok = verify[
        (verify["net_peak"] > 0)
        & (verify["sharpe"] >= 2.0)
        & (verify["calmar"] >= 1.5)
        & (verify["max_dd"] <= 0.10)
        & (verify["max_consec_losses"] <= 5)
        & (verify["n_trades"] > MIN_SAMPLES)
        & (verify["cost_executable"] == True)
    ]
    print(f"  全部通过的组合数：{len(ok)} / {len(verify)}")
    print(f"  最高 reg_rate 组合实测：")
    for _, r in verify.nlargest(3, "gross_total").iterrows():
        print(f"    {r['tag']:22s} {r['pair']:9s} {r['scale']:2s} "
              f"gross={r['gross_total']:+8.2f} cost={r['cost_total']:8.2f} "
              f"net={r['net_peak']*100:+6.2f}% sharpe={r['sharpe']:7.2f}")
    print("\n  仓位放大敏感性（成本/毛利比是否随仓位变化）：")
    pos = verify[verify["tag"].astype(str).str.startswith("position_pct_")]
    for _, r in pos.iterrows():
        print(f"    pct={r['leg_equity_pct']:.2f} gross={r['gross_total']:+9.2f} "
              f"cost={r['cost_total']:8.2f} ratio={r['cost_gross_ratio']} "
              f"net={r['net_peak']*100:+6.2f}%")
    print("\n  成本敏感性：")
    cost = verify[verify["tag"].astype(str).str.startswith("cost_")]
    for _, r in cost.iterrows():
        print(f"    {r['tag']:16s} fee={r['taker_fee_pct']:.5f} slip={r['slippage_bps']:.1f}bp "
              f"gross={r['gross_total']:+8.2f} cost={r['cost_total']:8.2f} "
              f"net={r['net_peak']*100:+6.2f}% sharpe={r['sharpe']:7.2f}")

    # 反推：需要把成本压到多少才满足 cost <= 0.35*gross
    base = verify[verify["tag"] == "cost_base"]
    if len(base):
        r = base.iloc[0]
        if r["gross_total"] and r["gross_total"] > 0 and r["cost_total"] > 0:
            k = 0.35 * r["gross_total"] / r["cost_total"]
            print(f"\n  可执行所需成本倍数 k = 0.35×毛利/成本 = {k:.4f}")
            print(f"    → taker 需从 0.1% 降到约 {0.001*k*100:.5f}%（滑点同比例压缩）")
            print(f"    → 即当前成本模型的 {k*100:.2f}%，Gate.io 现货最低档远达不到")


def main() -> int:
    phase = sys.argv[1] if len(sys.argv) > 1 else "all"
    grid = pd.DataFrame()
    if phase in ("grid", "all"):
        grid = run_grid()
    verify = pd.DataFrame()
    if phase in ("verify", "all"):
        verify = run_verify()
    elif phase == "summarize":
        p = OUT / "param_sweep_verify.csv"
        verify = pd.read_csv(p) if p.exists() else pd.DataFrame()
    if not verify.empty and phase != "grid":
        summarize(verify)
    return 0


if __name__ == "__main__":
    sys.exit(main())
