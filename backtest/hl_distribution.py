"""数据驱动重定币对 + 半衰期区间（任务9）。
把 410 天 1h K 线缓存到本地，避免重复拉取；算半衰期分布，按数据定区间。"""
import sys, time
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import numpy as np
import pandas as pd
from src.data_layer import build_kline_fetcher
from src.signal_layer.pair_screener import (
    screen_pairs, DEFAULT_CANDIDATES, _ols_hedge_ratio, _half_life_bars)

f = build_kline_fetcher()
cache = ROOT / "data" / "cache"

# 1) 先落盘 410 天 1h 原始 K 线（每币种一份，避免重拉）
need = []
for p in DEFAULT_CANDIDATES:
    fp = cache / f"{p}_1h_410d.parquet"
    if fp.exists():
        print(f"命中缓存 {p}", flush=True)
        need.append(pd.read_parquet(fp))
    else:
        print(f"拉取 {p} 410天...", flush=True)
        df = f.fetch_kline(p, "1h", lookback_days=410)
        df.to_parquet(fp, index=False)
        need.append(df)
        time.sleep(0.2)

# 2) 算每个币对的半衰期分布
pairs = []
for i in range(len(DEFAULT_CANDIDATES)):
    for j in range(i + 1, len(DEFAULT_CANDIDATES)):
        a, b = DEFAULT_CANDIDATES[i], DEFAULT_CANDIDATES[j]
        da, db = need[i].set_index("ts"), need[j].set_index("ts")
        m = pd.concat([da["close"], db["close"]], axis=1).dropna()
        m.columns = ["a", "b"]
        la, lb = np.log(m["a"]).dropna(), np.log(m["b"]).dropna()
        common = la.index.intersection(lb.index)
        la, lb = la[common], lb[common]
        if len(la) < 500:
            continue
        corr = la.corr(lb)
        h = _ols_hedge_ratio(la, lb)
        spread = (la - h * lb).dropna()
        hl = _half_life_bars(spread)
        pairs.append({"pair": (a, b), "corr": corr, "hl": hl})

hls = np.array([p["hl"] for p in pairs if not np.isnan(p["hl"])])
print("\n=== 币对半衰期分布（1h bar）===", flush=True)
for p in sorted(pairs, key=lambda x: x["hl"]):
    print(f"  {p['pair'][0][:3]}/{p['pair'][1][:3]}  corr={p['corr']:.3f}  HL={p['hl']:.0f}bar({p['hl']/24:.1f}天)", flush=True)
print(f"\n半衰期: min={hls.min():.0f} 中位={np.median(hls):.0f} 最大={hls.max():.0f} bar", flush=True)
print(f"CV(变异系数)={hls.std()/hls.mean():.2f}  (越小=币对间越接近，验证你的判断)", flush=True)
print(f"IQR=[{np.percentile(hls,25):.0f}, {np.percentile(hls,75):.0f}] bar", flush=True)
print(f"→ 建议半衰期区间: [{np.percentile(hls,25):.0f}, {np.percentile(hls,75):.0f}] bar (IQR，数据驱动)", flush=True)
