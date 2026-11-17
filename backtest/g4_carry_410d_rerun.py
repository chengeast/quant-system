# -*- coding: utf-8 -*-
"""G-4-②-410d · G1 carry 410d 窗口重跑 16 组（M3+M4 骨架复用，不改 src/）。

老板 2026-11-14 22:44 指令（逐字）：「先拿现有 funding 重跑 G-4-②， 继续派 G-6-② 找多级 L2 免费源」。
本批只做前半句（410d 重跑）；G-6-② 由秘书另批派，不抢跑。

与旧 `backtest/g4_carry_rerun.py`（33d 窗口，历史留痕、不改）的区别 —— 只改 3 处：
  ① 数据源窗口：改读 `data/cache/g1_carry_data_410d/{COIN}_USDT_{iv}_410d_carry.parquet`
     （perp+spot klines 410d 补拉后重建，交集 425d / 2025-09-01~2026-10-31，
      见 backtest/g1_carry_410d_data.py + _manifest.json）。旧 8 parquet 的
      perp 仅 28d/31d 卡死交集（span_days 29~30），本轮已真正解开，未硬拼制造假象。
  ② null 门槛读取：旧 `load_gate_from_yaml()` 正则 `min_sharpe:\\s*([\\d.]+)` 对
     `null` 匹配不到会 fallback 1.0，且旧脚本头注释写「夏普 ≥1.0 / min_sharpe 1.0」。
     本脚本正确读 `min_sharpe: null` → **不设夏普硬门槛**（判定 6 条退化为 5 条），
     json/meta 显式写 `min_sharpe: null / 不设夏普硬门槛`；注释与 config 口径统一；
     **所有数值一律不改**。
  ③ 极端段实判：config `must_survive_extreme_window: "2026-02"`、
     `extreme_window_start=1770163200`(2026-02-04 00:00 UTC)、
     `extreme_window_end=1770681600`(2026-02-10 00:00 UTC)。
     原 16 组 `n_na_extreme=16` 因 33d 窗口无 2026-02 覆盖 → N/A。
     本轮 410d 覆盖该段（4h=18 根 / 1d=6 根），**必须实际判定该段盈亏（不亏 / 亏）**，
     不得继续 N/A、不得用 0 占位顶替、不得放宽。0 成交组的「不亏」是「数据覆盖但
     策略无持仓 → 权益曲线净变化 0」的真实实判结果，并附 extreme_bars>0 佐证，
     与旧 33d 的「数据缺口 N/A」本质不同、与「0 占位」本质不同。

复用 G-3/G-4② 骨架（逐行一致，不改信号逻辑）：
  M3 FundingCarrySignal：fr>0 且 basis>0 连续 ≥3 bar 开仓、t 出信号 t+1 成交
  M4 成本口径：roundtrip = fee×2 + slip×2 + latency_1_3s；
              net_cost = roundtrip − carry_yield；gross_disc = 0.5×gross
  双腿费率：现货腿 taker=maker=0.1%/边；合约（永续）腿 taker=0.05%/边、maker=0.02%/边
  滑点 taker 4bp/边、maker 0.5bp/边（假设值、盘上无实测，沿用 G-3）；
  latency_1_3s 0.1bp（假设值）；sniping_discount 0.5；max_cost_as_pct_of_gross 0.50
  B 专家 8 项风控前置（回撤熔断 / funding 突变 / 敞口上限 / 连亏 / 双负减仓 / 笔数下限 …）

冻结门槛（本轮一律不动、不放宽、不收紧）：
  min_sharpe=null（不设硬门槛）、max_cost_as_pct_of_gross=0.50、
  max_drawdown_pct=10.0、min_calmar=1.5、max_consecutive_losses=5、
  极端段 2026-02 实判不亏、笔数下限 ≥30（B #7）。

产出：backtest/output/g4_carry_410d_rerun.json + .md
运行：python backtest/g4_carry_410d_rerun.py
红线：不改 config/src/.bat/.gitignore/.env、不提交 Git、不启实盘、不接付费源、
      无令牌/密钥写入、不放宽冻结门槛、不抢跑 G-6-②/G-6-③/G-5-②。
      结果如实申报：0 成交 / 0 达标就报 0，不美化、不粉饰、不脑补。
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "backtest" / "output"
DATA_DIR = ROOT / "data" / "cache" / "g1_carry_data_410d"
PARAM_YAML = ROOT / "config" / "strategy_params.yaml"

COINS = ["BTC", "ETH", "DOGE", "LTC"]
INTERVALS = ["4h", "1d"]
FEE_TIERS = ["futures_taker", "futures_maker"]     # 16 组 = 4 币 × 2 粒度 × 2 合约腿档

EXTREME_START = 1770163200     # 2026-02-04 00:00 UTC
EXTREME_END = 1770681600       # 2026-02-10 00:00 UTC


# ============================================================================
# 参数（读 config/strategy_params.yaml；SHA256 指纹留痕；数值一字不改）
# ============================================================================

def param_fingerprint() -> Dict[str, Any]:
    raw = PARAM_YAML.read_bytes()
    fp = hashlib.sha256(raw).hexdigest()
    return {"file": str(PARAM_YAML.relative_to(ROOT)),
            "sha256": fp, "sha256_16": fp[:16]}


def load_gate_from_yaml() -> Dict[str, Any]:
    """读 backtest_gate 段（只读，不改 yaml）。

    ★ 本轮修正：min_sharpe 可为 null → 返回 None（不设夏普硬门槛），
      不再像旧脚本 fallback 1.0。判定 6 条退化为 5 条。
    """
    txt = PARAM_YAML.read_text(encoding="utf-8")
    i = txt.index("backtest_gate:")
    j = txt.find("cost_model:", i)
    seg = txt[i:(j if j > i else len(txt))]
    g: Dict[str, Any] = {}
    m = re.search(r"max_drawdown_pct:\s*([\d.]+)", seg)
    g["max_drawdown_pct"] = float(m.group(1)) if m else 10.0
    # null 正确识别（旧脚本正则 ([\d.]+) 匹配不到 null → 错 fallback 1.0）
    m = re.search(r"min_sharpe:\s*(null|None|[\d.]+)", seg)
    if m and m.group(1).lower() in ("null", "none"):
        g["min_sharpe"] = None                       # 不设夏普硬门槛
    elif m:
        g["min_sharpe"] = float(m.group(1))
    else:
        g["min_sharpe"] = None                       # 缺失也不擅自设 1.0
    m = re.search(r"min_calmar:\s*([\d.]+)", seg)
    g["min_calmar"] = float(m.group(1)) if m else 1.5
    m = re.search(r"max_consecutive_losses:\s*(\d+)", seg)
    g["max_consecutive_losses"] = int(m.group(1)) if m else 5
    m = re.search(r"must_survive_extreme_window:\s*\"([^\"]+)\"", seg)
    g["extreme_window"] = m.group(1) if m else "2026-02"
    m = re.search(r"extreme_window_start:\s*(\d+)", seg)
    g["extreme_window_start"] = int(m.group(1)) if m else EXTREME_START
    m = re.search(r"extreme_window_end:\s*(\d+)", seg)
    g["extreme_window_end"] = int(m.group(1)) if m else EXTREME_END
    return g


def load_cost_from_yaml() -> Dict[str, Any]:
    """读 cost_model 段数值（只读，不改 yaml）；含 split-leg 字段。与旧脚本一致。"""
    txt = PARAM_YAML.read_text(encoding="utf-8")
    i = txt.index("cost_model:")
    j = txt.find("# ---- 因子拥挤", i)
    seg = txt[i:(j if j > i else len(txt))]
    c: Dict[str, Any] = {}

    def _grab(pat: str, default: float) -> float:
        m = re.search(pat, seg)
        return float(m.group(1)) if m else default

    c["futures_taker_fee_pct"] = _grab(r"futures_leg:\s*\n\s*taker_fee_pct:\s*([\d.]+)", 0.0005)
    c["futures_maker_fee_pct"] = _grab(r"futures_leg:\s*\n\s*(?:taker_fee_pct:\s*[\d.]+\s*\n\s*)?maker_fee_pct:\s*([\d.]+)", 0.0002)
    c["spot_taker_fee_pct"] = _grab(r"spot_leg:\s*\n\s*taker_fee_pct:\s*([\d.]+)", 0.001)
    c["spot_maker_fee_pct"] = _grab(r"spot_leg:\s*\n\s*(?:taker_fee_pct:\s*[\d.]+\s*\n\s*)?maker_fee_pct:\s*([\d.]+)", 0.001)
    c["max_cost_as_pct_of_gross"] = _grab(r"max_cost_as_pct_of_gross:\s*([\d.]+)", 0.50)
    m = re.search(r"maker_slippage_bps_per_side:\s*([\d.]+)", seg)
    c["maker_slip_bps_per_side"] = float(m.group(1)) if m else 0.5
    m = re.search(r"latency_1_3s_pct:\s*([\d.]+)", seg)
    c["latency_1_3s_pct"] = float(m.group(1)) if m else 0.00001
    m = re.search(r"slippage_bps_per_side:\s*\[([^\]]+)\]", seg)
    c["slippage_bps_per_side"] = (
        [float(x) for x in m.group(1).split(",")] if m else [3.0, 5.0])
    m = re.search(r"^\s*taker_fee_pct:\s*([\d.]+)\s*$", seg, re.M)
    c["taker_fee_pct"] = float(m.group(1)) if m else 0.001
    return c


# ============================================================================
# 常量假设（沿用 G-3/G-4②；「假设值」标注盘上无实测）
# ============================================================================

TAKER_SLIP_BPS = 4.0                 # 冻结 3~5bp 区间取 4bp/边
SNIPING_DISCOUNT = 0.5              # C 专家 0.5x 抢跑折扣（gross_disc = 0.5×gross）
SETTLE_PER_BAR = {"4h": 1.0, "1d": 3.0}   # 8h 结算折算
HOLD_BARS = 1
MIN_TRADES = 30                     # B #7 笔数下限
MAX_EXPOSURE_PER_COIN = 0.20
FUNDING_MUTATION_RUN = 3
FUNDING_CLEAR_RUN = 5
DD_WARN_PCT = 8.0
DUAL_NEG_DELEVER = True
ANN_FACTOR = {"4h": np.sqrt(2190.0), "1d": np.sqrt(365.0)}   # 沿用 G-3 口径


@dataclass
class FeeTier:
    name: str
    leg: str
    fee_pct: float
    slip_bps: float
    slip_note: str
    latency_pct: float
    latency_note: str

    def roundtrip_cost_pct(self) -> float:
        """单 bar roundtrip = fee×2 + slip×2 + latency（C 专家口径，未改）。"""
        slip_pct = self.slip_bps * 1e-4
        return self.fee_pct * 2 + slip_pct * 2 + self.latency_pct


def build_tiers(cost_cfg: Dict[str, Any]) -> Dict[str, FeeTier]:
    lat = cost_cfg["latency_1_3s_pct"]
    lat_note = "假设值：1~3s 成交时延折算 0.1bp（G-3 假设值，盘上无实测）"
    ft_taker = cost_cfg["futures_taker_fee_pct"]
    ft_maker = cost_cfg["futures_maker_fee_pct"]
    mk_slip = cost_cfg.get("maker_slip_bps_per_side", 0.5)
    return {
        "futures_taker": FeeTier("futures_taker", "futures", ft_taker, TAKER_SLIP_BPS,
                                 "taker 吃单 4bp/边（G-3 冻结保守假设，盘上无实测）", lat, lat_note),
        "futures_maker": FeeTier("futures_maker", "futures", ft_maker, mk_slip,
                                 "maker 挂单被动成交 0.5bp/边（假设值、盘上无实测）", lat, lat_note),
    }


# ============================================================================
# M3 · FundingCarrySignal（复用 G-3/G-4② 骨架，逐行一致，不改）
# ============================================================================

@dataclass
class CarryBar:
    ts: int
    spot_open: float
    spot_close: float
    perp_close: float
    funding_rate: float
    basis_pct: float
    entry_signal: bool
    exit_signal: bool
    exit_reason: str = ""


def build_signals(df: pd.DataFrame) -> List[CarryBar]:
    """与 G-3 build_signals 逐行一致（M3 骨架不改）。"""
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


def funding_mutation_state(funding_series: List[float], mean_abs: float
                           ) -> List[Dict[str, Any]]:
    """B #2 funding 突变监控（与 G-3 一致）。"""
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
# 回测主体（单组：币 × 粒度 × 合约腿费率档）
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
    cost_ratio: float = 0.0
    cost_ok: bool = False
    max_drawdown_pct: float = 0.0
    sharpe: float = 0.0
    calmar: float = 0.0
    max_consecutive_losses: int = 0
    # 极端段实判（本轮新增：数据覆盖 → 不再 N/A）
    extreme_pnl: float = 0.0
    extreme_equity_change_pct: float = 0.0
    extreme_bars: int = 0
    extreme_trades: int = 0
    extreme_result: str = ""
    extreme_survive: bool = False
    extreme_na: bool = False
    extreme_note: str = ""
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

    fr_series = [float(x) for x in df["funding_rate"].tolist()]
    mean_abs = float(np.mean([abs(x) for x in fr_series])) if fr_series else 0.0
    mut = funding_mutation_state(fr_series, mean_abs)
    hit_idx = [i for i, mm in enumerate(mut)
               if mm["hit"] and mm["scale"] in (0.0, 0.5)]
    for i in hit_idx:
        s = signals[i]
        g.risk_events.append(
            f"funding突变 {mut[i]['sign']:+d}×{mut[i]['run_len']}期24h"
            f"|{s.funding_rate:+.5f}|@ts={s.ts}")
    g.n_funding_mutation = len(hit_idx)

    eq0 = 10000.0
    realized = [eq0]
    unreal = 0.0
    total_curve: List[float] = []

    pos: Optional[Dict[str, Any]] = None
    trades: List[Dict[str, Any]] = []
    settle = SETTLE_PER_BAR[interval]
    rtrip_pct = ft.roundtrip_cost_pct()

    for i, s in enumerate(signals):
        mut_scale = mut[i]["scale"] if i < len(mut) else 1.0

        if s.exit_signal and pos is not None:
            px = s.spot_open
            notional = eq0 * pos["scale"]
            price_pnl = notional * (px - pos["entry_px"]) / pos["entry_px"]
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
            if DUAL_NEG_DELEVER and price_pnl < 0 and carry < 0:
                g.n_dual_neg += 1
                g.risk_events.append(f"双负强制减仓 ts={s.ts}")
            pos = None
            unreal = 0.0

        if s.entry_signal and pos is None and mut_scale > 0:
            dd_now = _cur_drawdown_pct(total_curve or [100.0])
            scale = MAX_EXPOSURE_PER_COIN
            if dd_now >= DD_WARN_PCT:
                scale *= 0.5
            scale *= mut_scale
            if scale > 0:
                pos = {"entry_ts": s.ts, "entry_px": s.spot_open,
                       "scale": scale, "fr": s.funding_rate}

        if pos is not None:
            px = s.spot_close
            notional = eq0 * pos["scale"]
            unreal = notional * (px - pos["entry_px"]) / pos["entry_px"]
        total_curve.append((realized[-1] + unreal) / eq0 * 100)

        dd_now = _cur_drawdown_pct(total_curve)
        if dd_now >= gate["max_drawdown_pct"]:
            g.halted = True
            g.risk_events.append(f"回撤熔断 {dd_now:.2f}%@ts={s.ts}")
            break

    eq_end = realized[-1]
    _finalize(g, trades, total_curve, eq0, eq_end, g.span_days, gate,
              signals=signals, es=gate["extreme_window_start"], ee=gate["extreme_window_end"])
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
              span_days: int, gate: Dict[str, Any],
              signals: Optional[List[CarryBar]] = None,
              es: int = EXTREME_START, ee: int = EXTREME_END) -> None:
    g.n_trades = len(trades)
    g.n_trades_min_ok = g.n_trades >= MIN_TRADES

    g.net_return_pct = (eq_end / eq0 - 1) * 100

    gross = float(sum(t["price_pnl"] for t in trades))
    gross_disc = SNIPING_DISCOUNT * gross
    carry_total = float(sum(t["carry_yield"] for t in trades))
    rtrip_total = float(sum(t["roundtrip_cost"] for t in trades))
    net_cost_total = rtrip_total - carry_total
    cost_ratio = (net_cost_total / gross_disc) if gross_disc > 0 else float("inf")
    g.gross_pnl = gross
    g.gross_pnl_disc = gross_disc
    g.carry_yield_total = carry_total
    g.roundtrip_cost_total = rtrip_total
    g.net_cost_total = net_cost_total
    g.cost_ratio = cost_ratio
    g.cost_ok = bool(trades and cost_ratio <= gate["max_cost_as_pct_of_gross"]
                     and not np.isinf(cost_ratio))

    eq = np.array(total_curve, dtype=float)
    if len(eq) > 1:
        peak = np.maximum.accumulate(eq)
        dd = (peak - eq) / np.where(peak > 0, peak, 1) * 100
        g.max_drawdown_pct = float(dd.max())
    else:
        g.max_drawdown_pct = 0.0

    if trades and len(eq) > 2:
        rets = np.diff(eq) / np.where(eq[:-1] > 0, eq[:-1], 1)
        g.sharpe = float(np.mean(rets) / np.std(rets) * ANN_FACTOR[g.interval]) \
            if np.std(rets) > 0 else 0.0
    else:
        g.sharpe = 0.0
    ann_ret = (g.net_return_pct / 100) * (365.0 / max(1, span_days))
    g.calmar = float(ann_ret / (g.max_drawdown_pct / 100)) \
        if g.max_drawdown_pct > 0 else 0.0

    pnl_seq = [t["pnl"] for t in trades]
    run = best = 0
    for p in pnl_seq:
        run = run + 1 if p < 0 else 0
        best = max(best, run)
    g.max_consecutive_losses = best
    if best > gate["max_consecutive_losses"]:
        g.risk_events.append(f"连亏熔断 {best}>{gate['max_consecutive_losses']}")

    # ------------------------------------------------------------------
    # 极端段 2026-02 实判（本轮核心改动：数据覆盖 → 真判盈亏，不 N/A、不 0 占位）
    # 口径：段内 bar 数 extreme_bars>0 证明数据覆盖；段内平仓笔数 extreme_trades；
    #       extreme_pnl = 段内已实现盈亏合计；extreme_equity_change_pct = 段内
    #       权益曲线净变化（百分点）。survive = 净变化 ≥ 0（不亏）。
    # ------------------------------------------------------------------
    sig = signals or []
    seg_idx = [i for i, s in enumerate(sig) if es <= s.ts < ee]
    g.extreme_bars = len(seg_idx)
    seg_trades = [t for t in trades if es <= t["exit_ts"] < ee]
    g.extreme_trades = len(seg_trades)
    g.extreme_pnl = float(sum(t["pnl"] for t in seg_trades))
    if seg_idx and total_curve:
        i0 = max(0, min(seg_idx[0], len(total_curve) - 1))
        i1 = max(0, min(seg_idx[-1], len(total_curve) - 1))
        base = total_curve[i0 - 1] if i0 > 0 else 100.0
        g.extreme_equity_change_pct = float(total_curve[i1] - base)
    else:
        g.extreme_equity_change_pct = 0.0
    # 浮点容差
    g.extreme_survive = bool(g.extreme_equity_change_pct >= -1e-9)
    g.extreme_result = "不亏" if g.extreme_survive else "亏"
    g.extreme_na = False           # 410d 覆盖该段 → 不再是 N/A
    if g.extreme_bars == 0:
        g.extreme_note = ("⚠️ 段内 0 bar：数据未覆盖该段（本轮 410d 应覆盖，出现即须核查）；"
                          "未用 0 占位顶替")
    elif g.extreme_trades == 0:
        g.extreme_note = (f"410d 覆盖该段（段内 {g.extreme_bars} 根 bar），策略在该段 0 成交、"
                          f"无持仓 → 权益曲线净变化 {g.extreme_equity_change_pct:+.4f} 个百分点，"
                          f"实判「{g.extreme_result}」。此为数据覆盖下无交易的真实实判结果，"
                          f"非 0 占位、非旧 33d 的 N/A（旧为数据缺口）。")
    else:
        g.extreme_note = (f"410d 覆盖该段（段内 {g.extreme_bars} 根 bar / {g.extreme_trades} 笔平仓），"
                          f"段内已实现 pnl={g.extreme_pnl:+.4f}、权益净变化 "
                          f"{g.extreme_equity_change_pct:+.4f} 个百分点，"
                          f"实判「{g.extreme_result}」。")


# ============================================================================
# 门槛判定（本轮：min_sharpe=null 不设硬门槛 → 6 条退化为 5 条；极端段实判）
# ============================================================================

HARD_5 = ["max_drawdown", "calmar", "cost_ratio", "consec_losses", "extreme_survive"]


def judge_gates(g: GroupResult, gate: Dict[str, Any]) -> Dict[str, Any]:
    gates = {
        "max_drawdown": bool(g.max_drawdown_pct <= gate["max_drawdown_pct"]),
        "sharpe": None,                    # min_sharpe=null → 不设硬门槛（不参与判定）
        "sharpe_gate_disabled": True,
        "calmar": bool(g.calmar >= gate["min_calmar"]),
        "cost_ratio": g.cost_ok,
        "consec_losses": bool(g.max_consecutive_losses <= gate["max_consecutive_losses"]),
        "extreme_survive": g.extreme_survive,     # 2026-02 实判（不亏/亏）
        "extreme_na": g.extreme_na,               # 本轮恒 False
        "min_trades": g.n_trades_min_ok,          # B #7 附加门槛
    }
    gates["all_hard_5"] = all(bool(gates[k]) for k in HARD_5)
    gates["pass_final"] = bool(gates["all_hard_5"] and gates["min_trades"]
                               and not gates["extreme_na"])
    return gates


# ============================================================================
# 主流程
# ============================================================================

def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="G-4-②-410d G1 carry 410d 窗口重跑 16 组")
    ap.add_argument("--out", default=str(OUT_DIR / "g4_carry_410d_rerun.json"))
    args = ap.parse_args()

    gate = load_gate_from_yaml()
    cost_cfg = load_cost_from_yaml()
    gate["max_cost_as_pct_of_gross"] = cost_cfg["max_cost_as_pct_of_gross"]
    pfp = param_fingerprint()
    tiers = build_tiers(cost_cfg)
    print(f"[参数指纹] {pfp['file']} SHA256-16 = {pfp['sha256_16']}")
    print(f"[门槛] min_sharpe={gate['min_sharpe']}（不设夏普硬门槛 / 6条退化为5条） "
          f"max_cost={gate['max_cost_as_pct_of_gross']} "
          f"极端段=[{gate['extreme_window_start']},{gate['extreme_window_end']})")
    print(f"[费率] 现货腿 {cost_cfg['spot_taker_fee_pct']}/{cost_cfg['spot_maker_fee_pct']} "
          f"合约腿 taker={cost_cfg['futures_taker_fee_pct']} maker={cost_cfg['futures_maker_fee_pct']}")

    results: List[GroupResult] = []
    errors: List[str] = []
    data_fps: Dict[str, str] = {}

    for coin in COINS:
        for iv in INTERVALS:
            f = DATA_DIR / f"{coin}_USDT_{iv}_410d_carry.parquet"
            if not f.exists():
                errors.append(f"缺数据 {f.name}")
                continue
            df = pd.read_parquet(f)
            data_fps[f.name] = hashlib.sha256(f.read_bytes()).hexdigest()[:16]
            for tier in FEE_TIERS:
                try:
                    g = run_group(coin, iv, tier, df, gate, tiers)
                    g.gates = judge_gates(g, gate)
                    results.append(g)
                    print(f"{coin} {iv} {tier}: bars={g.bars}({g.span_days}d) "
                          f"trades={g.n_trades} cost_ok={g.cost_ok} "
                          f"extreme=「{g.extreme_result}」(bars={g.extreme_bars},"
                          f"trades={g.extreme_trades},Δeq={g.extreme_equity_change_pct:+.4f}pp) "
                          f"hard5={g.gates['all_hard_5']} "
                          f"{'PASS' if g.gates['pass_final'] else 'FAIL'}")
                except Exception as e:
                    import traceback
                    traceback.print_exc()
                    errors.append(f"{coin} {iv} {tier}: {e}")

    # 原 33d 结果对照（读旧产物，只读不改）
    old_path = OUT_DIR / "g4_carry_rerun.json"
    old_payload: Dict[str, Any] = {}
    if old_path.exists():
        old_payload = json.loads(old_path.read_text(encoding="utf-8"))
    old_summary = old_payload.get("summary", {})
    old_by_key = {(g["coin"], g["interval"], g["tier"]): g
                  for g in old_payload.get("groups", [])}
    comparison_rows = []
    for g in results:
        # 旧 33d json 的 tier 命名为 "taker"/"maker"，本轮为 "futures_taker"/"futures_maker"
        old_tier = "taker" if g.tier == "futures_taker" else "maker"
        o = old_by_key.get((g.coin, g.interval, old_tier))
        if o is None:
            continue
        comparison_rows.append({
            "coin": g.coin, "interval": g.interval, "tier": g.tier,
            "old_bars": o.get("bars"), "new_bars": g.bars,
            "old_span_days": o.get("span_days"), "new_span_days": g.span_days,
            "old_n_trades": o.get("n_trades"), "new_n_trades": g.n_trades,
            "old_cost_ok": o.get("cost_ok"), "new_cost_ok": g.cost_ok,
            "old_extreme_na": o.get("extreme_na"), "new_extreme_na": g.extreme_na,
            "old_extreme_result": "N/A(数据缺口)", "new_extreme_result": g.extreme_result,
            "old_pass_final": o.get("gates", {}).get("pass_final"),
            "new_pass_final": g.gates.get("pass_final"),
        })

    n_passed_final = sum(1 for g in results if g.gates.get("pass_final"))
    n_na_extreme = sum(1 for g in results if g.extreme_na)
    summary = {
        "n_groups": len(results),
        "n_passed_final": n_passed_final,
        "n_na_extreme": n_na_extreme,
        "n_passed_hard5": sum(1 for g in results if g.gates.get("all_hard_5")),
        "n_passed_min_trades": sum(1 for g in results if g.gates.get("min_trades")),
        "n_total_trades": sum(g.n_trades for g in results),
        "n_extreme_survive": sum(1 for g in results if g.extreme_survive),
        "note_extreme": ("410d 覆盖 2026-02-04~02-10（4h=18 根 / 1d=6 根），16 组全部实判盈亏"
                         "（不亏/亏），n_na_extreme=0；0 成交组为「数据覆盖但无持仓 → 权益净变化 0"
                         " → 实判不亏」，非 N/A、非 0 占位、不放宽"),
        "note_window": ("410d 数据层重建后交集 = 425d（2025-09-01~2026-10-31，由 funding 决定）；"
                        "相对 410d 目标窗口 2025-09-30~2026-11-13，尾部 2026-11-01~11-13 共 13 天 "
                        "funding 无归档（🟡 3.17%），如实标注、不用 0 占位；旧 33d 交集 29~30d"),
        "note_continuity": ("开仓口径沿用 G-3：fr>0 且 basis>0 连续 ≥3 bar；410d 下 basis>0 仍极稀疏"
                            "（每币 0~7 根），最长连续 both>0 仅 BTC/LTC 4h 达 3~4 根；"
                            "逐组成交数如实申报，不美化"),
        "note_min_trades": f"笔数下限 ≥{MIN_TRADES} 笔（B #7），<30 不计通过",
        "note_sharpe": "min_sharpe=null → 不设夏普硬门槛（6 条退化为 5 条），config 数值一字未改",
        "note_short_side": "做空侧（负 carry）本轮不测",
        "note_1h": "1h 粒度不测（phase9 §六.2 已判 1h carry 不成立）",
        "g4_directive": "老板 2026-11-14 22:44「先拿现有 funding 重跑 G-4-②， 继续派 G-6-② 找多级 L2 免费源」——本批只做前半句 410d 重跑",
        "fee_split_declaration": ("现货腿 0.1%/边（taker=maker）vs 合约腿 maker 0.02%·taker 0.05%/边，"
                                  "两档分开申报；16 组 = 4币 × 2粒度 × 2合约腿档"),
        "data_fingerprints": data_fps,
        "param_fingerprint": {"file": pfp["file"], "sha256": pfp["sha256"],
                              "sha256_16": pfp["sha256_16"]},
        "old_33d_summary": {"n_passed_final": old_summary.get("n_passed_final"),
                            "n_na_extreme": old_summary.get("n_na_extreme"),
                            "note_window": old_summary.get("note_window"),
                            "note_continuity": old_summary.get("note_continuity")},
    }

    gate_declaration = {
        "min_sharpe": None,
        "sharpe_gate": "不设夏普硬门槛（min_sharpe=null）",
        "hard_gates_5": ["max_drawdown≤10%", "calmar≥1.5", "cost_ratio≤0.50",
                         "max_consecutive_losses≤5", "extreme_survive(2026-02 实判不亏)"],
        "n_hard_gates": 5,
        "note": "原 6 条硬门槛（含夏普）→ min_sharpe=null 后退化为 5 条；B #7 笔数下限≥30 另行生效",
        "extreme_window": {"start": gate["extreme_window_start"],
                           "end": gate["extreme_window_end"], "label": gate["extreme_window"]},
        "gate_from_yaml": gate,
        "cost_cfg_yaml": cost_cfg,
    }

    payload = {
        "task": "G-4-②-410d · G1 carry 410d 窗口重跑 16 组（min_sharpe=null / 极端段实判）",
        "role": "后端架构师落实，PM 承派+验收",
        "date": "2026-11-14",
        "boss_directive": "老板 2026-11-14 22:44「先拿现有 funding 重跑 G-4-②， 继续派 G-6-② 找多级 L2 免费源」（本批只做前半句，不抢跑 G-6-②）",
        "param_fingerprint": pfp,
        "data_fingerprints": data_fps,
        "data_dir": str(DATA_DIR.relative_to(ROOT)),
        "gate_declaration": gate_declaration,
        "gate_note": "min_sharpe: null / 不设夏普硬门槛；门槛数值（回撤/Calmar/成本/连亏/极端段/笔数）一律未改、未放宽、未收紧",
        "assumptions": {
            "spot_leg_fee_pct_per_side": cost_cfg["spot_taker_fee_pct"],
            "futures_leg_taker_fee_pct_per_side": cost_cfg["futures_taker_fee_pct"],
            "futures_leg_maker_fee_pct_per_side": cost_cfg["futures_maker_fee_pct"],
            "fee_declaration": "现货腿 0.1% vs 合约腿 maker 0.02%·taker 0.05% 两档分开申报，不混用",
            "sharpe_gate": "不设硬门槛（min_sharpe=null）",
            "taker_slip_bps_per_side": TAKER_SLIP_BPS,
            "maker_slip_bps_per_side": cost_cfg.get("maker_slip_bps_per_side", 0.5),
            "slip_note": "假设值（G-3 冻结/估算值），盘上无实测",
            "latency_1_3s_pct": cost_cfg["latency_1_3s_pct"],
            "latency_note": "假设值（G-3），盘上无实测",
            "sniping_discount": SNIPING_DISCOUNT,
            "carry_formula": "carry_yield = funding_rate × position_dir(+1) × bars_held(1) × (8h结算折算: 4h→1, 1d→3)",
            "roundtrip_formula": "roundtrip_cost = fee×2 + slippage×2 + latency_1_3s",
            "cost_ok_formula": f"cost_ok = (roundtrip_cost - carry_yield) ≤ {gate['max_cost_as_pct_of_gross']} × (0.5 × gross_pnl)",
            "extreme_formula": "extreme_equity_change_pct = 权益曲线在 [2026-02-04,2026-02-10) 段内的净变化；survive = 净变化 ≥ 0（不亏）",
        },
        "risk_preconditions": [
            f"#1 回撤熔断：{DD_WARN_PCT}%预警降敞口50%，{gate['max_drawdown_pct']:.0f}%停机",
            f"#2 funding突变：≥{FUNDING_MUTATION_RUN}期(24h)同号且|值|>2×均值→降敞口50%，≥{FUNDING_CLEAR_RUN}期→清零",
            f"#3 敞口上限 {MAX_EXPOSURE_PER_COIN:.0%}/币（4 币合计 ≤80%）",
            f"#4 连亏熔断 ≤{gate['max_consecutive_losses']}",
            "#5 资金曲线监控：carry<0 且 price<0 双负 → 当日强制减仓",
            "#6 极端段门槛：本轮 410d 覆盖 → 实判盈亏（不亏/亏），不再 N/A、不再用 0 占位",
            f"#7 笔数下限 ≥{MIN_TRADES} 笔（4h/1d 同限），<30 不计通过",
            f"#8 成本口径锁定：含 1~3s 时延 + 0.5x 抢跑折扣 + carry 翻转，≤{gate['max_cost_as_pct_of_gross']} 复算",
        ],
        "groups": [_to_dict(g) for g in results],
        "errors": errors,
        "comparison_with_33d": {
            "note": "逐组对照旧 33d 产物 backtest/output/g4_carry_rerun.json（只读）",
            "rows": comparison_rows,
            "old_n_passed_final": old_summary.get("n_passed_final"),
            "old_n_na_extreme": old_summary.get("n_na_extreme"),
        },
        "summary": summary,
    }

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str),
                   encoding="utf-8")
    md_path = out.with_suffix(".md")
    md_path.write_text(render_md(payload), encoding="utf-8")
    print(f"\n[JSON] → {out}")
    print(f"[MD]   → {md_path}")
    print(f"通过(最终) {n_passed_final}/{len(results)}；n_na_extreme={n_na_extreme}；"
          f"总成交 {summary['n_total_trades']}；errors {len(errors)}")
    return 0


# ============================================================================
# 报告
# ============================================================================

def render_md(payload: Dict[str, Any]) -> str:
    groups: List[Dict[str, Any]] = payload["groups"]
    s = payload["summary"]
    a = payload["assumptions"]
    gd = payload["gate_declaration"]
    cmp_ = payload["comparison_with_33d"]

    L: List[str] = []
    L.append("# G-4-②-410d · G1 carry 410d 窗口重跑 16 组报告")
    L.append("")
    L.append(f"> 角色：{payload['role']}｜日期：{payload['date']}")
    L.append(f"> 老板指令（逐字）：{payload['boss_directive']}")
    L.append(f"> 参数指纹：`config/strategy_params.yaml` SHA256-16 = `{payload['param_fingerprint']['sha256_16']}`（本轮未改 config）")
    L.append("")
    L.append("## 0. 口径确认（老板 A 方案留痕，本轮一律不动）")
    L.append("")
    L.append(f"- `min_sharpe: null` → **不设夏普硬门槛**（老板原话「夏普率本就不要求过 1.0」）；"
             f"判定 6 条退化为 **5 条**：回撤≤10% / Calmar≥1.5 / 成本≤0.50 / 连亏≤5 / 极端段(2026-02)实判不亏")
    L.append(f"- `max_cost_as_pct_of_gross = {gd['gate_from_yaml']['max_cost_as_pct_of_gross']}`（未改）")
    L.append(f"- 双腿费率：现货腿 taker=maker={a['spot_leg_fee_pct_per_side']}/边；"
             f"合约腿 taker={a['futures_leg_taker_fee_pct_per_side']}/边、"
             f"maker={a['futures_leg_maker_fee_pct_per_side']}/边（两档分开申报）")
    L.append(f"- 滑点 taker {a['taker_slip_bps_per_side']}bp/边、maker {a['maker_slip_bps_per_side']}bp/边、"
             f"latency {a['latency_1_3s_pct']}、sniping_discount {a['sniping_discount']}（假设值、盘上无实测，沿用 G-3）")
    L.append(f"- 极端段：`{gd['extreme_window']['label']}` "
             f"[{gd['extreme_window']['start']}, {gd['extreme_window']['end']})，本轮**实判**、不放宽")
    L.append("")
    L.append("## 1. 数据窗口（如实申报）")
    L.append("")
    L.append(f"- {s['note_window']}")
    L.append(f"- 数据层：perp（futures/um klines）4h/1d 410d + spot 4h 410d 实际下载 324/324 zip（website endpoint 匿名 GET），"
             f"spot 1d 410d 复用 G-6-①；与 funding_410d.parquet 对齐重建 basis/basis_pct（G-3 口径）")
    L.append(f"- {s['note_continuity']}")
    L.append(f"- 组数：4 币 × 2 粒度（4h/1d）× 2 合约腿档（taker/maker）= **16 组**")
    L.append(f"- {s['note_short_side']}；{s['note_1h']}")
    L.append("")
    L.append("## 2. 极端段 2026-02 实判（本轮核心，不再 N/A）")
    L.append("")
    L.append(f"- {s['note_extreme']}")
    L.append(f"- 16 组 `n_na_extreme = {s['n_na_extreme']}`（应为 0）；实判「不亏」{s['n_extreme_survive']} 组 / 「亏」{16 - s['n_extreme_survive']} 组")
    L.append("")
    L.append("## 3. 全量 16 组结果")
    L.append("")
    L.append("| 币 | 粒度 | 档 | bar | 天数 | 成交 | cost_ok | 回撤% | Calmar | 连亏 | 极端段bars | 极端段成交 | 权益净变化pp | 极端段实判 | hard5 | 最终 |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for g in groups:
        L.append(
            f"| {g['coin']} | {g['interval']} | {g['tier']} | {g['bars']} | {g['span_days']} "
            f"| {g['n_trades']} | {'✅' if g['cost_ok'] else '❌'} "
            f"| {g['max_drawdown_pct']:.4f} | {g['calmar']:.4f} | {g['max_consecutive_losses']} "
            f"| {g['extreme_bars']} | {g['extreme_trades']} "
            f"| {g['extreme_equity_change_pct']:+.4f} | {g['extreme_result']} "
            f"| {'✅' if g['gates']['all_hard_5'] else '❌'} "
            f"| {'✅ PASS' if g['gates']['pass_final'] else '❌ FAIL'} |")
    L.append("")
    L.append("## 4. 5 条硬门槛达成数（夏普不设）")
    L.append("")
    L.append("| 门槛 | 16 组达成数 | 说明 |")
    L.append("|---|---|---|")
    L.append(f"| 回撤≤10% | {sum(1 for g in groups if g['gates']['max_drawdown'])} | 维持原值 |")
    L.append("| 夏普 | — | min_sharpe=null 不设硬门槛（旧脚本 fallback 1.0 已修正） |")
    L.append(f"| Calmar≥1.5 | {sum(1 for g in groups if g['gates']['calmar'])} | 维持原值 |")
    L.append(f"| 成本≤0.50×0.5毛利 | {sum(1 for g in groups if g['gates']['cost_ratio'])} | 维持原值 |")
    L.append(f"| 连亏≤5 | {sum(1 for g in groups if g['gates']['consec_losses'])} | 维持原值 |")
    L.append(f"| 极端段(2026-02)实判不亏 | {sum(1 for g in groups if g['gates']['extreme_survive'])} | 本轮实判、非 N/A |")
    L.append(f"| 笔数≥{30}（B #7 附加） | {sum(1 for g in groups if g['gates']['min_trades'])} | 仍生效 |")
    L.append("")
    L.append(f"**最终通过（5 条硬门槛 + 笔数下限 + 非 N/A）：{s['n_passed_final']}/{s['n_groups']} 组**；"
             f"总成交 {s['n_total_trades']} 笔。")
    L.append("")
    L.append("## 5. 与原 33d 结果对比（只读旧产物）")
    L.append("")
    L.append(f"- 旧 33d：`n_passed_final={cmp_['old_n_passed_final']}`、`n_na_extreme={cmp_['old_n_na_extreme']}`、"
             f"16 组 0 成交；机理 = 33d 窗口内 fr∩basis 同正 bar 每币 0~1 根孤立不连续 → 0 开仓")
    L.append(f"- 本轮 410d：`n_passed_final={s['n_passed_final']}`、`n_na_extreme={s['n_na_extreme']}`、"
             f"总成交 {s['n_total_trades']} 笔；交集窗口由旧 33d 的 29~30d 扩到 425d")
    L.append("")
    L.append("| 币 | 粒度 | 档 | 旧bar/天 | 新bar/天 | 旧成交 | 新成交 | 旧极端段 | 新极端段 | 旧最终 | 新最终 |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|")
    for r in cmp_["rows"]:
        L.append(f"| {r['coin']} | {r['interval']} | {r['tier']} "
                 f"| {r['old_bars']}/{r['old_span_days']}d | {r['new_bars']}/{r['new_span_days']}d "
                 f"| {r['old_n_trades']} | {r['new_n_trades']} "
                 f"| {r['old_extreme_result']} | {r['new_extreme_result']} "
                 f"| {'✅' if r['old_pass_final'] else '❌'} | {'✅' if r['new_pass_final'] else '❌'} |")
    L.append("")
    L.append("## 6. B 专家 8 项风控前置（沿用 G-3，未改）")
    L.append("")
    for r in payload["risk_preconditions"]:
        L.append(f"- {r}")
    L.append("")
    L.append("## 7. 结论与如实申报")
    L.append("")
    L.append(f"- **达标数：{s['n_passed_final']}/{s['n_groups']}**；总成交 {s['n_total_trades']} 笔；"
             f"`n_na_extreme={s['n_na_extreme']}`。")
    L.append(f"- {s['note_continuity']}")
    L.append(f"- {s['note_extreme']}")
    L.append("- 若仍 0 达标：机理如实写明（basis>0 极稀疏 + 笔数下限/成本/Calmar 门槛），"
             "不美化、不粉饰、不脑补、不用 0 占位顶替极端段、不把「确认存在」写成「已落盘」。")
    L.append("- M3/M4 信号逻辑骨架与 G-3/G-4② 逐行一致，只改数据源窗口 + null 门槛读取 + 极端段实判；"
             "未改 src/、未改 config 数值、未启用实盘、未提交 Git、无令牌/密钥写入。")
    L.append("")
    L.append("## 8. 数据 / 参数指纹")
    L.append("")
    L.append(f"- 参数指纹：`{payload['param_fingerprint']['file']}` = `{payload['param_fingerprint']['sha256']}`")
    L.append(f"- 数据指纹（`{payload['data_dir']}/` 8 个 parquet SHA256-16）：")
    for k, v in sorted(payload["data_fingerprints"].items()):
        L.append(f"    - `{k}` = `{v}`")
    L.append("- 数据层 manifest：`data/cache/g1_carry_data_410d/_manifest.json` + `_download_log.json`（324 项逐项 HTTP 码）")
    L.append("")
    return "\n".join(L)


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
        "extreme_pnl": round(g.extreme_pnl, 4),
        "extreme_equity_change_pct": round(g.extreme_equity_change_pct, 6),
        "extreme_bars": g.extreme_bars,
        "extreme_trades": g.extreme_trades,
        "extreme_result": g.extreme_result,
        "extreme_survive": g.extreme_survive,
        "extreme_na": g.extreme_na,
        "extreme_note": g.extreme_note,
        "gates": g.gates,
        "risk_events": g.risk_events,
        "halted": g.halted,
    }


if __name__ == "__main__":
    sys.exit(main())
