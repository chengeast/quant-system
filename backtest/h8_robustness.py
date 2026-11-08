#!/usr/bin/env python
"""
第八轮 · 高夏普候选防过拟合稳健性检查

仅对 H8 扫描中夏普 ≥ 2 的「成片候选」做拆解：
  * 时段拆分（前后半 / 按月）
  * 多空拆分
  * 极端段 2026-02 检查
  * 参数邻域成片性
  * 跑赢 BTC 单边对照

不修改任何冻结参数 / 代码骨架 / M8 门槛。
产物：backtest/output/h8_robustness.csv + 控制台报告
"""
from __future__ import annotations
from pathlib import Path
import numpy as np, pandas as pd

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "cache"
OUT = ROOT / "backtest" / "output"
SYMS = ("BTC", "ETH", "SOL", "XRP", "DOGE")
WARMUP = 200
ANNUALIZE = np.sqrt(365.0 * 24.0)
TAKER, SLIP_CAL, SLIP_OLD = 0.001, 0.5, 4.0
COST_CAL = (TAKER + SLIP_CAL / 1e4) * 2 * 1e4
COST_OLD = (TAKER + SLIP_OLD / 1e4) * 2 * 1e4


def load():
    frames = []
    for s in SYMS:
        d = pd.read_parquet(CACHE / f"{s}_USDT_1h_410d.parquet")
        d = d[["ts", "open", "close"]].rename(columns={"open": f"{s}_open", "close": f"{s}_close"})
        frames.append(d)
    m = frames[0]
    for d in frames[1:]:
        m = m.merge(d, on="ts", how="inner")
    return m.sort_values("ts").reset_index(drop=True)


def spread_trades(m, aa, bb, w, sig, min_hold, slip_old):
    ratio = np.log(m[f"{aa}_close"] / m[f"{bb}_close"])
    rs = pd.Series(ratio)
    mu = rs.rolling(w).mean().to_numpy()
    sd = rs.rolling(w).std().to_numpy()
    z = np.where(sd > 0, (ratio - mu) / np.where(sd > 0, sd, 1), 0.0)
    trig = np.where(z <= -sig, 1, np.where(z >= sig, -1, 0))
    def _exit(j, s, z=z, min_hold=min_hold):
        return abs(z[j]) <= 0.5 or (j >= min_hold and z[j] * z[j - 1] < 0)
    n = len(m); out, i = [], WARMUP
    while i < n - 2:
        s = int(trig[i])
        if s == 0:
            i += 1; continue
        j = i + 1
        last = min(i + min_hold, n - 2)
        while j < last and not _exit(j, s):
            j += 1
        out.append((i, j, s))
        i = j + 1
    rows = []
    for (i, j, sgn) in out:
        ra = m[f"{aa}_open"].iat[j + 1] / m[f"{aa}_open"].iat[i + 1] - 1.0
        rb = m[f"{bb}_open"].iat[j + 1] / m[f"{bb}_open"].iat[i + 1] - 1.0
        g = 0.5 * (ra - rb) * sgn * 1e4
        rows.append({"i": i, "j": j, "sign": sgn, "gross_bp": g,
                     "ts": int(m["ts"].iat[i]), "net_cal": g - COST_CAL, "net_old": g - COST_OLD})
    return pd.DataFrame(rows)


def sharp_from_rec(rec):
    if len(rec) < 10:
        return 0.0
    return float(rec["net_cal"].mean() / rec["net_cal"].std() * np.sqrt(252 / (rec["j"] - rec["i"]).mean()) ) if rec["net_cal"].std() > 0 else 0.0


def main():
    m = load()
    ts = pd.to_datetime(m["ts"], unit="s")
    rec = spread_trades(m, "XRP", "DOGE", 240, 2.5, 168, SLIP_OLD)
    rec["month"] = ts.iloc[rec["i"].to_numpy()].dt.strftime("%Y-%m").to_numpy()
    rec["half"] = np.where(rec["i"] < len(m) // 2, "H1", "H2")

    print(f"候选 XRP/DOGE w240/sig2.5/minhold168：{len(rec)} 笔")
    print(f"总净收益(校准slip): {rec['net_cal'].sum():.0f}bp  (旧slip): {rec['net_old'].sum():.0f}bp")

    # 时段拆分
    print("\n[1] 前后半拆分")
    print(rec.groupby("half").agg(n=("net_cal", "count"), net_cal=("net_cal", "sum"),
                                   net_old=("net_old", "sum")).to_string())

    # 按月
    print("\n[2] 按月拆分（校准 slip 净 bp）")
    by_month = rec.groupby("month").agg(n=("net_cal", "count"), net_cal=("net_cal", "sum"))
    print(by_month.to_string())
    pos_months = int((by_month["net_cal"] > 0).sum())
    print(f"  净收益为正月份：{pos_months}/{len(by_month)}")

    # 多空拆分
    print("\n[3] 多空拆分（校准 slip）")
    print(rec.groupby("sign").agg(n=("net_cal", "count"), net_cal=("net_cal", "sum")).to_string())

    # 极端段 2026-02
    ext = rec[(rec["ts"] >= 1770163200) & (rec["ts"] <= 1770681600)]
    print(f"\n[4] 极端段 2026-02：{len(ext)} 笔，净 cal {ext['net_cal'].sum():.1f}bp / 旧 {ext['net_old'].sum():.1f}bp")

    # 成片性：同币对不同参数的夏普矩阵
    print("\n[5] 成片性（同族参数邻域，校准 slip 夏普）")
    mat = []
    for w in (120, 180, 240, 300, 360, 480):
        for sig in (2.0, 2.5, 3.0):
            for mh in (72, 168, 360):
                r2 = spread_trades(m, "XRP", "DOGE", w, sig, mh, SLIP_OLD)
                s = sharp_from_rec(r2)
                mat.append({"w": w, "sig": sig, "mh": mh, "n": len(r2), "sharpe": round(s, 2)})
    mat_df = pd.DataFrame(mat).pivot(index=["w", "sig"], columns="mh", values="sharpe")
    print(mat_df.to_string())

    # 跑赢 BTC 单边
    btc = m["BTC_close"].to_numpy()
    btc_total = (btc[-1] / btc[WARMUP] - 1) * 1e4
    print(f"\n[6] 跑赢对照：候选累计净(cal) {rec['net_cal'].sum():.0f}bp vs BTC 单边 {btc_total:.0f}bp")

    out_csv = OUT / "h8_robustness_xrp_doге.csv".replace("е", "e")
    rec.to_csv(out_csv, index=False)
    print(f"\n明细写入 {out_csv}")


if __name__ == "__main__":
    main()
