#!/usr/bin/env python
"""
第八轮 · 4h/1d 长持有统计套利扫描（成本摊薄路径）

【历史排查脚本，非新验收标准（2026-11-08）】
  本脚本已把 stop_loss_sigma 改成“相对开仓点逆向再偏离 N σ”，并加入 timeout_bars 兜底；
  但其止盈仍保留旧的绝对 0.5σ 口径，且未接入 StrategyRules 单一入口。
  若用户要求“全部按现在的新标准重新测试”，请以 backtest/run_strategy_with_rules.py 为准。

用已有 1h 410 天数据聚合出 4h/1d K 线（同源、免重拉），
再扫「长持有期 30-90 天 + 低换手 + 校准滑点 0.5bp」，
检验成本/毛利能否摊薄到冻结门槛 0.35 以内、夏普能否 >2。

不改冻结参数 / M8 门槛 / 实盘开关。
产物：backtest/output/h8b_long_holding_4h_1d.csv
"""
from __future__ import annotations
import itertools
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "cache"
OUT = ROOT / "backtest" / "output"
SYMS = ("BTC", "ETH", "SOL", "XRP", "DOGE")
WARMUP = 200
TAKER = 0.001
SLIP = 0.5
COST = (TAKER + SLIP / 1e4) * 2 * 1e4   # 一次 roundtrip 两腿开+平，单位 bp


def load_1h() -> pd.DataFrame:
    frames = []
    for s in SYMS:
        d = pd.read_parquet(CACHE / f"{s}_USDT_1h_410d.parquet")
        d = d[["ts", "open", "high", "low", "close", "volume"]].copy()
        d["ts"] = pd.to_datetime(d["ts"], unit="s", utc=True)
        for c in ("open", "high", "low", "close", "volume"):
            d[c] = d[c].astype(float)
        d = d.rename(columns={f"{c}": f"{s}_{c}" for c in ("open", "high", "low", "close", "volume")})
        frames.append(d)
    m = frames[0]
    for d in frames[1:]:
        m = m.merge(d, on="ts", how="inner")
    return m.sort_values("ts").reset_index(drop=True)


def resample_interval(m: pd.DataFrame, iv: str) -> pd.DataFrame:
    """1h → 4h/1d 聚合。open=首open, high=max, low=min, close=末close, volume=sum。"""
    rule = "4h" if iv == "4h" else "1D"
    agg = {}
    for s in SYMS:
        cols = [f"{s}_{c}" for c in ("open", "high", "low", "close", "volume")]
        sub = m.set_index("ts")[cols]
        sub = sub.rename(columns={f"{s}_{c}": c for c in ("open", "high", "low", "close", "volume")})
        a = sub.resample(rule).agg(
            {"open": "first", "high": "max", "low": "min",
             "close": "last", "volume": "sum"})
        a = a.dropna(subset=["open", "close"])
        for c in ("open", "high", "low", "close", "volume"):
            a = a.rename(columns={c: f"{s}_{c}"})
        agg[s] = a
    out = agg[SYMS[0]]
    for s in SYMS[1:]:
        out = out.join(agg[s], how="inner")
    out = out.dropna()
    out["ts"] = out.index.view("int64") // 10**9
    return out.reset_index(drop=True)


def bars_per_year(iv: str) -> float:
    return 365.0 * 24.0 / (4 if iv == "4h" else 24)


def seg_gross(m, i, j, a, b, sign):
    n = len(m)
    if i + 1 >= n or j + 1 >= n or j <= i:
        return np.nan
    ra = m[f"{a}_open"].iat[j + 1] / m[f"{a}_open"].iat[i + 1] - 1.0
    rb = m[f"{b}_open"].iat[j + 1] / m[f"{b}_open"].iat[i + 1] - 1.0
    return 0.5 * (ra - rb) * sign * 1e4


def triple_exit(z, i, exit_sigma=0.5, stop_sigma=2.0, timeout_bars=9999):
    """三线并行平仓（开仓前锁定参照系，相对开仓点 i 的 z[i] 计算）。

    ① 止盈 target：|z[j]| ≤ exit_sigma（回归到位）
    ② 止损 stop ：相对开仓点逆向再偏离 stop_sigma（不是绝对 ±Nσ）
    ③ 超时 timeout：持仓 bar ≥ timeout_bars（兜底）
    优先级：止盈 > 止损 > 超时；返回平仓 bar j（j ≥ i+1，t+1 成交由调用方保证）。
    """
    entry_z = z[i]
    long_pos = entry_z < 0
    last = min(i + timeout_bars, len(z) - 2)
    for j in range(i + 1, last + 1):
        if abs(z[j]) <= exit_sigma:
            return j
        dev = (z[j] - entry_z) if long_pos else (entry_z - z[j])
        if dev >= stop_sigma:
            return j
    return last


def run_pair(m, iv, a, b, w_bars, sig, stop_sigma, timeout_bars, exit_sigma=0.5):
    ratio = np.log(m[f"{a}_close"] / m[f"{b}_close"])
    rs = pd.Series(ratio)
    mu = rs.rolling(w_bars).mean().to_numpy()
    sd = rs.rolling(w_bars).std().to_numpy()
    z = np.where(sd > 0, (ratio - mu) / np.where(sd > 0, sd, 1), 0.0)
    trig = np.where(z <= -sig, 1, np.where(z >= sig, -1, 0))
    trig[:WARMUP] = 0
    n = len(m)
    out, i = [], WARMUP
    ann = np.sqrt(bars_per_year(iv))
    while i < n - 2:
        s = int(trig[i])
        if s == 0:
            i += 1
            continue
        # 平仓 = 三线并行（止盈/止损/超时，开仓前已设好），非「固定持有到期」
        j = triple_exit(z, i, exit_sigma=exit_sigma,
                        stop_sigma=stop_sigma, timeout_bars=timeout_bars)
        g = seg_gross(m, i, j, a, b, s)
        if np.isfinite(g):
            out.append((i, j, s, g))
        i = j + 1
    if len(out) < 10:
        return None
    gm = np.array([o[3] for o in out])
    net = gm - COST
    per_bar = np.zeros(n)
    for k, (i, j, s, g) in enumerate(out):
        if j > i:
            per_bar[i + 1:j + 2] += (net[k] / 1e4) / (j - i + 1)
    sdv = per_bar.std()
    shp = float(per_bar.mean() / sdv * ann) if sdv > 0 else 0.0
    return {
        "pair": f"{a}/{b}", "iv": iv, "w": w_bars, "sig": sig,
        "stop": stop_sigma, "timeout": timeout_bars, "n": len(gm),
        "gross_bp": round(float(gm.mean()), 2), "cost_bp": COST,
        "net_bp": round(float(net.mean()), 2),
        "cum_net_bp": round(float(net.sum()), 1),
        "sharpe": round(shp, 3), "sharpe_gt2": bool(shp >= 2.0),
        "cost_over_gross": round(COST / float(gm.mean()), 3) if gm.mean() > 0 else "inf",
        "win_rate": round(float((gm > COST).mean()), 3),
    }


def main():
    m1h = load_1h()
    rows = []
    for iv in ("4h", "1d"):
        m = resample_interval(m1h, iv)
        print(f"\n{iv}: {len(m)} bar（聚合自 1h 410 天）")
        # 持有期物理 30-90 天
        hold_days = (30, 60, 90)
        if iv == "4h":
            w_bars = (6, 12, 24, 48)          # 1天/2天/4天/8天 均值回归窗口
            timeout_bars = tuple(int(h * 6) for h in hold_days)  # 4h: 6根/天
        else:
            w_bars = (7, 14, 21, 30)
            timeout_bars = hold_days           # 1d: 1根/天
        stop_sigma = (1.5, 2.0, 3.0)
        sig_in = (2.0, 2.5, 3.0)
        combos = set()
        for w, sig, stop, timeout in itertools.product(
                w_bars, sig_in, stop_sigma, timeout_bars):
            for a, b in [
                ("ETH", "SOL"), ("BTC", "DOGE"), ("XRP", "DOGE"),
                ("SOL", "XRP"), ("BTC", "ETH"), ("SOL", "DOGE"),
            ]:
                key = (a, b, w, sig, stop, timeout)
                if key in combos:
                    continue
                combos.add(key)
                r = run_pair(m, iv, a, b, w, sig, stop, timeout)
                if r:
                    rows.append(r)
    df = pd.DataFrame(rows)
    OUT.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT / "h8b_long_holding_4h_1d.csv", index=False)
    print(f"\n共 {len(df)} 组")
    cols = ["iv", "pair", "w", "sig", "stop", "timeout", "n", "gross_bp",
            "cost_bp", "net_bp", "sharpe", "sharpe_gt2", "cost_over_gross"]
    print("\n=== 夏普 TOP 15 ===")
    print(df.sort_values("sharpe", ascending=False).head(15)[cols].to_string(index=False))
    gt2 = df[df["sharpe"] >= 2.0]
    ok = df[(df["sharpe"] >= 2.0)
            & (df["cost_over_gross"].apply(lambda x: (x != "inf") and (float(x) <= 0.35)))]
    print(f"\n夏普≥2 总组数：{len(gt2)}；其中夏普≥2 且 成本/毛利≤0.35：{len(ok)}")
    if len(ok):
        print(ok[cols].to_string(index=False))


if __name__ == "__main__":
    main()
