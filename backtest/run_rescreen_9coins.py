# -*- coding: utf-8 -*-
"""9 币重筛：扩池后 36 币对全指标表 + 候选 top5 + 显著对数量统计。

方法照搬 tmp_rescreen_410d.py：
  log 价 Pearson corr + 60bar 滚动收益率 OLS 对冲 h + 价差 z-score
  + AR(1) 自相关半衰期 + EG 残差 ADF p + Johansen trace
  + 日成交 + 价差稳定度。

不写交易信号，只出重筛指标。输出 backtest/output/pair_rescreen_9coins.md。
"""
import warnings
warnings.filterwarnings("ignore")
import os
import sys
import itertools
import json
import datetime
import numpy as np
import pandas as pd
from statsmodels.tsa.stattools import adfuller
from statsmodels.tsa.vector_ar.vecm import coint_johansen

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COINS = ["BTC", "ETH", "SOL", "XRP", "DOGE", "BCH", "LTC", "AVAX", "ADA"]
NEW_COINS = ["BCH", "LTC", "AVAX", "ADA"]
OLD_COINS = ["BTC", "ETH", "SOL", "XRP", "DOGE"]

# ---------- 载入 1h 深数据（9 币） ----------
data = {}
for c in COINS:
    p = os.path.join(ROOT, "data", "cache", f"{c}_USDT_1h_410d.parquet")
    df = pd.read_parquet(p)
    df = df.sort_values("ts").reset_index(drop=True)
    df["ts"] = pd.to_datetime(df["ts"], unit="s", utc=True)
    data[c] = df.set_index("ts")

# 载入 4h / 1d 辅助（9 币）
aux4h, aux1d = {}, {}
for c in COINS:
    d4 = pd.read_parquet(os.path.join(ROOT, "data", "cache", f"{c}_USDT_4h_400d.parquet")).sort_values("ts")
    d4["ts"] = pd.to_datetime(d4["ts"], unit="s", utc=True)
    aux4h[c] = d4.set_index("ts")["close"]
    d1 = pd.read_parquet(os.path.join(ROOT, "data", "cache", f"{c}_USDT_1d_900d.parquet")).sort_values("ts")
    d1["ts"] = pd.to_datetime(d1["ts"], unit="s", utc=True)
    aux1d[c] = d1.set_index("ts")["close"]

IQR_LO, IQR_HI = 368, 856  # 半衰期(bar=小时) IQR 区间（同 410d 口径）


def eg_cointeg(x, y):
    x = np.log(x.values)
    y = np.log(y.values)
    n = min(len(x), len(y))
    x, y = x[-n:], y[-n:]
    X = np.column_stack([x, np.ones(n)])
    beta = np.linalg.lstsq(X, y, rcond=None)[0]
    resid = y - X @ beta
    h = beta[0]
    p = adfuller(resid, autolag="AIC", result_object=False)[1]
    return p, h, resid


def half_life_ic(resid):
    r = np.array(resid)
    r = r - r.mean()
    def rho(k):
        return np.corrcoef(r[:-k], r[k:])[0, 1]
    phi = rho(1) - rho(2)
    if not np.isfinite(phi) or phi <= 0:
        return np.nan
    if phi >= 1:
        return np.nan
    return np.log(2) / np.log(1.0 / phi)


def sp_corr(x, y):
    return np.corrcoef(np.log(x.values), np.log(y.values))[0, 1]


rows = []
for A, B in itertools.combinations(COINS, 2):
    xa, xb = data[A]["close"], data[B]["close"]
    s = xa.index.intersection(xb.index)
    pa, pb = data[A].loc[s, "close"], data[B].loc[s, "close"]
    qa, qb = data[A].loc[s, "quote_volume"], data[B].loc[s, "quote_volume"]

    corr = sp_corr(pa, pb)

    ra = np.log(pa).diff()
    rb = np.log(pb).diff()
    win = 60
    hs = []
    for i in range(win, len(ra)):
        a = ra.iloc[i - win:i]
        b = rb.iloc[i - win:i]
        mask = a.notna() & b.notna()
        if mask.sum() < win // 2:
            continue
        bv, av = b[mask].values, a[mask].values
        h = np.cov(av, bv)[0, 1] / np.var(bv)
        hs.append(h)
    h_med = float(np.median(hs)) if hs else float("nan")
    h_range = f"{np.min(hs):.2f}~{np.max(hs):.2f}" if hs else "n/a"

    logpa, logpb = np.log(pa.values), np.log(pb.values)
    n = min(len(logpa), len(logpb))
    logpa, logpb = logpa[-n:], logpb[-n:]
    q = np.linalg.lstsq(np.column_stack([logpb, np.ones(n)]), logpa, rcond=None)[0]
    spread = logpa - (q[0] * logpb + q[1])
    z = (spread - spread.mean()) / (spread.std() + 1e-12)
    spread_stab = spread.std() / (abs(spread.mean()) + 1e-12)

    hl = half_life_ic(spread)

    daynotional_A = float(qa.mean() * 24)
    daynotional_B = float(qb.mean() * 24)

    eg_p_1h, eg_h_1h, _ = eg_cointeg(pa, pb)
    ea, eb = aux4h[A], aux4h[B]
    s2 = ea.index.intersection(eb.index)
    eg_p_4h, eg_h_4h, _ = eg_cointeg(ea.loc[s2], eb.loc[s2])

    jh_stat, jh_p = None, None
    try:
        y4 = np.column_stack([ea.loc[s2].values, eb.loc[s2].values])
        logy = np.log(y4)
        jres = coint_johansen(logy, 1, 2)  # det_order=1(含常数), k_ar_diff=2
        jh_stat = float(jres.trace_stat[0])
        # trace_stat_crit_vals 是 shape (k,3) 的数组，列对应 0.01/0.05/0.95 临界值
        cv = jres.trace_stat_crit_vals
        # 取 0.05 临界值（第3列索引2）比较
        crit05 = float(cv[0, 2]) if cv.ndim >= 2 and cv.shape[1] >= 3 else np.inf
        jh_sig = bool(jh_stat > crit05)
        jh_p = 0.0499 if jh_sig else 0.9999  # 近似二值指示，非精确 p
    except Exception:
        pass

    score = 0.0
    notes = []
    if corr >= 0.85:
        score += 2
    else:
        notes.append(f"corr<0.85({corr:.3f})")
    eg_p = min(eg_p_1h, eg_p_4h)
    if eg_p < 0.01:
        score += 3
    elif eg_p < 0.05:
        score += 2
    elif eg_p < 0.10:
        score += 1
    else:
        notes.append(f"EG弱(p={eg_p:.3f})")
    if np.isfinite(hl) and IQR_LO <= hl <= IQR_HI:
        score += 3
    elif np.isfinite(hl):
        notes.append(f"HL={hl:.0f}h在IQR外")
    else:
        notes.append("HL缺失(不回归)")
    if min(daynotional_A, daynotional_B) >= 50e6:
        score += 2
    elif min(daynotional_A, daynotional_B) >= 10e6:
        score += 1
        notes.append("日成交偏低")
    else:
        notes.append("日成交不足$10M")
    if spread_stab < 0.35:
        score += 2
    elif spread_stab < 0.6:
        score += 1
        notes.append("价差稳定度一般")

    is_new = (A in NEW_COINS) or (B in NEW_COINS)
    is_homog = (A in ("BTC", "ETH") and B in ("BCH", "LTC")) or (B in ("BTC", "ETH") and A in ("BCH", "LTC"))

    rows.append({
        "pair": f"{A}/{B}",
        "corr": round(corr, 4),
        "EG_p_1h": round(eg_p_1h, 4),
        "EG_p_4h": round(eg_p_4h, 4),
        "EG_p_min": round(eg_p, 4),
        "EG_h_beta": round(eg_h_1h, 4),
        "h_med": round(h_med, 4) if np.isfinite(h_med) else None,
        "h_range": h_range,
        "HL_h": round(hl, 1) if np.isfinite(hl) else None,
        "dayUSD_A": round(daynotional_A / 1e6, 1),
        "dayUSD_B": round(daynotional_B / 1e6, 1),
        "spread_stab": round(float(spread_stab), 4),
        "Jh_stat": round(jh_stat, 2) if jh_stat else None,
        "Jh_p": round(jh_p, 4) if jh_p is not None else None,
        "score": score,
        "new_pair": is_new,
        "homog": is_homog,
        "notes": "; ".join(notes),
    })

res = pd.DataFrame(rows).sort_values("score", ascending=False)
res = res.reset_index(drop=True)

# ---------- 统计 ----------
sig_pairs = res[res["EG_p_min"] < 0.05]
n_sig = len(sig_pairs)
n_sig_new = len(sig_pairs[sig_pairs["new_pair"]])

# 同质对（BCH/LTC × BTC/ETH）指标
homog = res[res["homog"]]

# ---------- 写报告 ----------
now_str = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
top5 = res.head(5)

report = []
report.append("# 9 币重筛（4h/1d 扩池）\n")
report.append(f"生成时间：{now_str}\n")
report.append(f"币池（{len(COINS)}）：{', '.join(COINS)}\n")
report.append(f"全组合币对：C({len(COINS)},2) = {len(res)} 对\n")
report.append("数据：data/cache/{{SYM}}_USDT_1h_410d.parquet + 4h_400d + 1d_900d（与老币同口径）\n")
report.append("方法：照搬 tmp_rescreen_410d.py（log 价 Pearson corr；60bar 滚动 OLS 对冲 h；"
              "价差 z-score；AR(1) 半衰期；EG OLS+ADF 1h&4h；Johansen trace 4h；日成交；价差稳定度）。\n")
report.append("打分：corr≥0.85(+2)；EG_p<0.01(+3)/<0.05(+2)/<0.10(+1)；"
              "半衰期∈IQR[368,856]h(+3)；日成交≥$50M(+2)/≥$10M(+1)；价差稳定度<0.35(+2)/<0.6(+1)。"
              "（总分满 10）\n")
report.append("## 显著对统计（EG_p_min < 0.05）\n")
report.append(f"- 全部 {len(res)} 币对中 EG_p_min<0.05：{n_sig} 对")
report.append(f"- 其中含新币（BCH/LTC/AVAX/ADA）：{n_sig_new} 对\n")
report.append(f"显著对清单：")
for _, r in sig_pairs.iterrows():
    report.append(f"  - {r['pair']}  corr={r['corr']}  EG_p_min={r['EG_p_min']}  "
                 f"1h={r['EG_p_1h']}  4h={r['EG_p_4h']}  score={r['score']}")
report.append("")

report.append("## 同质对（BCH/LTC × BTC/ETH）指标\n")
if len(homog):
    cols_h = ["pair", "corr", "EG_p_1h", "EG_p_4h", "EG_p_min", "HL_h", "score", "notes"]
    report.append(homog[cols_h].to_string(index=False))
else:
    report.append("（本轮 9 币中未出现 BCH/LTC 与 BTC/ETH 的直接组合）")
report.append("")

report.append("## 全 36 币对指标表\n")
cols = ["pair", "corr", "EG_p_1h", "EG_p_4h", "EG_p_min", "EG_h_beta", "h_med",
        "HL_h", "dayUSD_A", "dayUSD_B", "spread_stab", "Jh_p", "score", "new_pair", "notes"]
report.append(res[cols].to_string(index=False))
report.append("")

report.append("## 候选 Top5（按 score）\n")
for i, r in top5.iterrows():
    report.append(f"### {r['pair']}  score={r['score']}/10")
    report.append(f"- corr={r['corr']}  EG_p_min={r['EG_p_min']}(1h {r['EG_p_1h']}/4h {r['EG_p_4h']})  "
                 f"EG_h={r['EG_h_beta']}")
    hl_s = f"{r['HL_h']}h" if r['HL_h'] is not None else "N/A(不回归)"
    report.append(f"- 半衰期={hl_s} (IQR[368,856])  日成交: {r['dayUSD_A']}M / {r['dayUSD_B']}M  "
                 f"价差稳定度={r['spread_stab']}")
    if r["Jh_p"] is not None:
        report.append(f"- Johansen trace p={r['Jh_p']}")
    report.append(f"- 备注: {r['notes']}\n")

report.append("## 说明\n")
report.append("- 本报告仅为重筛指标输出，不定义交易信号、不下'是否扩池'结论（那是金融专家职责）。")
report.append("- new_pair=True 表示该对至少含一个新币（BCH/LTC/AVAX/ADA）。")
report.append("- 滑点实测与 corr 监控见 pair_slippage_check.md / corr_monitor_60bar.md。")

os.makedirs(os.path.join(ROOT, "backtest", "output"), exist_ok=True)
out_md = os.path.join(ROOT, "backtest", "output", "pair_rescreen_9coins.md")
with open(out_md, "w", encoding="utf-8") as f:
    f.write("\n".join(report))

# 同时落 json 便于程序读取
out_json = os.path.join(ROOT, "backtest", "output", "pair_rescreen_9coins.json")
res_json = res.drop(columns=["new_pair"]).where(pd.notnull(res), None)
res_json.to_json(out_json, orient="records", force_ascii=False, indent=2)

print(f"written: {out_md}")
print(f"written: {out_json}")
print(f"\n全 {len(res)} 币对；EG_p_min<0.05 显著 {n_sig} 对（含新币 {n_sig_new} 对）")
print("Top5:")
print(top5[["pair","corr","EG_p_min","score"]].to_string(index=False))
print("\n同质对:")
if len(homog):
    print(homog[["pair","corr","EG_p_1h","EG_p_4h","EG_p_min","HL_h","score"]].to_string(index=False))
