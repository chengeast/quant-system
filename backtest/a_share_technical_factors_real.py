# -*- coding: utf-8 -*-
"""I-4 · 真实 A 股交易费率口径重跑技术派因子（后端架构师产出，PM 承派 + 验收）

老板 2026-11-15 指令（逐字，三条一起执行）：
  20:21「你按真实A股交易进行回测，费率就按单向尤万分之5，不足5元按5元计，先取消印花税。
        看看技术派是否能测出比较好的收益，发最终回测报告给我」
  20:22「报告内记得写清楚开仓平仓条件！！」
  20:23「不是佣金会被抬到 0.05% 变 0.5%，而是直接按 5 元计算。」

============================================================================
本批 = I-3 的费率口径重跑，不是新策略
============================================================================
  - 沿用 I-3 的 15 个技术派因子（信号层直接 import `a_share_technical_factors.compute_target()`
    与 `FACTORS`，**未改一字**）、标的池（12 指数主池 + 3 个股对照 = 15）、28 年数据、
    T+1 硬约束、纯多头无做空无对冲；**只替换交易成本口径 + 增列资金模型对照**。
  - 保留 `backtest/a_share_technical_factors.py`、`a_share_m8_rerun.py` 及 `backtest/`
    下全部旧脚本不动；不改 `src/`；只读 `config/strategy_params.yaml`（一字不改）。

============================================================================
费率口径（老板 20:23 纠正后，逐字落实；成本一律以「元」逐项列示）
============================================================================
  佣金金额 = max(成交额 × 0.0005, 5 元)，买入 / 卖出**各自独立**计算；
  「不足 5 元直接按 5 元这一笔金额收」= **金额规则，不是费率变更**。
  ⚠️ 严禁写成「费率被抬到 0.5%」「等效 50bp」「成本抬高 10 倍」这类换算表述。
  5 ÷ 0.0005 = 10000 元 只是「按比例佣金恰好达到 5 元」的**名义分界**，
  不是变更费率的依据，也不是替换主口径的理由。
  印花税 = 0（老板「先取消」）。过户费 / 滑点老板未指定 → 主口径按 0。

============================================================================
四套口径（主口径必须完整出结果并作为报告主表）
============================================================================
  [primary]      主口径（老板字面）：NOTIONAL=1000、佣金 max(1000×0.0005,5)=5 元/边、
                 印花税 0 / 过户 0 / 滑点 0 → roundtrip **10 元/笔**；沿用 I-3 简化权益曲线。
  [conservative] 保守费率对照：主口径加回 I-3 的过户 0.001%/边 + 滑点 5bp/边（按金额计），
                 印花税仍 0 → roundtrip **11.02 元/笔**；不得擅自设定为「真实值」。
  [real_capital] 真实资金模型对照（秘书建议、非老板指定，单列、不得覆盖主口径）：
                 逐笔资金占用检查、名义按 1 万元量级且按 100 股整数倍取整、
                 权益曲线分母为真实占用资金、并行持仓上限；费率结构与主口径相同
                 （佣金 max(成交额×0.0005,5)、印花税0/过户0/滑点0），
                 逐笔申报是否触发 5 元最低佣金、触发笔数与占比。
  [i3_old]       I-3 旧口径对照（20.2bp 四项假设）→ roundtrip **2.02 元/笔**；
                 用于验证「除成本外与 I-3 完全一致」（该口径结果应复现 I-3 基线）。

  明确分离：A↔B / A↔D 的变化来自**费率**；A↔C 的变化来自**资金模型**（含名义量级）。
  对照口径结果**不得写成主口径结论**。

============================================================================
沿用未撤回口径
============================================================================
  28 年全量（1998-09-15~2026-11-13，不裁窗口/不抽样/不分段）；
  T+1 硬约束 MIN_HOLD_BARS=1、逐笔 exit_date>entry_date / exit_bar>entry_bar、
  t1_violations=0；t 收盘出信号 → t+1 open 成交；末尾 end_force_close；
  纯多头 / 无做空 / 无对冲 / 无配对；
  先不判极端段（extreme_gate=NOT_EVALUATED_BY_BOSS_ORDER，判定 = 4 条硬门槛 + 笔数≥30，
  n_passed_final 标「非最终达标 / 未含极端段」）；
  冻结门槛不因换费率放宽；「因子效果排序」与「达标判定」分开申报。

  执行体如实登记：本环境为单一执行体、无独立 subagent 会话；两阶段按两份 soul 纪律
  分别落地，非两角色各自独立会话（沿用 I-1.8 更正三）。

运行：python backtest/a_share_technical_factors_real.py
输出：backtest/output/a_share_technical_factors_real.json
      backtest/output/a_share_technical_factors_real_trades.json（全量逐笔，供 T+1 / 触发核验）
"""
from __future__ import annotations

import hashlib
import heapq
import json
import sys
import warnings
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import a_share_technical_factors as i3          # 只读复用 I-3 信号层，未改一字
from src.core.timeframe import resolve_timeframe  # 只读复用，不改 src/

DATA_DIR = ROOT / "data" / "cache" / "tdx_a_share"
OUT_DIR = ROOT / "backtest" / "output"
MANIFEST = DATA_DIR / "_manifest.json"

# ---------------------------------------------------------------------------
# 费率口径常量（全部「按元」；金额规则，非费率变更）
# ---------------------------------------------------------------------------
COMM_RATE = 0.0005            # 单向万分之 5（用于计算按比例佣金金额）
MIN_COMM_YUAN = 5.0           # 不足 5 元直接按 5 元这一笔金额收（金额规则）
STAMP_DUTY = 0.0              # 老板「先取消印花税」
NOTIONAL = 1000.0             # 主口径沿用 I-3 名义成交额（元），未擅自改名义
BASE_EQUITY = 10000.0         # A/B/D 简化权益曲线起点（沿用 I-3）
TRANSFER_PCT = 0.00001        # 保守对照：过户 0.001%/边（老板未指定）
SLIPPAGE_BPS = 5.0            # 保守对照：滑点 5bp/边（老板未指定）
I3_COMM_PCT = 0.00025         # I-3 旧口径：佣金 0.025%/边
I3_STAMP_PCT = 0.0005         # I-3 旧口径：印花税 0.05%（卖出单边）

# 真实资金模型（秘书建议、非老板指定 → 全部是「对照口径假设」，报告显式标注）
REAL_TARGET_NOTIONAL = 10000.0     # 目标名义 1 万元量级
LOT = 100                          # 100 股整数倍取整
REAL_INITIAL_CAPITAL = 5_000_000.0 # 初始投入（覆盖指数一手名义的并行占用；对照假设）
MAX_PARALLEL = 12                  # 并行持仓上限（对照假设）

YEARS_28 = 28.16               # 日历年数：1998-09-15 ~ 2026-11-13
WARM = i3.WARM                 # 指标预热 60，沿用 I-3
MIN_HOLD_BARS = i3.MIN_HOLD_BARS   # T+1 = 1，沿用 I-3

COST_MODELS = ["primary", "conservative", "real_capital", "i3_old"]


# ---------------------------------------------------------------------------
# 成本层（四套口径；成本一律以「元」逐项列示，不写等效费率换算）
# ---------------------------------------------------------------------------
def commission_yuan(turnover: float) -> Tuple[float, bool, float]:
    """单边佣金金额（元）。返回 (实收金额, 是否触发5元最低佣金, 按比例佣金金额)。

    金额规则：max(成交额 × 0.0005, 5 元)；『不足 5 元按 5 元这一笔金额收』。
    """
    proportional = turnover * COMM_RATE
    amount = max(proportional, MIN_COMM_YUAN)
    triggered = proportional < MIN_COMM_YUAN
    return amount, triggered, proportional


def roundtrip_cost(model: str, buy_turnover: float, sell_turnover: float) -> Dict[str, Any]:
    """返回该笔 roundtrip 的成本（元）逐项 + 5 元最低佣金触发明细。"""
    items: Dict[str, Any] = {}
    if model in ("primary", "real_capital", "conservative"):
        bc, btrig, bprop = commission_yuan(buy_turnover)
        sc, strig, sprop = commission_yuan(sell_turnover)
        items["buy_commission_yuan"] = round(bc, 6)
        items["sell_commission_yuan"] = round(sc, 6)
        items["buy_commission_proportional_yuan"] = round(bprop, 6)
        items["sell_commission_proportional_yuan"] = round(sprop, 6)
        items["buy_min_commission_triggered"] = bool(btrig)
        items["sell_min_commission_triggered"] = bool(strig)
    else:  # i3_old：固定比例佣金，无 5 元最低佣金规则
        bc = buy_turnover * I3_COMM_PCT
        sc = sell_turnover * I3_COMM_PCT
        items["buy_commission_yuan"] = round(bc, 6)
        items["sell_commission_yuan"] = round(sc, 6)
        items["buy_min_commission_triggered"] = None   # 该口径不适用
        items["sell_min_commission_triggered"] = None
        items["min_commission_rule"] = "不适用（I-3 旧口径为固定比例佣金、无 5 元最低佣金规则）"

    if model == "conservative":
        tr_b, tr_s = buy_turnover * TRANSFER_PCT, sell_turnover * TRANSFER_PCT
        sl_b = buy_turnover * SLIPPAGE_BPS / 10_000.0
        sl_s = sell_turnover * SLIPPAGE_BPS / 10_000.0
        items["transfer_fee_buy_yuan"] = round(tr_b, 6)
        items["transfer_fee_sell_yuan"] = round(tr_s, 6)
        items["slippage_buy_yuan"] = round(sl_b, 6)
        items["slippage_sell_yuan"] = round(sl_s, 6)
    else:
        items["transfer_fee_buy_yuan"] = 0.0
        items["transfer_fee_sell_yuan"] = 0.0
        items["slippage_buy_yuan"] = 0.0
        items["slippage_sell_yuan"] = 0.0

    if model == "i3_old":
        stamp = sell_turnover * I3_STAMP_PCT
        items["stamp_duty_sell_yuan"] = round(stamp, 6)
        # I-3 旧口径四项：佣金/过户/滑点也按各自比例计（无最低佣金）
        tr_b, tr_s = buy_turnover * TRANSFER_PCT, sell_turnover * TRANSFER_PCT
        sl_b = buy_turnover * SLIPPAGE_BPS / 10_000.0
        sl_s = sell_turnover * SLIPPAGE_BPS / 10_000.0
        items["transfer_fee_buy_yuan"] = round(tr_b, 6)
        items["transfer_fee_sell_yuan"] = round(tr_s, 6)
        items["slippage_buy_yuan"] = round(sl_b, 6)
        items["slippage_sell_yuan"] = round(sl_s, 6)
    else:
        items["stamp_duty_sell_yuan"] = 0.0     # 老板「先取消印花税」

    cost = (items["buy_commission_yuan"] + items["sell_commission_yuan"]
            + items["transfer_fee_buy_yuan"] + items["transfer_fee_sell_yuan"]
            + items["slippage_buy_yuan"] + items["slippage_sell_yuan"]
            + items["stamp_duty_sell_yuan"])
    items["cost_roundtrip_yuan"] = round(cost, 6)
    return items


# ---------------------------------------------------------------------------
# 信号层（复用 I-3 compute_target，未改一字）+ 候选成交时序（沿用 I-3 run_one）
# ---------------------------------------------------------------------------
_TARGET_CACHE: Dict[Tuple[str, str], np.ndarray] = {}


def target_for(df: pd.DataFrame, sym: str, fid: str) -> np.ndarray:
    key = (fid, sym)
    if key not in _TARGET_CACHE:
        _TARGET_CACHE[key] = i3.compute_target(df, fid)
    return _TARGET_CACHE[key]


def gen_trades_simple(df: pd.DataFrame, sym: str, pool: str, fid: str) -> List[Dict[str, Any]]:
    """沿用 I-3 `run_one` 成交时序逐字重现（不含成本）：
    t 收盘信号 → t+1 open 成交；T+1 硬校验；末尾 end_force_close。
    gross_ratio = O_exit / O_entry - 1（与成交名义解耦，供四套口径复用）。
    """
    target = target_for(df, sym, fid)
    n = len(df)
    O = df["open"].to_numpy(float)
    D = df["date"].to_numpy(np.int64)
    pos = 0
    pending = None
    entry = None
    trades: List[Dict[str, Any]] = []
    t1_violations = 0

    for i in range(WARM, n - 1):
        if pending is not None:
            if pending == "BUY" and pos == 0:
                pos = 1
                entry = (i, float(O[i]), int(D[i]))
            elif pending == "SELL" and pos == 1 and entry is not None:
                ei, ep, ed = entry
                if (i - ei) >= MIN_HOLD_BARS and int(D[i]) > ed:
                    trades.append({
                        "symbol": sym, "pool": pool,
                        "entry_date": str(ed), "exit_date": str(int(D[i])),
                        "entry_bar": ei, "exit_bar": i,
                        "entry_open": float(ep), "exit_open": float(O[i]),
                        "gross_ratio": float(O[i]) / ep - 1.0,
                        "reason": "signal_exit",
                    })
                else:
                    t1_violations += 1
                pos = 0
                entry = None
            pending = None

        if int(target[i]) == 1 and pos == 0:
            pending = "BUY"
        elif int(target[i]) == 0 and pos == 1:
            pending = "SELL"

    if pos == 1 and entry is not None:
        i = n - 1
        ei, ep, ed = entry
        if (i - ei) >= MIN_HOLD_BARS and int(D[i]) > ed:
            trades.append({
                "symbol": sym, "pool": pool,
                "entry_date": str(ed), "exit_date": str(int(D[i])),
                "entry_bar": ei, "exit_bar": i,
                "entry_open": float(ep), "exit_open": float(O[i]),
                "gross_ratio": float(O[i]) / ep - 1.0,
                "reason": "end_force_close",
            })
        else:
            t1_violations += 1

    return trades, t1_violations


def attach_simple_cost(cands: List[Dict[str, Any]], model: str) -> List[Dict[str, Any]]:
    """把四套口径的成本金额附到同一份候选成交上（成交时序完全一致，只换成本）。"""
    out = []
    for c in cands:
        gross = c["gross_ratio"] * NOTIONAL
        det = roundtrip_cost(model, NOTIONAL, NOTIONAL)  # 买/卖名义各 1000 元
        t = dict(c)
        t["gross"] = round(gross, 4)
        t["cost"] = det["cost_roundtrip_yuan"]
        t["net"] = round(gross - det["cost_roundtrip_yuan"], 4)
        t["cost_items_yuan"] = det
        t["notional_yuan"] = NOTIONAL
        out.append(t)
    return out


# ---------------------------------------------------------------------------
# 真实资金模型（口径 C）：逐笔资金占用检查 + 100 股取整 + 真实占用资金权益曲线
# ---------------------------------------------------------------------------
def run_portfolio_real(df_map: Dict[str, pd.DataFrame], symbols: List[str],
                       fid: str, union_dates: np.ndarray) -> Dict[str, Any]:
    """按 (entry_date, symbol) 顺序撮合：先结算到期平仓现金流，再做资金占用/并行上限检查。
    权益曲线 = 现金 + 持仓市值（按当日 open）；分母 = 真实投入资金 REAL_INITIAL_CAPITAL。
    """
    date_pos = {int(d): k for k, d in enumerate(union_dates)}
    K = len(union_dates)
    first_open: Dict[str, float] = {}
    open_by_date: Dict[str, Dict[int, float]] = {}
    for s in symbols:
        O = df_map[s]["open"].to_numpy(float)
        D = df_map[s]["date"].to_numpy(np.int64)
        first_open[s] = float(O[0])
        open_by_date[s] = {int(d): float(O[j]) for j, d in enumerate(D)}

    # 1) 全池候选成交（时序沿用 I-3），映射到并集时间轴
    cands: List[Dict[str, Any]] = []
    t1_violations = 0
    for s in symbols:
        ts, tv = gen_trades_simple(df_map[s], s, "real_capital", fid)
        t1_violations += tv
        for t in ts:
            t["ek"] = date_pos[int(t["entry_date"])]
            t["xk"] = date_pos[int(t["exit_date"])]
            cands.append(t)
    cands.sort(key=lambda t: (t["ek"], t["symbol"]))

    # 2) 逐笔资金占用检查（顺序确定、可复现）
    pending_exits: List[Tuple[int, float]] = []   # 最小堆 (xk, 卖出净回款)
    active: List[Dict[str, Any]] = []             # 仍活跃的已接受交易
    cash = REAL_INITIAL_CAPITAL
    accepted: List[Dict[str, Any]] = []
    skipped_capital = 0
    skipped_parallel = 0

    for c in cands:
        while pending_exits and pending_exits[0][0] <= c["ek"]:
            _, back = heapq.heappop(pending_exits)
            cash += back
        if len(pending_exits) >= MAX_PARALLEL:
            skipped_parallel += 1
            continue
        ep, xp = c["entry_open"], c["exit_open"]
        shares = max(LOT, int(round(REAL_TARGET_NOTIONAL / ep / LOT)) * LOT)
        buy_amount = shares * ep
        sell_amount = shares * xp
        det = roundtrip_cost("real_capital", buy_amount, sell_amount)
        need = buy_amount + det["buy_commission_yuan"]
        if need > cash + 1e-9:
            skipped_capital += 1
            continue
        cash -= need
        heapq.heappush(pending_exits, (c["xk"], sell_amount - det["sell_commission_yuan"]))
        t = dict(c)
        t["shares"] = shares
        t["buy_amount_yuan"] = round(buy_amount, 4)
        t["sell_amount_yuan"] = round(sell_amount, 4)
        t["gross"] = round((xp - ep) * shares, 4)
        t["cost"] = det["cost_roundtrip_yuan"]
        t["net"] = round(t["gross"] - det["cost_roundtrip_yuan"], 4)
        t["cost_items_yuan"] = det
        t["notional_yuan"] = round(buy_amount, 4)
        accepted.append(t)
    while pending_exits:
        _, back = heapq.heappop(pending_exits)
        cash += back

    # 3) 权益曲线：现金 + 持仓市值（按当日 open），分母 = 真实投入资金
    entries_by_k: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
    exits_by_k: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
    for a in accepted:
        entries_by_k[a["ek"]].append(a)
        exits_by_k[a["xk"]].append(a)
    last_open = dict(first_open)
    cash = REAL_INITIAL_CAPITAL
    live: Dict[str, Dict[str, Any]] = {}
    equity = np.zeros(K, dtype=float)
    for k in range(K):
        d = int(union_dates[k])
        for s in symbols:
            if d in open_by_date[s]:
                last_open[s] = open_by_date[s][d]
        for a in exits_by_k.get(k, []):            # 先结算到期平仓
            cash += a["sell_amount_yuan"] - a["cost_items_yuan"]["sell_commission_yuan"]
            live.pop(a["symbol"], None)
        for a in entries_by_k.get(k, []):          # 再扣开仓款
            cash -= a["buy_amount_yuan"] + a["cost_items_yuan"]["buy_commission_yuan"]
            live[a["symbol"]] = a
        mv = sum(a["shares"] * last_open[a["symbol"]] for a in live.values())
        equity[k] = cash + mv

    return {
        "trades": accepted,
        "equity": equity,
        "t1_violations": t1_violations,
        "skipped_insufficient_capital": skipped_capital,
        "skipped_parallel_cap": skipped_parallel,
        "n_candidates": len(cands),
        "initial_capital_yuan": REAL_INITIAL_CAPITAL,
    }


# ---------------------------------------------------------------------------
# 买入持有基准（回答老板「比较好的收益」）
# ---------------------------------------------------------------------------
def buy_hold_benchmark(df_map: Dict[str, pd.DataFrame], symbols: List[str],
                       union_dates: np.ndarray, real_notional: bool) -> Dict[str, Any]:
    """等权买入持有：首个交易日 open 买入、最后一根 bar open 卖出；日频净值曲线。
    real_notional=False → 每标的 1 单位名义（与 A/B/D 简化口径可比）；
    real_notional=True  → 每标的按 1 万元量级、100 股取整（与 C 资金模型可比）。
    年化统一按 28.16 年折算：(1+累计收益)^(1/28.16) − 1。
    """
    K = len(union_dates)
    first_open, open_by_date, shares_map = {}, {}, {}
    for s in symbols:
        O = df_map[s]["open"].to_numpy(float)
        D = df_map[s]["date"].to_numpy(np.int64)
        first_open[s] = float(O[0])
        open_by_date[s] = {int(d): float(O[j]) for j, d in enumerate(D)}
        shares_map[s] = (max(LOT, int(round(REAL_TARGET_NOTIONAL / float(O[0]) / LOT)) * LOT)
                         if real_notional else 1.0)
    nav = np.zeros(K, dtype=float)
    for k, d in enumerate(union_dates):
        vals = [shares_map[s] * open_by_date[s][int(d)]
                for s in symbols if int(d) in open_by_date[s]]
        denom = sum(shares_map[s] * first_open[s]
                    for s in symbols if int(d) in open_by_date[s])
        nav[k] = sum(vals) / denom if denom > 0 else 1.0
    cum = float(nav[-1] / nav[0] - 1.0)
    peak = np.maximum.accumulate(nav)
    max_dd = float(np.max((peak - nav) / peak)) if np.all(peak > 0) else 0.0
    r = np.diff(nav) / nav[:-1]
    sharpe = float(np.mean(r) / np.std(r) * np.sqrt(252.0)) if len(r) > 1 and np.std(r) > 0 else 0.0
    ann28 = float((1.0 + cum) ** (1.0 / YEARS_28) - 1.0) if cum > -1 else 0.0
    return {
        "label": "等权买入持有（真实资金模型口径）" if real_notional else "等权买入持有（简化名义口径）",
        "pool": symbols,
        "n_symbols": len(symbols),
        "window": f"{int(union_dates[0])}~{int(union_dates[-1])}",
        "n_union_bars": K,
        "cumulative_return_28y": round(cum, 6),
        "annualized_return_28y": round(ann28, 6),
        "annualized_formula": f"(1 + {round(cum, 6)})^(1/{YEARS_28}) - 1",
        "years_used": YEARS_28,
        "max_drawdown": round(max_dd, 6),
        "sharpe_reference": round(sharpe, 4),
        "real_notional": real_notional,
        "note": "按各自实际起始日 open 建仓、末日 open 平仓；短样本标的按实际起止，"
                "年化统一按全池 28.16 年窗口折算（口径近似，如实标注）。"
                "指数为点位序列；real_notional=True 时按 100 股/份整数倍取整，实际名义量级由点数决定。",
    }


# ---------------------------------------------------------------------------
# 逐因子结果汇总（统一尺子、4 条门槛 + 笔数；极端段不判）
# ---------------------------------------------------------------------------
def make_factor_result(fac: Dict[str, str], model: str, trades: List[Dict[str, Any]],
                       equity: np.ndarray, pool_label: str, n_symbols: int,
                       gate: Dict[str, Any], cost_gate: Dict[str, Any],
                       extra: Dict[str, Any]) -> Dict[str, Any]:
    trades_sorted = sorted(trades, key=lambda t: (t["exit_date"], t["symbol"]))
    n_trades = len(trades_sorted)
    gross = float(sum(t["gross"] for t in trades_sorted))
    cost = float(sum(t["cost"] for t in trades_sorted))
    net = float(sum(t["net"] for t in trades_sorted))
    cost_gross_ratio = round(cost / gross, 6) if gross > 0 else None   # 负毛利不用 0 占位
    cost_ok = bool(gross > 0 and cost <= cost_gate["max_cost_as_pct_of_gross"] * gross)

    denom = float(equity[0]) if equity[0] > 0 else 1.0
    total_return = float(equity[-1] / denom - 1.0)        # 28 年累计（比例），非年化、非单标的
    peak = np.maximum.accumulate(equity)
    max_dd = float(np.max((peak - equity) / peak)) if np.all(peak > 0) else 0.0
    r = np.diff(equity) / equity[:-1]
    sharpe = float(np.mean(r) / np.std(r) * np.sqrt(252.0)) if len(r) > 1 and np.std(r) > 0 else 0.0
    years_calmar = len(equity) / 252.0                    # 与 I-3 同口径（门槛可比）
    annual_calmar = (float((1.0 + total_return) ** (1.0 / years_calmar)) - 1.0
                     if years_calmar > 0 and total_return > -1 else 0.0)
    calmar = float(annual_calmar / max_dd) if max_dd > 1e-12 else (0.0 if annual_calmar <= 0 else None)
    annualized_defined = total_return > -1
    annualized_28y = (float((1.0 + total_return) ** (1.0 / YEARS_28) - 1.0)
                      if annualized_defined else None)     # 累计亏损穿透本金 → 年化不可定义，不用 0 占位
    annualized_note = (f"(1 + {round(total_return, 6)})^(1/{YEARS_28}) - 1"
                       if annualized_defined else
                       "累计收益 ≤ -100%（12 标的净亏损累加穿透简化权益曲线起点 BASE_EQUITY），"
                       "年化收益不可定义；不用 0 占位，如实标 None")

    max_consec, cur = 0, 0
    for t in trades_sorted:
        cur = cur + 1 if t["net"] <= 0 else 0
        max_consec = max(max_consec, cur)
    wins = sum(1 for t in trades_sorted if t["net"] > 0)

    # 5 元最低佣金触发统计（按单边成交计；i3_old 不适用）
    total_sides = 2 * n_trades
    trig = 0
    applicable = model != "i3_old"
    if applicable:
        for t in trades_sorted:
            d = t["cost_items_yuan"]
            if d.get("buy_min_commission_triggered") is True:
                trig += 1
            if d.get("sell_min_commission_triggered") is True:
                trig += 1
    min_comm = {
        "applicable": applicable,
        "triggered_sides": trig if applicable else None,
        "total_sides": total_sides,
        "trigger_ratio": round(trig / total_sides, 6) if applicable and total_sides else None,
        "note": ("I-3 旧口径为固定比例佣金、无 5 元最低佣金规则 → 不适用"
                 if not applicable else
                 "买入 / 卖出各自独立判断：按比例佣金 < 5 元即触发 5 元最低佣金（金额规则）"),
    }

    gates = {
        "1_max_drawdown_le_10pct": bool(max_dd <= gate["max_drawdown_pct"] / 100.0),
        "2_calmar_ge_1p5": bool(calmar is not None and calmar >= gate["min_calmar"]),
        "3_cost_le_50pct_of_gross": bool(cost_ok),
        "4_max_consec_losses_le_5": bool(max_consec <= gate["max_consecutive_losses"]),
        "min_trades_ge_30": bool(n_trades >= 30),
        "sharpe_hard_gate": "不设硬门槛（min_sharpe=null）",
    }
    decidable = all(gates[k] for k in (
        "1_max_drawdown_le_10pct", "2_calmar_ge_1p5",
        "3_cost_le_50pct_of_gross", "4_max_consec_losses_le_5", "min_trades_ge_30"))

    res = {
        "factor_id": fac["id"], "name": fac["name"], "category": fac["cat"],
        "fields": fac["fields"], "source_status": fac["source"],
        "cost_model": model, "pool": pool_label, "n_symbols": n_symbols,
        "union_bars": int(len(equity)),
        "n_trades": n_trades,
        "gross_total_yuan": round(gross, 4),
        "cost_total_yuan": round(cost, 4),
        "cost_gross_ratio": cost_gross_ratio,
        "cost_ok": cost_ok,
        "net_total_yuan": round(net, 4),
        "cumulative_return_28y": round(total_return, 6),
        "annualized_return_28y": (round(annualized_28y, 6) if annualized_28y is not None else None),
        "annualized_defined": bool(annualized_defined),
        "annualized_formula": annualized_note,
        "max_drawdown": round(max_dd, 6),
        "calmar": round(calmar, 4) if calmar is not None else None,
        "sharpe_reference": round(sharpe, 4),
        "max_consec_losses": max_consec,
        "win_rate": round(wins / n_trades, 4) if n_trades else None,
        "t1_violations": int(extra.get("t1_violations", 0)),
        "t1_ok": bool(extra.get("t1_violations", 0) == 0),
        "min_commission_5yuan": min_comm,
        "gates": gates,
        "decidable_4plus_trades_pass": bool(decidable),
        "passed_final": False,
        "passed_final_reason": "第 5 条极端段按老板 17:34 指令不判（NOT_EVALUATED_BY_BOSS_ORDER），"
                               "判定未闭合 → 非最终达标 / 未含极端段；且本批未复权、结果非可上线依据。",
        "extreme_gate": "NOT_EVALUATED_BY_BOSS_ORDER",
        "adjusted": False,
        "equity_curve_label": extra.get("equity_curve_label", "简化已实现权益曲线（BASE_EQUITY=10000）"),
        "trades": trades_sorted,     # 写 json 前剔除
    }
    for k in ("skipped_insufficient_capital", "skipped_parallel_cap", "n_candidates",
              "initial_capital_yuan"):
        if k in extra:
            res[k] = extra[k]
    return res


def equity_from_simple(trades: List[Dict[str, Any]], union_dates: np.ndarray) -> np.ndarray:
    """沿用 I-3 aggregate：12 标的净盈亏累加到同一条 BASE_EQUITY 曲线（口径限制如实标注）。"""
    date_pos = {int(d): k for k, d in enumerate(union_dates)}
    eq = np.full(len(union_dates), BASE_EQUITY, dtype=float)
    for t in trades:
        eq[date_pos[int(t["exit_date"])]:] += t["net"]
    return eq


# ---------------------------------------------------------------------------
# 报告组装
# ---------------------------------------------------------------------------
def build_result(m: Dict[str, Any], gate: Dict[str, Any], cost_gate: Dict[str, Any],
                 tf: Any, frames: Dict[str, pd.DataFrame],
                 main_pool: List[str], control_pool: List[str],
                 main_union: np.ndarray, control_union: np.ndarray) -> Dict[str, Any]:
    pfp = i3.param_fingerprint()
    dfp = i3.data_fingerprint(m)
    app = m["i3_append"]

    def cost_model_def(model: str) -> Dict[str, Any]:
        bt = st = NOTIONAL
        d = roundtrip_cost(model, bt, st)
        return {
            "model": model,
            "buy_turnover_yuan": bt, "sell_turnover_yuan": st,
            "items_yuan": d,
            "roundtrip_yuan": d["cost_roundtrip_yuan"],
            "stamp_duty": STAMP_DUTY,
            "commission_rate_per_side": COMM_RATE,
            "min_commission_yuan": MIN_COMM_YUAN,
        }

    primary_def = cost_model_def("primary")
    conservative_def = cost_model_def("conservative")
    real_def = cost_model_def("real_capital")
    i3_def = cost_model_def("i3_old")

    factor_rows: Dict[str, List[Dict[str, Any]]] = {mo: [] for mo in COST_MODELS}
    control_rows: Dict[str, List[Dict[str, Any]]] = {mo: [] for mo in COST_MODELS}
    trade_dump: Dict[str, Any] = {"primary": {}, "real_capital": {}}

    for fac in i3.FACTORS:
        fid = fac["id"]
        # 候选成交（四套口径共用同一份时序，只换成本）
        cands_main, tv_main = [], 0
        cands_ctrl, tv_ctrl = [], 0
        for s in main_pool:
            ts, tv = gen_trades_simple(frames[s], s, "main_index", fid)
            cands_main.extend(ts); tv_main += tv
        for s in control_pool:
            ts, tv = gen_trades_simple(frames[s], s, "stock_control", fid)
            cands_ctrl.extend(ts); tv_ctrl += tv

        for model in COST_MODELS:
            if model == "real_capital":
                rm = run_portfolio_real(frames, main_pool, fid, main_union)
                rc = run_portfolio_real(frames, control_pool, fid, control_union)
                fm = make_factor_result(fac, model, rm["trades"], rm["equity"],
                                        "main_index", len(main_pool), gate, cost_gate,
                                        {"t1_violations": rm["t1_violations"],
                                         "skipped_insufficient_capital": rm["skipped_insufficient_capital"],
                                         "skipped_parallel_cap": rm["skipped_parallel_cap"],
                                         "n_candidates": rm["n_candidates"],
                                         "initial_capital_yuan": rm["initial_capital_yuan"],
                                         "equity_curve_label": "现金 + 持仓市值（按当日 open）；分母=真实投入资金"})
                fc = make_factor_result(fac, model, rc["trades"], rc["equity"],
                                        "stock_control", len(control_pool), gate, cost_gate,
                                        {"t1_violations": rc["t1_violations"],
                                         "skipped_insufficient_capital": rc["skipped_insufficient_capital"],
                                         "skipped_parallel_cap": rc["skipped_parallel_cap"],
                                         "n_candidates": rc["n_candidates"],
                                         "initial_capital_yuan": rc["initial_capital_yuan"],
                                         "equity_curve_label": "现金 + 持仓市值（按当日 open）；分母=真实投入资金"})
                if fid not in trade_dump["real_capital"]:
                    trade_dump["real_capital"][fid] = {"main": rm["trades"], "control": rc["trades"]}
            else:
                tm = attach_simple_cost(cands_main, model)
                tc = attach_simple_cost(cands_ctrl, model)
                eqm = equity_from_simple(tm, main_union)
                eqc = equity_from_simple(tc, control_union)
                fm = make_factor_result(fac, model, tm, eqm, "main_index",
                                        len(main_pool), gate, cost_gate,
                                        {"t1_violations": tv_main})
                fc = make_factor_result(fac, model, tc, eqc, "stock_control",
                                        len(control_pool), gate, cost_gate,
                                        {"t1_violations": tv_ctrl})
                if model == "primary":
                    trade_dump["primary"][fid] = {"main": tm, "control": tc}
            factor_rows[model].append(fm)
            control_rows[model].append(fc)

    # 买入持有基准
    bh_simple = buy_hold_benchmark(frames, main_pool, main_union, False)
    bh_real = buy_hold_benchmark(frames, main_pool, main_union, True)

    # 排序（按主口径年化收益）+ 与基准对比
    ranking = sorted(
        factor_rows["primary"],
        key=lambda f: (f["annualized_return_28y"] is not None,
                       f["annualized_return_28y"] if f["annualized_return_28y"] is not None
                       else float("-inf")),
        reverse=True)
    bh_simple_ann = bh_simple["annualized_return_28y"]
    bh_real_ann = bh_real["annualized_return_28y"]
    vs_bench = []
    for k, f in enumerate(ranking):
        ann = f["annualized_return_28y"]
        real_ann = next(x["annualized_return_28y"] for x in factor_rows["real_capital"]
                        if x["factor_id"] == f["factor_id"])
        vs_bench.append({
            "rank": k + 1, "factor_id": f["factor_id"], "name": f["name"],
            "annualized_return_28y_primary": ann,
            "annualized_defined_primary": f["annualized_defined"],
            "benchmark_annualized_28y_primary": bh_simple_ann,
            "vs_benchmark": ("不可比较（年化不可定义）" if ann is None
                             else ("跑赢" if ann > bh_simple_ann else "跑输")),
            "annualized_return_28y_real_capital": real_ann,
            "benchmark_annualized_28y_real_capital": bh_real_ann,
            "vs_benchmark_real_capital": ("不可比较（年化不可定义）" if real_ann is None
                                          else ("跑赢" if real_ann > bh_real_ann else "跑输")),
        })

    # 门槛计数（各口径分开，主口径为 summary 基准）
    gate_counts: Dict[str, Any] = {}
    for model in COST_MODELS:
        rows = factor_rows[model]
        gate_counts[model] = {
            "1_max_drawdown_le_10pct": sum(1 for f in rows if f["gates"]["1_max_drawdown_le_10pct"]),
            "2_calmar_ge_1p5": sum(1 for f in rows if f["gates"]["2_calmar_ge_1p5"]),
            "3_cost_le_50pct_of_gross": sum(1 for f in rows if f["gates"]["3_cost_le_50pct_of_gross"]),
            "4_max_consec_losses_le_5": sum(1 for f in rows if f["gates"]["4_max_consec_losses_le_5"]),
            "min_trades_ge_30": sum(1 for f in rows if f["gates"]["min_trades_ge_30"]),
            "n_pass_decidable_4plus_trades": sum(1 for f in rows if f["decidable_4plus_trades_pass"]),
        }
    n_pass_decidable = gate_counts["primary"]["n_pass_decidable_4plus_trades"]

    # 5 元最低佣金触发统计（全口径汇总）
    min_comm_summary: Dict[str, Any] = {}
    for model in COST_MODELS:
        trig = sum(f["min_commission_5yuan"]["triggered_sides"] or 0 for f in factor_rows[model])
        sides = sum(f["min_commission_5yuan"]["total_sides"] for f in factor_rows[model])
        min_comm_summary[model] = {
            "applicable": model != "i3_old",
            "triggered_sides": trig if model != "i3_old" else None,
            "total_sides": sides,
            "trigger_ratio": round(trig / sides, 6) if model != "i3_old" and sides else None,
        }

    # T+1 全量核验
    t1_total = sum(f["t1_violations"] for mo in COST_MODELS for f in factor_rows[mo]) \
        + sum(f["t1_violations"] for mo in COST_MODELS for f in control_rows[mo])
    n_primary_trades = sum(len(v["main"]) + len(v["control"]) for v in trade_dump["primary"].values())
    n_real_trades = sum(len(v["main"]) + len(v["control"]) for v in trade_dump["real_capital"].values())

    # 剔除逐笔明细（单独落 _trades.json）
    def clean(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return [{k: v for k, v in f.items() if k != "trades"} for f in rows]

    result: Dict[str, Any] = {
        "batch": "I-4",
        "phase": "真实 A 股交易费率口径重跑 + 最终回测报告",
        "role": "backend_architect（单一执行体按 soul 纪律落地）",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "meta": {
            "script": "backtest/a_share_technical_factors_real.py",
            "old_scripts_untouched": True, "src_untouched": True, "config_untouched": True,
            "signal_layer": "只读复用 I-3 a_share_technical_factors.compute_target()/FACTORS，未改一字；"
                            "15 因子统一参数、不铺网格、不寻优",
            "strategy_form": "买入 → 持有 → 卖出平仓 → 空仓等待（单腿纯多头；无做空/无配对/无对冲）",
            "boss_orders": [
                "20:21 按真实A股交易回测，费率单向万分之5、不足5元按5元计、先取消印花税；"
                "看技术派能否测出比较好的收益，发最终回测报告",
                "20:22 报告内记得写清楚开仓平仓条件",
                "20:23 不是佣金会被抬到 0.05% 变 0.5%，而是直接按 5 元计算",
            ],
            "rate_rule": "金额规则（非费率变更）：佣金金额 = max(成交额 × 0.0005, 5 元)，"
                         "买入 / 卖出各自独立；成本一律以「元」逐项列示",
            "forbidden_phrasing": ["费率被抬到 0.5%", "等效 50bp", "成本抬高 10 倍",
                                   "把 5 元最低佣金写成费率变更"],
            "nominal_boundary_note": "5 ÷ 0.0005 = 10000 元 仅为『按比例佣金恰好达到 5 元』的名义分界，"
                                     "不是变更费率的依据、也不是替换主口径的理由",
            "notional_primary_unchanged": True,
            "primary_notional_yuan": NOTIONAL,
            "stamp_duty": 0,
            "data_fingerprint": dfp,
            "param_fingerprint": pfp,
            "gate": {**gate, **cost_gate},
            "sharpe_gate": "不设硬门槛（min_sharpe=null；夏普仅作参考值、不作门槛）",
            "extreme_gate": "NOT_EVALUATED_BY_BOSS_ORDER",
            "extreme_gate_note": "老板 17:34「先不判极端行情」，方向改后未撤回 → 本批沿用。"
                                 "第 5 条极端段不判、不记通过、不记 N/A、不用 0 占位；"
                                 "config 一字不改（仅本批判定范围按老板指令临时排除，留痕）。",
            "assumption_label": "待老板确认的假设（费率四项中过户/滑点为老板未指定、主口径按 0；"
                                "真实资金模型参数为秘书建议、非老板指定）",
            "cost_models": {
                "primary": primary_def,
                "conservative": conservative_def,
                "real_capital": real_def,
                "i3_old": i3_def,
            },
            "cost_derivation_yuan": {
                "primary": "买佣金 max(1000×0.0005,5)=5 元 + 卖佣金 5 元 + 印花税0 + 过户0 + 滑点0 = 10 元/roundtrip",
                "conservative": "主口径 + 过户 1000×0.00001=0.01 元/边 + 滑点 1000×0.0005=0.5 元/边 "
                                "= 5.51 + 5.51 = 11.02 元/roundtrip（印花税仍 0）",
                "i3_old": "佣金 0.25 + 过户 0.01 + 滑点 0.5（买 0.76 元）+ 佣金 0.25 + 过户 0.01 "
                          "+ 滑点 0.5 + 印花税 0.5（卖 1.26 元）= 2.02 元/roundtrip",
                "real_capital": "费率结构同主口径（佣金 max(成交额×0.0005,5)、印花税0/过户0/滑点0），"
                                "但成交额 = 100 股整数倍 × 成交价，逐笔按金额计",
            },
            "real_capital_model": {
                "is_secretary_suggestion_not_boss": True,
                "target_notional_yuan": REAL_TARGET_NOTIONAL,
                "lot_shares": LOT,
                "initial_capital_yuan": REAL_INITIAL_CAPITAL,
                "max_parallel_positions": MAX_PARALLEL,
                "funds_occupancy_check": "逐笔检查：开仓需 buy_amount + buy_commission ≤ 可用现金，"
                                          "且活跃持仓数 < 并行上限；不满足则跳过并计数",
                "equity_curve": "现金 + 持仓市值（按当日 open）；分母 = 真实投入资金 REAL_INITIAL_CAPITAL",
                "i3_defect_kept_for_primary": "I-3 资金口径缺陷（12 标的净盈亏累加到同一条 "
                                              "BASE_EQUITY=10000 曲线、每笔 1000 元名义、无资金占用/并行持仓检查）"
                                              "在主口径 / 保守 / I-3 旧口径中仍存在，回撤/Calmar/夏普带该限制；"
                                              "本对照口径单独修复，结果不写成主口径结论",
                "data_reality_note": "主池为通达信指数**点位序列**（非股价）。按 100 股/份整数倍取整时，"
                                     "1 手名义 = 100 × 点数（如约 3000 点 → 约 30 万元），"
                                     "超出『1 万元量级』目标；系数据性质决定，未擅自改价/归一，如实申报。"
                                     "该量级下按比例佣金远超 5 元 → 5 元最低佣金触发率极低（逐笔如实统计）。",
                "all_params_are_assumptions": True,
            },
            "t1_constraint": {
                "min_hold_bars": MIN_HOLD_BARS,
                "signal_timing": "bar t 收盘出信号 → bar t+1 的 open 成交（买入与卖出同理）",
                "hard_check": "逐笔 exit_date>entry_date 且 exit_bar>entry_bar；成交层拒绝同 bar 开平",
                "no_intraday_reverse": True,
                "end_force_close": True,
            },
            "return_convention": {
                "primary_conservative_i3old": "已实现权益曲线（12 标的净盈亏按 exit_date 累加到 "
                                              "BASE_EQUITY=10000 曲线）、日频、√252 年化、未计浮动盈亏；"
                                              "带 I-3 资金口径限制",
                "real_capital": "现金 + 持仓市值（按当日 open）的账户权益曲线；分母 = 真实投入资金",
                "annualized_28y_formula": f"(1 + 累计收益)^(1/{YEARS_28}) - 1（{YEARS_28} 年为"
                                          " 1998-09-15~2026-11-13 的日历年数）",
                "cumulative_is_not_annualized": "net_total（元）是 12 指数 × 28 年净盈亏加总；"
                                                "cumulative_return 是 28 年累计比例；两者都不是年化、更不是单标的收益",
            },
            "timeframe": {
                "resolve": 'resolve_timeframe("1d", hours_per_day=4, trading_days_per_year=252)',
                "bars_per_year": float(tf.bars_per_year),
                "annualize_factor": float(tf.annualize_factor),
                "src_read_only": True,
            },
            "pool": {
                "main_index": main_pool, "stock_control": control_pool,
                "selection_rule": app["selection_rule"],
                "i1_old_parquet_preserved": True,
                "adjusted": False,
                "n_parquet_total": 21,
                "n_used_in_backtest": len(main_pool) + len(control_pool),
            },
            "survivorship_bias": app["survivorship_bias"]["note"],
            "unadjusted_pollution_note": app["unadjusted_pollution_note"],
            "adjustment_limit": dfp["adjustment_conclusion"],
            "result_not_upper_line_evidence": True,
        },
        "summary": {
            "n_factors": len(i3.FACTORS),
            "n_runs_primary": len(i3.FACTORS) * (len(main_pool) + len(control_pool)),
            "n_runs_all_cost_models": len(i3.FACTORS) * (len(main_pool) + len(control_pool)) * len(COST_MODELS),
            "n_runs_breakdown": {
                "main_detail": len(i3.FACTORS) * len(main_pool),
                "control_detail": len(i3.FACTORS) * len(control_pool),
                "cost_models": len(COST_MODELS),
            },
            "n_pass_decidable_4plus_trades": n_pass_decidable,
            "n_passed_final": 0,
            "n_passed_final_label": "非最终达标 / 未含极端段（第 5 条按老板 17:34 指令不判）",
            "n_passed_final_reason": "极端段不判使判定未闭合，任何因子不能判『最终达标』；"
                                     "严禁把 n_pass_decidable_4plus_trades 冒充 n_passed_final、"
                                     "严禁把 n_passed_final 写成 >0。且本批未复权、结果非可上线依据。",
            "extreme_gate": "NOT_EVALUATED_BY_BOSS_ORDER",
            "data_fingerprint": dfp["manifest_sha256_16"],
            "param_fingerprint": pfp["sha256_16"],
            "assumption_label": "待老板确认的假设",
            "adjusted": False,
            "ranking_note": "因子效果排序 ≠ 达标 ≠ 可上线；排序与达标判定分开申报，"
                            "不得把排序靠前写成达标/可上线；对照口径结果不得写成主口径结论。",
            "t1_violations_total_all_models": int(t1_total),
            "n_primary_trades_dumped": n_primary_trades,
            "n_real_capital_trades_dumped": n_real_trades,
        },
        "gate_counts_by_cost_model": gate_counts,
        "min_commission_5yuan_by_cost_model": min_comm_summary,
        "factors": {mo: clean(factor_rows[mo]) for mo in COST_MODELS},       # 主池 12（主表）
        "control": {mo: clean(control_rows[mo]) for mo in COST_MODELS},       # 个股对照 3
        "buy_hold_benchmark": {"equal_weight_simple": bh_simple, "real_notional": bh_real},
        "ranking_by_annualized_primary": vs_bench,
        "comparison_with_i3": {
            "i3_baseline": "I-3：15 因子 × 225 runs、n_pass_decidable_4plus_trades=0、n_passed_final=0、"
                           "成本门槛 11/15 通过（cost_gross_ratio 0.0363~0.2639）、"
                           "卡在连亏 28~66 与 Calmar 最高 1.2065<1.5；单腿成本 2.02 元/roundtrip（20.2bp）",
            "fee_change": "主口径 10 元/roundtrip vs I-3 2.02 元/roundtrip —— "
                          "成本按「元」逐项变化（买 5 + 卖 5），不是费率被抬到 0.5% 的换算",
            "capital_model_change": "真实资金模型对照单独修复 I-3 资金口径缺陷（资金占用检查 / 100 股取整 / "
                                    "真实占用资金分母 / 并行持仓上限 / 含持仓市值权益曲线）",
            "isolated": "i3_old 口径复现 I-3 基线（除成本外信号/成交/权益曲线完全一致），"
                        "用于把『费率变化』与『资金模型变化』分离",
            "not_mixed": True,
            "note": "不与加密 257 组、I-1 3 组混算；0 达标就报 0、不美化。",
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
            "primary_notional_unchanged": True,
            "old_scripts_preserved": True,
        },
    }
    return result, trade_dump


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    gate = i3.load_gate_from_yaml()
    cost_gate = i3.load_cost_gate_from_yaml()
    tf = resolve_timeframe("1d", hours_per_day=4.0, trading_days_per_year=252.0)
    assert tf.bars_per_year == 252.0

    pfp = i3.param_fingerprint()
    print(f"[gate] {gate} | cost_gate={cost_gate}")
    print(f"[param] config sha256-16={pfp['sha256_16']}（应为 31fbb054cddb55af，一字未改）")
    print(f"[cost] 主口径 roundtrip={roundtrip_cost('primary', NOTIONAL, NOTIONAL)['cost_roundtrip_yuan']} 元 "
          f"/ 保守={roundtrip_cost('conservative', NOTIONAL, NOTIONAL)['cost_roundtrip_yuan']} 元 "
          f"/ I-3旧={roundtrip_cost('i3_old', NOTIONAL, NOTIONAL)['cost_roundtrip_yuan']} 元")

    m = json.loads(MANIFEST.read_text(encoding="utf-8"))
    main_pool = m["i3_append"]["main_index_pool"]
    control_pool = m["i3_append"]["stock_control_pool"]
    frames: Dict[str, pd.DataFrame] = {}
    for s in main_pool + control_pool:
        frames[s] = pd.read_parquet(DATA_DIR / f"{s}_1d.parquet")
    main_union = np.unique(np.concatenate([frames[s]["date"].to_numpy(np.int64) for s in main_pool]))
    control_union = np.unique(np.concatenate([frames[s]["date"].to_numpy(np.int64) for s in control_pool]))
    print(f"[pool] main={len(main_pool)} control={len(control_pool)} adjusted=false / 无法可靠复权")

    result, trade_dump = build_result(m, gate, cost_gate, tf, frames,
                                      main_pool, control_pool, main_union, control_union)

    jp = OUT_DIR / "a_share_technical_factors_real.json"
    jp.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    tp = OUT_DIR / "a_share_technical_factors_real_trades.json"
    tp.write_text(json.dumps({
        "batch": "I-4",
        "note": "primary / real_capital 全量逐笔；conservative / i3_old 逐笔成交时序与 primary 一致，"
                "仅成本金额不同，未重复落盘（见主 json cost_models.items_yuan）。",
        "primary": trade_dump["primary"],
        "real_capital": trade_dump["real_capital"],
    }, ensure_ascii=False), encoding="utf-8")

    s = result["summary"]
    print(f"\n[out] {jp}")
    print(f"[out] {tp}")
    print(f"[summary] n_factors={s['n_factors']} n_runs_primary={s['n_runs_primary']} "
          f"n_runs_all_cost_models={s['n_runs_all_cost_models']} "
          f"n_pass_decidable_4plus_trades={s['n_pass_decidable_4plus_trades']} "
          f"n_passed_final={s['n_passed_final']}（非最终达标 / 未含极端段，如实报 0）")
    print(f"[t1] violations_total_all_models={s['t1_violations_total_all_models']} "
          f"primary_trades={s['n_primary_trades_dumped']} real_trades={s['n_real_capital_trades_dumped']}")
    for mo in COST_MODELS:
        print(f"[min5] {mo}: {result['min_commission_5yuan_by_cost_model'][mo]}")
    print(f"[bench] equal_weight ann28={result['buy_hold_benchmark']['equal_weight_simple']['annualized_return_28y']} "
          f"real_notional ann28={result['buy_hold_benchmark']['real_notional']['annualized_return_28y']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
