# -*- coding: utf-8 -*-
"""410d 窗口 币对重筛：数据驱动找统计套利（配对均值回归）候选。"""
import warnings
warnings.filterwarnings("ignore")
import itertools
import numpy as np
import pandas as pd
from statsmodels.tsa.stattools import adfuller
from statsmodels.stats import urcg  # noqa
from statsmodels.tsa.vector_ar.vecstats import Johansen
from statsmodels.regression.linear_model import OLS

ROOT = "C:/Users/Administrator/WorkBuddy AI/2026-11-06-20-40-51"
COINS = ["BTC", "ETH", "SOL", "XRP", "DOGE"]

# ---------- 载入 1h 深数据 ----------
data = {}
for c in COINS:
    df = pd.read_parquet(f"{ROOT}/data/cache/{c}_USDT_1h_410d.parquet")
    df = df.sort_values("ts").reset_index(drop=True)
    df["ts"] = pd.to_datetime(df["ts"], unit="s", utc=True)
    data[c] = df.set_index("ts")

# 载入 4h / 1d 辅助
aux4h, aux1d = {}, {}
for c in COINS:
    d4 = pd.read_parquet(f"{ROOT}/data/cache/{c}_USDT_4h_400d.parquet").sort_values("ts")
    d4["ts"] = pd.to_datetime(d4["ts"], unit="s", utc=True)
    aux4h[c] = d4.set_index("ts")["close"]
    d1 = pd.read_parquet(f"{ROOT}/data/cache/{c}_USDT_1d_900d.parquet").sort_values("ts")
    d1["ts"] = pd.to_datetime(d1["ts"], unit="s", utc=True)
    aux1d[c] = d1.set_index("ts")["close"]

IQR_LO, IQR_HI = 368, 856  # 半衰期(bar=小时) IQR 区间


def eg_cointeg(x, y):
    """Engle-Granger: OLS y~x, ADF on residual. 返回 (EG_p, h_beta, resid)。"""
    x = np.log(x.values)
    y = np.log(y.values)
    n = min(len(x), len(y))
    x, y = x[-n:], y[-n:]
    X = sm_ols = np.column_stack([x, np.ones(n)])
    beta = np.linalg.lstsq(X, y, rcond=None)[0]
    resid = y - X @ beta
    h = beta[0]
    p, _ = adfuller(resid, autolag="AIC")
    return p, h, resid


def half_life_ic(resid):
    """价差自相关法半衰期(bar=小时)。AR(1): phi = rho(1)-rho(2), HL = -ln2/ln(phi)。"""
    r = np.array(resid)
    r = r - r.mean()
    def rho(k):
        return np.corrcoef(r[:-k], r[k:])[0, 1]
    phi = rho(1) - rho(2)
    if not np.isfinite(phi) or phi <= 0:
        # 无负自相关 -> 无限半衰期(不回归)
        return np.nan
    if phi >= 1:
        return np.nan
    return np.log(2) / np.log(1.0 / phi)


def sp_corr(x, y):
    return np.corrcoef(np.log(x.values), np.log(y.values))[0, 1]


rows = []
for A, B in itertools.combinations(COINS, 2):
    xa, xb = data[A]["close"], data[B]["close"]
    # 对齐
    s = xa.index.intersection(xb.index)
    pa, pb = data[A].loc[s, "close"], data[B].loc[s, "close"]
    qa, qb = data[A].loc[s, "quote_volume"], data[B].loc[s, "quote_volume"]

    # 1) 全 410 天 close 价 Pearson corr（用 log 价）
    corr = sp_corr(pa, pb)

    # 2) 滚动 60bar 收益率 OLS 对冲 h
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
    h_med = float(np.median(hs))
    h_range = f"{np.min(hs):.2f}~{np.max(hs):.2f}"

    # 3) 价差 & z-score
    logpa, logpb = np.log(pa.values), np.log(pb.values)
    n = min(len(logpa), len(logpb))
    logpa, logpb = logpa[-n:], logpb[-n:]
    q, r = np.linalg.lstsq(np.column_stack([logpb, np.ones(n)]), logpa, rcond=None)
    spread = logpa - (q[0] * logpb + q[1])
    z = (spread - spread.mean()) / (spread.std() + 1e-12)
    spread_stab = spread.std() / (abs(spread.mean()) + 1e-12)

    # 4) 半衰期（自相关法, bar=小时）
    hl = half_life_ic(spread)

    # 5) 单日成交额（美元, 1h quote_volume ×24, 全窗口均值）
    daynotional_A = float((qa.values * 24).mean() / n * 1)  # quote_volume 是 USD 成交额/h
    # quote_volume 通常已是 USDT 计价(美元)；用 ×24 得日均
    daynotional_A = float(qa.mean() * 24)
    daynotional_B = float(qb.mean() * 24)

    # 6) EG 协整（1h 全样本；辅助 4h）
    eg_p_1h, eg_h_1h, _ = eg_cointeg(pa, pb)
    ea, eb = aux4h[A], aux4h[B]
    s2 = ea.index.intersection(eb.index)
    eg_p_4h, eg_h_4h, _ = eg_cointeg(ea.loc[s2], eb.loc[s2])

    # 7) Johansen (4h 辅助, p_values[0]=trace 最小统计量对应最小特征值...)
    jh_stat, jh_p = None, None
    try:
        y4 = np.column_stack([ea.loc[s2].values, eb.loc[s2].values])
        logy = np.log(y4)
        j = Johansen(logy, det="c", trend="none")
        j.crit = j.critical_values
        jh_stat = float(j.lmax_stat[0])
        jh_p = float(j.p_values[0])
    except Exception:
        pass

    # ---------- 打分 ----------
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

    rows.append({
        "pair": f"{A}/{B}", "corr": round(corr, 4),
        "EG_p_1h": round(eg_p_1h, 4), "EG_p_4h": round(eg_p_4h, 4),
        "EG_p_min": round(eg_p, 4), "EG_h_beta": round(eg_h_1h, 4),
        "h_med": round(h_med, 4), "h_range": h_range,
        "HL_h": round(hl, 1) if np.isfinite(hl) else None,
        "dayUSD_A": round(daynotional_A / 1e6, 1), "dayUSD_B": round(daynotional_B / 1e6, 1),
        "spread_stab": round(spread_stab, 3),
        "Jh_stat": round(jh_stat, 2) if jh_stat else None,
        "Jh_p": round(jh_p, 4) if jh_p is not None else None,
        "score": score, "notes": "; ".join(notes),
    })

res = pd.DataFrame(rows).sort_values("score", ascending=False)
pd.set_option("display.width", 200)
pd.set_option("display.max_columns", 50)
print(res.to_string(index=False))

# ---------- 写出报告 ----------
top = res.head(4)
report = []
report.append("# 410 天窗口 统计套利（配对均值回归）币对重筛\n")
report.append(f"数据: data/cache/{{BTC,ETH,SOL,XRP,DOGE}}_USDT_1h_410d.parquet（{len(data['BTC'])} bars × 24 ≈ 410 天）\n")
report.append("辅助: 4h_400d（EG 双测 + Johansen）、1d_900d\n")
report.append("方法: 全样本 log 价 Pearson corr；60bar 滚动收益率 OLS 对冲 h；价差 z-score；"
              "AR(1) 自相关半衰期；Engle-Granger OLS+ADF（1h & 4h）；Johansen trace（4h）；"
              "日成交 = 1h quote_volume ×24（均值）；价差稳定度 = std/|mean|。\n")
report.append("打分: corr≥0.85 (+2)；EG_p<0.01 (+3)/<0.05 (+2)/<0.10 (+1)；"
              "半衰期∈IQR[368,856]h (+3)；日成交≥$50M (+2)/≥$10M (+1)；"
              "价差稳定度<0.35 (+2)/<0.6 (+1)。\n")
report.append("## 全 10 币对指标\n")
cols = ["pair", "corr", "EG_p_1h", "EG_p_4h", "EG_p_min", "EG_h_beta", "h_med", "h_range",
        "HL_h", "dayUSD_A", "dayUSD_B", "spread_stab", "Jh_stat", "Jh_p", "score", "notes"]
report.append(res[cols].to_string(index=False))
report.append("\n## 候选（按 score）\n")
for i, r in top.iterrows():
    report.append(f"### {r['pair']}  score={r['score']}/10")
    report.append(f"- corr={r['corr']}  EG_p={r['EG_p_min']}(1h {r['EG_p_1h']}/4h {r['EG_p_4h']})  "
                  f"EG_h={r['EG_h_beta']}")
    report.append(f"- 滚动h: med={r['h_med']} range={r['h_range']}  半衰期={r['HL_h']}h (IQR[368,856])")
    report.append(f"- 日成交: {r['dayUSD_A']}M / {r['dayUSD_B']}M  价差稳定度={r['spread_stab']}")
    if r["Jh_p"] is not None:
        report.append(f"- Johansen trace: stat={r['Jh_stat']}, p={r['Jh_p']}")
    report.append(f"- 备注: {r['notes']}\n")

with open(f"{ROOT}/backtest/output/pair_rescreen_410d.md", "w", encoding="utf-8") as f:
    f.write("\n".join(report))
print("written", f"{ROOT}/backtest/output/pair_rescreen_410d.md")
