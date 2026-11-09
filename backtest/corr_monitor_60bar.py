# -*- coding: utf-8 -*-
"""滚动 60bar log 价 corr 监控：9 币全组合（C(9,2)=36 对）
用 1h_410d 数据算滚动 60bar log 价 Pearson corr 序列，
标注跌破 0.70 的时点 + 跌破次数，归档 backtest/output/corr_monitor_60bar.md。

供 B 立场相关性监控实现参考。不写监控代码，只出数据。
"""
import itertools
import os
import datetime
import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COINS = ["BTC", "ETH", "SOL", "XRP", "DOGE", "BCH", "LTC", "AVAX", "ADA"]
WIN = 60          # 滚动窗口 bar 数（1h bar = 60h ≈ 2.5 天）
THRESHOLD = 0.70  # B 立场：跌破 0.70 → 自动平仓 + 冻结 7 天


def load(sym):
    p = os.path.join(ROOT, "data", "cache", f"{sym}_USDT_1h_410d.parquet")
    df = pd.read_parquet(p).sort_values("ts").reset_index(drop=True)
    df["ts"] = pd.to_datetime(df["ts"], unit="s", utc=True)
    return df.set_index("ts")["close"]


def rolling_corr(a, b, win):
    """对齐后滚动 win bar log 价 Pearson corr。返回 (ts_series, corr_series)。"""
    s = a.index.intersection(b.index)
    la, lb = np.log(a.loc[s]), np.log(b.loc[s])
    r = la.rolling(win).corr(lb)
    return s, r


def main():
    now_str = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    series = {}
    for c in COINS:
        series[c] = load(c)

    rows = []
    break_events = []  # 所有跌破事件（币对, 时点）

    for A, B in itertools.combinations(COINS, 2):
        s, r = rolling_corr(series[A], series[B], WIN)
        valid = r.dropna()
        cur_corr = float(valid.iloc[-1]) if len(valid) else float("nan")
        below = valid[valid < THRESHOLD]
        n_below = int(len(below))
        last_below_ts = str(below.index[-1]) if n_below else "无"
        # 跌破 0.70 后回升再跌破算多次；这里记录所有 < 阈值的时点（连续段按首次计）
        # 用"进入跌破区间的次数"统计（更贴近监控实现）
        is_below = (r < THRESHOLD).astype(int)
        # 连续段合并：1→0 边界
        enter = int((is_below.diff() == 1).sum())
        # 最近跌破时点
        below_idx = valid.index[valid < THRESHOLD]
        last_ts = str(below_idx[-1]) if len(below_idx) else "无"
        rows.append({
            "pair": f"{A}/{B}",
            "cur_corr": round(cur_corr, 4),
            "n_below": n_below,
            "n_episodes": enter,
            "last_below_ts": last_ts,
        })
        for t in below_idx:
            break_events.append((f"{A}/{B}", str(t), float(r.loc[t])))

    df = pd.DataFrame(rows).sort_values("n_episodes", ascending=False).reset_index(drop=True)
    top3 = df.head(3)

    # ---------- 写报告 ----------
    report = []
    report.append("# 滚动 60bar log 价 corr 监控（9 币 × 36 对）\n")
    report.append(f"生成时间：{now_str}\n")
    report.append(f"数据：data/cache/{{SYM}}_USDT_1h_410d.parquet（9 币，各 9841 bars ≈ 410 天）\n")
    report.append(f"方法：滚动 {WIN}bar log 价 Pearson corr（窗口 = {WIN}h ≈ {WIN/24:.1f} 天）；"
                 f"跌破阈值 = {THRESHOLD}。\n")
    report.append("用途：供 B 立场相关性监控实现参考（跌破 0.70 → 自动平仓 + 冻结 7 天）。"
                 "本文件只出数据，不写监控代码。\n")
    report.append("## 跌破 0.70 最多的 3 个币对\n")
    for _, r in top3.iterrows():
        report.append(f"- **{r['pair']}**：跌破区间 {r['n_episodes']} 次，共 {r['n_below']} 个 bar 低于阈值，"
                     f"最近跌破时点 {r['last_below_ts']}，当前 corr={r['cur_corr']}")
    report.append("")
    report.append("## 全 36 币对跌破统计（按跌破区间次数降序）\n")
    report.append(df.to_string(index=False))
    report.append("")
    report.append("## 跌破事件明细（全部 < 0.70 的时点，供监控回看）\n")
    ev_df = pd.DataFrame(break_events, columns=["pair", "ts_utc", "corr_at_ts"])
    ev_df = ev_df.sort_values(["pair", "ts_utc"]).reset_index(drop=True)
    report.append(f"共 {len(ev_df)} 条跌破记录。按币对汇总：\n")
    if len(ev_df):
        report.append(ev_df.to_string(index=False))
    else:
        report.append("（无跌破 0.70 事件）")

    os.makedirs(os.path.join(ROOT, "backtest", "output"), exist_ok=True)
    out_md = os.path.join(ROOT, "backtest", "output", "corr_monitor_60bar.md")
    with open(out_md, "w", encoding="utf-8") as f:
        f.write("\n".join(report))

    out_json = os.path.join(ROOT, "backtest", "output", "corr_monitor_60bar.json")
    import json
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump({
            "generated_at": now_str, "window_bars": WIN, "threshold": THRESHOLD,
            "per_pair": [dict(r) for r in rows],
            "break_events": break_events,
        }, f, ensure_ascii=False, indent=2)

    print(f"written: {out_md}")
    print(f"written: {out_json}")
    print(f"\n跌破 0.70 最多的 3 对:")
    print(top3[["pair", "n_episodes", "n_below", "last_below_ts", "cur_corr"]].to_string(index=False))


if __name__ == "__main__":
    main()
