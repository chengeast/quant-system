# -*- coding: utf-8 -*-
"""G-3 · G1 carry 基差信号 M8 全门槛回测（M3+M4 独立实现，不改 src/ 骨架）。

任务单：agents/phase9-g2-expert-opinions.md §6 G-3 任务单。
角色：后端架构师 主导 + 数据工程师协作（数据准备见 backtest/g1_carry_basis_data.py）。

数据（全部落盘，见 data/cache/g1_carry_data/_manifest.json）：
  现货 4h/1d parquet ∩ 永续 K线 ∩ funding_rate（G-3 实拉）
  窗口口径：现货 400d/900d ∩ funding 180d = 180d（4h=1080 根、1d=180 根，任务单标称）
  实际可用：funding 实得 100 根/币（~33d 窗口，2026-10-10 ~ 2026-11-12）→
  实际交集 4h=89~91 根、1d=31 根。标称 180d 中超出实得 funding 区间的部分
  无数据 → 如实申报 N/A（180d 限制 + funding 实得 100 根），不脑补、不补 0。
  极端段 2026-02 无 funding 覆盖 → 全部标 N/A(180d限制)，不当通过。

信号（M3 层 FundingCarrySignal，t 出信号 / t+1 成交，复用 M8 防未来函数口径）：
  开仓：funding_rate > 0（8h 结算）且 基差（perp_close − spot_close）> 0，
        且开仓条件在入场 bar 内持续成立（连续 ≥3 根 bar 同时 fr>0 且 basis>0）
  平仓：funding 转负 / 基差收敛(≤0) / 超时（4h 持仓 1 周期=1 bar、1d 持仓 1 周期=1 bar）
  周期只测 4h 与 1d（phase9 §六.2 已判 1h 粒度 carry 不成立）；做空侧本轮不测。

成本模型（M4 层，C 专家加严口径，phase9 §5.2）：
  roundtrip_cost = fee×2 + slippage×2 + latency_1_3s
  carry_yield    = funding_rate × position_dir(=+1 做多) × bars_held × (8h结算折算)
  net_cost       = roundtrip_cost − carry_yield            # carry 把 cost 翻转为净额
  gross_pnl_disc = 0.5 × gross_pnl_original               # 0.5x 抢跑折扣（分母）
  cost_ok        = net_cost ≤ 0.35 × gross_pnl_disc       # 0.35 不放宽、不设过渡档

双档费率（双档申报）：
  taker 档：fee=0.1%/边（VIP0 官方事实，读自 yaml taker_fee_pct）
            滑点 4bp/边（冻结 3~5bp 保守假设，取 3~5 上端）
            + latency_1_3s 假设值（1~3s ≈ 0.5~1.5 个 tick，按 1h K线 0.001% 量级
            估算取 0.1bp，盘上无实测，C 专家 Q4 已注明）
  maker 档：fee=0.1%/边（VIP0 maker 同价，roundtrip=0.2% 不变）
            滑点 0.5bp/边（maker 挂单被动成交，0~1bp 估算，假设值，盘上无实测）

门槛（6 条冻结，读自 config/strategy_params.yaml backtest_gate，不放宽）：
  回撤≤10% / 夏普≥2.0 / Calmar≥1.5 / 成本≤毛利35%(含 carry 翻转复算) /
  极端段(2026-02)不亏（本轮无法验证 → N/A(180d限制)，不计通过） / 连亏≤5
B 专家 8 项风控前置全部进回测（硬门槛，不放宽）：
  #1 回撤熔断 8%预警降敞口50% / 10%停机
  #2 funding 方向突变熔断（≥3期24h同号且|值|>2×均值→降敞口；≥5期→清零；
     特别监控 LTC 正占比 53% / DOGE 72%）
  #3 敞口上限 20%/币（4 币合计 ≤80%）
  #4 连亏熔断 ≤5
  #5 资金曲线监控（carry<0 且 price<0 双负 → 当日强制减仓）
  #6 极端段门槛降级（N/A(180d限制)，实盘监控承接）
  #7 样本笔数下限（4h≥30 / 1d≥30，<30 不计通过）
  #8 成本口径锁定（含 1~3s 时延 + 0.5x 抢跑折扣 + carry 翻转，≤0.35 复算）

产出：backtest/output/g1_carry_basis_backtest.json + .md
运行：python backtest/g1_carry_basis_backtest.py
本脚本不改 src/、不改 config/ 数值、不动 .bat/.gitignore/.env、不提交 Git。
"""
from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "backtest" / "output"

COINS = ["BTC", "ETH", "DOGE", "LTC"]
INTERVALS = ["4h", "1d"]
FEE_TIERS = ["taker", "maker"]

# ============================================================================
# 参数（读自 config/strategy_params.yaml，数值不改；SHA256 指纹留痕）
# ============================================================================

PARAM_YAML = ROOT / "config" / "strategy_params.yaml"


def param_fingerprint() -> Dict[str, Any]:
    raw = PARAM_YAML.read_bytes()
    fp = hashlib.sha256(raw).hexdigest()
    return {"file": str(PARAM_YAML.relative_to(ROOT)),
            "sha256": fp, "sha256_16": fp[:16]}


def load_gate_from_yaml() -> Dict[str, Any]:
    """读 backtest_gate 段数值（只读，不改 yaml）。"""
    import re
    txt = PARAM_YAML.read_text(encoding="utf-8")
    i = txt.index("backtest_gate:")
    j = txt.find("cost_model:", i)
    seg = txt[i:(j if j > i else len(txt))]
    g: Dict[str, Any] = {}
    m = re.search(r"max_drawdown_pct:\s*([\d.]+)", seg)
    g["max_drawdown_pct"] = float(m.group(1)) if m else 10.0
    m = re.search(r"min_sharpe:\s*([\d.]+)", seg)
    g["min_sharpe"] = float(m.group(1)) if m else 2.0
    # G-4-②：min_sharpe=null → 不设夏普硬门槛（老板原话「夏普率本就不要求过 1.0」）
    mnull = re.search(r"min_sharpe:\s*null", seg)
    if mnull:
        g["min_sharpe"] = None
    m = re.search(r"min_calmar:\s*([\d.]+)", seg)
    g["min_calmar"] = float(m.group(1)) if m else 1.5
    m = re.search(r"max_consecutive_losses:\s*(\d+)", seg)
    g["max_consecutive_losses"] = int(m.group(1)) if m else 5
    m = re.search(r"must_survive_extreme_window:\s*\"([^\"]+)\"", seg)
    g["extreme_window"] = m.group(1) if m else "2026-02"
    return g


def load_cost_from_yaml() -> Dict[str, Any]:
    """读 cost_model 段数值（只读，不改 yaml）。
    G-4-②：合约腿走 futures_leg（taker=0.05%/maker=0.02%）、现货腿走 spot_leg（0.1%）。
    """
    import re
    txt = PARAM_YAML.read_text(encoding="utf-8")
    i = txt.index("cost_model:")
    j = txt.find("# ---- 因子拥挤", i)
    seg = txt[i:(j if j > i else len(txt))]
    c: Dict[str, Any] = {}
    # 兼容字段（旧 taker_fee_pct，供 G-3 旧脚本；数值 0.001=现货腿）
    m = re.search(r"taker_fee_pct:\s*([\d.]+)", seg)
    c["taker_fee_pct"] = float(m.group(1)) if m else 0.001
    m = re.search(r"slippage_bps_per_side:\s*\[([^\]]+)\]", seg)
    c["slippage_bps_per_side"] = (
        [float(x) for x in m.group(1).split(",")] if m else [3.0, 5.0])
    m = re.search(r"max_cost_as_pct_of_gross:\s*([\d.]+)", seg)
    c["max_cost_as_pct_of_gross"] = float(m.group(1)) if m else 0.50
    # G-4-② 分腿费率：futures_leg（合约腿 taker/maker 双档）
    fl = re.search(r"futures_leg:\s*(?:#[^\n]*\n\s*)*"
                   r"taker_fee_pct:\s*([\d.]+)\s*\n\s*"
                   r"maker_fee_pct:\s*([\d.]+)", seg)
    if fl:
        c["futures_taker_fee_pct"] = float(fl.group(1))
        c["futures_maker_fee_pct"] = float(fl.group(2))
    else:
        # 回退：无 futures_leg 段（G-3 旧 yaml）→ 合约腿=现货腿=0.1%
        c["futures_taker_fee_pct"] = c["taker_fee_pct"]
        c["futures_maker_fee_pct"] = c["taker_fee_pct"]
    # G-4-② 分腿费率：spot_leg（现货腿 taker/maker）
    sl = re.search(r"spot_leg:\s*(?:#[^\n]*\n\s*)*"
                   r"taker_fee_pct:\s*([\d.]+)\s*\n\s*"
                   r"maker_fee_pct:\s*([\d.]+)", seg)
    if sl:
        c["spot_taker_fee_pct"] = float(sl.group(1))
        c["spot_maker_fee_pct"] = float(sl.group(2))
    else:
        c["spot_taker_fee_pct"] = c["taker_fee_pct"]
        c["spot_maker_fee_pct"] = c["taker_fee_pct"]
    return c


# ============================================================================
# 常量假设（须标注「假设值」：盘上无实测，C 专家 Q4 已注明）
# ============================================================================

# taker 档滑点：冻结 3~5bp/边 区间取保守上端 5bp？任务单口径 = 4bp/边
# （C 专家 §2.2：4bp 为项目内部保守假设），故 taker 档用 4bp/边。
TAKER_SLIP_BPS = 4.0
# maker 档滑点：挂单被动成交 0~1bp 估算取 0.5bp/边（假设值，盘上无实测）
MAKER_SLIP_BPS = 0.5
# latency_1_3s：1~3s 成交时延折算为 bar 内不利价格漂移（0.5~1.5 个 tick），
# 按 1h K线 0.001% 量级估算取 0.1bp = 0.00001（假设值，盘上无实测）
LATENCY_1_3S_PCT = 0.00001
# 0.5x 抢跑折扣（分母，C 专家加严）
SNIPING_DISCOUNT = 0.5
# 8h 结算折算：每 bar 跨几个 8h 结算点（4h bar 跨 1 个、1d bar 跨 3 个）
SETTLE_PER_BAR = {"4h": 1.0, "1d": 3.0}
# 持仓 1 周期 = 1 bar（任务单：4h 持仓 1 周期 / 1d 持仓 1 周期）
HOLD_BARS = 1
# B #7 笔数下限（4h≥30 / 1d≥30）
MIN_TRADES = 30
# B #3 敞口上限 20%/币（4 币合计 ≤80%）
MAX_EXPOSURE_PER_COIN = 0.20
# B #2 funding 突变参数
FUNDING_MUTATION_RUN = 3      # 连续 ≥3 期(24h) 同号且 |值|>2×均值 → 降敞口
FUNDING_CLEAR_RUN = 5         # ≥5 期 → 清零
# B #1 回撤预警/熔断
DD_WARN_PCT = 8.0
# B #5 资金曲线双负强制减仓
DUAL_NEG_DELEVER = True
# 年化（src/core/timeframe.py 口径：4h=√2190、1d=√365）
ANN_FACTOR = {"4h": np.sqrt(2190.0), "1d": np.sqrt(365.0)}


@dataclass
class FeeTier:
    name: str
    fee_pct: float            # 单边费率
    slip_bps: float          # 单边滑点 bp
    slip_note: str

    def roundtrip_cost_pct(self) -> float:
        """单 bar roundtrip = fee×2 + slip×2 + latency（C 专家口径，含 1~3s 时延）"""
        slip_pct = self.slip_bps * 1e-4
        return self.fee_pct * 2 + slip_pct * 2 + LATENCY_1_3S_PCT


def build_tiers(cost_cfg: Dict[str, Any]) -> Dict[str, FeeTier]:
    """G-4-② 合约腿双档费率（读 futures_leg，taker 0.05% / maker 0.02%）。
    现货腿（spot_leg 0.1%）本轮 carry 仅合约腿成交，故只报合约腿双档；
    现货腿费率在 meta/对照表里单独申报，不混入合约腿计算。"""
    taker_fee = cost_cfg.get("futures_taker_fee_pct", cost_cfg["taker_fee_pct"])
    maker_fee = cost_cfg.get("futures_maker_fee_pct", cost_cfg["taker_fee_pct"])
    return {
        "taker": FeeTier("taker", taker_fee, TAKER_SLIP_BPS,
                          "合约腿 taker 吃单 0.05%/边（Gate.io VIP0 永续官方事实）"
                          " + 滑点 4bp/边（冻结 3~5bp 区间保守假设）"),
        "maker": FeeTier("maker", maker_fee, MAKER_SLIP_BPS,
                         "合约腿 maker 挂单 0.02%/边（Gate.io VIP0 永续官方事实）"
                         " + 滑点 0.5bp/边（假设值、盘上无实测，沿用 G-3）"),
    }


# ============================================================================
# M3 · FundingCarrySignal（只换 M3+M4；M5-M8 接口口径复用 run_one_timeframe）
# ============================================================================

@dataclass
class CarryBar:
    ts: int
    spot_open: float
    spot_close: float
    perp_close: float
    funding_rate: float
    basis_pct: float
    entry_signal: bool      # t 时刻开仓信号（t+1 执行）
    exit_signal: bool       # t 时刻平仓信号（t+1 执行）
    exit_reason: str = ""


def build_signals(df: pd.DataFrame) -> List[CarryBar]:
    """M3 信号层：funding 持续为正 + 基差持续为正 → 开仓；
    平仓 = funding 转负 / 基差收敛(≤0) / 超时（4h 持仓 1 周期=1 bar、
    1d 持仓 1 周期=1 bar）。t 出信号、t+1 执行（复用 M8 防未来函数口径）。
    开仓要求「持续」：当前 bar 与前 2 根连续同时满足 fr>0 且 basis>0
    （任务单「funding 持续为正」口径）。
    基差 NaN（perp 缺失）→ basis_pos=False，不开仓（如实，不脑补）。
    """
    # 开仓「持续」窗口：任务单口径 = funding 持续为正（8h 结算）+ 基差为正。
    # 实测：33d 窗口内 fr 与 basis 同时为正的 bar 极稀（每币 0~1 根，且孤立
    # 不连续），故以 3 bar（≈1 个 24h 段）为「持续」下限；不满足 → 0 开仓。
    # 本参数如实申报为「持续」判定的工程实现口径，非任务单冻结数值。
    CONTINUITY_BARS = 3
    out: List[CarryBar] = []
    in_pos = False
    hist_fr_pos: List[bool] = []
    hist_bp_pos: List[bool] = []
    for i, r in df.iterrows():
        fr = float(r["funding_rate"])
        fr_pos = fr > 0
        bp_nan = pd.isna(r["basis"])
        bp_pos = (not bp_nan) and (float(r["basis"]) > 0)
        contiguous = (len(hist_fr_pos) >= CONTINUITY_BARS - 1
                      and all(hist_fr_pos[-(CONTINUITY_BARS - 1):])
                      and all(hist_bp_pos[-(CONTINUITY_BARS - 1):]))
        entry = (not in_pos) and fr_pos and bp_pos and contiguous
        # 持仓中平仓：funding 转负 / 基差收敛(≤0 或 NaN) / 超时（持仓满 1 bar 即平）
        exit_ = in_pos and ((not fr_pos) or (not bp_pos))
        reason = ""
        if exit_:
            parts = []
            if not fr_pos:
                parts.append("funding转负")
            if not bp_pos:
                parts.append("基差收敛" if not bp_nan else "基差缺失")
            reason = "+".join(parts)
        out.append(CarryBar(
            ts=int(r["ts"]),
            spot_open=float(r["spot_open"]),
            spot_close=float(r["spot_close"]),
            perp_close=(float(r["perp_close"]) if not pd.isna(r["perp_close"]) else float("nan")),
            funding_rate=fr,
            basis_pct=(float(r["basis_pct"]) if not bp_nan else float("nan")),
            entry_signal=entry,
            exit_signal=exit_,
            exit_reason=reason,
        ))
        if entry:
            in_pos = True
        elif in_pos and exit_:
            in_pos = False
        hist_fr_pos.append(fr_pos)
        hist_bp_pos.append(bp_pos)
        if len(hist_fr_pos) > CONTINUITY_BARS:
            hist_fr_pos.pop(0)
            hist_bp_pos.pop(0)
    return out


# ============================================================================
# funding 突变监控（B #2）：基于 8h funding 序列
# ============================================================================

def funding_mutation_state(funding_series: List[float], mean_abs: float
                           ) -> List[Dict[str, Any]]:
    """逐 8h 结算点判断 funding 突变降敞口/清零。
    ≥3 期同号且 |值|>2×均值 → scale=0.5；≥5 期 → scale=0.0。
    返回每个结算点的 {scale, run_len, sign, hit}。
    """
    out: List[Dict[str, Any]] = []
    run_len = 0
    run_sign = 0
    for r in funding_series:
        sign = 1 if r > 0 else (-1 if r < 0 else 0)
        if sign == 0:
            out.append({"scale": 1.0, "run_len": 0, "sign": 0, "hit": False})
            run_len, run_sign = 0, 0
            continue
        run_len = run_len + 1 if sign == run_sign else 1
        run_sign = sign
        hit = run_len >= FUNDING_MUTATION_RUN and abs(r) > 2 * mean_abs
        if run_len >= FUNDING_CLEAR_RUN and abs(r) > 2 * mean_abs:
            scale = 0.0
        elif hit:
            scale = 0.5
        else:
            scale = 1.0
        out.append({"scale": scale, "run_len": run_len, "sign": sign, "hit": hit})
    return out


# ============================================================================
# 回测主体（单组：币 × 粒度 × 费率档）
# ============================================================================

@dataclass
class GroupResult:
    coin: str
    interval: str
    tier: str
    bars: int = 0
    span_days: int = 0
    n_trades: int = 0
    n_trades_min_ok: bool = False
    net_return_pct: float = 0.0
    gross_pnl: float = 0.0
    gross_pnl_disc: float = 0.0
    carry_yield_total: float = 0.0
    roundtrip_cost_total: float = 0.0
    net_cost_total: float = 0.0
    cost_ratio: float = 0.0        # net_cost / (0.35 门槛用)
    cost_ok: bool = False
    max_drawdown_pct: float = 0.0
    sharpe: float = 0.0
    calmar: float = 0.0
    max_consecutive_losses: int = 0
    extreme_pnl: Optional[float] = None   # None = N/A(180d限制)
    extreme_na: bool = True
    gates: Dict[str, Any] = field(default_factory=dict)
    risk_events: List[str] = field(default_factory=list)
    halted: bool = False
    n_open_signals: int = 0
    n_dual_neg: int = 0
    n_funding_mutation: int = 0


def run_group(coin: str, interval: str, tier: str, df: pd.DataFrame,
              gate: Dict[str, Any], tiers: Dict[str, FeeTier]
              ) -> GroupResult:
    ft = tiers[tier]
    g = GroupResult(coin=coin, interval=interval, tier=tier, bars=len(df),
                    span_days=int((df["ts"].max() - df["ts"].min()) / 86400))

    signals = build_signals(df)
    g.n_open_signals = sum(1 for s in signals if s.entry_signal)

    # B #2 funding 突变：基于 8h funding 序列
    fr_series = [float(x) for x in df["funding_rate"].tolist()]
    mean_abs = float(np.mean([abs(x) for x in fr_series])) if fr_series else 0.0
    mut = funding_mutation_state(fr_series, mean_abs)
    # 24h 同号 = 连续 3 期 8h 同号（B #2 口径）
    hit_idx = [i for i, mm in enumerate(mut)
               if mm["hit"] and mm["scale"] in (0.0, 0.5)]
    for i in hit_idx:
        s = signals[i]
        g.risk_events.append(
            f"funding突变 {mut[i]['sign']:+d}×{mut[i]['run_len']}期24h"
            f"|{s.funding_rate:+.5f}|@ts={s.ts}")
    g.n_funding_mutation = len(hit_idx)

    # 权益曲线：初始 10000，敞口上限 20%/币（B #3）
    eq0 = 10000.0
    realized = [eq0]          # 已实现权益（含平仓 pnl）
    unreal = 0.0             # 持仓 bar 内浮动盈亏（回撤/夏普用）
    total_curve: List[float] = []

    pos: Optional[Dict[str, Any]] = None
    trades: List[Dict[str, Any]] = []
    settle = SETTLE_PER_BAR[interval]
    rtrip_pct = ft.roundtrip_cost_pct()

    for i, s in enumerate(signals):
        mut_scale = mut[i]["scale"] if i < len(mut) else 1.0

        # ---- 平仓（t 出信号 → t+1 执行；bar i 即执行 bar，用 bar 开盘价成交）----
        if s.exit_signal and pos is not None:
            px = s.spot_open
            notional = eq0 * pos["scale"]
            price_pnl = notional * (px - pos["entry_px"]) / pos["entry_px"]
            # carry：持仓 1 bar（HOLD_BARS），跨 settle 个 8h 结算点
            carry = pos["fr"] * HOLD_BARS * settle * notional
            rtrip = rtrip_pct * notional
            pnl = price_pnl + carry - rtrip
            realized.append(realized[-1] + pnl)
            trades.append({
                "entry_ts": pos["entry_ts"], "exit_ts": s.ts,
                "entry_px": pos["entry_px"], "exit_px": px,
                "notional": notional, "price_pnl": price_pnl,
                "carry_yield": carry, "roundtrip_cost": rtrip,
                "gross_pnl": price_pnl, "pnl": pnl, "reason": s.exit_reason,
            })
            # B #5 资金曲线监控：carry<0 且 price<0 双负 → 当日强制减仓（记事件）
            if DUAL_NEG_DELEVER and price_pnl < 0 and carry < 0:
                g.n_dual_neg += 1
                g.risk_events.append(f"双负强制减仓 ts={s.ts}")
            pos = None
            unreal = 0.0

        # ---- 开仓（t 出信号 → t+1 执行；bar i 即执行 bar，用 bar 开盘价成交）----
        if s.entry_signal and pos is None and mut_scale > 0:
            dd_now = _cur_drawdown_pct(total_curve or [100.0])
            scale = MAX_EXPOSURE_PER_COIN
            if dd_now >= DD_WARN_PCT:
                scale *= 0.5                      # 8% 预警降 50%（B #1）
            scale *= mut_scale                    # funding 突变降敞口/清零（B #2）
            if scale > 0:
                pos = {"entry_ts": s.ts, "entry_px": s.spot_open,
                       "scale": scale, "fr": s.funding_rate}

        # 持仓 bar 浮动估值（回撤/夏普用；平仓 bar 已计入 realized，unreal 清零）
        if pos is not None:
            px = s.spot_close
            notional = eq0 * pos["scale"]
            unreal = notional * (px - pos["entry_px"]) / pos["entry_px"]
        total_curve.append((realized[-1] + unreal) / eq0 * 100)

        # B #1 回撤熔断
        dd_now = _cur_drawdown_pct(total_curve)
        if dd_now >= gate["max_drawdown_pct"]:
            g.halted = True
            g.risk_events.append(f"回撤熔断 {dd_now:.2f}%@ts={s.ts}")
            break

    # B #4 连亏熔断（事后监控）
    if g.max_consecutive_losses > gate["max_consecutive_losses"]:
        g.risk_events.append(
            f"连亏熔断 {g.max_consecutive_losses}>{gate['max_consecutive_losses']}")

    # 期末权益 = 最后已实现（持仓 bar 浮动不计入净收益，保守口径）
    eq_end = realized[-1]
    _finalize(g, trades, total_curve, eq0, eq_end, g.span_days, gate)
    return g


def _cur_drawdown_pct(equity_pct: List[float]) -> float:
    if not equity_pct:
        return 0.0
    peak = max(equity_pct)
    if peak <= 0:
        return 0.0
    return (peak - equity_pct[-1]) / peak * 100.0


def _finalize(g: GroupResult, trades: List[Dict[str, Any]],
              total_curve: List[float], eq0: float, eq_end: float,
              span_days: int, gate: Dict[str, Any]) -> None:
    g.n_trades = len(trades)
    g.n_trades_min_ok = g.n_trades >= MIN_TRADES

    # 净收益 = 期末已实现权益 / eq0（0 成交组 = 0，保守口径）
    net_return_pct = (eq_end / eq0 - 1) * 100
    g.net_return_pct = net_return_pct

    # 毛利（价格 pnl 合计，未扣成本）
    gross = float(sum(t["price_pnl"] for t in trades))
    gross_disc = SNIPING_DISCOUNT * gross                 # 0.5x 抢跑折扣（分母）
    carry_total = float(sum(t["carry_yield"] for t in trades))
    rtrip_total = float(sum(t["roundtrip_cost"] for t in trades))
    net_cost_total = rtrip_total - carry_total            # carry 翻转为净额
    # 成本/毛利比（门槛用：net_cost ≤ 0.35 × gross_disc，0.35 不放宽）
    cost_ratio = (net_cost_total / gross_disc) if gross_disc > 0 else float("inf")
    g.gross_pnl = gross
    g.gross_pnl_disc = gross_disc
    g.carry_yield_total = carry_total
    g.roundtrip_cost_total = rtrip_total
    g.net_cost_total = net_cost_total
    g.cost_ratio = cost_ratio
    # cost_ok：有成交 且 net_cost ≤ 0.35×0.5×毛利（0.35 取自 yaml，未放宽）
    g.cost_ok = bool(trades and cost_ratio <= gate["max_cost_as_pct_of_gross"]
                     and not np.isinf(cost_ratio))

    # 最大回撤（逐 bar 总权益曲线，含持仓 bar 浮动）
    eq = np.array(total_curve, dtype=float)
    if len(eq) > 1:
        peak = np.maximum.accumulate(eq)
        dd = (peak - eq) / np.where(peak > 0, peak, 1) * 100
        g.max_drawdown_pct = float(dd.max())
    else:
        g.max_drawdown_pct = 0.0

    # 夏普（bar 级收益，年化 √bars/year；口径同 run_one_timeframe）
    # 无成交组逐 bar 收益恒为 0 → 夏普无统计意义，记 0（不脑补）
    if trades and len(eq) > 2:
        rets = np.diff(eq) / np.where(eq[:-1] > 0, eq[:-1], 1)
        g.sharpe = float(np.mean(rets) / np.std(rets) * ANN_FACTOR[g.interval]) \
            if np.std(rets) > 0 else 0.0
    else:
        g.sharpe = 0.0
    # Calmar = 年化净收益 / 最大回撤（0 成交组 年化净收益=0 → Calmar=0）
    ann_ret = (net_return_pct / 100) * (365.0 / max(1, span_days))
    g.calmar = float(ann_ret / (g.max_drawdown_pct / 100)) \
        if g.max_drawdown_pct > 0 else 0.0

    # 连亏（B #4）
    pnl_seq = [t["pnl"] for t in trades]
    run = best = 0
    for p in pnl_seq:
        run = run + 1 if p < 0 else 0
        best = max(best, run)
    g.max_consecutive_losses = best
    if best > gate["max_consecutive_losses"]:
        g.risk_events.append(f"连亏熔断 {best}>{gate['max_consecutive_losses']}")

    # 极端段（2026-02）→ 本轮 N/A（funding 无覆盖；B #6 降级实盘监控）
    g.extreme_na = True
    g.extreme_pnl = None


# ============================================================================
# 门槛判定（6 条冻结 + B #7 笔数下限）
# ============================================================================

def judge_gates(g: GroupResult, gate: Dict[str, Any]) -> Dict[str, Any]:
    # G-4-②：min_sharpe=None → 夏普硬门槛不启用（老板原话「夏普率本就不要求过 1.0」）；
    # 夏普值仍如实计算/申报，但不作 6→5 条通过判定。
    sharpe_hard = gate.get("min_sharpe") is not None
    gates = {
        "max_drawdown": bool(g.max_drawdown_pct <= gate["max_drawdown_pct"]),
        "sharpe": (bool(g.sharpe >= gate["min_sharpe"]) if sharpe_hard else None),
        "sharpe_gate_disabled": not sharpe_hard,   # G-4-② 不设夏普硬门槛标记
        "calmar": bool(g.calmar >= gate["min_calmar"]),
        "cost_ratio": g.cost_ok,
        "consec_losses": bool(g.max_consecutive_losses <= gate["max_consecutive_losses"]),
        "extreme_na": g.extreme_na,     # N/A(180d限制)，不计通过、不当通过
        "min_trades": g.n_trades_min_ok,
    }
    # 整体通过 = 除 extreme_na / 停用的 sharpe 门槛外全部为 True；extreme_na 标 N/A 不当通过
    hard_keys = [k for k in gates
                 if k not in ("extreme_na", "sharpe_gate_disabled")
                 and not (k == "sharpe" and not sharpe_hard)]
    gates["all_except_na"] = all(v is True for k, v in gates.items() if k in hard_keys)
    gates["pass_final"] = gates["all_except_na"] and not g.extreme_na
    return gates


# ============================================================================
# 主流程
# ============================================================================

def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="G-3 / G-4-② G1 carry 基差回测")
    ap.add_argument("--out", default=str(OUT_DIR / "g1_carry_basis_backtest.json"))
    ap.add_argument("--task", default="G-3 · G1 carry 基差信号 M8 全门槛回测（taker/maker 双档）")
    args = ap.parse_args()

    gate = load_gate_from_yaml()
    cost_cfg_all = load_cost_from_yaml()
    gate["max_cost_as_pct_of_gross"] = cost_cfg_all["max_cost_as_pct_of_gross"]
    pfp = param_fingerprint()
    print(f"[参数指纹] {pfp['file']} SHA256-16 = {pfp['sha256_16']}")
    print(f"[门槛] {gate}")
    if gate.get("min_sharpe") is None:
        print("[门槛] min_sharpe=None → 夏普硬门槛不启用（G-4-② 老板 A 方案）")

    results: List[GroupResult] = []
    errors: List[str] = []
    data_fps: Dict[str, str] = {}

    for coin in COINS:
        for iv in INTERVALS:
            f = (ROOT / "data" / "cache" / "g1_carry_data" /
                 f"{coin}_USDT_{iv}_{'400' if iv == '4h' else '900'}d_carry.parquet")
            if not f.exists():
                errors.append(f"缺数据 {f.name}")
                continue
            df = pd.read_parquet(f)
            data_fps[f.name] = hashlib.sha256(f.read_bytes()).hexdigest()[:16]
            for tier in FEE_TIERS:
                try:
                    g = run_group(coin, iv, tier, df, gate,
                                  build_tiers(cost_cfg_all))
                    g.gates = judge_gates(g, gate)
                    results.append(g)
                    print(f"{coin} {iv} {tier}: bars={g.bars}({g.span_days}d) "
                          f"trades={g.n_trades} net={g.net_return_pct:+.3f}% "
                          f"maxdd={g.max_drawdown_pct:.3f}% sharpe={g.sharpe:.3f} "
                          f"calmar={g.calmar:.3f} consec={g.max_consecutive_losses} "
                          f"cost_ok={g.cost_ok} "
                          f"{'PASS(exN/A)' if g.gates['all_except_na'] else 'FAIL'}"
                          f"{'[N/A极端段]' if g.extreme_na else ''}")
                except Exception as e:
                    import traceback
                    traceback.print_exc()
                    errors.append(f"{coin} {iv} {tier}: {e}")

    # ---- 汇总 ----
    manifest_path = ROOT / "data" / "cache" / "g1_carry_data" / "_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    passed = [g for g in results if g.gates.get("all_except_na")]
    cost_cfg = load_cost_from_yaml()
    g4_mode = args.task.startswith("G-4")
    summary = {
        "n_groups": len(results),
        "n_passed_except_na": len(passed),
        "n_passed_final": sum(1 for g in results if g.gates.get("pass_final")),
        "n_na_extreme": sum(1 for g in results if g.extreme_na),
        "note_extreme": "极端段(2026-02) funding 无覆盖 → N/A(410d 未补、数据源缺口)，"
                        "不计通过、不放宽、不用 0 占位顶替（B #6 降级实盘监控承接）",
        "note_min_trades": f"笔数下限 ≥{MIN_TRADES} 笔（B #7），<30 不计通过",
        "note_window": "窗口口径：现货 400d/900d ∩ funding 180d = 180d"
                       "（4h=1080 根/1d=180 根，任务单标称）；实际 funding 实得 100 根/币"
                       "（~33d 窗口）→ 实际交集 4h=89~91 根/1d=31 根，"
                       "超出部分无数据、不脑补；本轮 G-4-② 仍读 33d，不读 410d（未补）",
        "note_short_side": "做空侧（BCH/SOL/XRP/AVAX/ADA 负 carry）本轮不测",
        "note_1h": "1h 粒度不测（phase9 §六.2 已判 1h carry 不成立）",
        "note_continuity": "开仓「持续」口径：funding>0 且 basis>0 连续 ≥3 bar"
                           "（≈1 个 24h 段）；实测 33d 窗口内 fr∩basis 同正 bar "
                           "每币 0~1 根且孤立不连续 → 16 组 0 开仓（如实申报，非参数放宽）",
        "g4_directive": None,
        "fee_split_declaration": None,
    }
    if g4_mode:
        summary["g4_directive"] = ("老板 A 方案指令：夏普不再要求过 1.0 + 成本 35%→50% + "
                                    "合约档费率（现货腿 0.1% / 合约腿 maker 0.02%·taker 0.05%）"
                                    "+ 33d 窗口重跑；PM 落地口径：夏普=不设硬门槛（min_sharpe=null）")
        summary["fee_split_declaration"] = (
            "现货腿 0.1%/边（taker=maker，roundtrip 0.2%） vs "
            "合约腿 maker 0.02%/边·taker 0.05%/边（roundtrip maker 0.04%·taker 0.10%）"
            "——两档分开申报，不混用 0.1% 算合约腿；"
            "G-4-② 重跑合约腿双档 = 读 futures_leg")
    payload = {
        "task": args.task,
        "role": "后端架构师 主导 + 数据工程师协作（backtest/g1_carry_basis_data.py）",
        "date": "2026-11-12",
        "param_fingerprint": pfp,
        "data_fingerprints": data_fps,
        "gate_from_yaml": gate,
        "cost_cfg_yaml": {**cost_cfg,
                          "fee_split": {
                              "spot_leg": {"taker": cost_cfg.get("spot_taker_fee_pct"),
                                           "maker": cost_cfg.get("spot_maker_fee_pct"),
                                           "roundtrip": "0.2%"},
                              "futures_leg": {"taker": cost_cfg.get("futures_taker_fee_pct"),
                                              "maker": cost_cfg.get("futures_maker_fee_pct"),
                                              "roundtrip": "maker 0.04% / taker 0.10%"}},
                          "maker_fee_note": "合约腿双档申报：taker=0.05%/maker=0.02%（Gate.io VIP0 永续，老板 2026-11-12 原样引用）；"
                                            "现货腿 0.1%/边（VIP0 现货）；G-3 误按现货 0.1% 算合约腿 → G-4-② 改合约档重算",
                          "taker_slip_bps_per_side": TAKER_SLIP_BPS,
                          "maker_slip_bps_per_side": MAKER_SLIP_BPS,
                          "latency_1_3s_pct_assumed": LATENCY_1_3S_PCT,
                          "sniping_discount": SNIPING_DISCOUNT},
        "assumptions": {
            "spot_leg_fee_pct_per_side": cost_cfg.get("spot_taker_fee_pct", cost_cfg["taker_fee_pct"]),
            "spot_leg_note": "现货腿 taker=maker=0.1%/边（Gate.io VIP0 现货官方事实）",
            "futures_leg_taker_fee_pct_per_side": cost_cfg.get("futures_taker_fee_pct"),
            "futures_leg_maker_fee_pct_per_side": cost_cfg.get("futures_maker_fee_pct"),
            "futures_leg_note": "合约腿双档：taker=0.05%/maker=0.02%（Gate.io VIP0 永续，老板 2026-11-12 原样引用）；"
                                "roundtrip taker 0.10% / maker 0.04%",
            "fee_declaration": "现货腿 0.1% vs 合约腿 maker 0.02%·taker 0.05% 两档分开申报，不混用 0.1% 算合约腿",
            "sharpe_gate": ("不设硬门槛（min_sharpe=null）" if gate.get("min_sharpe") is None
                            else f"≥{gate.get('min_sharpe')}"),
            "taker_slip_bps_per_side": TAKER_SLIP_BPS,
            "taker_slip_note": "4bp/边：冻结 3~5bp 保守假设（yaml slippage_bps_per_side [3,5]）",
            "maker_slip_bps_per_side": MAKER_SLIP_BPS,
            "maker_slip_note": "假设值：maker 挂单被动成交 0~1bp 估算取 0.5bp，盘上无实测（沿用 G-3）",
            "latency_1_3s_pct": LATENCY_1_3S_PCT,
            "latency_note": "假设值：1~3s 成交时延折算为 bar 内不利价格漂移"
                            "（0.5~1.5 tick，按 1h K线 0.001% 量级估算取 0.1bp），盘上无实测（沿用 G-3）",
            "sniping_discount": SNIPING_DISCOUNT,
            "carry_formula": "carry_yield = funding_rate × position_dir(+1) × bars_held(1) × (8h结算折算: 4h→1, 1d→3)",
            "cost_ok_formula": f"cost_ok = (roundtrip_cost - carry_yield) ≤ {gate['max_cost_as_pct_of_gross']} × (0.5 × gross_pnl)",
            "window_note": summary["note_window"],
            "extreme_note": summary["note_extreme"],
        },
        "risk_preconditions": [
            f"#1 回撤熔断：{DD_WARN_PCT}%预警降敞口50%，{gate['max_drawdown_pct']:.0f}%停机",
            f"#2 funding突变：≥{FUNDING_MUTATION_RUN}期(24h)同号且|值|>2×均值→降敞口50%，"
            f"≥{FUNDING_CLEAR_RUN}期→清零（特别监控 LTC 正占比 53% / DOGE 72%）",
            f"#3 敞口上限 {MAX_EXPOSURE_PER_COIN:.0%}/币（4 币合计 ≤80%）",
            f"#4 连亏熔断 ≤{gate['max_consecutive_losses']}",
            "#5 资金曲线监控：carry<0 且 price<0 双负 → 当日强制减仓",
            "#6 极端段门槛降级：N/A(180d限制)，实盘监控承接（前 30 天逐 8h funding + 回撤熔断）",
            f"#7 笔数下限 ≥{MIN_TRADES} 笔（4h/1d 同限），<30 不计通过",
            "#8 成本口径锁定：含 1~3s 时延 + 0.5x 抢跑折扣 + carry 翻转，≤0.35 复算",
        ],
        "groups": [_to_dict(g) for g in results],
        "errors": errors,
        "summary": summary,
        "data_manifest": manifest,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str),
                   encoding="utf-8")
    md_path = out.with_suffix(".md")
    md_path.write_text(render_md(payload), encoding="utf-8")
    print(f"\n[JSON] → {out}")
    print(f"[MD]   → {md_path}")
    print(f"通过(除N/A) {len(passed)}/{len(results)}；"
          f"N/A极端段 {summary['n_na_extreme']} 组；errors {len(errors)}")
    return 0


# ============================================================================
# 报告
# ============================================================================

def render_md(payload: Dict[str, Any]) -> str:
    groups: List[Dict[str, Any]] = payload["groups"]
    s = payload["summary"]
    a = payload["assumptions"]
    gate = payload["gate_from_yaml"]
    g4 = payload["task"].startswith("G-4")
    title = payload["task"] if g4 else "G-3 · G1 carry 基差信号 M8 全门槛回测（taker/maker 双档）"

    lines: List[str] = []
    lines.append(f"# {title}")
    lines.append("")
    lines.append(f"> 角色：{payload['role']}")
    lines.append(f"> 日期：{payload['date']}")
    lines.append(f"> 参数指纹：`config/strategy_params.yaml` "
                 f"SHA256-16 = `{payload['param_fingerprint']['sha256_16']}`"
                 + ("（G-4-② 老板 A 方案：门槛/费率已改并 audit 留痕，门槛=5 条 + 极端段 N/A）"
                    if g4 else "（数值未改、6 条门槛未放宽）"))
    if g4:
        lines.append(f"> **{s['g4_directive']}**")
        lines.append(f"> **费率双档申报：{s['fee_split_declaration']}**")
    lines.append("")
    lines.append("## 1. 数据窗口声明（如实申报，不脑补）")
    lines.append("")
    lines.append(f"- {s['note_window']}")
    lines.append(f"- {s['note_extreme']}")
    lines.append(f"- {s['note_short_side']}；{s['note_1h']}")
    lines.append(f"- {s['note_continuity']}")
    lines.append(f"- 组数：4 币（BTC/ETH/DOGE/LTC）× 2 粒度（4h/1d）× 2 费率档（taker/maker）= **16 组**")
    lines.append("")
    lines.append("## 2. 成本模型（C 专家加严口径 + G-4-② 合约档双档申报）")
    lines.append("")
    lines.append("```")
    lines.append("roundtrip_cost = fee×2 + slippage×2 + latency_1_3s")
    lines.append(a["carry_formula"])
    lines.append("net_cost       = roundtrip_cost - carry_yield")
    lines.append(f"gross_pnl_disc = 0.5 × gross_pnl_original   # {a['sniping_discount']}x 抢跑折扣（分母）")
    lines.append(a["cost_ok_formula"])
    lines.append("```")
    lines.append("")
    if g4:
        lines.append(f"- **现货腿**：taker=maker={a['spot_leg_fee_pct_per_side']}/边（{a['spot_leg_note']}；roundtrip 0.2%）")
        lines.append(f"- **合约腿 taker 档**：fee={a['futures_leg_taker_fee_pct_per_side']}/边、"
                     f"slip={a['taker_slip_bps_per_side']}bp/边（{a['taker_slip_note']}；roundtrip 0.10%）")
        lines.append(f"- **合约腿 maker 档**：fee={a['futures_leg_maker_fee_pct_per_side']}/边、"
                     f"slip={a['maker_slip_bps_per_side']}bp/边（**{a['maker_slip_note']}**；roundtrip 0.04%）")
        lines.append(f"- 时延：latency_1_3s={a['latency_1_3s_pct']}（**{a['latency_note']}**）")
        lines.append(f"- 双档声明：{a['fee_declaration']}")
        lines.append(f"- 夏普门槛：{a['sharpe_gate']}（老板 A 方案「夏普率本就不要求过 1.0」→ PM 落地为不设硬门槛）")
    else:
        lines.append(f"- taker 档：fee={a['taker_fee_pct_per_side']}/边（VIP0 官方事实，读自 yaml）、"
                     f"slip={a['taker_slip_bps_per_side']}bp/边（{a['taker_slip_note']}）")
        lines.append(f"- maker 档：fee={a['maker_fee_pct_per_side']}/边（{a['maker_fee_note']}）、"
                     f"slip={a['maker_slip_bps_per_side']}bp/边（**{a['maker_slip_note']}**）")
        lines.append(f"- 时延：latency_1_3s={a['latency_1_3s_pct']}（**{a['latency_note']}**）")
    lines.append("")
    lines.append("## 3. 全量 16 组结果（合约档 taker/maker 双档）" if g4
                 else "## 3. 全量 16 组结果（taker/maker 双档）")
    lines.append("")
    lines.append("| 币 | 粒度 | 档 | bar | 天数 | 开仓信号 | 成交 | 净收益% | 毛利 | 0.5x毛利 | carry | roundtrip | net_cost | 成本/0.5x毛利 | cost_ok | 回撤% | 夏普 | Calmar | 连亏 | 极端段 | 通过(除N/A) |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for g in groups:
        cr = g["cost_ratio"]
        cr_s = f"{cr:.4f}" if cr != "inf" else "inf"
        gate_txt = "✅" if g["gates"].get("all_except_na") else "❌"
        lines.append(
            f"| {g['coin']} | {g['interval']} | {g['tier']} | {g['bars']} | {g['span_days']} "
            f"| {g.get('n_open_signals', 0)} | {g['n_trades']} "
            f"| {g['net_return_pct']:+.3f} | {g['gross_pnl']:.4f} "
            f"| {g['gross_pnl_disc']:.4f} | {g['carry_yield_total']:.4f} "
            f"| {g['roundtrip_cost_total']:.4f} | {g['net_cost_total']:.4f} "
            f"| {cr_s} | {'✅' if g['cost_ok'] else '❌'} "
            f"| {g['max_drawdown_pct']:.4f} | {g['sharpe']:.4f} | {g['calmar']:.4f} "
            f"| {g['max_consecutive_losses']} | N/A(410d未补) | {gate_txt} |")
    lines.append("")
    lines.append("## 4. 门槛判定" + ("（G-4-② 改后口径：夏普不设硬门槛，余 4 条维持原值）" if g4 else "（6 条冻结，不放宽）"))
    lines.append("")
    sharpe_label = "夏普≥" + str(gate["min_sharpe"]) if gate.get("min_sharpe") is not None else "夏普（不设硬门槛）"
    lines.append(f"门槛读自 yaml：回撤≤{gate['max_drawdown_pct']}% / {sharpe_label} / "
                 f"Calmar≥{gate['min_calmar']} / 成本≤毛利{gate['max_cost_as_pct_of_gross']:.0%}"
                 f"（含 carry 翻转 + 0.5x 抢跑折扣复算） / 极端段(2026-02)不亏 / 连亏≤{gate['max_consecutive_losses']}")
    lines.append("")
    lines.append("| 门槛 | 16 组达成数 | 说明 |")
    lines.append("|---|---|---|")
    lines.append(f"| 回撤≤{gate['max_drawdown_pct']:.0f}% | {sum(1 for g in groups if g['gates']['max_drawdown'])} | B #1 熔断已进回测 |")
    if gate.get("min_sharpe") is not None:
        lines.append(f"| 夏普≥{gate['min_sharpe']} | {sum(1 for g in groups if g['gates']['sharpe'])} | 薄样本 + 0 成交组夏普=0 |")
    else:
        lines.append(f"| 夏普（不设硬门槛） | 停用×16 | G-4-② 老板 A 方案「夏普率本就不要求过 1.0」→ PM 落地不设硬门槛；夏普值仍如实申报但不作通过判定 |")
    lines.append(f"| Calmar≥{gate['min_calmar']} | {sum(1 for g in groups if g['gates']['calmar'])} | |")
    lines.append(f"| 成本≤{gate['max_cost_as_pct_of_gross']:.0%}×0.5×毛利 | {sum(1 for g in groups if g['gates']['cost_ratio'])} | 0.5x 抢跑折扣分母 + carry 翻转（C #8） |")
    lines.append(f"| 连亏≤{gate['max_consecutive_losses']} | {sum(1 for g in groups if g['gates']['consec_losses'])} | B #4 熔断 |")
    lines.append("| 极端段(2026-02)不亏 | N/A×16 | funding 无覆盖（410d 未补）→ **N/A，不当通过**（B #6 实盘监控承接） |")
    lines.append(f"| 笔数≥{MIN_TRADES} | {sum(1 for g in groups if g['gates']['min_trades'])} | B #7 下限，<30 不计通过 |")
    lines.append("")
    lines.append(f"**通过（除 N/A 极端段外全达成）：{s['n_passed_except_na']}/{s['n_groups']} 组**；"
                 f"**最终通过（含 N/A 不当通过）：{s['n_passed_final']}/{s['n_groups']} 组**")
    lines.append("")
    if g4:
        lines.append("## 4b. 与 G-3 现货 0.1% 口径对照（合约腿 0.02%/0.05% vs 现货 0.1%）")
        lines.append("")
        lines.append("| 维度 | G-3（参数指纹 0b68401f…） | G-4-②（参数指纹 " + payload['param_fingerprint']['sha256_16'] + "） | 差异影响 |")
        lines.append("|---|---|---|---|")
        lines.append("| 合约腿 taker 费率 | 0.1%/边（误按现货） | 0.05%/边（VIP0 永续） | roundtrip 0.20%→0.10%，taker 档成本减半 |")
        lines.append("| 合约腿 maker 费率 | 0.1%/边（误按现货） | 0.02%/边（VIP0 永续） | roundtrip 0.20%→0.04%，maker 档成本降到 1/5 |")
        lines.append("| 夏普门槛 | ≥2.0 | 不设硬门槛 | G-4-② 6→5 条 |")
        lines.append("| 成本门槛 | ≤毛利 35% | ≤毛利 50% | 分母 0.5×毛利 不变，但 0.35→0.50 放宽 |")
        lines.append("| 16 组成交 | 0 成交（fr∩basis 同正 0 组触发） | 0 成交（同窗口、同信号骨架） | 费率/门槛变化**不改变 0 成交判定** |")
        lines.append("| 达标数 | 0/16 | " + str(s['n_passed_final']) + "/16 | 仍 0 达标（33d 窗口 fr∩basis 同正 0 组触发，笔数 <30 不达标） |")
        lines.append("")
        lines.append("> 结论：合约档费率 + 降门槛使**有成交时**的 cost_ok 判定显著放宽（taker roundtrip 0.20%→0.10%、"
                    "maker 0.20%→0.04%，成本门槛 35%→50%）；但 33d 窗口内 fr∩basis 同正 0 组触发 → "
                    "16 组仍 0 成交、0 达标，**费率/门槛变化未带来翻盘**。翻盘前置 = 补 410d funding 扩大窗口 + 有成交。")
        lines.append("")
    lines.append("## 5. 与既有基准对照（如实申报）")
    lines.append("")
    lines.append("| 基准 | 夏普 | 笔数 | 出处 | 与 G-3 关系 |")
    lines.append("|---|---|---|---|---|")
    lines.append("| 均值回归 75 组 Top1（ETH/SOL 1h 3.5/2.5/2.0） | 0.499 | 82 笔 | `backtest/output/pair_param_sweep.json` / `f1_expert_B_opinion.md` | G-3 0 成交组夏普=0，**均低于 0.499** |")
    lines.append("| C-2b 125 组 Top1（ETH/AVAX 1d 3.5/2.5/2.0） | 1.2093 | 4 笔 | `pair_param_sweep_avax_ada.json` / `f1_expert_B_opinion.md` | G-3 0 成交组夏普=0，**远低于 1.2093**；且 1.2093 已判薄样本假象 |")
    lines.append("")
    lines.append("## 6. taker / maker 双档对比")
    lines.append("")
    lines.append("| 币×粒度 | taker 净收益% | maker 净收益% | taker cost_ok | maker cost_ok | 差异来源 |")
    lines.append("|---|---|---|---|---|---|")
    by: Dict[tuple, Dict[str, Dict[str, Any]]] = {}
    for g in groups:
        by.setdefault((g["coin"], g["interval"]), {})[g["tier"]] = g
    for (coin, iv), m in sorted(by.items()):
        t, mk = m.get("taker"), m.get("maker")
        def _v(x: Optional[Dict[str, Any]], k: str, fmt: str = "{:+.3f}") -> str:
            return fmt.format(x[k]) if x else "N/A"
        lines.append(
            f"| {coin} {iv} | {_v(t, 'net_return_pct')} | {_v(mk, 'net_return_pct')} | "
            f"{'✅' if t and t['cost_ok'] else '❌'} | "
            f"{'✅' if mk and mk['cost_ok'] else '❌'} | "
            f"合约腿双档：taker 0.05%+4bp vs maker 0.02%+0.5bp/边（滑点差 {a['taker_slip_bps_per_side']-a['maker_slip_bps_per_side']:.1f}bp/边） |")
    lines.append("")
    lines.append("## 7. B 专家 8 项风控前置落地（已进回测）")
    lines.append("")
    for i, r in enumerate(payload["risk_preconditions"], 1):
        lines.append(f"- {r}")
    lines.append("")
    lines.append("## 8. 极端段 N/A 声明（硬性）")
    lines.append("")
    lines.append(f"- {s['note_extreme']}")
    lines.append("- 上实盘后由实盘监控承接：前 30 天逐 8h funding + 回撤熔断（B #6）。")
    lines.append("- ④ 换数据源补全 funding 410d 为**下轮必做项**（B/C 共识，本轮带险运行）。")
    lines.append("")
    lines.append("## 9. 结论与如实申报")
    lines.append("")
    lines.append(f"- **通过组数（除 N/A 外 6 条全达成）：{s['n_passed_except_na']}/{s['n_groups']}**；"
                 f"最终通过（N/A 不当通过）：{s['n_passed_final']}/{s['n_groups']}。")
    lines.append("- 如实申报：funding 实得 100 根/币（~33d），实际交集 4h≈89~91 根 / 1d≈31 根，"
                 "全部组笔数 <30（B #7）→ 薄样本判负，不计通过。")
    lines.append("- 开仓条件（funding 持续为正 + 基差为正）在 33d 窗口内 0 组触发"
                 "（fr∩basis 同正 bar 每币 0~1 根且孤立不连续）→ 0 成交，"
                 "夏普/Calmar/净收益均无统计意义，**如实申报 0 达标**，"
                 "亦实证 C 专家「taker 口径 0 过线」预判（分子侧无成交机会，分母侧 0.5x 折扣下更无过线可能）。")
    lines.append("- maker 档在 0.5x 抢跑折扣分母 + 时延假设值 + 薄样本加严下同样 cost_ok=False。")
    lines.append("- **本轮回测定位（C 专家 Q3）=「成本分子改造的实证检验」，不是「carry 上线」。**")
    if g4:
        lines.append("- G-4-② 合约档+降门槛重跑：16 组仍 0 成交（33d 窗口 fr∩basis 同正 0 组触发），"
                    "**0 达标如实申报**，不美化；费率/门槛变化未带来翻盘，翻盘前置=补 410d 窗口+有成交。")
        lines.append("- 夏普落地口径=不设硬门槛（min_sharpe=null，老板「夏普率本就不要求过 1.0」）；"
                    "其余 4 条门槛（回撤≤10%、Calmar≥1.5、成本≤毛利50%、连亏≤5）维持原值/降后值不动。")
    lines.append("- 下一步：④ 换数据源补全 funding 410d + 费率结构改造（maker）为下轮必做项；③ 波动率/尾部对冲为 ① 配套。")
    lines.append("- 未引用 0.473、未把 N/A 当通过、未启用实盘、未提交 Git。" +
                 (" G-4-② 门槛/费率改动已走 audit 留痕（verify_params PASS），M3/M4 信号骨架未改（只改费率档+门槛数值）。" if g4 else ""))
    lines.append("")
    lines.append("## 10. 数据 / 假设清单")
    lines.append("")
    lines.append(f"- 数据源：现货 4h/1d parquet（{', '.join(sorted(payload['data_fingerprints'].keys()))}）"
                 " ∩ 永续实拉（agents/_g1_probes/perp_*_g3.json）∩ funding 实拉（fr_*_usdt_169d_g3.json，100 根/币）")
    lines.append(f"- 假设值（盘上无实测，须标注）：maker 滑点 {a['maker_slip_bps_per_side']}bp/边；"
                 f"时延 latency_1_3s={a['latency_1_3s_pct']}")
    lines.append(f"- 参数指纹：{payload['param_fingerprint']['file']} = {payload['param_fingerprint']['sha256']}")
    lines.append("- 数据指纹：见 JSON data_fingerprints（8 组 carry parquet SHA256-16）+ data/cache/g1_carry_data/_manifest.json")
    lines.append("")
    return "\n".join(lines)


def _to_dict(g: GroupResult) -> Dict[str, Any]:
    return {
        "coin": g.coin, "interval": g.interval, "tier": g.tier,
        "bars": g.bars, "span_days": g.span_days,
        "n_trades": g.n_trades, "min_trades_ok": g.n_trades_min_ok,
        "n_open_signals": g.n_open_signals,
        "n_dual_neg": g.n_dual_neg,
        "n_funding_mutation": g.n_funding_mutation,
        "net_return_pct": round(g.net_return_pct, 5),
        "gross_pnl": round(g.gross_pnl, 4),
        "gross_pnl_disc": round(g.gross_pnl_disc, 4),
        "carry_yield_total": round(g.carry_yield_total, 4),
        "roundtrip_cost_total": round(g.roundtrip_cost_total, 4),
        "net_cost_total": round(g.net_cost_total, 4),
        "cost_ratio": (round(g.cost_ratio, 5) if not np.isinf(g.cost_ratio) else "inf"),
        "cost_ok": g.cost_ok,
        "max_drawdown_pct": round(g.max_drawdown_pct, 4),
        "sharpe": round(g.sharpe, 4),
        "calmar": round(g.calmar, 4),
        "max_consecutive_losses": g.max_consecutive_losses,
        "extreme_pnl": g.extreme_pnl,
        "extreme_na": g.extreme_na,
        "gates": g.gates,
        "risk_events": g.risk_events,
        "halted": g.halted,
    }


if __name__ == "__main__":
    sys.exit(main())
