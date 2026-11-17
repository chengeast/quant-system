# -*- coding: utf-8 -*-
"""G-4-② · G1 carry 合约档费率 + 降门槛 · ~33d 窗口重跑 16 组（M3+M4 独立实现，不改 src/ 骨架）。

任务单：老板 2026-11-12 A 方案指令（派单见 agents/dispatch_log.md G-4-② 段）。
角色：后端架构师 落实，PM 验收后回报秘书。

与 G-3（backtest/g1_carry_basis_backtest.py）的关系：
  - 复用 G-3 的 M3 信号骨架（FundingCarrySignal：fr>0 且 basis>0 连续 ≥3 bar 开仓，
    t 出信号 t+1 成交）+ M4 成本口径（roundtrip = fee×2 + slip×2 + latency_1_3s；
    net_cost = roundtrip − carry_yield；gross_disc = 0.5×gross）；
  - 只改两处：① 费率档拆「现货腿 / 合约腿」双腿分报，合约腿按老板 2026-11-12
    澄清的 Gate.io VIP0 永续档 taker=0.05%/边、maker=0.02%/边（原样引用、不改数值）；
    ② 门槛读改后 config（min_sharpe 1.0 / max_cost 0.50），其余 4 条维持原值；
  - 不改 M3/M4 信号逻辑骨架，不读 410d（没有），funding 仍读 ~33d 实得 100 根/币。

老板 2026-11-12 澄清关键事实（须原样引用、不改写）：
  Gate.io VIP0 现货 taker=maker=0.1%/边；合约（永续）maker=0.02%、taker=0.05%/边。
  G-3 把合约腿误按现货 0.1% 算，成本算贵 2~5 倍，须按合约档重算。

老板 2026-11-12 拍板：不补 410d，先按合约档 + 降门槛在 ~33d funding 窗口重跑 16 组。

费率分腿申报（json/meta 显式，不再混用 0.1% 算合约腿）：
  现货腿（期现套利/均值回归现货一侧）：taker=maker=0.1%/边，roundtrip 0.2%
  合约腿（carry 永续一侧，funding 收取方）：taker=0.05%/边、maker=0.02%/边
    roundtrip maker 0.04%、taker 0.10%（各 + 滑点×2 + latency_1_3s）
  滑点沿用 G-3 假设值（taker 4bp/边、maker 0.5bp/边），标注「假设值、盘上无实测」
  latency_1_3s 沿用 G-3 假设值 0.1bp（盘上无实测）

门槛判定（改后口径）：
  夏普：落地为「≥1.0」（老板明示「夏普率本就不要求过 1.0」，二选一取此项）
  成本：net_cost ≤ 0.50 × gross_disc（0.35→0.50）
  其余 4 条维持原值：回撤≤10%、Calmar≥1.5、连亏≤5、极端段(2026-02)不亏
  极端段(2026-02)：410d 未补、数据源缺口 → 维持 N/A，不计通过、不用 0 顶替
  笔数下限（4h≥30 / 1d≥30）仍生效，<30 不计通过

产出：backtest/output/g4_carry_rerun.json + .md
运行：python backtest/g4_carry_rerun.py
本脚本不改 src/、不改 config 数值（读改后 config 做指纹）、不动 .bat/.gitignore/.env、不提交 Git、无令牌/密钥写入。
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
# G-4-②：16 组 = 4 币 × 2 粒度 × 2 档（合约腿 taker / 合约腿 maker）
FEE_TIERS = ["futures_taker", "futures_maker"]

# ============================================================================
# 参数（读改后 config/strategy_params.yaml；SHA256 指纹留痕）
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
    g["min_sharpe"] = float(m.group(1)) if m else 1.0
    m = re.search(r"min_calmar:\s*([\d.]+)", seg)
    g["min_calmar"] = float(m.group(1)) if m else 1.5
    m = re.search(r"max_consecutive_losses:\s*(\d+)", seg)
    g["max_consecutive_losses"] = int(m.group(1)) if m else 5
    m = re.search(r"must_survive_extreme_window:\s*\"([^\"]+)\"", seg)
    g["extreme_window"] = m.group(1) if m else "2026-02"
    return g


def load_cost_from_yaml() -> Dict[str, Any]:
    """读 cost_model 段数值（只读，不改 yaml）；含 split-leg 字段。"""
    import re
    txt = PARAM_YAML.read_text(encoding="utf-8")
    i = txt.index("cost_model:")
    j = txt.find("# ---- 因子拥挤", i)
    seg = txt[i:(j if j > i else len(txt))]
    c: Dict[str, Any] = {}

    def _grab(pat: str, default: float) -> float:
        m = re.search(pat, seg)
        return float(m.group(1)) if m else default

    # 合约腿（G-4-② 主用）
    c["futures_taker_fee_pct"] = _grab(r"futures_leg:\s*\n\s*taker_fee_pct:\s*([\d.]+)", 0.0005)
    c["futures_maker_fee_pct"] = _grab(r"futures_leg:\s*\n\s*(?:taker_fee_pct:\s*[\d.]+\s*\n\s*)?maker_fee_pct:\s*([\d.]+)", 0.0002)
    # 现货腿（对照申报，数值不变 0.1%）
    c["spot_taker_fee_pct"] = _grab(r"spot_leg:\s*\n\s*taker_fee_pct:\s*([\d.]+)", 0.001)
    c["spot_maker_fee_pct"] = _grab(r"spot_leg:\s*\n\s*(?:taker_fee_pct:\s*[\d.]+\s*\n\s*)?maker_fee_pct:\s*([\d.]+)", 0.001)
    # 门槛 + 假设值
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
# 常量假设（沿用 G-3；「假设值」须标注：盘上无实测）
# ============================================================================

# taker 档滑点：G-3 冻结 3~5bp 区间保守假设取 4bp/边
TAKER_SLIP_BPS = 4.0
# maker 档滑点：G-3 假设值 0.5bp/边（读改后 yaml maker_slippage_bps_per_side）
# latency_1_3s：G-3 假设值 0.1bp
# 0.5x 抢跑折扣（分母，C 专家加严口径不变）
SNIPING_DISCOUNT = 0.5
# 8h 结算折算：4h bar 跨 1 个、1d bar 跨 3 个
SETTLE_PER_BAR = {"4h": 1.0, "1d": 3.0}
HOLD_BARS = 1
MIN_TRADES = 30
MAX_EXPOSURE_PER_COIN = 0.20
FUNDING_MUTATION_RUN = 3
FUNDING_CLEAR_RUN = 5
DD_WARN_PCT = 8.0
DUAL_NEG_DELEVER = True
# 年化（4h=√2190、1d=√365，src/core/timeframe.py 口径）
ANN_FACTOR = {"4h": np.sqrt(2190.0), "1d": np.sqrt(365.0)}


@dataclass
class FeeTier:
    name: str
    leg: str              # "futures" | "spot"
    fee_pct: float        # 单边费率
    slip_bps: float
    slip_note: str
    latency_pct: float
    latency_note: str

    def roundtrip_cost_pct(self) -> float:
        """单 bar roundtrip = fee×2 + slip×2 + latency（C 专家口径）"""
        slip_pct = self.slip_bps * 1e-4
        return self.fee_pct * 2 + slip_pct * 2 + self.latency_pct


def build_tiers(cost_cfg: Dict[str, Any]) -> Dict[str, FeeTier]:
    """G-4-②：16 组 = 2 个合约腿档（taker 0.05% / maker 0.02%）。
    现货腿（0.1%）仅保留在 json 对照申报区，不进 16 组主判定。"""
    lat = cost_cfg["latency_1_3s_pct"]
    lat_note = "假设值：1~3s 成交时延折算 0.1bp（G-3 假设值，盘上无实测）"
    ft_taker = cost_cfg["futures_taker_fee_pct"]
    ft_maker = cost_cfg["futures_maker_fee_pct"]
    mk_slip = cost_cfg.get("maker_slip_bps_per_side", 0.5)
    return {
        "futures_taker": FeeTier(
            "futures_taker", "futures", ft_taker, TAKER_SLIP_BPS,
            "taker 吃单 4bp/边（G-3 冻结保守假设，盘上无实测）",
            lat, lat_note),
        "futures_maker": FeeTier(
            "futures_maker", "futures", ft_maker, mk_slip,
            "maker 挂单被动成交 0.5bp/边（假设值、盘上无实测）",
            lat, lat_note),
    }


# ============================================================================
# M3 · FundingCarrySignal（复用 G-3 骨架，不改）
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


# ============================================================================
# funding 突变监控（B #2，与 G-3 一致）
# ============================================================================

def funding_mutation_state(funding_series: List[float], mean_abs: float
                           ) -> List[Dict[str, Any]]:
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
    extreme_pnl: Optional[float] = None
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

    if g.max_consecutive_losses > gate["max_consecutive_losses"]:
        g.risk_events.append(
            f"连亏熔断 {g.max_consecutive_losses}>{gate['max_consecutive_losses']}")

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

    net_return_pct = (eq_end / eq0 - 1) * 100
    g.net_return_pct = net_return_pct

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
    # G-4-②：cost_ok 门槛 = max_cost_as_pct_of_gross（改后 0.50）
    g.cost_ok = bool(trades and cost_ratio <= gate["max_cost_as_pct_of_gross"]
                     and not np.isinf(cost_ratio))

    eq = np.array(total_curve, dtype=float)
    if len(eq) > 1:
        peak = np.maximum.accumulate(eq)
        dd = (peak - eq) / np.where(peak > 0, peak, 1) * 100
        g.max_drawdown_pct = float(dd.max())
    else:
        g.max_drawdown_pct = 0.0

    # 夏普（口径同 G-3；0 成交组夏普=0，不脑补）
    if trades and len(eq) > 2:
        rets = np.diff(eq) / np.where(eq[:-1] > 0, eq[:-1], 1)
        g.sharpe = float(np.mean(rets) / np.std(rets) * ANN_FACTOR[g.interval]) \
            if np.std(rets) > 0 else 0.0
    else:
        g.sharpe = 0.0
    ann_ret = (net_return_pct / 100) * (365.0 / max(1, span_days))
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

    # 极端段（2026-02）→ 410d 未补、数据源缺口 → N/A（不计通过）
    g.extreme_na = True
    g.extreme_pnl = None


# ============================================================================
# 门槛判定（G-4-② 改后口径：夏普≥1.0、成本≤50%，其余维持原值）
# ============================================================================

def judge_gates(g: GroupResult, gate: Dict[str, Any]) -> Dict[str, Any]:
    gates = {
        "max_drawdown": bool(g.max_drawdown_pct <= gate["max_drawdown_pct"]),
        "sharpe": bool(g.sharpe >= gate["min_sharpe"]),   # G-4-② ≥1.0
        "calmar": bool(g.calmar >= gate["min_calmar"]),
        "cost_ratio": g.cost_ok,                           # G-4-② ≤50%
        "consec_losses": bool(g.max_consecutive_losses <= gate["max_consecutive_losses"]),
        "extreme_na": g.extreme_na,     # N/A(410d未补)，不计通过
        "min_trades": g.n_trades_min_ok,
    }
    gates["all_except_na"] = all(v for k, v in gates.items() if k != "extreme_na")
    gates["pass_final"] = gates["all_except_na"] and not g.extreme_na
    return gates


# ============================================================================
# 主流程
# ============================================================================

def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="G-4-② G1 carry 合约档+降门槛 33d 窗口重跑")
    ap.add_argument("--out", default=str(OUT_DIR / "g4_carry_rerun.json"))
    args = ap.parse_args()

    gate = load_gate_from_yaml()
    gate["max_cost_as_pct_of_gross"] = load_cost_from_yaml()["max_cost_as_pct_of_gross"]
    pfp = param_fingerprint()
    cost_cfg = load_cost_from_yaml()
    tiers = build_tiers(cost_cfg)
    print(f"[参数指纹] {pfp['file']} SHA256-16 = {pfp['sha256_16']}")
    print(f"[门槛(改后)] {gate}")
    print(f"[合约腿费率] taker={cost_cfg['futures_taker_fee_pct']}/边 "
          f"maker={cost_cfg['futures_maker_fee_pct']}/边")

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
                    g = run_group(coin, iv, tier, df, gate, tiers)
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

    manifest_path = ROOT / "data" / "cache" / "g1_carry_data" / "_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    passed = [g for g in results if g.gates.get("all_except_na")]
    passed_taker = [g for g in passed if g.tier == "futures_taker"]
    passed_maker = [g for g in passed if g.tier == "futures_maker"]

    summary = {
        "n_groups": len(results),
        "n_passed_except_na": len(passed),
        "n_passed_taker": len(passed_taker),
        "n_passed_maker": len(passed_maker),
        "n_passed_final": sum(1 for g in results if g.gates.get("pass_final")),
        "n_na_extreme": sum(1 for g in results if g.extreme_na),
        "note_extreme": ("极端段(2026-02) 410d 未补、数据源缺口（G-4-① 实测四大所 API 全 502）"
                          "→ 维持 N/A（数据源缺口），不计通过、不用 0 顶替（B #6 降级实盘监控承接）"),
        "note_min_trades": f"笔数下限 ≥{MIN_TRADES} 笔（B #7），<30 不计通过",
        "note_window": ("窗口口径：funding 实得 100 根/币（~33d，2026-10-10~2026-11-12），"
                        "实际交集 4h≈89~91 根 / 1d≈31 根；本轮不读 410d（没有）"),
        "note_short_side": "做空侧（负 carry）本轮不测",
        "note_1h": "1h 粒度不测（phase9 §六.2 已判 1h carry 不成立）",
        "note_sharpe_decision": ("老板明示「夏普率本就不要求过 1.0」→ 落地为「夏普≥1.0」"
                                 "（二选一取此项，非不设硬门槛）；yaml min_sharpe=1.0"),
        "note_continuity": ("开仓「持续」口径与 G-3 一致：fr>0 且 basis>0 连续 ≥3 bar；"
                            "33d 窗口内 fr∩basis 同正 bar 每币 0~1 根且孤立不连续 → 16 组 0 开仓"),
    }

    # G-3 对照口径（现货 0.1% 双档）：从 G-3 输出 json 读 16 组结果做对照
    g3_path = OUT_DIR / "g1_carry_basis_backtest.json"
    g3_payload: Dict[str, Any] = {}
    g3_fp: Optional[Dict[str, Any]] = None
    if g3_path.exists():
        g3_payload = json.loads(g3_path.read_text(encoding="utf-8"))
        g3_fp = g3_payload.get("param_fingerprint")
        print(f"[G-3 对照] {g3_path.name} 参数指纹 SHA256-16 = "
              f"{g3_fp.get('sha256_16') if g3_fp else 'N/A'}")

    payload = {
        "task": "G-4-② · G1 carry 合约档费率 + 降门槛 · ~33d 窗口重跑 16 组",
        "role": "后端架构师 落实，PM 验收后回报秘书",
        "date": "2026-11-12",
        "boss_directive": {
            "ts": "2026-11-12",
            "A_plan": "老板 A 方案指令：夏普不再要求过 1.0 + 成本 35%→50% + 合约档费率"
                      "（现货 0.1% / 合约 maker 0.02%·taker 0.05%）+ 33d 窗口重跑",
            "fact_quote": ("老板 2026-11-12 澄清关键事实（原样引用、不改写）："
                           "Gate.io VIP0 现货 taker=maker=0.1%/边；"
                           "合约（永续）maker=0.02%、taker=0.05%/边。"
                           "G-3 把合约腿误按现货 0.1% 算，成本算贵 2~5 倍，须按合约档重算。"),
            "decision": "不补 410d，先按合约档 + 降门槛在 ~33d funding 窗口重跑 16 组，"
                        "看合约档下有无组过线",
        },
        "param_fingerprint": pfp,
        "data_fingerprints": data_fps,
        "gate_after_change": gate,
        "gate_note": ("夏普：落地为「≥1.0」（非不设硬门槛）；成本：0.35→0.50；"
                      "回撤≤10%、Calmar≥1.5、连亏≤5、极端段(2026-02)维持原值不动"),
        "fee_split_declaration": {
            "note": "合约腿 0.02%/0.05% vs 现货 0.1% 两档分开申报，不再混用 0.1% 算合约腿",
            "spot_leg": {
                "taker_fee_pct": cost_cfg["spot_taker_fee_pct"],
                "maker_fee_pct": cost_cfg["spot_maker_fee_pct"],
                "roundtrip_pct": round((cost_cfg["spot_taker_fee_pct"] * 2), 6),
                "source": "老板 2026-11-12 澄清官方事实（现货 taker=maker=0.1%/边）",
            },
            "futures_leg": {
                "taker_fee_pct": cost_cfg["futures_taker_fee_pct"],
                "maker_fee_pct": cost_cfg["futures_maker_fee_pct"],
                "roundtrip_pct": {
                    "taker": round(cost_cfg["futures_taker_fee_pct"] * 2, 6),
                    "maker": round(cost_cfg["futures_maker_fee_pct"] * 2, 6),
                },
                "source": "老板 2026-11-12 澄清官方事实（永续 maker=0.02%、taker=0.05%/边）",
            },
            "assumed_values": {
                "taker_slip_bps_per_side": TAKER_SLIP_BPS,
                "maker_slip_bps_per_side": cost_cfg.get("maker_slip_bps_per_side", 0.5),
                "latency_1_3s_pct": cost_cfg["latency_1_3s_pct"],
                "note": "滑点/时延均为假设值（沿用 G-3），盘上无实测",
            },
        },
        "assumptions": {
            "futures_taker_fee_pct_per_side": cost_cfg["futures_taker_fee_pct"],
            "futures_maker_fee_pct_per_side": cost_cfg["futures_maker_fee_pct"],
            "fee_source": "老板 2026-11-12 口头澄清的 Gate.io VIP0 官方事实，原样引用、未改数值",
            "taker_slip_bps_per_side": TAKER_SLIP_BPS,
            "maker_slip_bps_per_side": cost_cfg.get("maker_slip_bps_per_side", 0.5),
            "slip_note": "假设值（G-3 冻结/估算值），盘上无实测",
            "latency_1_3s_pct": cost_cfg["latency_1_3s_pct"],
            "latency_note": "假设值（G-3），盘上无实测",
            "sniping_discount": SNIPING_DISCOUNT,
            "carry_formula": "carry_yield = funding_rate × position_dir(+1) × bars_held(1) × (8h结算折算: 4h→1, 1d→3)",
            "cost_ok_formula": f"cost_ok = (roundtrip_cost - carry_yield) ≤ {gate['max_cost_as_pct_of_gross']} × (0.5 × gross_pnl)",
            "sharpe_decision": summary["note_sharpe_decision"],
            "roundtrip_formula": "roundtrip_cost = fee×2 + slippage×2 + latency_1_3s",
        },
        "risk_preconditions": [
            f"#1 回撤熔断：{DD_WARN_PCT}%预警降敞口50%，{gate['max_drawdown_pct']:.0f}%停机",
            f"#2 funding突变：≥{FUNDING_MUTATION_RUN}期(24h)同号且|值|>2×均值→降敞口50%，"
            f"≥{FUNDING_CLEAR_RUN}期→清零（特别监控 LTC 正占比 53% / DOGE 72%）",
            f"#3 敞口上限 {MAX_EXPOSURE_PER_COIN:.0%}/币（4 币合计 ≤80%）",
            f"#4 连亏熔断 ≤{gate['max_consecutive_losses']}",
            "#5 资金曲线监控：carry<0 且 price<0 双负 → 当日强制减仓",
            "#6 极端段门槛降级：N/A(410d未补)，实盘监控承接",
            f"#7 笔数下限 ≥{MIN_TRADES} 笔（4h/1d 同限），<30 不计通过",
            f"#8 成本口径锁定：含 1~3s 时延 + 0.5x 抢跑折扣 + carry 翻转，≤{gate['max_cost_as_pct_of_gross']} 复算",
        ],
        "groups": [_to_dict(g) for g in results],
        "errors": errors,
        "g3_comparison": {
            "note": "合约腿 0.02%/0.05% vs 现货 0.1%：G-3 16 组全按现货 0.1% 算"
                    "（taker/maker 同价 0.1%），成本贵 2~5 倍；G-4-② 改合约档后逐组对照",
            "g3_param_sha256_16": g3_fp.get("sha256_16") if g3_fp else None,
            "g3_groups": g3_payload.get("groups", []),
            "diff_rows": [],  # main 里填充
        },
        "summary": summary,
        "data_manifest": manifest,
    }

    # 生成 G-3 对照差异行（同 coin×interval：taker→futures_taker、maker→futures_maker）
    g3_by_key = {(g["coin"], g["interval"], g["tier"]): g
                 for g in payload["g3_comparison"].get("g3_groups", [])}
    for g in results:
        g3_key = (g.coin, g.interval, "taker" if g.tier == "futures_taker" else "maker")
        g3g = g3_by_key.get(g3_key)
        if g3g is None:
            continue
        g3_rtrip = g3g.get("roundtrip_cost_total")
        g4_rtrip = g.roundtrip_cost_total
        diff = {
            "coin": g.coin, "interval": g.interval,
            "tier_g3": "现货0.1%(" + g3g["tier"] + ")",
            "tier_g4": f"合约{'0.05%' if g.tier == 'futures_taker' else '0.02%'}({g.tier})",
            "g3_roundtrip": g3_rtrip, "g4_roundtrip": g4_rtrip,
            "g3_cost_ok": g3g.get("cost_ok"), "g4_cost_ok": g.cost_ok,
            "g3_sharpe": g3g.get("sharpe"), "g4_sharpe": g.sharpe,
            "g3_pass_except_na": g3g.get("gates", {}).get("all_except_na"),
            "g4_pass_except_na": g.gates.get("all_except_na"),
            "flip_reason": _flip_reason(g, g3g, gate),
        }
        payload["g3_comparison"]["diff_rows"].append(diff)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str),
                   encoding="utf-8")
    md_path = out.with_suffix(".md")
    md_path.write_text(render_md(payload), encoding="utf-8")
    print(f"\n[JSON] → {out}")
    print(f"[MD]   → {md_path}")
    print(f"通过(除N/A) {len(passed)}/{len(results)}（taker {len(passed_taker)} / "
          f"maker {len(passed_maker)}）；N/A极端段 {summary['n_na_extreme']} 组；"
          f"errors {len(errors)}")
    return 0


def _flip_reason(g: GroupResult, g3g: Dict[str, Any], gate: Dict[str, Any]) -> str:
    """对照 G-3 现货 0.1% 口径，判断 G-4-② 合约档 + 降门槛下的翻盘/仍不过原因。"""
    if g3g.get("n_trades", 0) == 0 and g.n_trades == 0:
        return "两组均 0 成交（33d 窗口 fr∩basis 同正 bar 0~1 根孤立）→ 均不过，费率档位不影响"
    reasons: List[str] = []
    if g3g.get("cost_ok") is False and g.cost_ok:
        reasons.append(f"cost_ok 翻盘：G-3 现货 0.1% roundtrip 贵 "
                       f"{g3g.get('roundtrip_cost_total', 0) - g.roundtrip_cost_total:.4f}"
                       f"，G-4 合约档 + 50% 门槛翻正")
    if g3g.get("gates", {}).get("sharpe", False) is False and g.gates.get("sharpe", False):
        reasons.append("夏普门槛翻盘：≥2.0→≥1.0")
    return "；".join(reasons) if reasons else "费率/门槛变化下仍不过（见差异表各行）"


# ============================================================================
# 报告
# ============================================================================

def render_md(payload: Dict[str, Any]) -> str:
    groups: List[Dict[str, Any]] = payload["groups"]
    s = payload["summary"]
    a = payload["assumptions"]
    gate = payload["gate_after_change"]
    fee = payload["fee_split_declaration"]
    cmp_ = payload["g3_comparison"]

    lines: List[str] = []
    lines.append("# G-4-② · G1 carry 合约档费率 + 降门槛 · ~33d 窗口重跑 16 组报告")
    lines.append("")
    lines.append(f"> 角色：{payload['role']}")
    lines.append(f"> 日期：{payload['date']}")
    lines.append(f"> 参数指纹：`config/strategy_params.yaml` "
                 f"SHA256-16 = `{payload['param_fingerprint']['sha256_16']}`（改后门槛 + 分腿费率）")
    lines.append(f"> G-3 对照指纹：SHA256-16 = `{cmp_.get('g3_param_sha256_16')}`")
    lines.append("")
    lines.append("## 0. 老板指令（原样引用）")
    lines.append("")
    lines.append(f"> {payload['boss_directive']['fact_quote']}")
    lines.append(f"> **{payload['boss_directive']['A_plan']}**")
    lines.append(f"> 拍板：{payload['boss_directive']['decision']}")
    lines.append("")
    lines.append("## 1. 数据窗口声明（如实申报，不脑补）")
    lines.append("")
    lines.append(f"- {s['note_window']}")
    lines.append(f"- {s['note_extreme']}")
    lines.append(f"- {s['note_short_side']}；{s['note_1h']}")
    lines.append(f"- {s['note_continuity']}")
    lines.append(f"- 组数：4 币（BTC/ETH/DOGE/LTC）× 2 粒度（4h/1d）× 2 合约腿费率档"
                 f"（futures_taker / futures_maker）= **16 组**")
    lines.append("")
    lines.append("## 2. 费率分腿申报（合约腿 0.02%/0.05% vs 现货 0.1%，分开、不混用）")
    lines.append("")
    lines.append("| 腿 | taker/边 | maker/边 | roundtrip | 来源 |")
    lines.append("|---|---|---|---|---|")
    lines.append(f"| 现货腿（对照） | {fee['spot_leg']['taker_fee_pct']:.4f} | "
                 f"{fee['spot_leg']['maker_fee_pct']:.4f} | "
                 f"{fee['spot_leg']['roundtrip_pct']:.4%} | "
                 f"老板 2026-11-12 澄清（现货 0.1%/边） |")
    lines.append(f"| 合约腿 taker（G-4-② 主判） | "
                 f"{fee['futures_leg']['taker_fee_pct']:.4f} | — | "
                 f"{fee['futures_leg']['roundtrip_pct']['taker']:.4%} | "
                 f"老板 2026-11-12 澄清（永续 taker 0.05%/边） |")
    lines.append(f"| 合约腿 maker（G-4-② 主判） | — | "
                 f"{fee['futures_leg']['maker_fee_pct']:.4f} | "
                 f"{fee['futures_leg']['roundtrip_pct']['maker']:.4%} | "
                 f"老板 2026-11-12 澄清（永续 maker 0.02%/边） |")
    lines.append("")
    lines.append(f"- 假设值（盘上无实测）：taker 滑点 {a['taker_slip_bps_per_side']}bp/边、"
                 f"maker 滑点 {a['maker_slip_bps_per_side']}bp/边（沿用 G-3）、"
                 f"latency_1_3s={a['latency_1_3s_pct']}（G-3 假设值）")
    lines.append(f"- {a['fee_source']}")
    lines.append("")
    lines.append("## 3. 门槛判定口径（改后）")
    lines.append("")
    lines.append(f"- 夏普：**≥{gate['min_sharpe']}**（{a['sharpe_decision']}）")
    lines.append(f"- 成本：net_cost ≤ {gate['max_cost_as_pct_of_gross']} × 0.5×毛利"
                 f"（0.35→{gate['max_cost_as_pct_of_gross']}）")
    lines.append(f"- 其余 4 条维持原值：回撤≤{gate['max_drawdown_pct']}% / "
                 f"Calmar≥{gate['min_calmar']} / 连亏≤{gate['max_consecutive_losses']} / "
                 f"极端段({gate['extreme_window']})不亏（本轮 N/A）")
    lines.append(f"- {s['note_min_trades']}")
    lines.append("")
    lines.append("## 4. 全量 16 组结果（合约腿 taker/maker 双档）")
    lines.append("")
    lines.append("| 币 | 粒度 | 档 | bar | 天数 | 开仓 | 成交 | 净收益% | 毛利 | 0.5x毛利 | carry | roundtrip | net_cost | 成本/0.5x毛利 | cost_ok | 回撤% | 夏普 | Calmar | 连亏 | 极端段 | 通过(除N/A) |")
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
    lines.append("## 5. 门槛 6 条判定（改后口径）")
    lines.append("")
    lines.append("| 门槛 | 16 组达成数 | 说明 |")
    lines.append("|---|---|---|")
    lines.append(f"| 回撤≤{gate['max_drawdown_pct']:.0f}% | {sum(1 for g in groups if g['gates']['max_drawdown'])} | 维持原值 |")
    lines.append(f"| 夏普≥{gate['min_sharpe']} | {sum(1 for g in groups if g['gates']['sharpe'])} | G-4-② 降为≥1.0；0 成交组夏普=0 |")
    lines.append(f"| Calmar≥{gate['min_calmar']} | {sum(1 for g in groups if g['gates']['calmar'])} | 维持原值 |")
    lines.append(f"| 成本≤{gate['max_cost_as_pct_of_gross']}×0.5×毛利 | {sum(1 for g in groups if g['gates']['cost_ratio'])} | G-4-② 0.35→{gate['max_cost_as_pct_of_gross']} |")
    lines.append(f"| 连亏≤{gate['max_consecutive_losses']} | {sum(1 for g in groups if g['gates']['consec_losses'])} | 维持原值 |")
    lines.append(f"| 极端段({gate['extreme_window']})不亏 | N/A×{s['n_na_extreme']} | 410d 未补、数据源缺口 → **N/A 不当通过** |")
    lines.append(f"| 笔数≥{MIN_TRADES} | {sum(1 for g in groups if g['gates']['min_trades'])} | B #7 仍生效 |")
    lines.append("")
    lines.append(f"**通过（除 N/A 极端段外全达成）：{s['n_passed_except_na']}/{s['n_groups']} 组**"
                 f"（taker {s['n_passed_taker']} / maker {s['n_passed_maker']}）；"
                 f"**最终通过（N/A 不当通过）：{s['n_passed_final']}/{s['n_groups']} 组**")
    lines.append("")
    lines.append("## 6. 与 G-3 现货 0.1% 口径对照表")
    lines.append("")
    lines.append(f"{cmp_['note']}")
    lines.append("")
    lines.append("| 币 | 粒度 | G-3 档 | G-4 档 | G-3 roundtrip | G-4 roundtrip | G-3 cost_ok | G-4 cost_ok | G-3 夏普 | G-4 夏普 | G-3 通过 | G-4 通过 | 差异说明 |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for d in cmp_.get("diff_rows", []):
        lines.append(
            f"| {d['coin']} | {d['interval']} | {d['tier_g3']} | {d['tier_g4']} "
            f"| {d['g3_roundtrip']} | {d['g4_roundtrip']} "
            f"| {'✅' if d['g3_cost_ok'] else '❌'} | {'✅' if d['g4_cost_ok'] else '❌'} "
            f"| {d['g3_sharpe']} | {d['g4_sharpe']} "
            f"| {'✅' if d['g3_pass_except_na'] else '❌'} | {'✅' if d['g4_pass_except_na'] else '❌'} "
            f"| {d['flip_reason']} |")
    lines.append("")
    lines.append("## 7. B 专家 8 项风控前置落地（沿用 G-3，未改）")
    lines.append("")
    for r in payload["risk_preconditions"]:
        lines.append(f"- {r}")
    lines.append("")
    lines.append("## 8. 极端段 N/A 声明（硬性）")
    lines.append("")
    lines.append(f"- {s['note_extreme']}")
    lines.append("- 上实盘后由实盘监控承接：前 30 天逐 8h funding + 回撤熔断（B #6）。")
    lines.append("")
    lines.append("## 9. 结论与如实申报")
    lines.append("")
    lines.append(f"- **达标数（除 N/A 外全达成）：{s['n_passed_except_na']}/{s['n_groups']}**；"
                 f"最终通过（N/A 不当通过）：{s['n_passed_final']}/{s['n_groups']}。")
    lines.append(f"- taker 0.05% 档过线 {s['n_passed_taker']} 组；maker 0.02% 档过线 {s['n_passed_maker']} 组。")
    lines.append("- 如实申报：若合约档 + 降门槛下仍 0 达标，主因是 33d 窗口 0 成交"
                 "（开仓条件触发 0 根），非费率/门槛项；翻盘与否须逐组看 §6 差异表。")
    lines.append("- 未引用作废 0.473、未把 N/A 当通过、未启用实盘、未提交 Git、无令牌/密钥写入。")
    lines.append("- M3/M4 信号逻辑骨架未改（与 G-3 build_signals/funding_mutation_state/"
                 "run_group 逐行一致），只改费率档 + 门槛数值。")
    lines.append("")
    lines.append("## 10. 数据 / 假设清单")
    lines.append("")
    lines.append(f"- 数据源：现货 4h/1d parquet（{', '.join(sorted(payload['data_fingerprints'].keys()))}），"
                 "funding 实得 100 根/币（~33d）；不读 410d（没有）")
    lines.append(f"- 假设值（盘上无实测，须标注）：maker 滑点 {a['maker_slip_bps_per_side']}bp/边；"
                 f"taker 滑点 {a['taker_slip_bps_per_side']}bp/边；"
                 f"时延 latency_1_3s={a['latency_1_3s_pct']}")
    lines.append(f"- 参数指纹（改后）：{payload['param_fingerprint']['file']} = "
                 f"{payload['param_fingerprint']['sha256']}")
    lines.append(f"- 数据指纹：见 JSON data_fingerprints（8 组 carry parquet SHA256-16）"
                 "+ data/cache/g1_carry_data/_manifest.json")
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
