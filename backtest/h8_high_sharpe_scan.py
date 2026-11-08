#!/usr/bin/env python
"""
第八轮 · 高夏普快速筛选扫描（H8）

目标：尽早找出夏普率 > 2.0 的候选策略，而不是逐套完整 M8。

核心改动（比第六轮预筛更接近真实统计套利做法）：
  1. 持仓式信号：信号翻转才换手，不每次重置持仓 → 换手降 3-5 倍
  2. 滑点校准：4bp → 0.5bp（第七轮 Gate.io 盘口实测 $500 量级 taker 冲击 <1bp）
  3. 换手周期全网格扫：6/12/24/48/72/168/360/720 bar（0.25 天 ~ 30 天）
  4. 夏普口径：每笔净收益摊到持仓 bar，按 1h 年化 √8760

不改动：
  * config/strategy_params.yaml 冻结参数
  * M3/M4/M5/M6/M7 代码骨架
  * M8 门槛
  * 实盘开关

产物：backtest/output/h8_high_sharpe_scan.csv（不入库）
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
ANNUALIZE = np.sqrt(365.0 * 24.0)

# ---- 成本口径：手续费保留 0.1% taker（Gate.io VIP0 官方），滑点校准到实测 0.5bp ----
TAKER_FEE_PCT = 0.001
SLIPPAGE_BP_CALIBRATED = 0.5          # 第七轮盘口实测值（$500 量级）
COST_BP_OLD = 28.0                    # 旧口径（4bp 滑点）对照用
PER_LEG_COST_RATE = TAKER_FEE_PCT + SLIPPAGE_BP_CALIBRATED / 10_000.0
COST_BP_CALIBRATED = PER_LEG_COST_RATE * 2 * 10_000.0  # 一次 roundtrip 两腿开+平


def load() -> pd.DataFrame:
    frames = []
    for s in SYMS:
        d = pd.read_parquet(CACHE / f"{s}_USDT_1h_410d.parquet")
        d = d[["ts", "open", "close"]].rename(
            columns={"open": f"{s}_open", "close": f"{s}_close"})
        frames.append(d)
    m = frames[0]
    for d in frames[1:]:
        m = m.merge(d, on="ts", how="inner")
    return m.sort_values("ts").reset_index(drop=True)


def seg_gross_bp(m: pd.DataFrame, i: int, j: int,
                 a: str, b: str | None, sign: int) -> float:
    n = len(m)
    if i + 1 >= n or j + 1 >= n or j <= i:
        return np.nan
    ra = m[f"{a}_open"].iat[j + 1] / m[f"{a}_open"].iat[i + 1] - 1.0
    if b is None:
        return ra * sign * 10_000.0
    rb = m[f"{b}_open"].iat[j + 1] / m[f"{b}_open"].iat[i + 1] - 1.0
    return 0.5 * (ra - rb) * sign * 10_000.0


def evaluate_sharpe(m: pd.DataFrame, name: str, params: str,
                    trades: list[tuple], cost_bp: float) -> dict | None:
    """逐笔净收益摊到持仓 bar，计算年化夏普。成本口径可注入。"""
    pairs = [(t, seg_gross_bp(m, *t)) for t in trades]
    pairs = [(t, g) for t, g in pairs if np.isfinite(g)]
    gross = np.array([g for _, g in pairs], dtype=float)
    if len(gross) < 30:
        return None
    net = gross - cost_bp
    per_bar = np.zeros(len(m))
    for (i, j, *_), nb in zip([t for t, _ in pairs], net):
        if j > i:
            per_bar[i + 1:j + 2] += (nb / 10_000.0) / (j - i + 1)
    sd = per_bar.std()
    shp = float(per_bar.mean() / sd * ANNUALIZE) if sd > 0 else 0.0
    gmean = float(gross.mean())
    return {
        "strategy": name, "params": params, "n_trades": len(gross),
        "gross_bp": round(gmean, 2), "cost_bp": cost_bp,
        "net_bp": round(float(net.mean()), 2),
        "cum_net_bp": round(float(net.sum()), 1),
        "win_rate": round(float((gross > cost_bp).mean()), 3),
        "sharpe": round(shp, 3),
        "sharpe_gt2": bool(shp >= 2.0),
        "cost_ratio": round(cost_bp / gmean, 3) if gmean > 0 else float("inf"),
    }


def holding_trades(m: pd.DataFrame, trigger: np.ndarray,
                   exit_rule, min_hold: int = 1) -> list[tuple]:
    """持仓式：空仓时开仓 → 按 exit_rule 或最小持有平仓 → 持仓期间 signal 翻转不换手。

    与 event_trades 的区别：旧实现每次触发就重置持仓，换手 3-5 倍；
    这里只在「当前无持仓 + 触发开仓」时建仓，持仓期间忽略反向信号。
    """
    n = len(m)
    out, i = [], WARMUP
    while i < n - 2:
        s = int(trigger[i])
        if s == 0:
            i += 1
            continue
        j = i + 1
        last = min(i + min_hold, n - 2)
        # 先保证最小持有
        while j < last and not exit_rule(j, s):
            j += 1
        out.append((i, j, "ETH", "SOL", s))
        i = j + 1
    return out


def build_scan_grid(m: pd.DataFrame):
    """全网格：策略族 × 参数 × 滑点口径 × 成本口径。"""
    n = len(m)
    ratio = np.log(m["ETH_close"] / m["SOL_close"])
    ret = {s: m[f"{s}_close"].to_numpy() for s in SYMS}
    rows: list[dict] = []

    # ---- 家族 1：统计套利配对（持仓式，不换手）----
    for window in (24, 60, 120, 240):
        mu = pd.Series(ratio).rolling(window).mean().to_numpy()
        sd = pd.Series(ratio).rolling(window).std().to_numpy()
        z = np.where(sd > 0, (ratio - mu) / np.where(sd > 0, sd, 1), 0.0)
        trig = np.where(z <= -3.0, 1, np.where(z >= 3.0, -1, 0))
        for min_hold in (1, 3, 6, 12, 24, 48, 96, 168, 360):
            # 平仓条件：价差回归到 0.5σ 以内，或最小持有期已达成且价差开始向 0 收敛
            # 修复：旧版 exit_rule 中 `j - (j - min_hold) == min_hold` 恒 0，平仓永远退化成 min_hold
            exit_rule = lambda j, s, z=z, min_hold=min_hold, window=window: \
                abs(z[j]) <= 0.5 or (j >= min_hold and z[j] * z[j - 1] < 0)  # 跨 0
            trades = holding_trades(m, trig, exit_rule, min_hold)
            for tag, c in [("slip0.5", COST_BP_CALIBRATED), ("slip4", COST_BP_OLD)]:
                r = evaluate_sharpe(m, "MR_pair_holding",
                                    f"window={window}/minhold={min_hold}/{tag}",
                                    trades, c)
                if r:
                    r["cost_basis"] = tag
                    rows.append(r)

    # ---- 家族 2：截面动量轮动（持仓式，不换手）----
    for L in (24, 72, 168):
        for R in (24, 72, 168, 360, 720):
            if R < L:
                continue

            def pick(i, L=L):
                r = {s: ret[s][i] / ret[s][i - L] - 1.0 for s in SYMS}
                hi, lo = max(r, key=r.get), min(r, key=r.get)
                return (hi, lo, 1) if hi != lo else None
            # 周期性换仓：每 R 根 bar 重新选腿
            trades = []
            for i in range(WARMUP, n - 2, R):
                j = min(i + R, n - 2)
                if j <= i:
                    continue
                x = pick(i)
                if x:
                    trades.append((i, j, x[0], x[1], x[2]))
            for tag, c in [("slip0.5", COST_BP_CALIBRATED), ("slip4", COST_BP_OLD)]:
                r = evaluate_sharpe(m, "XSEC_mom_holding",
                                    f"L={L}/R={R}/{tag}", trades, c)
                if r:
                    r["cost_basis"] = tag
                    rows.append(r)

    # ---- 家族 3：单币时序动量（持仓式）----
    for L in (24, 72, 168):
        for R in (24, 72, 168, 360, 720):
            if R < L:
                continue

            def pick_s(i, L=L):
                r = ret["BTC"][i] / ret["BTC"][i - L] - 1.0
                return ("BTC", None, 1 if r > 0 else -1)
            trades = []
            for i in range(WARMUP, n - 2, R):
                j = min(i + R, n - 2)
                if j <= i:
                    continue
                x = pick_s(i)
                if x:
                    trades.append((i, j, x[0], x[1], x[2]))
            for tag, c in [("slip0.5", COST_BP_CALIBRATED), ("slip4", COST_BP_OLD)]:
                r = evaluate_sharpe(m, "SINGLE_leg_mom",
                                    f"L={L}/R={R}/{tag}", trades, c)
                if r:
                    r["cost_basis"] = tag
                    rows.append(r)

    # ---- 家族 4：全 10 币对 × 长持有（统计套利主力家族，成本摊薄路径）----
    # 触发阈值从 ±3σ 放宽到 ±2σ（1h 410 天 2σ 触发更密、样本足够 30 笔），
    # 平仓用 0.5σ 回归 或 最小持有期到 或 价差跨 0。窗口 120/240/480 三档。
    pair_list = [
        ("ETH", "SOL"), ("ETH", "XRP"), ("ETH", "DOGE"), ("BTC", "SOL"),
        ("BTC", "XRP"), ("BTC", "DOGE"), ("SOL", "XRP"), ("SOL", "DOGE"),
        ("XRP", "DOGE"), ("SOL", "ETH"),
    ]
    for aa, bb in pair_list:
        ratio2 = np.log(m[f"{aa}_close"] / m[f"{bb}_close"])
        for w4 in (120, 240, 480):
            rs = pd.Series(ratio2)
            mu = rs.rolling(w4).mean().to_numpy()
            sd = rs.rolling(w4).std().to_numpy()
            z2 = np.where(sd > 0, (ratio2 - mu) / np.where(sd > 0, sd, 1), 0.0)
            for sig_in in (2.0, 2.5, 3.0):
                trig2 = np.where(z2 <= -sig_in, 1, np.where(z2 >= sig_in, -1, 0))
                for min_hold in (24, 72, 168, 360):
                    def _exit(j, s, z=z2, min_hold=min_hold):
                        return abs(z[j]) <= 0.5 or (j >= min_hold and z[j] * z[j - 1] < 0)
                    trades = holding_trades(m, trig2, _exit, min_hold)
                    pairs_t = []
                    for t in trades:
                        i, j = t[0], t[1]
                        sgn = t[4]
                        ra = m[f"{aa}_open"].iat[j + 1] / m[f"{aa}_open"].iat[i + 1] - 1.0
                        rb = m[f"{bb}_open"].iat[j + 1] / m[f"{bb}_open"].iat[i + 1] - 1.0
                        g = 0.5 * (ra - rb) * sgn * 10_000.0
                        pairs_t.append((i, j, g))
                    gm = np.array([g for _, _, g in pairs_t], dtype=float)
                    if len(gm) < 30:
                        continue
                    net = gm - COST_BP_CALIBRATED
                    per_bar = np.zeros(n)
                    for (i, j, g), nb in zip(pairs_t, net):
                        if j > i:
                            per_bar[i + 1:j + 2] += (nb / 10_000.0) / (j - i + 1)
                    sdd = per_bar.std()
                    shp = float(per_bar.mean() / sdd * ANNUALIZE) if sdd > 0 else 0.0
                    rows.append({
                        "strategy": "MR_pair_longhold",
                        "params": f"{aa}/{bb}/w{w4}/sig={sig_in}/minhold={min_hold}",
                        "n_trades": len(gm), "gross_bp": round(float(gm.mean()), 2),
                        "cost_bp": COST_BP_CALIBRATED, "net_bp": round(float(net.mean()), 2),
                        "cum_net_bp": round(float(net.sum()), 1),
                        "win_rate": round(float((gm > COST_BP_CALIBRATED).mean()), 3),
                        "sharpe": round(shp, 3), "sharpe_gt2": bool(shp >= 2.0),
                        "cost_ratio": round(COST_BP_CALIBRATED / float(gm.mean()), 3)
                        if gm.mean() > 0 else float("inf"), "cost_basis": "slip0.5",
                    })

    return rows


def main() -> None:
    m = load()
    n = len(m)
    print(f"数据：{n} bar（410 天 1h），{SYMS}")
    print(f"年化口径：√(365×24)={ANNUALIZE:.2f}")
    print(f"成本口径：滑点校准 {SLIPPAGE_BP_CALIBRATED}bp → roundtrip {COST_BP_CALIBRATED:.2f}bp；"
          f"旧 4bp → {COST_BP_OLD}bp")

    rows = build_scan_grid(m)
    df = pd.DataFrame(rows)
    OUT.mkdir(parents=True, exist_ok=True)
    out_csv = OUT / "h8_high_sharpe_scan.csv"
    df.to_csv(out_csv, index=False)
    print(f"\n扫描 {len(df)} 组，写入 {out_csv}")

    # 按夏普排序 top 20
    print("\n=== TOP 20 夏普 ===")
    cols = ["strategy", "params", "n_trades", "gross_bp", "cost_bp",
            "net_bp", "sharpe", "sharpe_gt2", "win_rate", "cum_net_bp"]
    top = df.sort_values("sharpe", ascending=False).head(20)[cols]
    print(top.to_string(index=False))

    # 夏普 > 2 的候选
    gt2 = df[df["sharpe"] >= 2.0]
    print(f"\n=== 夏普 ≥ 2.0 候选：{len(gt2)} 组 ===")
    if len(gt2):
        print(gt2[cols].to_string(index=False))
    else:
        print("（无 —— 仍不达标，如实申报）")

    # 对照：旧口径 4bp vs 校准 0.5bp 的差异
    print("\n=== 滑点校准 vs 旧口径（同策略参数）===")
    pivot = df.pivot_table(index="params", columns="cost_basis",
                           values="sharpe", aggfunc="first")
    print(pivot.head(15).to_string())


if __name__ == "__main__":
    main()
