# -*- coding: utf-8 -*-
"""I-1 阶段二 · A 股同口径 M8 回测程序（后端架构师产出，PM 承派 + 验收）

老板 2026-11-15 16:55 指令（逐字）：「…你让PM再做一个一样的回测程序出来。」
「一样的」= 同一套 M8 全门槛判定 + 同一 `StrategyRules` 配对均值回归信号骨架，
数据源换成 A 股（本批为 phase6 §4「换市场」结构性变量落地：骨架本身未被证伪，
换市场只需换 M3+M4 的数据来源，6 层骨架 / 5 个跨市场 ABC 不动）。

按 agents/souls/backend_architect.md 纪律：
  - 明确模块边界（数据层 parquet ← 阶段一；信号层/决策层复用 src/ 骨架；本脚本只做回测编排）
  - 接口可测试、可回滚：**新增脚本，保留 backtest/ 下全部旧脚本不动**；不改 src/、不改 config/
  - 结果如实申报：0 达标就报 0，不美化、不粉饰、不与加密 257 组混算

============================================================================
骨架复用（逐项可验证，不改 src/ 一行）
============================================================================
  M3 信号：`src.signal_layer.mean_reversion_signals.MeanReversionSignals`
       - h = 60bar 滚动「收益率」OLS（红线 R2，不是价格回归）
       - h∈[0.5,2.0] 越界 → 短窗口(30bar)重估钳制 → DEGRADED（可交易）；
         重估失败（窗口方差=0）→ **INVALID**，FLAT 不开仓 / 持仓强制离场（红线，见下「口径差异」）
       - spread = lnA − h·lnB（R1）、z-score 取原始价差滚动 μ/σ（R3）
       - half_life 一阶自相关、ADF 平稳性
  M4 决策：`src.decision_layer.mean_reversion_strategy.MeanReversionStrategy`
       + `StrategyRules`（单一配置入口）+ `PositionSnapshot`
       - |z| ≥ entry_sigma 开仓；FLAT + INVALID 不开仓
       - 三线并行先到先平（止盈 > 止损 > 超时），全部**相对开仓点** σ 距离锁定参照系
       - 超时 = max(timeout_hours×bars_per_hour, 1.2×median_half_life, timeout_bars)
       - 直接调用 `decide()`，逐 bar 驱动（滑动窗口 tail(200)，避免 O(n²) 全量切片）
  R4 防未来函数：**bar t 收盘出信号 → bar t+1 的 open 成交**（比 M8 用 close 更保守，
       同 phase6 §1）。日线 1d 下开仓 t+1、最早平仓 t+2 → T+1 制度天然满足。

  ★ 口径差异（如实记录，不擅自改骨架、不脑补）：派活单简写「h∈[0.5,2.0] 越界判
    INVALID 不开仓」；骨架 P1 修复后的实际语义是「越界先重估钳制 DEGRADED（仍可
    交易），重估失败才 INVALID」。本脚本**直接复用骨架原语义**，最终「重估失败=
    INVALID=不开仓」与派活单一致；DEGRADED 段如实参与、不隐瞒、不改写骨架。

============================================================================
冻结门槛（config/strategy_params.yaml 现行，**一字不动、只读、不写回**）
============================================================================
  min_sharpe: null          → 不设夏普硬门槛（判定 6 条退化为 5 条）
  max_cost_as_pct_of_gross: 0.50
  max_drawdown_pct: 10.0
  min_calmar: 1.5
  max_consecutive_losses: 5
  笔数下限 ≥30；极端段不亏
  ★ 旧脚本 `g4_carry_rerun.py:85` 正则 `min_sharpe:\\s*([\\d.]+)` 对 null 匹配不到
    会 fallback 1.0；本脚本按 `g4_carry_410d_rerun.py:97-103` 的正确读法把 null
    读成 None，并显式留痕「不设夏普硬门槛」。
  ★ phase9-h1 §0.2 口径区分：历史讨论基线是「夏普≥2.0 / 成本≤35%」，config 现行
    是「null / 0.50」。本批以 **config 现行值为准**，两者区分记录、不混淆、不放宽。

============================================================================
A 股成本参数（独立，**全部标注「待老板确认的假设」**，绝不写进 config）
============================================================================
  绝不拿 Gate.io 现货 VIP0 0.1%/边 硬套 A 股（phase7「taker 0.1% 占 71% /
  roundtrip 28bp」仅适用于加密现货均值回归主线，见 phase9-h1 §0.2）。
  逐项数值 + 来源性质见下方 `A_SHARE_COST` 常量。

============================================================================
极端段（老板决策权，PM 与工程师均不得擅自替换）
============================================================================
  冻结门槛 `must_survive_extreme_window: "2026-02"` 是**加密崩盘段**。A 股改用哪段
  （2008/2015/2018/2020/2022/2024…）属老板拍板。本批只做多段敏感性对照并列建议表，
  **冻结判定第 5 条单列「待老板拍板」**：不得把 N/A 当通过、不得用 0 占位顶替。
  因此 `n_passed_final = 0`（判定未闭合，任何组不能判「最终达标」，如实而非美化）。

============================================================================
阶段一限制继承（硬前置结论，不得写成「可上线依据」）
============================================================================
  `data/cache/tdx_a_share/_manifest.json` 的 `adjustment.conclusion = 无法可靠复权`：
  gbbq 内容加密、base.dbf 无历史除权序列 → 本批**未复权**。降级：主组纯指数无除权、
  ETF 分红 residual 🟡、个股 🔴 仅对照。全部结果显式标注该限制。

红线：本机文件只读；不改 src/、config/strategy_params.yaml、.bat/.gitignore/.env；
      不提交 Git、不放宽冻结门槛、不启用实盘、无令牌/密钥、不接付费源、不注册 key；
      不抢跑 G-6-③ / G-5-②；撞 429 隔 3 分钟重试一次并如实记录。

运行：python backtest/a_share_m8_rerun.py
输出：backtest/output/a_share_m8_rerun.json + .md
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.signal_layer.mean_reversion_signals import MeanReversionSignals           # M3
from src.decision_layer.mean_reversion_strategy import (                            # M4
    StrategyRules, MeanReversionStrategy, PositionSnapshot,
)
from src.core.timeframe import resolve_timeframe

DATA_DIR = ROOT / "data" / "cache" / "tdx_a_share"
OUT_DIR = ROOT / "backtest" / "output"
PARAM_YAML = ROOT / "config" / "strategy_params.yaml"       # 只读，一字不动
MANIFEST = DATA_DIR / "_manifest.json"

# ---------------------------------------------------------------------------
# A 股成本参数（独立常量；全部「待老板确认的假设」；绝不写进 config）
# ---------------------------------------------------------------------------
A_SHARE_COST: Dict[str, Any] = {
    "_label": "待老板确认的假设",
    "_note": "以下为项目假设 / 公开费率的单一代表值，均未经交易所对本账户核实；"
             "A 股历史费率多次调整（印花税 2008/2023、过户费 2022 等），本批不分段回溯，"
             "统一用单一代表值并标注。绝不套用 Gate.io 现货 0.1%/边。",
    "commission": {
        "value_pct_per_side": 0.00025, "display": "万2.5（0.025%）/边，双边收",
        "nature": "项目假设", "label": "待老板确认的假设",
    },
    "stamp_duty": {
        "value_pct_sell_only": 0.0005, "display": "卖出单边 0.05%（2023-08-28 起减半后档）",
        "nature": "交易所/财政部公开税率，但历史多次变动；本批取单一现行代表值",
        "label": "待老板确认的假设",
    },
    "transfer_fee": {
        "value_pct_per_side": 0.00001, "display": "0.001%（0.01‰）/边，双边收（2022-04-29 起沪深统一档）",
        "nature": "交易所公开费率，但历史沪深不一致；本批取单一现行代表值",
        "label": "待老板确认的假设",
    },
    "slippage": {
        "value_bps_per_side": 5.0, "display": "5bp/边",
        "nature": "项目假设（盘上无 A 股历史盘口实测；src/core/market_ifaces.py "
                  "BrokerAdapter 注释『A股 0.3-0.5%』为架构占位口径，未实测、不硬套）",
        "label": "待老板确认的假设",
    },
}
LEG_NOTIONAL = 500.0            # 每腿名义（元）；两腿各 50%，总名义 1000（对齐加密 BASE_EQUITY×leg_equity_pct 口径）
BASE_EQUITY = 10_000.0
ANNUAL_BARS = 252               # A 股 1d→252 根/年（config timeframe 已预留）

# 每笔完整交易（开+平=4 次成交：2 买 2 卖）恒定成本
_COMM = A_SHARE_COST["commission"]["value_pct_per_side"]
_TRANS = A_SHARE_COST["transfer_fee"]["value_pct_per_side"]
_SLIP = A_SHARE_COST["slippage"]["value_bps_per_side"] / 10_000.0
_STAMP = A_SHARE_COST["stamp_duty"]["value_pct_sell_only"]
COST_PER_ROUNDTRIP = (
    4 * LEG_NOTIONAL * (_COMM + _TRANS + _SLIP)      # 4 次成交的佣金/过户/滑点
    + 2 * LEG_NOTIONAL * _STAMP                      # 2 次卖出成交的印花税
)
ROUNDTRIP_BP = COST_PER_ROUNDTRIP / (2 * LEG_NOTIONAL) * 10_000.0   # 相对总名义 bp

# ---------------------------------------------------------------------------
# 冻结门槛（只读 config；null 正确读法；SHA256-16 指纹留痕）
# ---------------------------------------------------------------------------
def param_fingerprint() -> Dict[str, Any]:
    raw = PARAM_YAML.read_bytes()
    fp = hashlib.sha256(raw).hexdigest()
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
        g["min_sharpe"] = None                        # 缺失也不擅自设硬门槛
    m = re.search(r"min_calmar:\s*([\d.]+)", seg)
    g["min_calmar"] = float(m.group(1)) if m else 1.5
    m = re.search(r"max_consecutive_losses:\s*(\d+)", seg)
    g["max_consecutive_losses"] = int(m.group(1)) if m else 5
    m = re.search(r"must_survive_extreme_window:\s*\"([^\"]+)\"", seg)
    g["extreme_window"] = m.group(1) if m else "2026-02"
    m = re.search(r"extreme_window_start:\s*(\d+)", seg)
    g["extreme_window_start"] = int(m.group(1)) if m else 1770163200
    m = re.search(r"extreme_window_end:\s*(\d+)", seg)
    g["extreme_window_end"] = int(m.group(1)) if m else 1770681600
    return g


def load_cost_gate_from_yaml() -> Dict[str, Any]:
    """读 cost_model.max_cost_as_pct_of_gross（只读，现行 0.50）。"""
    txt = PARAM_YAML.read_text(encoding="utf-8")
    i = txt.index("cost_model:")
    j = txt.find("# ---- 因子拥挤", i)
    seg = txt[i:(j if j > i else len(txt))]
    m = re.search(r"max_cost_as_pct_of_gross:\s*([\d.]+)", seg)
    return {"max_cost_as_pct_of_gross": float(m.group(1)) if m else 0.50}


# ---------------------------------------------------------------------------
# 数据加载（阶段一 parquet；只读）
# ---------------------------------------------------------------------------
def load_pair(leg_a: str, leg_b: str) -> pd.DataFrame:
    a = pd.read_parquet(DATA_DIR / f"{leg_a}_1d.parquet")[["date", "ts", "open", "close"]]
    b = pd.read_parquet(DATA_DIR / f"{leg_b}_1d.parquet")[["date", "open", "close"]]
    a = a.rename(columns={"open": "open_a", "close": "close_a"})
    b = b.rename(columns={"open": "open_b", "close": "close_b"})
    # 保留 a 的 ts 作为统一时间轴（compute_signals 需要 ts 列）；两腿按 date inner join 对齐
    al = a.merge(b, on="date").sort_values("date").reset_index(drop=True)
    return al


def data_fingerprint() -> Dict[str, Any]:
    m = json.loads(MANIFEST.read_text(encoding="utf-8"))
    fps = {f["symbol"]: {"source_sha256_16": f["source_sha256_16"],
                         "parquet_sha256_16": f["parquet_sha256_16"],
                         "n_bars": f["n_bars"]}
           for f in m["files"]}
    return {"manifest_sha256_16": hashlib.sha256(MANIFEST.read_bytes()).hexdigest()[:16],
            "adjustment_conclusion": m["adjustment"]["conclusion"],
            "files": fps}


# ---------------------------------------------------------------------------
# 单组回测（复用 M3/M4 骨架，逐 bar t+1 成交）
# ---------------------------------------------------------------------------
def run_group(pair: Dict[str, Any], gate: Dict[str, Any]) -> Dict[str, Any]:
    leg_a, leg_b = pair["leg_a"], pair["leg_b"]
    al = load_pair(leg_a, leg_b)
    n = len(al)

    # M3：60bar 收益率 OLS、[0.5,2.0] 越界重估/INVALID、spread=lnA−h·lnB、z-score 原始价差
    sig = MeanReversionSignals(
        hedge_window=60, hedge_range=(0.5, 2.0),
        entry_sigma=3.0, exit_sigma=0.5,
    ).compute_signals(al[["ts", "close_a", "close_b"]], window=60)

    # M4：StrategyRules 单一配置入口（冻结默认值），interval=1d
    rules = StrategyRules(
        entry_sigma=3.0, take_profit_sigma_from_entry=2.5,
        stop_loss_sigma_from_entry=2.0, timeout_bars=0,
        timeout_hours=3.0, timeout_half_life_factor=1.2,
        position_cap=0.05,
    )
    rules.validate()
    strat = MeanReversionStrategy(rules=rules, interval="1d")

    ts_ms = (al["ts"].astype("int64") * 1000).to_numpy()
    oa = al["open_a"].to_numpy(float)
    ob = al["open_b"].to_numpy(float)

    pos = PositionSnapshot()                 # FLAT
    pending = None                           # (intent, z_at_signal, reason)
    entry_px = None                          # (px_a, px_b, bar_index)
    trades: List[Dict[str, Any]] = []
    equity = np.full(n, BASE_EQUITY, dtype=float)   # 已实现权益曲线（不含浮动，口径如实标注）

    WARM = 60                                # h/z 前 60 根为 NaN（NaN_GUARD）
    for i in range(WARM, n - 1):
        # ---- 1) t=i-1 收盘挂起的意图，在 bar i 的 open 成交（R4 防未来函数）----
        if pending is not None:
            intent, z_sig, reason = pending
            pending = None
            if reason == "entry":
                pos.state = ("LONG_SPREAD" if intent.symbol == "long_spread"
                             else "SHORT_SPREAD")
                pos.entry_ts_ms = int(ts_ms[i])
                pos.entry_zscore = float(z_sig)
                entry_px = (oa[i], ob[i], i)
            else:  # close（exit / stop_loss / timeout / invalid_signal_exit / end_force_close）
                if entry_px is not None:
                    ea, eb, ei = entry_px
                    ret_a = oa[i] / ea - 1.0
                    ret_b = ob[i] / eb - 1.0
                    sign = 1.0 if pos.state == "LONG_SPREAD" else -1.0
                    gross = sign * (ret_a - ret_b) * LEG_NOTIONAL
                    net = gross - COST_PER_ROUNDTRIP
                    trades.append({
                        "entry_bar": ei, "exit_bar": i, "reason": reason,
                        "state": pos.state, "gross": round(float(gross), 4),
                        "cost": round(COST_PER_ROUNDTRIP, 4), "net": round(float(net), 4),
                        "entry_date": str(int(al["date"].iloc[ei])),
                        "exit_date": str(int(al["date"].iloc[i])),
                    })
                    equity[i:] += net
                pos = PositionSnapshot()      # FLAT
                entry_px = None

        # ---- 2) bar i 收盘出决策（复用 M4 decide，滑动窗口 tail(200)）----
        window = sig.iloc[max(0, i - 199): i + 1]
        intent = strat.decide(window, pos, int(ts_ms[i]))
        if intent is not None:
            reason = intent.reason or ("entry" if intent.side in ("buy", "sell") else "close")
            pending = (intent, float(sig["zscore"].iloc[i]), reason)

    # 末尾未平仓持仓：最后一根 bar 的 open 强平（如实计入，标注 end_force_close）
    if pos.state != "FLAT" and entry_px is not None:
        i = n - 1
        ea, eb, ei = entry_px
        ret_a, ret_b = oa[i] / ea - 1.0, ob[i] / eb - 1.0
        sign = 1.0 if pos.state == "LONG_SPREAD" else -1.0
        gross = sign * (ret_a - ret_b) * LEG_NOTIONAL
        net = gross - COST_PER_ROUNDTRIP
        trades.append({
            "entry_bar": ei, "exit_bar": i, "reason": "end_force_close",
            "state": pos.state, "gross": round(float(gross), 4),
            "cost": round(COST_PER_ROUNDTRIP, 4), "net": round(float(net), 4),
            "entry_date": str(int(al["date"].iloc[ei])),
            "exit_date": str(int(al["date"].iloc[i])),
        })
        equity[i:] += net

    # ---- 指标（口径如实标注：已实现权益曲线、日频、√252 年化，未计浮动盈亏）----
    n_trades = len(trades)
    gross_total = float(sum(t["gross"] for t in trades))
    cost_total = float(sum(t["cost"] for t in trades))
    cost_ok = bool(gross_total > 0 and cost_total <= gate["max_cost_as_pct_of_gross"] * gross_total)
    total_return = float(equity[-1] / BASE_EQUITY - 1.0)
    peak = np.maximum.accumulate(equity)
    max_dd = float(np.max((peak - equity) / peak)) if np.all(peak > 0) else 0.0
    r = np.diff(equity) / equity[:-1]
    sharpe = float(np.mean(r) / np.std(r) * np.sqrt(ANNUAL_BARS)) if len(r) > 1 and np.std(r) > 0 else 0.0
    years = n / ANNUAL_BARS
    annual_return = (float((1.0 + total_return) ** (1.0 / years)) - 1.0
                     if years > 0 and total_return > -1 else 0.0)
    calmar = float(annual_return / max_dd) if max_dd > 1e-12 else (0.0 if annual_return <= 0 else None)
    max_consec, cur = 0, 0
    for t in trades:
        cur = cur + 1 if t["net"] <= 0 else 0
        max_consec = max(max_consec, cur)

    # ---- 5 条冻结门槛逐条（min_sharpe=null → 夏普不设硬门槛，判定退化为 5 条）----
    gates = {
        "1_max_drawdown_le_10pct": bool(max_dd <= gate["max_drawdown_pct"] / 100.0),
        "2_calmar_ge_1p5": bool(calmar is not None and calmar >= gate["min_calmar"]),
        "3_cost_le_50pct_of_gross": bool(cost_ok),
        "4_max_consec_losses_le_5": bool(max_consec <= gate["max_consecutive_losses"]),
        # 第 5 条「极端段不亏」：A 股段属老板拍板 → 单列 PENDING，不计通过、不用 0 占位
        "5_extreme_not_loss": "PENDING_BOSS_DECISION",
    }
    gates["sharpe_hard_gate"] = "不设硬门槛（min_sharpe=null）"
    gates["min_trades_ge_30"] = bool(n_trades >= 30)
    # 前 4 条 + 笔数 的可判定结论（供老板参考；不等同最终达标）
    decidable_pass = (gates["1_max_drawdown_le_10pct"] and gates["2_calmar_ge_1p5"]
                      and gates["3_cost_le_50pct_of_gross"]
                      and gates["4_max_consec_losses_le_5"]
                      and gates["min_trades_ge_30"])

    return {
        "id": pair["id"], "pair": f"{leg_a}×{leg_b}", "leg_a": leg_a, "leg_b": leg_b,
        "kind": pair["kind"], "corporate_action_risk": pair["corporate_action_risk"],
        "reason": pair["reason"],
        "n_bars": n, "start_date": str(int(al["date"].iloc[0])),
        "end_date": str(int(al["date"].iloc[-1])),
        "n_trades": n_trades,
        "gross_total": round(gross_total, 4), "cost_total": round(cost_total, 4),
        "cost_gross_ratio": round(cost_total / gross_total, 4) if gross_total > 0 else None,
        "cost_ok": cost_ok,
        "total_return": round(total_return, 6), "max_dd": round(max_dd, 6),
        "calmar": (round(calmar, 4) if calmar is not None else None),
        "sharpe": round(sharpe, 4), "max_consec_losses": max_consec,
        "gates": gates,
        "decidable_4plus_trades_pass": bool(decidable_pass),
        "passed_final": False,             # 第 5 条未闭合 → 任何组不判最终达标
        "passed_final_reason": "极端段冻结判定『待老板拍板』，第 5 条未闭合；"
                               "不得把 N/A 当通过、不得用 0 占位 → passed_final=False（如实）",
        "trades_sample": trades[:5],
        "equity_for_extreme": [
            {"date": str(int(al["date"].iloc[k])), "equity": round(float(equity[k]), 4),
             "ts_ms": int(ts_ms[k])}
            for k in range(n)
        ],
        "trades_for_extreme": trades,
        "adjusted": False,
        "adjustment_limit": "未复权（阶段一结论：无法可靠复权）；结果不得写成『可上线依据』",
    }


# ---------------------------------------------------------------------------
# 多段极端段敏感性对照（仅建议表，冻结判定仍 PENDING）
# ---------------------------------------------------------------------------
EXTREME_CANDIDATES = [
    {"id": "2008_crisis", "start": "20080101", "end": "20081231", "label": "2008 金融危机"},
    {"id": "2015_crash", "start": "20150601", "end": "20151231", "label": "2015 股灾"},
    {"id": "2018_bear", "start": "20180101", "end": "20181231", "label": "2018 熊市"},
    {"id": "2020_covid", "start": "20200101", "end": "20200430", "label": "2020 疫情冲击"},
    {"id": "2022_bear", "start": "20220101", "end": "20221231", "label": "2022 熊市"},
    {"id": "2024_feb", "start": "20240101", "end": "20240229", "label": "2024-02 流动性冲击"},
    {"id": "2026_feb_crypto_window", "start": "20260204", "end": "20260210",
     "label": "2026-02（加密崩盘段原冻结值，A 股同期对照）"},
]


def sensitivity(group: Dict[str, Any]) -> List[Dict[str, Any]]:
    eq = group["equity_for_extreme"]
    out = []
    for seg in EXTREME_CANDIDATES:
        s, e = int(seg["start"]), int(seg["end"])
        idx = [k for k, x in enumerate(eq) if s <= int(x["date"]) <= e]
        if not idx:
            out.append({"segment": seg["id"], "label": seg["label"],
                        "covered": False, "note": "该组数据不覆盖此段（上市前）",
                        "bars": 0, "closed_trades": 0, "equity_change": None})
            continue
        i0, i1 = idx[0], idx[-1]
        base, end = eq[i0]["equity"], eq[i1]["equity"]
        chg = end - base
        seg_trades = [t for t in group["trades_for_extreme"]
                      if s <= int(t["exit_date"]) <= e]
        out.append({
            "segment": seg["id"], "label": seg["label"], "covered": True,
            "bars": len(idx), "closed_trades": len(seg_trades),
            "segment_realized_pnl": round(float(sum(t["net"] for t in seg_trades)), 4),
            "equity_change": round(float(chg), 4),
            "equity_change_pct": round(float(chg / base), 6) if base else None,
            "survived_if_chosen": bool(chg >= -1e-9),
        })
    return out


# ---------------------------------------------------------------------------
# 报告（json + md）
# ---------------------------------------------------------------------------
def build_report(gate: Dict[str, Any], cost_gate: Dict[str, Any],
                 groups: List[Dict[str, Any]]) -> Dict[str, Any]:
    dfp = data_fingerprint()
    pfp = param_fingerprint()
    n_passed_final = sum(1 for g in groups if g["passed_final"])

    result: Dict[str, Any] = {
        "batch": "I-1", "phase": "阶段二 · 回测程序",
        "role": "backend_architect",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "meta": {
            "script": "backtest/a_share_m8_rerun.py",
            "old_scripts_untouched": True,
            "src_untouched": True, "config_untouched": True,
            "data_fingerprint": dfp,
            "param_fingerprint": pfp,
            "gate": {**gate, **cost_gate},
            "sharpe_gate": "不设硬门槛（min_sharpe=null；判定 6 条退化为 5 条）",
            "gate_note": "以 config/strategy_params.yaml 现行值为准；与 phase9-h1 §0.2 "
                         "『历史讨论基线 夏普≥2.0 / 成本≤35%』区分记录，不混淆、不放宽、不回滚",
            "assumption_label": "待老板确认的假设",
            "a_share_cost": A_SHARE_COST,
            "cost_per_roundtrip_yuan": round(COST_PER_ROUNDTRIP, 4),
            "cost_roundtrip_bp_vs_total_notional": round(ROUNDTRIP_BP, 3),
            "no_gateio_fee_applied": True,
            "gateio_note": "未套用 Gate.io 现货 0.1%/边 / roundtrip 28bp（phase7 仅适用加密现货主线）",
            "adjustment_limit": dfp["adjustment_conclusion"],
            "result_not_upper_line_evidence": True,
            "extreme_gate": "PENDING_BOSS_DECISION",
            "extreme_gate_note": "冻结门槛 2026-02 是加密崩盘段；A 股改用哪段属老板拍板。"
                                 "本批仅多段敏感性对照列建议表，冻结判定单列待拍板，"
                                 "不得把 N/A 当通过、不得用 0 占位顶替。",
            "skeleton_reuse": {
                "M3": "src/signal_layer/mean_reversion_signals.py MeanReversionSignals（60bar 收益率 OLS / [0.5,2.0] / INVALID / spread=lnA−h·lnB）",
                "M4": "src/decision_layer/mean_reversion_strategy.py StrategyRules + MeanReversionStrategy.decide()（|z|≥entry 开仓 / 三线并行先到先平 / 相对开仓点 σ / 超时 max(3h,1.2H,bars)）",
                "R4": "bar t 收盘出信号 → bar t+1 open 成交（防未来函数）",
                "timeframe": "resolve_timeframe('1d', hours_per_day=4, trading_days_per_year=252) → bars_per_year=252（A 股已预留）",
                "p1_degraded_note": "骨架 P1：h 越界先重估钳制 DEGRADED（可交易），重估失败才 INVALID；"
                                    "本脚本复用原语义，不改写骨架",
            },
        },
        "summary": {
            "n_groups": len(groups),
            "n_passed_final": n_passed_final,
            "n_passed_final_reason": "0 达标如实申报：极端段冻结判定『待老板拍板』使第 5 条未闭合，"
                                     "任何组不能判『最终达标』；且本批未复权、结果非可上线依据。"
                                     "不与加密 257 组混算。",
            "data_fingerprint": dfp["manifest_sha256_16"],
            "param_fingerprint": pfp["sha256_16"],
            "assumption_label": "待老板确认的假设",
            "adjustment_limit": "未复权（无法可靠复权）",
            "extreme_gate": "PENDING_BOSS_DECISION",
        },
        "groups": [{k: v for k, v in g.items()
                    if k not in ("equity_for_extreme", "trades_for_extreme")}
                   for g in groups],
        "extreme_sensitivity": {g["id"]: sensitivity(g) for g in groups},
        "comparison_with_crypto": {
            "crypto_mainline": "加密现货 1h 配对均值回归：M8 132 笔、累计毛利 +$107.93、"
                               "成本 $192.54、成本/毛利 1.784；phase7 roundtrip 28bp、taker 占 71%；"
                               "phase9-h1 结论：M8 候选 0 达标（257 组网格口径 0 通过）",
            "a_share_this_batch": "3 组代表性配对 × 冻结参数 1 组，1d 日线 1998-09-15~2026-11-13；"
                                  f"成本假设 roundtrip≈{ROUNDTRIP_BP:.1f}bp（待确认）、未复权",
            "n_passed_final_a_share": n_passed_final,
            "not_mixed": True,
            "note": "换市场属 phase6 §4 结构性变量；本批只验证『同一骨架 + A 股数据 + 独立成本』"
                    "能否跑通全门槛判定，不与加密 257 组合并统计、不据此宣告骨架被证伪或救活",
        },
        "compliance": {
            "local_files_read_only": True, "clients_not_started": True,
            "no_network_market_data": True, "no_registry_change": True,
            "src_unchanged": True, "config_unchanged": True,
            "bat_gitignore_env_unchanged": True, "no_git_commit": True,
            "no_gate_relax": True, "no_live_trading": True,
            "no_token_or_key": True, "no_paid_source": True,
            "no_api_key_registration": True,
            "no_g6_3_g5_2_jump": True,
            "result_not_sugarcoated": True,
        },
    }
    return result


def write_md(result: Dict[str, Any], path: Path) -> None:
    m = result["meta"]
    L: List[str] = []
    L.append("# I-1 阶段二 · A 股同口径 M8 回测结果")
    L.append("")
    L.append(f"- 生成：{result['generated_at']}　｜　脚本：`backtest/a_share_m8_rerun.py`（新增，旧脚本全保留）")
    L.append(f"- 数据指纹：`{m['data_fingerprint']['manifest_sha256_16']}`　参数指纹：`{m['param_fingerprint']['sha256_16']}`")
    L.append(f"- 复权限制：**{m['adjustment_limit']}**（阶段一硬前置结论；结果不得写成『可上线依据』）")
    L.append("")
    L.append("## 1. 口径")
    L.append("")
    L.append(f"- 冻结门槛（config 现行，一字不动）：回撤≤{m['gate']['max_drawdown_pct']}% / "
             f"Calmar≥{m['gate']['min_calmar']} / 成本≤毛利 {m['gate']['max_cost_as_pct_of_gross']*100:.0f}% / "
             f"连亏≤{m['gate']['max_consecutive_losses']} / 笔数≥30 / 极端段不亏")
    L.append(f"- **{m['sharpe_gate']}**（旧脚本正则把 null fallback 1.0 的坑已避开；判定 6 条退化为 5 条）")
    L.append(f"- 第 5 条「极端段不亏」：**{m['extreme_gate']}**——A 股段属老板拍板，"
             "本批只做多段敏感性对照，冻结判定单列待拍板，不把 N/A 当通过、不用 0 占位")
    L.append(f"- A 股成本（全部「{m['assumption_label']}」，绝不写进 config、绝不套 Gate.io 0.1%/边）：")
    for k, v in m["a_share_cost"].items():
        if k.startswith("_"):
            continue
        val = (v.get("display") or v.get("value_bps_per_side"))
        L.append(f"    - {k}: **{val}** — {v['nature']}")
    L.append(f"- 每笔完整交易 roundtrip ≈ **{m['cost_per_roundtrip_yuan']} 元 / "
             f"{m['cost_roundtrip_bp_vs_total_notional']}bp**（相对总名义；待确认假设）")
    L.append("- 骨架复用：M3 `MeanReversionSignals`（60bar 收益率 OLS、[0.5,2.0]、INVALID）+ "
             "M4 `StrategyRules`/`MeanReversionStrategy.decide()`（三线并行先到先平、相对开仓点 σ、"
             "超时 max(3h,1.2H,bars)）+ R4 t 出信号 t+1 open 成交")
    L.append("")
    L.append("## 2. 逐组结果（如实申报）")
    L.append("")
    L.append("| 组 | 配对 | 类型 | bar 数 | 成交数 | 毛利 | 成本 | 成本/毛利 | cost_ok | 回撤 | Calmar | 夏普 | 连亏 | 前4条+笔数 | 最终达标 |")
    L.append("|---|---|---|---:|---:|---:|---:|---:|---|---:|---:|---:|---:|---|---|")
    for g in result["groups"]:
        L.append(
            f"| {g['id']} | {g['pair']} | {g['kind']} | {g['n_bars']} | {g['n_trades']} | "
            f"{g['gross_total']} | {g['cost_total']} | {g['cost_gross_ratio']} | "
            f"{'✅' if g['cost_ok'] else '❌'} | {g['max_dd']*100:.2f}% | {g['calmar']} | "
            f"{g['sharpe']} | {g['max_consec_losses']} | "
            f"{'✅' if g['decidable_4plus_trades_pass'] else '❌'} | "
            f"❌（第5条待拍板） |")
    L.append("")
    L.append("### 5 条冻结门槛逐条")
    L.append("")
    for g in result["groups"]:
        L.append(f"**{g['id']}**（{g['pair']}）：")
        for k, v in g["gates"].items():
            if k in ("sharpe_hard_gate", "min_trades_ge_30"):
                L.append(f"  - {k}: {v}")
            elif isinstance(v, bool):
                L.append(f"  - {k}: {'✅' if v else '❌'}")
            else:
                L.append(f"  - {k}: **{v}**（待老板拍板，不计通过）")
        L.append("")
    L.append(f"> **最终达标数 `n_passed_final` = {result['summary']['n_passed_final']} / "
             f"{result['summary']['n_groups']}**。理由：{result['summary']['n_passed_final_reason']}")
    L.append("")
    L.append("## 3. 多段极端段敏感性对照（仅建议表，冻结判定仍待老板拍板）")
    L.append("")
    L.append("| 段 | 覆盖 | 段内 bar | 段内平仓笔数 | 段内权益变化 | 若选此段是否『不亏』 |")
    L.append("|---|---|---:|---:|---:|---|")
    for gid, segs in result["extreme_sensitivity"].items():
        for s in segs:
            cov = "✅" if s["covered"] else "❌（上市前）"
            chg = s.get("equity_change")
            surv = ("—" if not s["covered"] else ("✅ 不亏" if s.get("survived_if_chosen") else "❌ 亏"))
            L.append(f"| {gid} · {s['label']} | {cov} | {s['bars']} | {s['closed_trades']} | "
                     f"{chg if chg is not None else 'N/A'} | {surv} |")
    L.append("")
    L.append("> 上表**仅供老板拍板参考**：本批不擅自把任何一段替换进冻结门槛，"
             "不把 N/A 当通过、不用 0 占位顶替。")
    L.append("")
    L.append("## 4. 与加密主线对比（不混算）")
    L.append("")
    L.append(f"- 加密主线：{result['comparison_with_crypto']['crypto_mainline']}")
    L.append(f"- 本批 A 股：{result['comparison_with_crypto']['a_share_this_batch']}")
    L.append(f"- 本批最终达标 **{result['comparison_with_crypto']['n_passed_final_a_share']}**；"
             "不与加密 257 组混算、不据此宣告骨架被证伪或救活。")
    L.append("")
    L.append("## 5. 合规确认（全否/全是逐项）")
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
    print(f"[gate] {gate} | cost_gate={cost_gate}")
    print(f"[gate] min_sharpe=None → 不设夏普硬门槛（判定 6 条退化为 5 条）")
    print(f"[timeframe] 1d A股 bars_per_year={tf.bars_per_year:g} annualize=√{tf.bars_per_year:g}={tf.annualize_factor:.2f}")
    print(f"[cost] A股 roundtrip≈{COST_PER_ROUNDTRIP:.4f}元 / {ROUNDTRIP_BP:.2f}bp "
          f"（全部『待老板确认的假设』，未套 Gate.io 0.1%/边）")

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    print(f"[phase1] 复权结论={manifest['adjustment']['conclusion']} → 阶段二显式标注该限制")
    print(f"[phase2] 标的池 {len(manifest['pairs'])} 组（阶段一确定，本批不擅自替换）")

    groups = []
    for p in manifest["pairs"]:
        print(f"[run] {p['id']} {p['leg_a']}×{p['leg_b']} ...", flush=True)
        g = run_group(p, {**gate, **cost_gate})
        groups.append(g)
        print(f"       bars={g['n_bars']} trades={g['n_trades']} gross={g['gross_total']} "
              f"cost={g['cost_total']} cost_ok={g['cost_ok']} dd={g['max_dd']} "
              f"calmar={g['calmar']} consec={g['max_consec_losses']} "
              f"passed_final={g['passed_final']}", flush=True)

    result = build_report(gate, cost_gate, groups)
    jp = OUT_DIR / "a_share_m8_rerun.json"
    jp.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    mp = OUT_DIR / "a_share_m8_rerun.md"
    write_md(result, mp)
    print(f"\n[out] {jp}")
    print(f"[out] {mp}")
    print(f"[summary] n_passed_final = {result['summary']['n_passed_final']} / "
          f"{result['summary']['n_groups']}（极端段待拍板，如实报 0）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
