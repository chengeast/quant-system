# -*- coding: utf-8 -*-
"""I-3 阶段二 · A 股技术派因子统一口径回测（后端架构师产出，PM 承派 + 验收）

老板 2026-11-15 指令（逐字）：
  17:37「不对，A股市场里不存在配对的股票，找不到的，别浪费时间！」→ 终止配对/统计套利线
  17:40「对A股主要应该回测的是技术派的策略。应该收集技术派的因子，回测因子的效果。」
  17:46「是的，散户是无法直接对A股股票做空的。」→ 单腿纯多头、无做空腿、无对冲
  18:11「请继续执行任务」

按 agents/souls/backend_architect.md 纪律：模块边界清晰（数据层 parquet ← 阶段一；
信号计算与成交层在本脚本内自洽；只读复用 src/core/timeframe.py 与 config 门槛值）；
接口可测试、可回滚（**新增脚本，保留 backtest/ 下全部旧脚本不动**；不改 src/、不改 config/）。

============================================================================
严格边界（逐项可验证）
============================================================================
  - **不 import M3 配对骨架 / StrategyRules 的价差逻辑**：本批无配对、无 spread、
    无 z-score 价差、无对冲比率 h、无融券、无 ETF 替代做空腿、无股指期货对冲。
    策略形态只能是「买入 → 持有 → 卖出平仓 → 空仓等待」（老板 17:37/17:46 裁决）。
  - 只读复用：`src.core.timeframe.resolve_timeframe("1d", 4, 252)` → bars_per_year=252、
    annualize_factor=√252（A 股已预留）；**不改 src/**。
  - 只读 config：`config/strategy_params.yaml` 的 backtest_gate / cost_model 门槛值
    （`min_sharpe: null` 正确读成 None，绝不 fallback 1.0/2.0），**一字不改、不写回**。
  - 成本常量独立写在脚本、**绝不写进 config**、**绝不套 Gate.io 现货 0.1%/边**
    （phase7 仅适用加密现货主线）。

============================================================================
单腿成本重新推导（不得照抄 I-1 配对 4 次成交公式）
============================================================================
  I-1 配对口径（**仅作对照，本批不采用**）：4 次成交（2 买 2 卖）、每腿名义 500、
  总名义 1000；`COST = 4*500*(佣金+过户+滑点) + 2*500*印花税 = 2.02 元 = 20.2bp`。
  本批**单腿 1 买 1 卖**（1 个标的、名义 NOTIONAL=1000），逐项重新推导：
    买入侧：佣金 0.025% + 过户 0.001% + 滑点 5bp        = 0.00025+0.00001+0.00050 = 7.6bp
    卖出侧：佣金 0.025% + 过户 0.001% + 滑点 5bp + 印花税 0.05%(卖出单边)
                                                        = 7.6bp + 5bp = 12.6bp
    单笔完整交易 = 7.6 + 12.6 = **20.2bp**（相对单腿名义 NOTIONAL）
              = NOTIONAL * (0.00076 + 0.00126) = 1000 * 0.00202 = 2.02 元
  ★ 数值与 I-1 的 20.2bp **巧合相同，但推导完全不同**：
    - I-1：4 次成交、印花税 2 次（2 卖出）、分母「双腿总名义 1000」、每腿 500；
    - 本批：2 次成交、印花税 1 次（1 卖出）、分母「单腿名义 1000」；
    - 巧合机理：两种口径下「买入侧总名义 = 1000、卖出侧总名义 = 1000」
      （I-1 = 2 腿×500；本批 = 1 腿×1000），故佣金/过户/滑点/印花税四项逐项金额相同
      → roundtrip bp 同为 20.2。**成交笔数不同(4 vs 2)、公式结构不同、分母定义不同**，
      **绝非复用 I-1 的 `4 * LEG_NOTIONAL * (...)` 公式**。
  四项成本数值全部「待老板确认的假设」（项目假设 / 公开费率单一代表值），
  A 股历史费率多次调整（印花税 2008/2023、过户费 2022），本批不分段回溯、统一单一代表值。

============================================================================
统一尺子 + 口径（沿用 17:34 未被撤回三条 + 17:46 做空约束）
============================================================================
  1. **先不判极端行情**：判定集合 = 4 条硬门槛（回撤≤10% / Calmar≥1.5 /
     成本≤0.50×毛利 / 连亏≤5，`min_sharpe:null` 不设夏普硬门槛）+ 笔数下限≥30。
     第 5 条极端段**不判、不记通过、不记 N/A、不用 0 占位**：
     json 写 `extreme_gate: "NOT_EVALUATED_BY_BOSS_ORDER"`。
     另设 `n_pass_decidable_4plus_trades`（本批可判定通过数）；`n_passed_final` 仍须标注
     「**非最终达标 / 未含极端段**」——严禁把「先不判」写成放行、严禁把可判定通过数
     冒充最终达标、严禁把 `n_passed_final` 写成 >0。**config 一字不改**（仅本批判定范围
     按老板指令临时排除，留痕）。
     > 秘书理解：17:34 三条虽在配对语境提出，方向改后老板未撤回 → 本批沿用；
       若老板要改，另行一句话即可。此理解写进报告。
  2. **28 年全量**：1998-09-15~2026-11-13，不裁窗口、不抽样、不分段；
     短样本标的如实申报实际起止与 bar 数，不得写成「28 年」。
  3. **T+1 硬约束**：`MIN_HOLD_BARS=1`，成交层拒绝同 bar 开平；逐笔 `exit_date > entry_date`、
     `exit_bar > entry_bar`，汇总 `t1_violations=0`，违规组判 FAIL。
     t 收盘出信号 → **t+1 open 成交**（防未来函数），卖出同理；
     禁止用日内 OHLC 臆造同日反向成交。
  4. **门槛不因换策略而放宽**：冻结 4 条 + 笔数一个不少。「因子效果排序」
     （净收益 / 夏普 / 胜率 / 成本占比）与「达标判定」**分开申报**，
     不得把「排序靠前」写成「达标 / 可上线」。

============================================================================
回报口径（如实标注）
============================================================================
  **已实现权益曲线、日频、√252 年化、未计浮动盈亏**：每因子把标的池内所有成交按
  `exit_date` 合并到一条「全池交易日并集」时间轴，逐笔在平仓 bar 累加已实现 net，
  据此算 total_return / max_dd / sharpe / calmar / 连亏 / 胜率。夏普为**参考值、不设硬门槛**。

  执行体如实登记：本环境为单一执行体、无独立 subagent 会话；两阶段按两份 soul 纪律
  分别落地，非两角色各自独立会话（见 catalog / 报告 / dispatch_log I-3 段）。

运行：python backtest/a_share_technical_factors.py
输出：backtest/output/a_share_technical_factors.json + .md
      backtest/output/a_share_technical_factors_trades.json（附加：全量逐笔，供 T+1 核验）
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.core.timeframe import resolve_timeframe          # 只读复用，不改 src/

DATA_DIR = ROOT / "data" / "cache" / "tdx_a_share"
OUT_DIR = ROOT / "backtest" / "output"
PARAM_YAML = ROOT / "config" / "strategy_params.yaml"    # 只读，一字不动
MANIFEST = DATA_DIR / "_manifest.json"

# ---------------------------------------------------------------------------
# A 股成本（单腿；全部「待老板确认的假设」；绝不写进 config、绝不套 Gate.io 0.1%/边）
# ---------------------------------------------------------------------------
A_SHARE_COST: Dict[str, Any] = {
    "_label": "待老板确认的假设",
    "_note": "项目假设 / 公开费率单一代表值，均未经交易所对本账户核实；A 股历史费率多次调整，"
             "本批不分段回溯、统一单一代表值。绝不套用 Gate.io 现货 0.1%/边。",
    "commission": {"value_pct_per_side": 0.00025, "display": "万2.5（0.025%）/边，双边收",
                   "nature": "项目假设", "label": "待老板确认的假设"},
    "stamp_duty": {"value_pct_sell_only": 0.0005,
                   "display": "卖出单边 0.05%（2023-08-28 起减半后档）",
                   "nature": "交易所/财政部公开税率，历史多次变动；本批取单一现行代表值",
                   "label": "待老板确认的假设"},
    "transfer_fee": {"value_pct_per_side": 0.00001,
                     "display": "0.001%（0.01‰）/边，双边收（2022-04-29 起沪深统一档）",
                     "nature": "交易所公开费率，历史沪深不一致；本批取单一现行代表值",
                     "label": "待老板确认的假设"},
    "slippage": {"value_bps_per_side": 5.0, "display": "5bp/边",
                 "nature": "项目假设（盘上无 A 股历史盘口实测；不硬套 market_ifaces 架构占位口径）",
                 "label": "待老板确认的假设"},
}

_COMM = A_SHARE_COST["commission"]["value_pct_per_side"]
_TRANS = A_SHARE_COST["transfer_fee"]["value_pct_per_side"]
_SLIP = A_SHARE_COST["slippage"]["value_bps_per_side"] / 10_000.0
_STAMP = A_SHARE_COST["stamp_duty"]["value_pct_sell_only"]

# 单腿 1 买 1 卖：买入侧费率 / 卖出侧费率（逐项重新推导，不照抄 I-1 的 4 次成交公式）
BUY_SIDE_RATE = _COMM + _TRANS + _SLIP                 # 0.00076 = 7.6bp
SELL_SIDE_RATE = _COMM + _TRANS + _SLIP + _STAMP     # 0.00126 = 12.6bp
NOTIONAL = 1000.0                                    # 单腿名义（元）
BASE_EQUITY = 10_000.0
COST_PER_ROUNDTRIP = NOTIONAL * (BUY_SIDE_RATE + SELL_SIDE_RATE)   # 2.02 元
ROUNDTRIP_BP = COST_PER_ROUNDTRIP / NOTIONAL * 10_000.0            # 20.2bp（相对单腿名义）

MIN_HOLD_BARS = 1
WARM = 60                                            # 指标预热（最大窗口 26+9 / 30 / 20，60 足够）

# ---------------------------------------------------------------------------
# 冻结门槛（只读 config；null 正确读法；SHA256-16 指纹留痕）
# ---------------------------------------------------------------------------
def param_fingerprint() -> Dict[str, Any]:
    fp = hashlib.sha256(PARAM_YAML.read_bytes()).hexdigest()
    return {"file": str(PARAM_YAML.relative_to(ROOT)), "sha256": fp, "sha256_16": fp[:16]}


def load_gate_from_yaml() -> Dict[str, Any]:
    """读 backtest_gate 段（只读）。min_sharpe:null → None，绝不 fallback 1.0/2.0。"""
    txt = PARAM_YAML.read_text(encoding="utf-8")
    i = txt.index("backtest_gate:")
    j = txt.find("cost_model:", i)
    seg = txt[i:(j if j > i else len(txt))]
    g: Dict[str, Any] = {}
    m = re.search(r"max_drawdown_pct:\s*([\d.]+)", seg)
    g["max_drawdown_pct"] = float(m.group(1)) if m else 10.0
    m = re.search(r"min_sharpe:\s*(null|None|[\d.]+)", seg)
    if m and m.group(1).lower() in ("null", "none"):
        g["min_sharpe"] = None                       # 不设夏普硬门槛
    elif m:
        g["min_sharpe"] = float(m.group(1))
    else:
        g["min_sharpe"] = None
    m = re.search(r"min_calmar:\s*([\d.]+)", seg)
    g["min_calmar"] = float(m.group(1)) if m else 1.5
    m = re.search(r"max_consecutive_losses:\s*(\d+)", seg)
    g["max_consecutive_losses"] = int(m.group(1)) if m else 5
    m = re.search(r"must_survive_extreme_window:\s*\"([^\"]+)\"", seg)
    g["extreme_window"] = m.group(1) if m else "2026-02"
    return g


def load_cost_gate_from_yaml() -> Dict[str, Any]:
    txt = PARAM_YAML.read_text(encoding="utf-8")
    i = txt.index("cost_model:")
    j = txt.find("# ---- 因子拥挤", i)
    seg = txt[i:(j if j > i else len(txt))]
    m = re.search(r"max_cost_as_pct_of_gross:\s*([\d.]+)", seg)
    return {"max_cost_as_pct_of_gross": float(m.group(1)) if m else 0.50}


def data_fingerprint(m: Dict[str, Any]) -> Dict[str, Any]:
    app = m["i3_append"]
    fps = {f["symbol"]: {"parquet_sha256_16": f["parquet_sha256_16"],
                         "source_sha256_16": f["source_sha256_16"],
                         "n_bars": f["n_bars"],
                         "start_date": f["start_date"], "end_date": f["end_date"]}
           for f in app["new_files"] if "error" not in f}
    return {
        "manifest_sha256_16": hashlib.sha256(MANIFEST.read_bytes()).hexdigest()[:16],
        "i1_manifest_sha256_16_before_append": app["i1_manifest_sha256_16_before_append"],
        "adjustment_conclusion": m["adjustment"]["conclusion"],
        "adjusted": False,
        "new_files": fps,
    }


# ---------------------------------------------------------------------------
# 因子清单（15 个；统一参数、不铺网格、不寻优；来源核实状态来自 catalog §2）
# ---------------------------------------------------------------------------
FACTORS: List[Dict[str, str]] = [
    {"id": "F01", "name": "MA 金叉死叉 (5/20)", "cat": "趋势/均线", "fields": "close",
     "source": "✅ 经典文献 Murphy(1999)", "long_only": "金叉买入持有、死叉卖出空仓；无做空腿"},
    {"id": "F02", "name": "MACD (12/26/9)", "cat": "趋势/均线", "fields": "close",
     "source": "✅ 经典文献 Appel(1979)+WebSearch命中", "long_only": "DIF上穿DEA持有、下穿空仓；无做空腿"},
    {"id": "F03", "name": "DMI/ADX (14)", "cat": "趋势/均线", "fields": "high,low,close",
     "source": "✅ WebFetch核验 Wilder(1978)", "long_only": "仅多方占优且ADX≥20持有；转空方/趋势弱即空仓，不反手做空"},
    {"id": "F04", "name": "动量 ROC (12)", "cat": "趋势/均线", "fields": "close",
     "source": "✅ 经典文献 Wilder(1978) MOM", "long_only": "动量为正持有、为负空仓；无做空腿"},
    {"id": "F05", "name": "唐奇安/海龟通道 (20)", "cat": "通道·经典公开策略", "fields": "high,low,close",
     "source": "✅ WebFetch核验(CSDN海龟+知乎)", "long_only": "突破前20日高买入、跌破前20日低卖出；剔除原版做空/反手腿"},
    {"id": "F06", "name": "布林带 (20,±2σ)含带宽", "cat": "波动率", "fields": "close",
     "source": "✅ 经典文献 Bollinger(2001)+WebSearch命中", "long_only": "收盘上破上轨持有、跌破中轨空仓；不做下轨做空"},
    {"id": "F07", "name": "ATR 通道 (14,1.5×,MA20)", "cat": "波动率", "fields": "high,low,close",
     "source": "✅ WebFetch核验 Wilder(1978) ATR", "long_only": "上穿MA20+1.5ATR持有、跌破MA20空仓；不做下轨做空"},
    {"id": "F08", "name": "RSI 反转 (14,30/70)", "cat": "超买超卖反转", "fields": "close",
     "source": "✅ WebFetch核验 Wilder(1978)+StockCharts译文", "long_only": "超卖回升上穿30持有、超买回落下穿70空仓；不做超买做空"},
    {"id": "F09", "name": "KDJ (9,3,3)", "cat": "超买超卖反转", "fields": "high,low,close",
     "source": "⚠️ 部分未核实（J值变体未核实，不编URL）", "long_only": "低位金叉持有、高位死叉空仓；不做高位做空"},
    {"id": "F10", "name": "CCI (14,±100)", "cat": "超买超卖反转", "fields": "high,low,close",
     "source": "未核实（Lambert 1980 出处未取回正文，不编URL）", "long_only": "上穿-100持有、下穿+100空仓；不做+100做空"},
    {"id": "F11", "name": "威廉 %R (14,-80/-20)", "cat": "超买超卖反转", "fields": "high,low,close",
     "source": "✅ WebSearch命中(百度百科 威廉指标)", "long_only": "超卖回升上穿-80持有、超买回落下穿-20空仓；不做超买做空"},
    {"id": "F12", "name": "OBV 量价 (OBV,MA30)", "cat": "量价", "fields": "close,volume",
     "source": "✅ 经典文献 Granville(1963)", "long_only": "OBV上穿其30日均线持有、下穿空仓；无做空腿"},
    {"id": "F13", "name": "放量突破 (量MA20×1.5+前20日高)", "cat": "量价", "fields": "high,low,close,volume",
     "source": "✅ WebFetch核验(CSDN海龟成交量确认)", "long_only": "放量破前20日高持有、跌破MA20空仓；不做缩量做空"},
    {"id": "F14", "name": "Dual Thrust (N=4,k=0.5)·日线近似", "cat": "经典公开策略", "fields": "open,high,low,close",
     "source": "✅ WebFetch核验(知乎 Range/上下轨公式)", "long_only": "收盘上破上轨持有、下破下轨平多空仓；剔除反手开空/日内强平"},
    {"id": "F15", "name": "R-Breaker (前H/L/C)·日线近似", "cat": "经典公开策略", "fields": "high,low,close",
     "source": "✅ WebFetch核验(知乎 pivot/r1/r3/s1/s3公式)", "long_only": "收盘破r3持有、跌破r1平多空仓；剔除跌破S3追空/反手做空"},
]

# ---------------------------------------------------------------------------
# 指标工具（全部只用截至 bar t 及之前的数据；无未来函数）
# ---------------------------------------------------------------------------
def shiftk(a: np.ndarray, k: int) -> np.ndarray:
    out = np.full(len(a), np.nan, dtype=float)
    out[k:] = a[:-k]
    return out


def roll_mean(a: np.ndarray, n: int) -> np.ndarray:
    return pd.Series(a).rolling(n).mean().to_numpy()


def roll_max(a: np.ndarray, n: int) -> np.ndarray:
    return pd.Series(a).rolling(n).max().to_numpy()


def roll_min(a: np.ndarray, n: int) -> np.ndarray:
    return pd.Series(a).rolling(n).min().to_numpy()


def roll_std0(a: np.ndarray, n: int) -> np.ndarray:
    return pd.Series(a).rolling(n).std(ddof=0).to_numpy()


def rma_np(a: np.ndarray, n: int) -> np.ndarray:
    """Wilder 平滑：首个有效值 = 前 n 个 SMA，之后 out=(out*(n-1)+x)/n。"""
    a = np.asarray(a, dtype=float)
    out = np.full(len(a), np.nan)
    if len(a) >= n:
        out[n - 1] = np.mean(a[:n])
        for i in range(n, len(a)):
            out[i] = (out[i - 1] * (n - 1) + a[i]) / n
    return out


def events_to_target(e: np.ndarray, x: np.ndarray, n: int) -> np.ndarray:
    """事件→持仓目标状态机：空仓遇进场事件转持有，持有遇出场事件转空仓；warm 前不交易。"""
    ee = np.nan_to_num(np.asarray(e, dtype=float), nan=0.0).astype(bool)
    xx = np.nan_to_num(np.asarray(x, dtype=float), nan=0.0).astype(bool)
    tgt = np.zeros(n, dtype=int)
    state = 0
    for i in range(WARM, n):
        if state == 0 and ee[i]:
            state = 1
        elif state == 1 and xx[i]:
            state = 0
        tgt[i] = state
    return tgt


def true_range(C: np.ndarray, H: np.ndarray, L: np.ndarray) -> np.ndarray:
    prevC = shiftk(C, 1)
    prevC[0] = C[0]
    return np.maximum.reduce([H - L, np.abs(H - prevC), np.abs(L - prevC)])


def compute_target(df: pd.DataFrame, fid: str) -> np.ndarray:
    """返回该因子的持仓目标序列 target[0..n-1] ∈ {0,1}（0=空仓，1=持有）。"""
    C = df["close"].to_numpy(float)
    H = df["high"].to_numpy(float)
    L = df["low"].to_numpy(float)
    O = df["open"].to_numpy(float)
    V = df["volume"].to_numpy(float)
    n = len(C)
    s1 = lambda a: shiftk(a, 1)

    if fid == "F01":                                   # MA 金叉死叉 (5/20)
        ma5, ma20 = roll_mean(C, 5), roll_mean(C, 20)
        e = (ma5 > ma20) & (s1(ma5) <= s1(ma20))
        x = (ma5 < ma20) & (s1(ma5) >= s1(ma20))
    elif fid == "F02":                                 # MACD (12/26/9)
        e12 = pd.Series(C).ewm(span=12, adjust=False).mean().to_numpy()
        e26 = pd.Series(C).ewm(span=26, adjust=False).mean().to_numpy()
        dif = e12 - e26
        dea = pd.Series(dif).ewm(span=9, adjust=False).mean().to_numpy()
        e = (dif > dea) & (s1(dif) <= s1(dea))
        x = (dif < dea) & (s1(dif) >= s1(dea))
    elif fid == "F03":                                 # DMI/ADX (14)
        tr = true_range(C, H, L)
        up = np.diff(H, prepend=H[0])
        dn = -np.diff(L, prepend=L[0])
        plus_dm = np.where((up > dn) & (up > 0), up, 0.0)
        minus_dm = np.where((dn > up) & (dn > 0), dn, 0.0)
        atr = rma_np(tr, 14)
        pdi = 100.0 * rma_np(plus_dm, 14) / atr
        mdi = 100.0 * rma_np(minus_dm, 14) / atr
        den = pdi + mdi
        dx = np.where(den > 0, 100.0 * np.abs(pdi - mdi) / np.where(den > 0, den, 1.0), 0.0)
        adx = rma_np(dx, 14)
        e = (pdi > mdi) & (s1(pdi) <= s1(mdi)) & (adx >= 20)
        x = ((pdi < mdi) & (s1(pdi) >= s1(mdi))) | (adx < 20)
    elif fid == "F04":                                 # 动量 ROC (12)
        roc = (C / shiftk(C, 12) - 1.0) * 100.0
        e = (roc > 0) & (s1(roc) <= 0)
        x = (roc < 0) & (s1(roc) >= 0)
    elif fid == "F05":                                 # 唐奇安/海龟通道 (20)
        U = shiftk(roll_max(H, 20), 1)                 # 前 20 日高（shift1，防自我指涉）
        D = shiftk(roll_min(L, 20), 1)
        e = (C > U) & (s1(C) <= s1(U))
        x = (C < D) & (s1(C) >= s1(D))
    elif fid == "F06":                                 # 布林带 (20, ±2σ)
        mid = roll_mean(C, 20)
        sd = roll_std0(C, 20)
        up = mid + 2.0 * sd
        e = (C > up) & (s1(C) <= s1(up))
        x = (C < mid) & (s1(C) >= s1(mid))
    elif fid == "F07":                                 # ATR 通道 (14, 1.5×, MA20)
        atr = rma_np(true_range(C, H, L), 14)
        mid = roll_mean(C, 20)
        up = mid + 1.5 * atr
        e = (C > up) & (s1(C) <= s1(up))
        x = (C < mid) & (s1(C) >= s1(mid))
    elif fid == "F08":                                 # RSI 反转 (14, 30/70)
        delta = np.diff(C, prepend=C[0])
        gain = np.maximum(delta, 0.0)
        loss = np.maximum(-delta, 0.0)
        ag, al = rma_np(gain, 14), rma_np(loss, 14)
        rsi = np.where(al <= 0, 100.0,
                       np.where(ag <= 0, 0.0,
                                100.0 - 100.0 / (1.0 + ag / np.where(al > 0, al, 1.0))))
        e = (rsi > 30) & (s1(rsi) <= 30)
        x = (rsi < 70) & (s1(rsi) >= 70)
    elif fid == "F09":                                 # KDJ (9,3,3)
        llv, hhv = roll_min(L, 9), roll_max(H, 9)
        den = hhv - llv
        rsv = np.where(den > 0, (C - llv) / np.where(den > 0, den, 1.0) * 100.0, 50.0)
        K = np.empty(n); D = np.empty(n)
        k, d = 50.0, 50.0
        for i in range(n):
            k = (2.0 / 3.0) * k + (1.0 / 3.0) * rsv[i]
            d = (2.0 / 3.0) * d + (1.0 / 3.0) * k
            K[i], D[i] = k, d
        e = (K > D) & (s1(K) <= s1(D)) & (K < 20)
        x = (K < D) & (s1(K) >= s1(D)) & (K > 80)
    elif fid == "F10":                                 # CCI (14, ±100)
        tp = (H + L + C) / 3.0
        ma = roll_mean(tp, 14)
        md = pd.Series(tp).rolling(14).apply(lambda w: np.abs(w - w.mean()).mean(), raw=True).to_numpy()
        cci = np.where(md > 0, (tp - ma) / (0.015 * np.where(md > 0, md, 1.0)), 0.0)
        e = (cci > -100) & (s1(cci) <= -100)
        x = (cci < 100) & (s1(cci) >= 100)
    elif fid == "F11":                                 # 威廉 %R (14, -80/-20)
        hhv, llv = roll_max(H, 14), roll_min(L, 14)
        den = hhv - llv
        wr = np.where(den > 0, (hhv - C) / np.where(den > 0, den, 1.0) * (-100.0), -50.0)
        e = (wr > -80) & (s1(wr) <= -80)
        x = (wr < -20) & (s1(wr) >= -20)
    elif fid == "F12":                                 # OBV 量价 (OBV, MA30)
        obv = np.zeros(n)
        obv[0] = V[0]
        for i in range(1, n):
            if C[i] > C[i - 1]:
                obv[i] = obv[i - 1] + V[i]
            elif C[i] < C[i - 1]:
                obv[i] = obv[i - 1] - V[i]
            else:
                obv[i] = obv[i - 1]
        obvma = roll_mean(obv, 30)
        e = (obv > obvma) & (s1(obv) <= s1(obvma))
        x = (obv < obvma) & (s1(obv) >= s1(obvma))
    elif fid == "F13":                                 # 放量突破
        vma_prev = shiftk(roll_mean(V, 20), 1)         # 前 20 日均量
        UH = shiftk(roll_max(H, 20), 1)                # 前 20 日高
        ma20 = roll_mean(C, 20)
        e = (V > 1.5 * vma_prev) & (C > UH)
        x = (C < ma20) & (s1(C) >= s1(ma20))
    elif fid == "F14":                                 # Dual Thrust (N=4, k=0.5) 日线近似
        N = 4
        HH = shiftk(roll_max(H, N), 1)
        LC = shiftk(roll_min(C, N), 1)
        HC = shiftk(roll_max(C, N), 1)
        LL = shiftk(roll_min(L, N), 1)
        rng = np.maximum(HH - LC, HC - LL)
        U = O + 0.5 * rng                              # 锚 = 当日 open（t 收盘已知，无未来函数）
        D = O - 0.5 * rng
        e = (C > U) & (s1(C) <= s1(U))
        x = (C < D) & (s1(C) >= s1(D))
    elif fid == "F15":                                 # R-Breaker 日线近似
        pH, pL, pC = shiftk(H, 1), shiftk(L, 1), shiftk(C, 1)
        pivot = (pH + pL + pC) / 3.0
        R = pH - pL
        r3 = pivot + 1.0 * R
        r1 = pivot + 0.5 * R
        e = (C > r3) & (s1(C) <= s1(r3))
        x = (C < r1) & (s1(C) >= s1(r1))
    else:
        raise ValueError(f"未知因子 {fid}")

    return events_to_target(e, x, n)


# ---------------------------------------------------------------------------
# 单组回测（factor × symbol），逐笔 T+1 硬校验
# ---------------------------------------------------------------------------
def run_one(df: pd.DataFrame, symbol: str, pool: str, fid: str) -> Dict[str, Any]:
    target = compute_target(df, fid)
    n = len(df)
    O = df["open"].to_numpy(float)
    D = df["date"].to_numpy(np.int64)
    thin_bars = int(df["thin"].sum())

    pos = 0                                          # 0=空仓 / 1=持有
    pending = None                                   # 'BUY' | 'SELL'
    entry: Tuple[int, float, int] | None = None      # (bar, open_px, date)
    trades: List[Dict[str, Any]] = []
    t1_violations = 0

    for i in range(WARM, n - 1):
        # 1) 执行 t-1 收盘挂起的意图，在 bar i 的 open 成交（R4 防未来函数）
        if pending is not None:
            if pending == "BUY" and pos == 0:
                pos = 1
                entry = (i, float(O[i]), int(D[i]))
            elif pending == "SELL" and pos == 1 and entry is not None:
                ei, ep, ed = entry
                # T+1 硬校验：成交层拒绝同 bar 开平；exit_bar>entry_bar 且 exit_date>entry_date
                if (i - ei) >= MIN_HOLD_BARS and int(D[i]) > ed:
                    gross = (O[i] / ep - 1.0) * NOTIONAL
                    net = gross - COST_PER_ROUNDTRIP
                    trades.append({
                        "symbol": symbol, "pool": pool,
                        "entry_date": str(ed), "exit_date": str(int(D[i])),
                        "entry_bar": ei, "exit_bar": i,
                        "gross": round(float(gross), 4), "cost": round(COST_PER_ROUNDTRIP, 4),
                        "net": round(float(net), 4), "reason": "signal_exit",
                    })
                else:
                    t1_violations += 1
                pos = 0
                entry = None
            pending = None

        # 2) bar i 收盘：按 target 产生下一 bar 的成交意图
        if int(target[i]) == 1 and pos == 0:
            pending = "BUY"
        elif int(target[i]) == 0 and pos == 1:
            pending = "SELL"

    # 末尾未平仓 → 最后一根 bar 的 open 强平（如实计入，标注 end_force_close）
    if pos == 1 and entry is not None:
        i = n - 1
        ei, ep, ed = entry
        if (i - ei) >= MIN_HOLD_BARS and int(D[i]) > ed:
            gross = (O[i] / ep - 1.0) * NOTIONAL
            net = gross - COST_PER_ROUNDTRIP
            trades.append({
                "symbol": symbol, "pool": pool,
                "entry_date": str(ed), "exit_date": str(int(D[i])),
                "entry_bar": ei, "exit_bar": i,
                "gross": round(float(gross), 4), "cost": round(COST_PER_ROUNDTRIP, 4),
                "net": round(float(net), 4), "reason": "end_force_close",
            })
        else:
            t1_violations += 1

    gross = float(sum(t["gross"] for t in trades))
    cost = float(sum(t["cost"] for t in trades))
    net = float(sum(t["net"] for t in trades))
    wins = sum(1 for t in trades if t["net"] > 0)
    return {
        "symbol": symbol, "pool": pool, "factor": fid,
        "n_bars": n, "start_date": str(int(D[0])), "end_date": str(int(D[-1])),
        "thin_bars": thin_bars, "n_trades": len(trades),
        "gross": round(gross, 4), "cost": round(cost, 4), "net": round(net, 4),
        "win_rate": round(wins / len(trades), 4) if trades else None,
        "t1_violations": t1_violations,
        "t1_ok": bool(t1_violations == 0),
        "trades": trades,
    }


# ---------------------------------------------------------------------------
# 逐因子汇总（统一尺子：池内所有成交按 exit_date 合并成一条已实现权益曲线）
# ---------------------------------------------------------------------------
def aggregate(factor: Dict[str, str], runs: List[Dict[str, Any]],
              union_dates: np.ndarray, gate: Dict[str, Any],
              cost_gate: Dict[str, Any], pool_label: str) -> Dict[str, Any]:
    all_trades: List[Dict[str, Any]] = []
    for r in runs:
        all_trades.extend(r["trades"])
    all_trades.sort(key=lambda t: (t["exit_date"], t["symbol"]))

    date_pos = {int(d): k for k, d in enumerate(union_dates)}
    equity = np.full(len(union_dates), BASE_EQUITY, dtype=float)
    for t in all_trades:
        k = date_pos[int(t["exit_date"])]
        equity[k:] += t["net"]

    n_trades = len(all_trades)
    gross = float(sum(t["gross"] for t in all_trades))
    cost = float(sum(t["cost"] for t in all_trades))
    net = float(sum(t["net"] for t in all_trades))
    cost_ok = bool(gross > 0 and cost <= cost_gate["max_cost_as_pct_of_gross"] * gross)

    total_return = float(equity[-1] / BASE_EQUITY - 1.0)
    peak = np.maximum.accumulate(equity)
    max_dd = float(np.max((peak - equity) / peak)) if np.all(peak > 0) else 0.0
    r = np.diff(equity) / equity[:-1]
    sharpe = float(np.mean(r) / np.std(r) * np.sqrt(252.0)) if len(r) > 1 and np.std(r) > 0 else 0.0
    years = len(union_dates) / 252.0
    annual_return = (float((1.0 + total_return) ** (1.0 / years)) - 1.0
                     if years > 0 and total_return > -1 else 0.0)
    calmar = float(annual_return / max_dd) if max_dd > 1e-12 else (0.0 if annual_return <= 0 else None)

    max_consec, cur = 0, 0
    for t in all_trades:
        cur = cur + 1 if t["net"] <= 0 else 0
        max_consec = max(max_consec, cur)
    wins = sum(1 for t in all_trades if t["net"] > 0)
    t1_violations = int(sum(r["t1_violations"] for r in runs))
    thin_bars = int(sum(r["thin_bars"] for r in runs))

    # 4 条冻结门槛 + 笔数（第 5 条极端段不判、不记通过、不记 N/A、不用 0 占位）
    gates = {
        "1_max_drawdown_le_10pct": bool(max_dd <= gate["max_drawdown_pct"] / 100.0),
        "2_calmar_ge_1p5": bool(calmar is not None and calmar >= gate["min_calmar"]),
        "3_cost_le_50pct_of_gross": bool(cost_ok),
        "4_max_consec_losses_le_5": bool(max_consec <= gate["max_consecutive_losses"]),
        "min_trades_ge_30": bool(n_trades >= 30),
        "sharpe_hard_gate": "不设硬门槛（min_sharpe=null）",
    }
    decidable = (gates["1_max_drawdown_le_10pct"] and gates["2_calmar_ge_1p5"]
                 and gates["3_cost_le_50pct_of_gross"]
                 and gates["4_max_consec_losses_le_5"]
                 and gates["min_trades_ge_30"])

    trade_log_bytes = json.dumps(all_trades, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return {
        "factor_id": factor["id"], "name": factor["name"], "category": factor["cat"],
        "fields": factor["fields"], "source_status": factor["source"],
        "long_only_reason": factor["long_only"],
        "no_future_function": "信号仅用截至 bar t 及之前数据；t 收盘出信号 → t+1 open 成交",
        "pool": pool_label,
        "n_symbols": len(runs), "union_bars": int(len(union_dates)),
        "n_trades": n_trades,
        "gross_total": round(gross, 4), "cost_total": round(cost, 4),
        "cost_gross_ratio": round(cost / gross, 4) if gross > 0 else None,
        "cost_ok": cost_ok, "net_total": round(net, 4),
        "total_return": round(total_return, 6), "max_dd": round(max_dd, 6),
        "calmar": (round(calmar, 4) if calmar is not None else None),
        "sharpe_reference": round(sharpe, 4),
        "max_consec_losses": max_consec,
        "win_rate": round(wins / n_trades, 4) if n_trades else None,
        "t1_violations": t1_violations, "t1_ok": bool(t1_violations == 0),
        "thin_bars_in_pool": thin_bars,
        "thin_bars_note": "thin=零成交 bar；不等同 LOCKED、不脑补涨跌停；仍按 open 成交，如实申报",
        "gates": gates,
        "decidable_4plus_trades_pass": bool(decidable),
        "passed_final": False,
        "passed_final_reason": "第 5 条极端段按老板 17:34 指令不判（NOT_EVALUATED_BY_BOSS_ORDER），"
                               "判定未闭合 → 非最终达标 / 未含极端段；且本批未复权、结果非可上线依据。"
                               "严禁把可判定通过数冒充最终达标。",
        "extreme_gate": "NOT_EVALUATED_BY_BOSS_ORDER",
        "adjusted": False,
        "adjustment_limit": "未复权（无法可靠复权）；个股对照另标除权跳空污染 + 幸存者偏差",
        "trades_sample": all_trades[:5],
        "trade_log_n": n_trades,
        "trade_log_sha256_16": hashlib.sha256(trade_log_bytes).hexdigest()[:16],
        "per_symbol": [{k: r[k] for k in
                        ("symbol", "n_bars", "start_date", "end_date", "n_trades",
                         "gross", "cost", "net", "win_rate", "t1_violations", "t1_ok")}
                       for r in runs],
        "_all_trades": all_trades,          # 内部用；写 json 前剔除
    }


# ---------------------------------------------------------------------------
# 报告（json + md + 附加全量逐笔 json）
# ---------------------------------------------------------------------------
def build_report(m: Dict[str, Any], gate: Dict[str, Any], cost_gate: Dict[str, Any],
                 factors: List[Dict[str, Any]], control: List[Dict[str, Any]],
                 tf: Any) -> Dict[str, Any]:
    dfp = data_fingerprint(m)
    pfp = param_fingerprint()
    app = m["i3_append"]
    n_pass_decidable = sum(1 for f in factors if f["decidable_4plus_trades_pass"])

    clean_factors = [{k: v for k, v in f.items() if k != "_all_trades"} for f in factors]
    clean_control = [{k: v for k, v in f.items() if k != "_all_trades"} for f in control]

    ranking = sorted(
        clean_factors,
        key=lambda f: f["net_total"], reverse=True,
    )

    result: Dict[str, Any] = {
        "batch": "I-3", "phase": "阶段二 · 技术派因子统一口径回测",
        "role": "backend_architect",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "meta": {
            "script": "backtest/a_share_technical_factors.py",
            "old_scripts_untouched": True,
            "src_untouched": True, "config_untouched": True,
            "no_pair_logic_imported": True,
            "strategy_form": "买入 → 持有 → 卖出平仓 → 空仓等待（单腿纯多头；无做空/无配对/无对冲）",
            "forbidden_confirmed": ["SHORT_SPREAD", "配对价差", "z-score价差", "对冲比率h",
                                    "融券", "ETF替代做空腿", "股指期货对冲", "任何空头暴露"],
            "data_fingerprint": dfp,
            "param_fingerprint": pfp,
            "gate": {**gate, **cost_gate},
            "sharpe_gate": "不设硬门槛（min_sharpe=null；夏普仅作参考值、不作门槛）",
            "extreme_gate": "NOT_EVALUATED_BY_BOSS_ORDER",
            "extreme_gate_note": "老板 17:34「先不判极端行情」；方向改后未撤回 → 本批沿用。"
                                 "第 5 条极端段不判、不记通过、不记 N/A、不用 0 占位。"
                                 "config 一字不改（仅本批判定范围按老板指令临时排除，留痕）。"
                                 "秘书理解（17:34 三条虽在配对语境提出、方向改后老板未撤回故沿用）"
                                 "已写进报告；若老板要改，另行一句话即可。",
            "assumption_label": "待老板确认的假设",
            "a_share_cost": A_SHARE_COST,
            "cost_derivation_single_leg": {
                "form": "1 买 1 卖（单腿），不照抄 I-1 配对 4 次成交公式",
                "buy_side_rate": BUY_SIDE_RATE, "buy_side_bp": BUY_SIDE_RATE * 10_000.0,
                "buy_side_items": "佣金 0.025% + 过户 0.001% + 滑点 5bp = 7.6bp",
                "sell_side_rate": SELL_SIDE_RATE, "sell_side_bp": SELL_SIDE_RATE * 10_000.0,
                "sell_side_items": "佣金 0.025% + 过户 0.001% + 滑点 5bp + 印花税 0.05%(卖出单边) = 12.6bp",
                "notional": NOTIONAL,
                "cost_per_roundtrip_yuan": round(COST_PER_ROUNDTRIP, 4),
                "cost_roundtrip_bp_vs_single_leg_notional": round(ROUNDTRIP_BP, 3),
            },
            "i1_formula_comparison": {
                "i1_pair_formula": "4*500*(佣金+过户+滑点) + 2*500*印花税 = 2.02元；分母双腿总名义1000 → 20.2bp",
                "i3_single_leg_formula": "1000*(买入侧7.6bp + 卖出侧12.6bp) = 2.02元；分母单腿名义1000 → 20.2bp",
                "coincidence": True,
                "coincidence_reason": "两种口径下买入侧总名义=1000、卖出侧总名义=1000"
                                     "（I-1=2腿×500；I-3=1腿×1000），四项成本逐项金额相同 → bp 同为20.2；"
                                     "但成交笔数(4 vs 2)、公式结构、分母定义完全不同，绝非照抄 I-1 公式。",
                "not_copied_from_i1": True,
            },
            "no_gateio_fee_applied": True,
            "gateio_note": "未套用 Gate.io 现货 0.1%/边 / roundtrip 28bp（phase7 仅适用加密现货主线）；"
                           "且不得说『A股费率低一个数量级』：20.2bp vs 28bp 仅降 27.9%，四项皆假设值。",
            "t1_constraint": {
                "min_hold_bars": MIN_HOLD_BARS,
                "signal_timing": "bar t 收盘出信号 → bar t+1 的 open 成交（买入与卖出同理）",
                "hard_check": "逐笔 exit_date>entry_date 且 exit_bar>entry_bar；成交层拒绝同 bar 开平",
                "no_intraday_reverse": True,
            },
            "return_convention": "已实现权益曲线、日频、√252 年化、未计浮动盈亏（逐因子把池内成交按 exit_date 并集累加）",
            "timeframe": {
                "resolve": 'resolve_timeframe("1d", hours_per_day=4, trading_days_per_year=252)',
                "bars_per_year": float(tf.bars_per_year),
                "annualize_factor": float(tf.annualize_factor),
                "src_read_only": True,
            },
            "pool": {
                "main_index": app["main_index_pool"],
                "stock_control": app["stock_control_pool"],
                "selection_rule": app["selection_rule"],
                "i1_old_parquet_preserved": True,
                "adjusted": False,
            },
            "survivorship_bias": app["survivorship_bias"]["note"],
            "survivorship_bias_grade": app["survivorship_bias"]["grade"],
            "unadjusted_pollution_note": app["unadjusted_pollution_note"],
            "institutional_difference_note": app["institutional_difference_note"],
            "adjustment_limit": dfp["adjustment_conclusion"],
            "result_not_upper_line_evidence": True,
            "factor_catalog_ref": "agents/phase9-i3-technical-factor-catalog.md",
        },
        "summary": {
            "n_factors": len(factors),
            "n_runs": len(factors) * (len(app["main_index_pool"]) + len(app["stock_control_pool"])),
            "n_runs_breakdown": {
                "main_detail": len(factors) * len(app["main_index_pool"]),
                "control_detail": len(factors) * len(app["stock_control_pool"]),
            },
            "n_pass_decidable_4plus_trades": n_pass_decidable,
            "n_passed_final": 0,
            "n_passed_final_label": "非最终达标 / 未含极端段（第 5 条按老板 17:34 指令不判）",
            "n_passed_final_reason": "极端段不判使判定未闭合，任何因子不能判『最终达标』；"
                                     "严禁把 n_pass_decidable_4plus_trades 冒充 n_passed_final、"
                                     "严禁把 n_passed_final 写成 >0。且本批未复权、结果非可上线依据。",
            "extreme_gate": "NOT_EVALUATED_BY_BOSS_ORDER",
            "data_fingerprint": dfp["manifest_sha256_16"],
            "i1_manifest_sha256_16_before_append": dfp["i1_manifest_sha256_16_before_append"],
            "param_fingerprint": pfp["sha256_16"],
            "assumption_label": "待老板确认的假设",
            "adjusted": False,
            "ranking_note": "因子效果排序 ≠ 达标 ≠ 可上线；排序与达标判定分开申报，不得把排序靠前写成达标/可上线。",
        },
        "factors": clean_factors,                       # 主池逐因子（主判定 / 排序）
        "control": clean_control,                       # 个股对照（标污染、不进 n_passed_final、不混算）
        "ranking_by_net_total": [
            {"rank": k + 1, "factor_id": f["factor_id"], "name": f["name"],
             "net_total": f["net_total"], "sharpe_reference": f["sharpe_reference"],
             "win_rate": f["win_rate"], "cost_gross_ratio": f["cost_gross_ratio"],
             "decidable_4plus_trades_pass": f["decidable_4plus_trades_pass"],
             "passed_final": f["passed_final"]}
            for k, f in enumerate(ranking)
        ],
        "comparison": {
            "i1_baseline": "I-1：3 组配对 n_passed_final=0 / cost_ok 0/3 / Calmar≥1.5 0/3 / 主组 cost_gross_ratio 11.8968",
            "crypto_mainline": "加密现货 1h 配对均值回归 257 组网格 0 达标",
            "not_mixed": True,
            "note": "本批不与加密 257 组、I-1 3 组混算；不与配对口径混算；0 达标就报 0、不美化。",
        },
        "compliance": {
            "local_files_read_only": True, "clients_not_started": True,
            "no_network_market_data": True, "no_registry_change": True,
            "src_unchanged": True, "config_unchanged": True,
            "bat_gitignore_env_unchanged": True, "no_git_commit": True,
            "no_gate_relax": True, "no_live_trading": True,
            "no_token_or_key": True, "no_paid_source": True,
            "no_api_key_registration": True,
            "no_pair_no_short_no_hedge": True,
            "no_g6_3_g5_2_jump": True,
            "result_not_sugarcoated": True,
            "i1_old_parquet_not_overwritten": True,
        },
    }
    return result


def write_md(result: Dict[str, Any], path: Path) -> None:
    m = result["meta"]
    s = result["summary"]
    L: List[str] = []
    L.append("# I-3 阶段二 · A 股技术派因子统一口径回测结果")
    L.append("")
    L.append(f"- 生成：{result['generated_at']}　｜　脚本：`backtest/a_share_technical_factors.py`（新增，旧脚本全保留）")
    L.append(f"- 数据指纹（追加后）：`{m['data_fingerprint']['manifest_sha256_16']}`　"
             f"（I-1 旧指纹 `{m['data_fingerprint']['i1_manifest_sha256_16_before_append']}` 留痕）")
    L.append(f"- 参数指纹：`{m['param_fingerprint']['sha256_16']}`（config 一字未改）")
    L.append(f"- 复权限制：**{m['adjustment_limit']}**；结果不得写成『可上线依据』")
    L.append("")
    L.append("## 1. 口径")
    L.append("")
    L.append(f"- 冻结门槛（config 现行，一字不动）：回撤≤{m['gate']['max_drawdown_pct']}% / "
             f"Calmar≥{m['gate']['min_calmar']} / 成本≤毛利 {m['gate']['max_cost_as_pct_of_gross']*100:.0f}% / "
             f"连亏≤{m['gate']['max_consecutive_losses']} / 笔数≥30")
    L.append(f"- **{m['sharpe_gate']}**（夏普仅作参考值，不设硬门槛）")
    L.append(f"- 第 5 条极端段：**{m['extreme_gate']}**——不判、不记通过、不记 N/A、不用 0 占位；"
             "config 一字不改（仅本批判定范围临时排除，留痕）")
    L.append(f"- 策略形态：**{m['strategy_form']}**；禁止：{' / '.join(m['forbidden_confirmed'])}")
    L.append(f"- T+1：`MIN_HOLD_BARS={m['t1_constraint']['min_hold_bars']}`；"
             f"{m['t1_constraint']['signal_timing']}；{m['t1_constraint']['hard_check']}")
    L.append(f"- 回报口径：**{m['return_convention']}**")
    L.append(f"- 周期：`{m['timeframe']['resolve']}` → bars_per_year={m['timeframe']['bars_per_year']:g}、"
             f"annualize=√252（只读复用 `src/core/timeframe.py`）")
    L.append("")
    L.append("### 单腿成本重新推导（不照抄 I-1 配对 4 次成交公式）")
    L.append("")
    L.append(f"- 买入侧：{m['cost_derivation_single_leg']['buy_side_items']} → "
             f"**{m['cost_derivation_single_leg']['buy_side_bp']:.1f}bp**")
    L.append(f"- 卖出侧：{m['cost_derivation_single_leg']['sell_side_items']} → "
             f"**{m['cost_derivation_single_leg']['sell_side_bp']:.1f}bp**")
    L.append(f"- 单笔完整交易（1 买 1 卖，名义 {m['cost_derivation_single_leg']['notional']:.0f} 元）= "
             f"**{m['cost_derivation_single_leg']['cost_per_roundtrip_yuan']} 元 / "
             f"{m['cost_derivation_single_leg']['cost_roundtrip_bp_vs_single_leg_notional']}bp**"
             f"（全部「{m['assumption_label']}」；绝不写进 config、绝不套 Gate.io 0.1%/边）")
    L.append(f"- 四项成本逐项：")
    for k, v in m["a_share_cost"].items():
        if k.startswith("_"):
            continue
        L.append(f"    - {k}：**{v.get('display')}** — {v['nature']}（{v['label']}）")
    L.append(f"- **与 I-1 公式区别**：{m['i1_formula_comparison']['i1_pair_formula']}；"
             f"本批 {m['i1_formula_comparison']['i3_single_leg_formula']}。"
             f"数值巧合同为 20.2bp 的机理：{m['i1_formula_comparison']['coincidence_reason']}")
    L.append(f"- {m['gateio_note']}")
    L.append("")
    L.append("## 2. 标的池 + 未复权 / 幸存者偏差限制")
    L.append("")
    L.append(f"- 主池指数（{len(m['pool']['main_index'])}，无除权）：`{', '.join(m['pool']['main_index'])}`")
    L.append(f"- 个股对照（{len(m['pool']['stock_control'])}，🔴 未复权污染）：`{', '.join(m['pool']['stock_control'])}`")
    L.append(f"- 选取规则：{m['pool']['selection_rule']['main_index']}；对照 {m['pool']['selection_rule']['stock_control']}")
    L.append(f"- **幸存者偏差（{m['survivorship_bias_grade']}）**：{m['survivorship_bias']}")
    L.append(f"- **未复权污染**：{m['unadjusted_pollution_note']}")
    L.append(f"- **制度差异**：{m['institutional_difference_note']}")
    L.append("- I-1 旧 6 个 parquet 保留不覆盖；短样本标的如实申报实际起止与 bar 数（见 json `per_symbol`），不得写成「28 年」。")
    L.append("")
    L.append("## 3. 逐因子结果（主池 12 指数统一尺子，如实申报）")
    L.append("")
    L.append("| 因子 | 类别 | 来源核实 | 成交数 | 毛利 | 成本 | 成本/毛利 | 净收益 | 夏普(参考) | Calmar | 最大回撤 | 连亏 | 胜率 | 4条+笔数 | t1违规 | 最终达标 |")
    L.append("|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---:|---|")
    for f in result["factors"]:
        L.append(
            f"| {f['factor_id']} {f['name']} | {f['category']} | {f['source_status']} | "
            f"{f['n_trades']} | {f['gross_total']} | {f['cost_total']} | {f['cost_gross_ratio']} | "
            f"{f['net_total']} | {f['sharpe_reference']} | {f['calmar']} | "
            f"{f['max_dd']*100:.2f}% | {f['max_consec_losses']} | {f['win_rate']} | "
            f"{'✅' if f['decidable_4plus_trades_pass'] else '❌'} | {f['t1_violations']} | "
            f"❌（非最终达标） |")
    L.append("")
    L.append("### 4 条冻结门槛逐条（第 5 条不判）")
    L.append("")
    for f in result["factors"]:
        g = f["gates"]
        L.append(f"**{f['factor_id']} {f['name']}**："
                 f"回撤≤10% {'✅' if g['1_max_drawdown_le_10pct'] else '❌'} / "
                 f"Calmar≥1.5 {'✅' if g['2_calmar_ge_1p5'] else '❌'} / "
                 f"成本≤50%毛利 {'✅' if g['3_cost_le_50pct_of_gross'] else '❌'} / "
                 f"连亏≤5 {'✅' if g['4_max_consec_losses_le_5'] else '❌'} / "
                 f"笔数≥30 {'✅' if g['min_trades_ge_30'] else '❌'} / "
                 f"{g['sharpe_hard_gate']} / 极端段 **{f['extreme_gate']}**（不判、不记 N/A、不用 0 占位）")
    L.append("")
    L.append(f"> **T+1 校验**：`t1_violations` 全部 = "
             f"{sum(f['t1_violations'] for f in result['factors'])}（主池）/ "
             f"{sum(f['t1_violations'] for f in result['control'])}（对照）；逐笔 `entry_date/exit_date/entry_bar/exit_bar` "
             f"见主 json `trades_sample` 与附加 `a_share_technical_factors_trades.json`。")
    L.append("")
    L.append("## 4. 统一排序（按净收益，Top N；**排序 ≠ 达标 ≠ 可上线**）")
    L.append("")
    L.append("| 排名 | 因子 | 净收益 | 夏普(参考) | 胜率 | 成本/毛利 | 4条+笔数 | 最终达标 |")
    L.append("|---:|---|---:|---:|---:|---:|---|---|")
    for r in result["ranking_by_net_total"]:
        L.append(f"| {r['rank']} | {r['factor_id']} {r['name']} | {r['net_total']} | "
                 f"{r['sharpe_reference']} | {r['win_rate']} | {r['cost_gross_ratio']} | "
                 f"{'✅' if r['decidable_4plus_trades_pass'] else '❌'} | ❌ 非最终达标 |")
    L.append("")
    L.append(f"> {s['ranking_note']}")
    L.append("")
    L.append("## 5. 达标数（如实申报 0）")
    L.append("")
    L.append(f"- `n_factors` = {s['n_factors']}；`n_runs` = {s['n_runs']}"
             f"（主池 {s['n_runs_breakdown']['main_detail']} + 对照 {s['n_runs_breakdown']['control_detail']} 明细）")
    L.append(f"- `n_pass_decidable_4plus_trades` = **{s['n_pass_decidable_4plus_trades']}**"
             "（本批可判定通过数，4 条硬门槛 + 笔数≥30）")
    L.append(f"- `n_passed_final` = **{s['n_passed_final']}** —— **{s['n_passed_final_label']}**")
    L.append(f"- 理由：{s['n_passed_final_reason']}")
    L.append(f"- `extreme_gate` = `{s['extreme_gate']}`（config 未改；秘书理解写进报告）")
    L.append(f"- 0 达标就报 0，不美化、不与加密 257 组 / I-1 3 组混算：{result['comparison']['note']}")
    L.append("")
    L.append("## 6. 个股对照（🔴 未复权 + 除权跳空污染 + 幸存者偏差，仅对照、不进主判定）")
    L.append("")
    L.append("| 因子 | 成交数 | 毛利 | 成本 | 净收益 | 夏普(参考) | Calmar | t1违规 |")
    L.append("|---|---:|---:|---:|---:|---:|---:|---:|")
    for f in result["control"]:
        L.append(f"| {f['factor_id']} | {f['n_trades']} | {f['gross_total']} | {f['cost_total']} | "
                 f"{f['net_total']} | {f['sharpe_reference']} | {f['calmar']} | {f['t1_violations']} |")
    L.append("")
    L.append("> 对照组**不与主池混算、不进 `n_passed_final`、不得写成可上线依据**。")
    L.append("")
    L.append("## 7. 合规确认（逐项）")
    L.append("")
    for k, v in result["compliance"].items():
        L.append(f"- {k}: {'✅' if v else '❌'}")
    L.append("")
    path.write_text("\n".join(L), encoding="utf-8")


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    gate = load_gate_from_yaml()
    cost_gate = load_cost_gate_from_yaml()
    tf = resolve_timeframe("1d", hours_per_day=4.0, trading_days_per_year=252.0)
    assert tf.bars_per_year == 252.0, "A 股 1d 应得 bars_per_year=252（timeframe 只读复用）"

    pfp = param_fingerprint()
    print(f"[gate] {gate} | cost_gate={cost_gate}")
    print(f"[gate] min_sharpe=None → 不设夏普硬门槛；extreme_gate=NOT_EVALUATED_BY_BOSS_ORDER")
    print(f"[param] config sha256-16={pfp['sha256_16']}（应为 31fbb054cddb55af，一字未改）")
    print(f"[timeframe] 1d A股 bars_per_year={tf.bars_per_year:g} annualize=√{tf.bars_per_year:g}={tf.annualize_factor:.2f}")
    print(f"[cost] 单腿 1买1卖 roundtrip={COST_PER_ROUNDTRIP:.4f}元 / {ROUNDTRIP_BP:.2f}bp "
          f"（买入侧7.6bp+卖出侧12.6bp；全部待老板确认的假设；未套 Gate.io 0.1%/边）")

    m = json.loads(MANIFEST.read_text(encoding="utf-8"))
    app = m["i3_append"]
    main_pool = app["main_index_pool"]
    control_pool = app["stock_control_pool"]
    print(f"[pool] main_index={len(main_pool)} control={len(control_pool)} adjusted=false / 无法可靠复权")

    # 载入 parquet（只读）
    frames: Dict[str, pd.DataFrame] = {}
    for sym in main_pool + control_pool:
        frames[sym] = pd.read_parquet(DATA_DIR / f"{sym}_1d.parquet")

    main_union = np.unique(np.concatenate([frames[s]["date"].to_numpy(np.int64) for s in main_pool]))
    control_union = np.unique(np.concatenate([frames[s]["date"].to_numpy(np.int64) for s in control_pool]))

    factors: List[Dict[str, Any]] = []
    control: List[Dict[str, Any]] = []
    trade_dump: Dict[str, Any] = {"main": {}, "control": {}}

    for fac in FACTORS:
        fid = fac["id"]
        runs_main = [run_one(frames[s], s, "main_index", fid) for s in main_pool]
        runs_ctrl = [run_one(frames[s], s, "stock_control", fid) for s in control_pool]
        fm = aggregate(fac, runs_main, main_union, gate, cost_gate, "main_index")
        fc = aggregate(fac, runs_ctrl, control_union, gate, cost_gate, "stock_control")
        factors.append(fm)
        control.append(fc)
        trade_dump["main"][fid] = fm["_all_trades"]
        trade_dump["control"][fid] = fc["_all_trades"]
        print(f"[run] {fid} {fac['name']}: main trades={fm['n_trades']} "
              f"net={fm['net_total']} cost_ok={fm['cost_ok']} dd={fm['max_dd']} "
              f"calmar={fm['calmar']} sharpe={fm['sharpe_reference']} "
              f"consec={fm['max_consec_losses']} t1={fm['t1_violations']} "
              f"decidable={fm['decidable_4plus_trades_pass']} | "
              f"control trades={fc['n_trades']} t1={fc['t1_violations']}", flush=True)

    result = build_report(m, gate, cost_gate, factors, control, tf)

    jp = OUT_DIR / "a_share_technical_factors.json"
    jp.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    mp = OUT_DIR / "a_share_technical_factors.md"
    write_md(result, mp)
    tp = OUT_DIR / "a_share_technical_factors_trades.json"
    tp.write_text(json.dumps(trade_dump, ensure_ascii=False), encoding="utf-8")

    print(f"\n[out] {jp}")
    print(f"[out] {mp}")
    print(f"[out] {tp}（附加：全量逐笔，供 T+1 核验）")
    print(f"[summary] n_factors={result['summary']['n_factors']} "
          f"n_runs={result['summary']['n_runs']} "
          f"n_pass_decidable_4plus_trades={result['summary']['n_pass_decidable_4plus_trades']} "
          f"n_passed_final={result['summary']['n_passed_final']}（非最终达标 / 未含极端段，如实报 0）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
