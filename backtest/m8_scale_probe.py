"""第四轮 · 降尺度可行性试跑（1h → 15m / 30m）

用户问题：1h 统计套利已被第三轮辩论 3:0 判死，改成 15m 或 30m 再试是否可行？
本脚本不预设结论，按「以计算结果为准、不凭经验」用真实 Gate.io 数据实测。

必须如实申报的三条结构性约束：
1. Gate.io 只保留最近 10000 根 K 线（请求更早区间直接 400
   "Candlestick too long ago. Maximum 10000 points ago are allowed"）。
   故 15m 最多 ~104 天、30m 最多 ~208 天，日历窗口比 1h 的 410 天短 4×/2×。
   冻结门槛的极端段 2026-02-04~02-10 **不在窗口内**，本次记 N/A（不可评估），
   绝不把 extreme_pnl=0 当作「不亏 ✅ 通过」。
2. 尺度参数必须显式改，否则夏普/Calmar/超时全是错的：
   bars_per_hour = 1/scale (15m=4, 30m=2)、annualize_factor = √(365×24/scale)；
   engine.compute_performance() 的 ann_return 已修为与 annualize_factor 同源。
3. 冻结参数、成本模型、回撤/夏普/Calmar/连亏/成本门槛一律不放宽。
   hl_range 在 M3 中仅赋值、不参与状态判定（已核实），故保留 (368,856) 仅作留痕。

用法：
  python backtest/m8_scale_probe.py fetch   # 拉 5 币 × {15m,30m}，落 data/cache
  python backtest/m8_scale_probe.py probe   # 1h/15m/30m × 3 币对 × 2 参数组对照
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
from src.data_layer.gate_kline_fetcher import GateKLineFetcher
from src.signal_layer.mean_reversion_signals import MeanReversionSignals

CACHE = ROOT / "data" / "cache"
OUT = ROOT / "backtest" / "output"
OUT.mkdir(parents=True, exist_ok=True)

PAIRS = ["XRP/DOGE", "SOL/DOGE", "BTC/ETH"]          # P3 保留的 3 个币对
SYMS = ["XRP", "DOGE", "SOL", "BTC", "ETH"]          # 含 BTC 基准
SCALES = {"1h": 1.0, "1h_95d": 1.0, "1h_195d": 1.0,
          "15m": 0.25, "30m": 0.5}                     # 相对 1h 的 bar 长度
LOOKBACK = {"1h": 410, "15m": 95, "30m": 195}          # 实测可达（留余量）
# 同窗口 1h 对照：把 410 天 1h 数据截到 15m/30m 各自的时间范围再跑一遍。
# 15m/30m 的日历窗口比 1h 短，不同行情段不能直接比绝对收益；
# 加这组对照可把「尺度效应」与「窗口效应」分离开。
REF_FOR_WINDOW = {"1h_95d": "15m", "1h_195d": "30m"}
HL_RANGE = (368, 856)

# 冻结门槛的极端段（保持原值，不放宽；新窗口内不可评估）
EXTREME_START = 1770163200                            # 2026-02-04 UTC
EXTREME_END = 1770681600                              # 2026-02-10 UTC

# (标签, 进场σ, 离场σ, 超时倍数)：冻结组 + 第三轮唯一毛利覆盖成本的参数组
COMBOS = [
    ("frozen", 3.0, 0.5, 1.2),
    ("alt_5s_1s_8H", 5.0, 1.0, 8.0),
]

_SIG: dict = {}
_BM: dict = {}


def _file(sym: str, iv: str) -> Path:
    if iv.startswith("1h"):
        return CACHE / f"{sym}_USDT_1h_410d.parquet"
    return CACHE / f"{sym}_USDT_{iv}_{LOOKBACK[iv]}d.parquet"


def _window(iv: str) -> tuple:
    """同窗口对照用的 [起, 止] 秒，取自对应降尺度数据的真实时间范围。"""
    ref = REF_FOR_WINDOW[iv]
    d = pd.read_parquet(_file("BTC", ref))[["ts"]]
    return int(d["ts"].min()), int(d["ts"].max())


# ---------------------------------------------------------------- 数据
def fetch() -> None:
    """拉真实 15m/30m 行情（只读 KEY，签名通道），落 parquet。"""
    f = GateKLineFetcher(cache_dir=str(CACHE), use_cache=False)
    for iv in ("15m", "30m"):
        for sym in SYMS:
            pair = f"{sym}_USDT"
            t0 = time.time()
            df = f.fetch_kline(pair, iv, lookback_days=LOOKBACK[iv])
            p = _file(sym, iv)
            f.to_parquet(df, str(p))
            lo = pd.to_datetime(df["ts"].min(), unit="s").strftime("%Y-%m-%d")
            hi = pd.to_datetime(df["ts"].max(), unit="s").strftime("%Y-%m-%d")
            print(f"[fetch] {pair:10s} {iv:3s} rows={len(df):5d} {lo}~{hi} "
                  f"-> {p.name} ({time.time()-t0:.1f}s)", flush=True)


def aligned(pair: str, iv: str) -> pd.DataFrame:
    a, b = pair.split("/")
    pa = (pd.read_parquet(_file(a, iv))[["ts", "close"]]
          .rename(columns={"close": "close_a"}))
    pb = (pd.read_parquet(_file(b, iv))[["ts", "close"]]
          .rename(columns={"close": "close_b"}))
    al = pa.merge(pb, on="ts").sort_values("ts").reset_index(drop=True)
    if iv in REF_FOR_WINDOW:
        lo, hi = _window(iv)
        al = al[(al["ts"] >= lo) & (al["ts"] <= hi)].reset_index(drop=True)
    return al


def benchmark(iv: str) -> pd.DataFrame:
    if iv not in _BM:
        d = pd.read_parquet(_file("BTC", iv))[["ts", "close"]]
        if iv in REF_FOR_WINDOW:
            lo, hi = _window(iv)
            d = d[(d["ts"] >= lo) & (d["ts"] <= hi)].reset_index(drop=True)
        _BM[iv] = d
    return _BM[iv]


def get_sig(pair: str, iv: str) -> pd.DataFrame:
    key = (pair, iv)
    if key not in _SIG:
        al = aligned(pair, iv)
        calc = MeanReversionSignals(hedge_window=60, entry_sigma=3.0,
                                    exit_sigma=0.5, hl_range=HL_RANGE)
        sig = calc.compute_signals(al, window=60)
        sig["ts_ms"] = sig["ts"].astype("int64") * 1000
        _SIG[key] = sig
    return _SIG[key]


# ---------------------------------------------------------------- 回测
def run_one(pair: str, iv: str, tag: str, entry_s: float,
            exit_s: float, timeout_f: float, sig: pd.DataFrame,
            bm: pd.DataFrame, meta: dict) -> dict:
    a, b = pair.split("/")
    scale = SCALES[iv]
    cfg = BacktestConfig(
        leg_a_file=_file(a, iv).name,
        leg_b_file=_file(b, iv).name,
        benchmark_file=_file("BTC", iv).name,
        hedge_window=60,
        entry_sigma=entry_s, exit_sigma=exit_s,
        stop_loss_sigma=2.0, timeout_hours=3.0,
        timeout_half_life_factor=timeout_f,
        hl_range=HL_RANGE,
        bars_per_hour=1.0 / scale,
        annualize_factor=float(np.sqrt(365.0 * 24.0 / scale)),
        leg_equity_pct=0.05,
        taker_fee_pct=0.001, slippage_bps=4.0,
        extreme_window_start=EXTREME_START,
        extreme_window_end=EXTREME_END,
    )
    t0 = time.time()
    res = Backtester(cfg).run(sig=sig, benchmark=bm)
    m = res.metrics
    pnls = [float(t.get("pnl", 0.0)) for t in res.trades]
    gross = float(np.sum(pnls))
    cost = float(res.cost_paid)
    n = int(len(res.trades))
    rec = {
        "tag": tag, "pair": pair, "scale": iv, "bars": meta["bars"],
        "span_days": meta["span_days"],
        "median_h": meta["median_h"], "n_entries_3s": meta["n_entries_3s"],
        "invalid_rate": meta["invalid_rate"], "degraded_rate": meta["degraded_rate"],
        "entry_sigma": entry_s, "exit_sigma": exit_s, "timeout_factor": timeout_f,
        "bars_per_hour": round(1.0 / scale, 2),
        "annualize_factor": round(float(np.sqrt(365.0 * 24.0 / scale)), 2),
        "n_trades": n,
        "gross_total": round(gross, 2), "cost_total": round(cost, 2),
        "gross_per_trade": round(gross / n, 4) if n else None,
        "cost_per_trade": round(cost / n, 4) if n else None,
        "cost_gross_ratio": round(cost / gross, 2) if gross > 0 else None,
        "net_peak": round(float(m.get("total_return", 0.0)), 6),
        "max_dd": round(float(m.get("max_drawdown", 0.0)), 6),
        "sharpe": round(float(m.get("sharpe", 0.0)), 4),
        "calmar": round(float(m.get("calmar", 0.0)), 4),
        "max_consec_losses": int(m.get("max_consecutive_losses", 0)),
        "cost_executable": bool(m.get("cost_executable", False)),
        "btc_benchmark": round(float(m.get("benchmark_total_return", 0.0)), 6),
        # 极端段：窗口不含 2026-02 → 不可评估，绝不视为通过
        "extreme_evaluable": meta["extreme_evaluable"],
        "extreme_survived": (bool(m.get("extreme_survived", False))
                             if meta["extreme_evaluable"] else None),
        "seconds": round(time.time() - t0, 1),
    }
    print(f"[bt] {pair:9s} {iv:3s} {tag:14s} bars={meta['bars']:5d} "
          f"medianH={meta['median_h']:6.1f} trades={n:4d} "
          f"gross={gross:+9.2f} cost={cost:8.2f} "
          f"net={rec['net_peak']*100:+6.2f}% sharpe={rec['sharpe']:7.2f} "
          f"({rec['seconds']}s)", flush=True)
    return rec


def probe(scales: tuple = ("1h", "15m", "30m", "1h_95d", "1h_195d")) -> None:
    rows = []
    for iv in scales:
        # 同窗口 1h 对照只跑冻结参数（核心对照），控制运行时间
        combos = COMBOS if iv not in REF_FOR_WINDOW else COMBOS[:1]
        for pair in PAIRS:
            al = aligned(pair, iv)
            sig = get_sig(pair, iv)
            hv = sig["half_life"].dropna()
            hv = hv[hv > 0]
            z = sig["zscore"].to_numpy(float)
            st = sig["state"].to_numpy()
            valid = np.isin(st, ["OK", "DEGRADED"]) & np.isfinite(z)
            meta = {
                "bars": int(len(al)),
                "span_days": round((al["ts"].max() - al["ts"].min()) / 86400.0, 1),
                "median_h": round(float(np.median(hv)), 2) if len(hv) else None,
                "n_entries_3s": int(np.sum(valid & (np.abs(z) >= 3.0))),
                "invalid_rate": round(float(np.mean(st == "INVALID")), 4),
                "degraded_rate": round(float(np.mean(st == "DEGRADED")), 4),
                "extreme_evaluable": bool(((al["ts"] >= EXTREME_START)
                                           & (al["ts"] <= EXTREME_END)).any()),
            }
            bm = benchmark(iv)
            for tag, es, xs, tf in combos:
                rows.append(run_one(pair, iv, tag, es, xs, tf, sig, bm, meta))

    df = pd.DataFrame(rows)
    path = OUT / "scale_probe_15m_30m.csv"
    # 部分尺度重跑时与已有结果按 scale 去重合并，避免覆盖全量结果
    if path.exists() and len(scales) < 5:
        old = pd.read_csv(path)
        old = old[~old["scale"].isin(scales)]
        df = pd.concat([old, df], ignore_index=True)
    df.to_csv(path, index=False)

    print("\n" + "=" * 100)
    print("降尺度对照 · 门槛判定（极端段 2026-02 不在 15m/30m 窗口内 → N/A，不计通过）")
    print("=" * 100)
    hdr = (f"{'scale':4s} {'pair':9s} {'tag':14s} {'trades':>6s} "
           f"{'gross':>9s} {'cost':>9s} {'g/trade':>9s} {'c/trade':>9s} "
           f"{'net':>8s} {'sharpe':>8s} {'calmar':>8s} {'costOK':>7s}")
    print(hdr)
    for _, r in df.iterrows():
        g = r["gross_per_trade"] if pd.notna(r["gross_per_trade"]) else float("nan")
        c = r["cost_per_trade"] if pd.notna(r["cost_per_trade"]) else float("nan")
        print(f"{r['scale']:4s} {r['pair']:9s} {r['tag']:14s} "
              f"{r['n_trades']:6d} {r['gross_total']:+9.2f} {r['cost_total']:9.2f} "
              f"{g:+9.3f} {c:9.3f} {r['net_peak']*100:+7.2f}% "
              f"{r['sharpe']:8.3f} {r['calmar']:8.3f} "
              f"{str(r['cost_executable']):>7s}")
    print("\n冻结门槛逐项（不含 N/A 的极端段）：")
    for _, r in df.iterrows():
        checks = {
            "net>0": r["net_peak"] > 0,
            "sharpe>=2": r["sharpe"] >= 2.0,
            "calmar>=1.5": r["calmar"] >= 1.5,
            "dd<=10%": r["max_dd"] <= 0.10,
            "consec<=5": r["max_consec_losses"] <= 5,
            "cost_exec": bool(r["cost_executable"]),
            "sample>30": r["n_trades"] > 30,
        }
        passed = sum(checks.values())
        failed = [k for k, v in checks.items() if not v]
        print(f"  {r['scale']:3s} {r['pair']:9s} {r['tag']:14s} "
              f"{passed}/7  未过: {','.join(failed)}"
              f"  极端段={'N/A' if not r['extreme_evaluable'] else str(r['extreme_survived'])}")
    print(f"\n-> {path}  ({len(df)} 组)")


def main() -> int:
    phase = sys.argv[1] if len(sys.argv) > 1 else "probe"
    if phase == "fetch":
        fetch()
    elif phase == "probe":
        # 可选：python backtest/m8_scale_probe.py probe 1h  （单尺度冒烟测试）
        scales = (tuple(sys.argv[2:]) if len(sys.argv) > 2
                  else ("1h", "15m", "30m", "1h_95d", "1h_195d"))
        probe(scales)
    else:
        print("用法: python backtest/m8_scale_probe.py fetch|probe")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
