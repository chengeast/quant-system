#!/usr/bin/env python
"""
相似策略 · 成本可行性预筛（phase6）

问题：1h 配对均值回归 MVP 已被 3:0 裁决关闭上线评估分支。
      还有哪些「相似策略」值得投入完整 M8 回测？

判据来自 M8 实测，不凭经验（backtest/output/m8_backtest_report.md §4 / golden_value.json）：
    132 笔，累计毛利 $107.93，累计成本 $192.54
    → 成本/毛利 = 1.784；冻结门槛 max_cost_as_pct_of_gross = 0.35
    → 单笔毛利须提升 1.784 / 0.35 = 5.10 倍才可能通过。

关键结构性事实：成本是**比例费率**（taker 0.1% + 滑点 4bp/边），
单笔成本与单笔毛利都随名义敞口线性缩放，因此「成本/毛利」比值
与 ① 仓位大小 ② 换手次数 ③ K 线尺度 全都无关。
降换手、降尺度只会等比缩小总成本与总毛利，不改变比值
（第四轮 15m/30m 24 组对照已实证：0/24 通过，成本/笔恒定 $1.40-1.50）。
故本预筛只检验一个**必要条件**：单笔毛利(bp) 相对基准能否 ≥ 5.1 倍。

统一口径（所有策略同一把尺子，用于组间相对比较，非 M8 绝对复现）：
  * 两腿各占总名义 50%，总名义恒定 → 全部结果用 bp（相对总名义）表达。
  * 成交：bar t 出信号，bar t+1 的 open 成交 —— 与 M8「t+1 跨 bar」红线一致，
    且比 M8 用 close 更保守，杜绝未来函数。
  * 成本：每次成交 0.14% × 总名义（两腿各 0.14%×单腿）；
    一次完整交易（开+平）= 0.28% = 28bp。
  * 毛利 = 0.5 × (r_A − r_B) × sign × 10000；sign=+1 多 A 空 B，−1 反之。
    单币对照策略毛利 = r_A × sign × 10000，成本同为 28bp。

边界（务必知悉）：
  * 这是「一票否决预筛」，不接 M5/M6/M7，不做回撤 / 夏普 / Calmar /
    极端段 / 拒单率判定，结论只回答「哪个方向有资格进完整 M8」。
  * 不修生产代码、不改 config/strategy_params.yaml 冻结参数、不启用实盘。
  * 基准 B0 是均值回归的简化复现，绝对 bp 与 M8 未必同量级；
    主判据用「相对基准倍数」而非绝对值，以规避口径差异。

用法：
    python backtest/strategy_feasibility_probe.py
产物：backtest/output/strategy_probe.csv（不入库，*.csv 被 .gitignore 排除）
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
COST_BP = 28.0                 # 一次完整交易（开+平）的成本，相对总名义
GATE_COST_OVER_GROSS = 0.35    # config/strategy_params.yaml 冻结值
NEED_MULT = 1.784 / GATE_COST_OVER_GROSS   # ≈ 5.10，M8 实测反推
ANNUALIZE = np.sqrt(365.0 * 24.0)


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
    """一笔交易从 bar i 出信号到 bar j 平仓信号，均以 t+1 open 成交。"""
    n = len(m)
    if i + 1 >= n or j + 1 >= n or j <= i:
        return np.nan
    ra = m[f"{a}_open"].iat[j + 1] / m[f"{a}_open"].iat[i + 1] - 1.0
    if b is None:                      # 单币方向性对照
        return ra * sign * 10_000.0
    rb = m[f"{b}_open"].iat[j + 1] / m[f"{b}_open"].iat[i + 1] - 1.0
    return 0.5 * (ra - rb) * sign * 10_000.0


def evaluate(m: pd.DataFrame, name: str, params: str,
             trades: list[tuple]) -> dict | None:
    pairs = [(t, seg_gross_bp(m, *t)) for t in trades]
    pairs = [(t, g) for t, g in pairs if np.isfinite(g)]
    gross = np.array([g for _, g in pairs], dtype=float)
    if len(gross) < 20:                # 样本太少不评价，避免小样本假象
        return None
    net = gross - COST_BP
    gmean = float(gross.mean())
    ratio = COST_BP / gmean if gmean > 0 else float("inf")
    # 粗夏普：把每笔净收益摊到持仓 bar，仅用于同口径排序，不作门槛判定
    per_bar = np.zeros(len(m))
    for (i, j, *_), nb in zip([t for t, _ in pairs], net):
        if j > i:
            per_bar[i + 1:j + 2] += (nb / 10_000.0) / (j - i + 1)
    sd = per_bar.std()
    shp = float(per_bar.mean() / sd * ANNUALIZE) if sd > 0 else 0.0
    return {
        "strategy": name, "params": params, "n_trades": len(gross),
        "gross_bp_per_trade": round(gmean, 2),
        "cost_bp_per_trade": COST_BP,
        "net_bp_per_trade": round(float(net.mean()), 2),
        "cost_over_gross": round(ratio, 3),
        "gate_cost_ok": ratio <= GATE_COST_OVER_GROSS and gmean > 0,
        "win_rate": round(float((gross > COST_BP).mean()), 3),
        "cum_net_bp": round(float(net.sum()), 1),
        "sharpe_rough": round(shp, 2),
        "vs_baseline": None,           # 后填
    }


def event_trades(m: pd.DataFrame, trigger: np.ndarray,
                 exit_rule, max_hold: int,
                 a="ETH", b="SOL") -> list[tuple]:
    """事件驱动：空仓时触发开仓，按 exit_rule 或 max_hold 平仓，段间不重叠。"""
    n = len(m)
    out, i = [], WARMUP
    while i < n - 2:
        s = int(trigger[i])
        if s == 0:
            i += 1
            continue
        j = i + 1
        last = min(i + max_hold, n - 2)
        while j < last and not exit_rule(j, s):
            j += 1
        out.append((i, j, a, b, s))
        i = j + 1
    return out


def periodic_trades(m: pd.DataFrame, rebal: int,
                    pick, a=None, b=None) -> list[tuple]:
    """固定周期换仓：每 rebal 根 bar 重新选腿，段间不重叠。"""
    n = len(m)
    out = []
    for i in range(WARMUP, n - 2, rebal):
        j = min(i + rebal, n - 2)
        if j <= i:
            continue
        x = pick(i)
        if x is None:
            continue
        aa, bb, s = x
        out.append((i, j, aa, bb, s))
    return out


def build_candidates(m: pd.DataFrame):
    n = len(m)
    ratio = np.log(m["ETH_close"] / m["SOL_close"])
    mu = ratio.rolling(60).mean()
    sd = ratio.rolling(60).std().replace(0, np.nan)
    z = ((ratio - mu) / sd).fillna(0.0).to_numpy()
    ret = {s: m[f"{s}_close"].to_numpy() for s in SYMS}
    cands = []

    # ---- B0 基准：配对均值回归（简化复现，±3σ 进 / 0.5σ 出 / 最长 24bar）----
    trig = np.where(z <= -3.0, 1, np.where(z >= 3.0, -1, 0))
    cands.append(("B0_mean_reversion", "3σ/0.5σ/24bar",
                  event_trades(m, trig, lambda j, s: abs(z[j]) <= 0.5, 24)))

    # ---- S1 价差动量：突破后「跟随」而非赌回归，固定持有 ----
    for k, h in itertools.product((1.5, 2.0), (6, 24, 72)):
        trig = np.where(z >= k, 1, np.where(z <= -k, -1, 0))
        cands.append((f"S1_spread_momentum", f"k={k}/H={h}",
                      event_trades(m, trig, lambda j, s: False, h)))

    # ---- S2 截面动量轮动：5 币按过去 L bar 收益排序，多最强空最弱 ----
    for L, R in itertools.product((24, 72, 168), (24, 72, 168)):
        def pick(i, L=L):
            r = {s: ret[s][i] / ret[s][i - L] - 1.0 for s in SYMS}
            hi, lo = max(r, key=r.get), min(r, key=r.get)
            return (hi, lo, 1) if hi != lo else None
        cands.append(("S2_cross_sectional_momentum", f"L={L}/R={R}",
                      periodic_trades(m, R, pick)))

    # ---- S3 均值回归 + 固定长持有（延长持有期，看单笔毛利能否变厚）----
    trig_mr = np.where(z <= -3.0, 1, np.where(z >= 3.0, -1, 0))
    for h in (24, 72, 168):
        cands.append(("S3_mean_rev_long_hold", f"3σ/H={h}",
                      event_trades(m, trig_mr, lambda j, s: False, h)))

    # ---- S4 相对强弱通道突破：log(A/B) 突破过去 24bar 高低点 → 跟随 ----
    up = pd.Series(ratio).shift(1).rolling(24).max().to_numpy()
    dn = pd.Series(ratio).shift(1).rolling(24).min().to_numpy()
    rv = ratio.to_numpy()
    trig = np.where(rv > up, 1, np.where(rv < dn, -1, 0))
    trig[:WARMUP] = 0
    for h in (24, 72):
        cands.append(("S4_relative_breakout", f"W=24/H={h}",
                      event_trades(m, trig, lambda j, s: False, h)))

    # ---- S5 单币时序动量（方向性对照，偏离中性骨架，仅作参照）----
    for L, R in itertools.product((24, 72, 168), (72, 168)):
        def pick(i, L=L):
            r = ret["BTC"][i] / ret["BTC"][i - L] - 1.0
            return ("BTC", None, 1 if r > 0 else -1)
        cands.append(("S5_single_leg_momentum", f"L={L}/R={R}",
                      periodic_trades(m, R, pick)))

    return cands


def robustness(m: pd.DataFrame) -> None:
    """对唯一扣成本后为正的方向做防过拟合检查：网格成片性 + 多空/时段/极端段拆分。"""
    btc = m["BTC_close"].to_numpy()
    n = len(m)
    print("\n" + "=" * 96)
    print("稳健性检查：唯一为正的方向，是「成片信号」还是单点过拟合？")
    print("=" * 96)

    def pick_factory(L):
        def pick(i):
            r = btc[i] / btc[i - L] - 1.0
            return ("BTC", None, 1 if r > 0 else -1)
        return pick

    grid = []
    for L in (12, 24, 36, 48, 72):
        for R in (48, 72, 96, 120, 168):
            r = evaluate(m, "grid", f"L={L}/R={R}",
                         periodic_trades(m, R, pick_factory(L)))
            if r:
                grid.append((L, R, r["n_trades"], r["net_bp_per_trade"],
                             r["cum_net_bp"]))
    g = pd.DataFrame(grid, columns=["L", "R", "n", "net_bp", "cum_net_bp"])
    pos = g[g["net_bp"] > 0]
    print(f"\n[1] 参数网格 {len(g)} 组，扣成本后单笔净收益为正：{len(pos)} 组")
    print("    单笔净 bp（行 L / 列 R）：")
    print(g.pivot(index="L", columns="R", values="net_bp").round(1).to_string())
    if len(pos):
        print("    为正的组合：", ", ".join(
            f"L={int(x.L)}/R={int(x.R)}({x.net_bp:.1f}bp)" for x in pos.itertuples()))

    # 最佳单点拆分
    trades = periodic_trades(m, 72, pick_factory(24))
    rec = []
    for (i, j, a, b, s) in trades:
        gbp = seg_gross_bp(m, i, j, a, b, s)
        if np.isfinite(gbp):
            rec.append((i, s, gbp - COST_BP))
    d = pd.DataFrame(rec, columns=["i", "sign", "net_bp"])
    d["month"] = pd.to_datetime(m["ts"].to_numpy()[d["i"].to_numpy()],
                                unit="s").strftime("%Y-%m")
    print(f"\n[2] 单腿动量 L=24/R=72 共 {len(d)} 笔，累计净 {d['net_bp'].sum():.1f}bp")
    print("    多 / 空拆分：")
    print(d.assign(dir=np.where(d["sign"] > 0, "做多", "做空"))
          .groupby("dir")["net_bp"].agg(["count", "mean", "sum"])
          .round(1).to_string())
    half = n // 2
    print("    前半段 / 后半段：")
    print(d.assign(seg=np.where(d["i"] < half, "前半", "后半"))
          .groupby("seg")["net_bp"].agg(["count", "mean", "sum"])
          .round(1).to_string())
    monthly = d.groupby("month")["net_bp"].sum().round(1)
    print("    逐月净 bp：", ", ".join(f"{k}:{v:+.0f}" for k, v in monthly.items()))
    # 极端段 2026-02（config 冻结门槛段）
    lo, hi = 1770163200, 1770681600
    ts_i = m["ts"].to_numpy()[d["i"].to_numpy()]
    in_ext = (ts_i >= lo) & (ts_i <= hi)
    print(f"    2026-02 极端段：{in_ext.sum()} 笔，净 "
          f"{d.loc[in_ext, 'net_bp'].sum():+.1f}bp｜剔除该段后 "
          f"{d.loc[~in_ext, 'net_bp'].sum():+.1f}bp")


def main() -> int:
    m = load()
    print(f"数据：{len(m)} bar × {len(SYMS)} 币  "
          f"({pd.to_datetime(m['ts'].iat[0], unit='s')} → "
          f"{pd.to_datetime(m['ts'].iat[-1], unit='s')} UTC)")
    print(f"成本口径：{COST_BP:.0f}bp/笔（开+平）｜冻结门槛 成本/毛利 ≤ "
          f"{GATE_COST_OVER_GROSS}｜M8 实测比值 1.784 → 需提升 {NEED_MULT:.2f} 倍\n")

    rows = []
    for name, params, trades in build_candidates(m):
        r = evaluate(m, name, params, trades)
        if r is not None:
            rows.append(r)
    if not rows:
        print("无有效结果"); return 1
    df = pd.DataFrame(rows)
    base = df.loc[df["strategy"] == "B0_mean_reversion", "gross_bp_per_trade"]
    if len(base) == 0 or base.iloc[0] <= 0:
        print("⚠️ 基准毛利非正，无法计算相对倍数；只报绝对值。")
        df["vs_baseline"] = np.nan
    else:
        b0 = float(base.iloc[0])
        df["vs_baseline"] = (df["gross_bp_per_trade"] / b0).round(2)
        df["enough_uplift"] = df["vs_baseline"] >= NEED_MULT
        print(f"基准 B0 单笔毛利 {b0:.2f}bp｜成本/毛利 "
              f"{df.loc[df['strategy']=='B0_mean_reversion','cost_over_gross'].iloc[0]:.3f}"
              f"（M8 实测 1.784；绝对量级口径不同，以相对倍数为准）\n")

    OUT.mkdir(parents=True, exist_ok=True)
    csv = OUT / "strategy_probe.csv"
    df.to_csv(csv, index=False, encoding="utf-8")

    show = ["strategy", "params", "n_trades", "gross_bp_per_trade",
            "net_bp_per_trade", "cost_over_gross", "vs_baseline",
            "win_rate", "cum_net_bp", "sharpe_rough", "gate_cost_ok"]
    with pd.option_context("display.max_rows", 100, "display.width", 200):
        print(df[show].to_string(index=False))

    passed = df[df["gate_cost_ok"]] if "gate_cost_ok" in df else pd.DataFrame()
    enough = df[df.get("enough_uplift", False)] if "enough_uplift" in df else pd.DataFrame()
    print(f"\n合计 {len(df)} 组｜直接通过成本门槛 {len(passed)} 组｜"
          f"相对基准 ≥{NEED_MULT:.2f}×  {len(enough)} 组")
    if len(enough):
        print(enough[["strategy", "params", "gross_bp_per_trade",
                      "vs_baseline", "cum_net_bp"]].to_string(index=False))
    print(f"\n产物：{csv}")
    robustness(m)
    print("\n⚠️ 预筛 ≠ M8 验收：夏普/回撤/Calmar/极端段/拒单率仍需完整 M8 判定。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
