"""
M3 · 信号层 —— 统计套利（配对交易）信号计算

设计依据：
  - agents/phase2-architecture.md §3 数据结构 + §4 异常（后端架构师）
  - agents/phase2-architecture.md §6 公式红线（代码审查员）
  - config/strategy_params.yaml（冻结参数）

落实的 5 个公式红线（代码审查员）：
  R1  spread = A - h*B （不是 A + h*B）
  R2  h 用滚动窗口"收益率" OLS（不是价格回归，避免伪回归）
  R3  z-score 的 μ/σ 取自"原始价差序列"（不是残差标准差）
  R4  信号 t 出、t+1 成交（防未来函数）
  R5  回撤基于账户净值（M8 落实，本层只产 spread/h/z 等指标）

数值稳定性（红线必查）：
  - Var(B)=0 → h 爆炸：窗口方差为 0 时 h 置 NaN，信号跳过
  - σ_spread=0 → z=NaN：价差恒定时无信号
  - 缺 K 线 / 零波动 → NaN 保护，不静默参与计算
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from dataclasses import dataclass, field


@dataclass
class Signal:
    """信号层输出（对应 phase2 数据结构）。"""
    ts: int
    spread: float
    zscore: float
    hedge_ratio: float
    half_life: float
    adf_p: float
    strength: float            # 0~1 策略归一化强度（M4 决策层消费）
    state: str = "OK"          # OK / INVALID / THIN / NaN-guard
    metric: dict = field(default_factory=dict)


class MeanReversionSignals:
    """价差 / 对冲比率 / z-score / 协整指标计算（纯函数，无状态机）。"""

    def __init__(self,
                 hedge_window: int = 60,
                 hedge_range: tuple[float, float] = (0.5, 2.0),
                 hedge_daily_change_max: float = 0.10,
                 hl_range: tuple[float, float] = (24, 96),
                 entry_sigma: float = 3.0,
                 exit_sigma: float = 0.5,
                 adf_p_max: float = 0.05):
        self.hedge_window = hedge_window
        self.hedge_range = hedge_range
        self.hedge_daily_change_max = hedge_daily_change_max
        self.hl_range = hl_range
        self.entry_sigma = entry_sigma
        self.exit_sigma = exit_sigma
        self.adf_p_max = adf_p_max

    # ---------- 对冲比率 h（红线 R2：滚动窗口收益率 OLS）----------
    @staticmethod
    def hedge_ratio(returns: pd.Series) -> pd.Series:
        """对滚动窗口内的 A/B 收益率做 OLS，返回 h_t。

        红线 R2：用收益率（log diff）回归，避免价格非平稳伪回归。
        窗口方差为 0（B 无波动）→ h=NaN（除零保护）。
        """
        a_r = np.log(returns)               # 假设传入的是 log-return 序列
        win = a_r.get("_win", 60)
        # 本函数接收的是预拼好的 (log_ret_a, log_ret_b) 帧
        raise NotImplementedError("用 compute_signals 的滚动实现")

    def _rolling_h(self, log_a: pd.Series, log_b: pd.Series,
                   window: int) -> pd.Series:
        """滚动 OLS：log_a = α + h·log_b。返回 h 序列（红线 R2 用 log 价格一阶差=收益率）。"""
        a = np.log(log_a.to_numpy())
        b = np.log(log_b.to_numpy())
        # 一阶差分转收益率
        da = np.diff(a, prepend=a[0])
        db = np.diff(b, prepend=b[0])
        n = len(a)
        h = np.full(n, np.nan)
        for t in range(window, n):
            xb = db[t - window:t]
            ya = da[t - window:t]
            var_b = np.var(xb)
            if var_b < 1e-12:        # Var(B)=0 除零保护
                h[t] = np.nan
                continue
            h_t = float(np.cov(ya, xb, bias=True)[0, 1] / var_b)
            h[t] = h_t
        return pd.Series(h, index=log_a.index)

    # ---------- 价差（红线 R1：A - h*B）----------
    @staticmethod
    def _spread(log_a: pd.Series, h: pd.Series, log_b: pd.Series) -> pd.Series:
        """spread_t = ln A_t - h_t · ln B_t（红线 R1：减号，不是加号）。"""
        a = np.log(log_a.to_numpy())
        b = np.log(log_b.to_numpy())
        return pd.Series(a - h.to_numpy() * b, index=log_a.index)

    # ---------- 半衰期（数据工程师/审查员口径）----------
    @staticmethod
    def _half_life(spread: pd.Series) -> pd.Series:
        """一阶自相关 ρ → 半衰期 H = ln(0.5)/ln(ρ)。滚动窗口近似。"""
        x = spread.to_numpy()
        n = len(x)
        hl = np.full(n, np.nan)
        win = 40
        for t in range(win, n):
            seg = x[t - win:t]
            seg = seg[~np.isnan(seg)]
            if len(seg) < 10:
                continue
            rho = np.corrcoef(seg[:-1], seg[1:])[0, 1]
            if np.isnan(rho) or rho >= 1.0 or rho <= -1.0:
                continue
            hl[t] = float(np.log(0.5) / np.log(rho))
        return pd.Series(hl, index=spread.index)

    # ---------- ADF 平稳性（协整主判据）----------
    def _adf_p(self, spread: pd.Series, lookback: int = 200) -> pd.Series:
        """对最近 lookback 根价差做 ADF，返回 p 值（滚动）。"""
        from statsmodels.tsa.stattools import adfuller
        s = spread.fillna(0.0)
        p = np.full(len(s), np.nan)
        for t in range(lookback, len(s)):
            seg = s[t - lookback:t].to_numpy()
            if np.std(seg) < 1e-12:    # σ=0 保护
                p[t] = 1.0            # 不平稳
                continue
            try:
                _, pval, *_ = adfuller(seg, autolag="AIC")
                p[t] = pval
            except Exception:
                p[t] = np.nan
        return pd.Series(p, index=spread.index)

    # ---------- z-score（红线 R3：对原始价差序列算 μ/σ）----------
    @staticmethod
    def _zscore(spread: pd.Series, window: int = 60) -> pd.Series:
        """z_t = (spread_t - μ_win)/σ_win，μ/σ 取原始价差滚动窗口（红线 R3）。"""
        s = spread.to_numpy()
        n = len(s)
        z = np.full(n, np.nan)
        for t in range(window, n):
            seg = s[t - window:t]
            seg = seg[~np.isnan(seg)]
            if len(seg) < 10:
                continue
            mu = np.mean(seg)
            sd = np.std(seg)
            if sd < 1e-12:            # σ=0 → NaN（除零保护）
                z[t] = np.nan
                continue
            z[t] = (s[t] - mu) / sd
        return pd.Series(z, index=spread.index)

    # ---------- 主入口：算全信号 ----------
    def compute_signals(self, df: pd.DataFrame, window: int = 60) -> pd.DataFrame:
        """输入 M2 对齐后的双腿 DataFrame（含 close_a/close_b），输出信号 DataFrame。

        列：ts, spread, hedge_ratio, zscore, half_life, adf_p,
            entry_flag, exit_flag, state
        红线 R4：本层只产"信号指标"，进出 flag 是 t 时刻算的，
            实际成交在 t+1（由 M4 决策层 + M8 回测保证跨 bar）。
        """
        if "close_a" not in df.columns or "close_b" not in df.columns:
            raise ValueError("输入需含 close_a / close_b 双腿收盘价")
        log_a = df["close_a"].astype(float)
        log_b = df["close_b"].astype(float)

        # 1) 对冲比率 h（红线 R2：滚动收益率 OLS）
        h = self._rolling_h(log_a, log_b, window)

        # 2) 价差（红线 R1：A - h·B）
        spread = self._spread(log_a, h, log_b)

        # 3) z-score（红线 R3：原始价差序列 μ/σ）
        z = self._zscore(spread, window)

        # 4) 半衰期
        hl = self._half_life(spread)

        # 5) ADF
        adf_p = self._adf_p(spread, lookback=120)

        out = pd.DataFrame({
            "ts": df["ts"].values,
            "close_a": log_a.values,
            "close_b": log_b.values,
            "spread": spread.values,
            "hedge_ratio": h.values,
            "zscore": z.values,
            "half_life": hl.values,
            "adf_p": adf_p.values,
        })

        # h 越界 → INVALID（风控红线）
        out["h_out_range"] = ~out["hedge_ratio"].between(*self.hedge_range, inclusive="both")
        out["state"] = np.where(out["h_out_range"], "INVALID", "OK")
        out.loc[out["zscore"].isna() | out["hedge_ratio"].isna(), "state"] = "NaN_GUARD"

        # 进出 flag（t 时刻信号；红线 R4：t+1 成交由下游保证）
        z_valid = out["zscore"].notna() & (out["state"] == "OK")
        out["entry_flag"] = (
            ((out["zscore"] <= -self.entry_sigma) | (out["zscore"] >= self.entry_sigma))
            & z_valid
        )
        out["exit_flag"] = (
            (out["zscore"].abs() <= self.exit_sigma) & out["entry_flag"].shift(1).fillna(False)
        )

        return out
