"""【已废弃旧验收口径（2026-11-08）】
  本脚本是“±3σ → 到时间窗口内是否回归 0.5σ”的历史回归率诊断，
  不是“开仓前锁定止盈/止损/超时三线并行”的新标准。
  如需重新测试全周期并生成验收报告，请运行 backtest/run_strategy_with_rules.py。

3 专家辩论数据包：全 10 币对 ±3σ 信号未来 1.2H 回归率。

喂给 3 金融专家（A均值回归/B风险/C高频因子）辩论"M8 不达标根因 + 后续方向"。
输出：backtest/output/regress_rate_10pairs.csv + 打印对照表。
"""
import sys
import itertools
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import warnings
warnings.filterwarnings("ignore")

import pandas as pd
import numpy as np
from src.signal_layer.mean_reversion_signals import MeanReversionSignals

CACHE = ROOT / "data" / "cache"
PAIRS = ["BTC", "ETH", "SOL", "XRP", "DOGE"]
SIG = MeanReversionSignals(hedge_window=60, entry_sigma=3.0,
                           exit_sigma=0.5, hl_range=(368, 856))


def regress_rate(df: pd.DataFrame):
    """±3σ 进 → 未来 max(3h, 1.2×H_t) 内回归到 0.5σ 带的 bar 占比。

    修复口径：用**逐 bar 实时 H_t**（设计里的超时窗口 = max(3h, 1.2×H_t)），
    而非全样本中位数（旧版 7-9 bar 中位数会压缩 407 bar 半衰期币对的窗口到 8 bar）。
    """
    z = df["zscore"].reset_index(drop=True)
    H = df["half_life"].reset_index(drop=True)
    st = df["state"].reset_index(drop=True)
    timeout_floor = 3.0  # max(3h, 1.2H) 的下限
    reg = div = 0
    for i in range(len(df) - 1):
        if st.iloc[i] != "OK" or np.isnan(z.iloc[i]) or abs(z.iloc[i]) < 3.0:
            continue
        H_i = float(H.iloc[i]) if not np.isnan(H.iloc[i]) else 407.0
        t = max(int(timeout_floor), int(1.2 * H_i))
        if t < 1:
            continue
        win = z.iloc[i + 1:i + 1 + t].dropna()
        if len(win) == 0:
            continue
        if bool(win.abs().le(0.5).any()):
            reg += 1
        else:
            div += 1
    return reg, div, (reg / (reg + div)) if (reg + div) else None


def main() -> int:
    rows = []
    for a, b in itertools.combinations(PAIRS, 2):
        pa = pd.read_parquet(CACHE / f"{a}_USDT_1h_410d.parquet")
        pb = pd.read_parquet(CACHE / f"{b}_USDT_1h_410d.parquet")
        al = pa[["ts", "close"]].rename(columns={"close": "close_a"})
        al2 = pb[["ts", "close"]].rename(columns={"close": "close_b"})
        m = al.merge(al2, on="ts").sort_values("ts").reset_index(drop=True)
        m["ts_ms"] = m["ts"].astype("int64") * 1000
        df = SIG.compute_signals(m, window=60)
        z = df["zscore"].dropna()
        H = df["half_life"].dropna()
        reg, div, rate = regress_rate(df)
        corr = float(df["close_a"].corr(df["close_b"]))
        h_out = int((~df["hedge_ratio"].between(0.5, 2.0)).sum())
        rows.append({
            "pair": f"{a}/{b}", "n_entries": int((z.abs() >= 3.0).sum()),
            "regressed": reg, "diverged": div,
            "reg_rate": None if rate is None else round(rate, 4),
            "corr": round(corr, 4),
            "hl_median": None if H.empty else round(float(H.median()), 1),
            "h_out_of_05_2": h_out,
            "half_life_median_bars": None if H.empty else int(H.median()),
        })

    out = pd.DataFrame(rows).sort_values("reg_rate", ascending=False)
    out_dir = ROOT / "backtest" / "output"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "regress_rate_10pairs.csv"
    out.to_csv(out_path, index=False)

    print("=" * 80)
    print("3 专家辩论数据包 · 全 10 币对 · 410 天 1h 深数据")
    print("信号：±3σ 进 / 0.5σ 出 / 超时 1.2×半衰期（M2 冻结参数）")
    print("=" * 80)
    for r in out.itertuples():
        rr = "—" if r.reg_rate is None else f"{r.reg_rate*100:5.1f}%"
        print(f"  {r.pair:8s} 回归率 {rr:>6s} "
              f"({r.regressed:3d}回归/{r.diverged:3d}发散, 共{r.n_entries:3d}个进信号)  "
              f"相关性 {r.corr:.4f}  半衰期 {r.hl_median or 0:6.1f}bar  "
              f"h越界 {r.h_out_of_05_2:4d}bar")
    print()
    print(f"→ {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
