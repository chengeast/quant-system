# -*- coding: utf-8 -*-
"""I-6 阶段二 · 选股规则对照分离 + 老板指定组合参数（后端架构师产出）

PM 承派 + 验收；按 agents/souls/backend_architect.md 纪律：模块边界清晰、接口可测试可回滚
（新增脚本、保留 backtest/ 下全部旧脚本一字不动、不改 src/、不改 config/）。

老板 2026-11-15 22:42 指令（逐字）：
「① 补复权因子后重跑；② 换选股规则做对照分离；③ 组合参数（10 万 / 2 只 / 等权）；
  最多只开4成仓；④ 排除ST及减持、·退市名录；」

============================================================================
本批边界（复权挂起，阶段一已判定）
============================================================================
  - ① 复权三路径均未达到「拿到且验证通过」→ **正式复权重跑挂起**（见
    agents/phase9-i6-adjustment-and-roster-feasibility.md 与
    data/cache/tdx_a_share_adjusted/_UNAVAILABLE.md）。本脚本 ②③ 仍用 **未复权数据**
    data/cache/tdx_a_share_full/（adjusted=false），**不把未复权冒充复权**。
  - ④ 当前 ST 本机不可可靠读出、历史 ST/减持/退市全名录本机缺 → **不落盘名录、
    不执行 ST 排除**（roster_exclusion 如实留痕），严禁编造名录、严禁把「确认存在」写成「已排除」。
  - 本批是 **参数/规则敏感性对照，非寻优、不挑最优**（is_optimization=false）；
    4 规则共享同一未复权数据 → 规则间差异可归因于规则，共同部分不能据此归因于策略优劣；
    **不得把「换规则后数字变好」写成策略达标 / 可上线**。

============================================================================
只读复用（逐字不改）
============================================================================
  import a_share_technical_factors as i3  → FACTORS / compute_target() / WARM /
    MIN_HOLD_BARS / load_gate_from_yaml() / load_cost_gate_from_yaml() / param_fingerprint()
  import a_share_portfolio_factors as i5  → load_context() / build_all_factor_events() /
    curve_metrics() / commission_yuan() / make_factor_result() / 费率常量 / BUY_DTYPE / SELL_DTYPE
  成交层因「10 万 / 2 只 / ≤4 成仓」+「4 组选股规则」与 I-5 主档（100 万 / 10 只）不同，
  在本脚本内**重写 simulate_i6()**（旧脚本保留不动、不覆盖）。

============================================================================
③ 组合参数（老板指定，一字不改；脚本常量 ↔ json 双留痕）
============================================================================
  INITIAL_CAPITAL      = 100_000 元
  MAX_POSITIONS        = 2
  单票等权             = PER_POSITION_BUDGET = 20_000 元
  MAX_EXPOSURE_RATIO   = 0.4   → EXPOSURE_CAP_YUAN = 0.4 × 100_000 = 40_000 元
  口径选择：**固定初始资金 100,000 元**（exposure_basis = fixed_initial_capital）
  理由（不得擅自放宽 4 成仓）：
    1) 老板指令「最多只开 4 成仓」+「组合参数（10 万 / 2 只 / 等权）」直接给出
       0.4 × 100000 = 40000、单票 20000，固定口径一字对应、静态上限、可复现；
    2) 动态权益口径在权益上涨时会把 0.4×权益 放大到 >40000 元（相对初始资金），
       与「最多只开 4 成仓」的静态上限语义不符，且引入路径依赖、与 I-5
       「按初始资金固定分 N 份」口径不一致；
    3) 固定口径下每票预算恒为 20000 元，买入前现金校验 + 4 成仓校验均简单可复现。
  4 成仓显式校验：开仓时按 Σ(各持仓买入成交额) + 本次成交额 ≤ 40000 元 校验，
    超限直接跳过并计数 skipped_exposure_cap（保守、不放宽；用买入成交额口径，
    不随盯市价漂移）。2 票 × 20000 = 40000 恰好等于上限，设计上不突破。

============================================================================
② 四组选股规则（同一 15 因子 / 同一未复权数据 / 同一组合参数）
============================================================================
  A 信号日成交额降序 + 代码升序 tie-break（= I-5 主档）：np.lexsort((sy, -amt, g))
  B 股票代码升序：                                      np.lexsort((sy, g))
  C 信号日成交额升序 + 代码升序 tie-break：              np.lexsort((sy, amt, g))
  D 固定种子随机：splitmix64 风格确定性哈希 over (seed, fid, g, sy, bar)
    → np.lexsort((sy, rand_key, g))；seed 固定写进脚本与 json，
      **可复现、与数组顺序无关、只用事件自身的 g/sy/bar（当期已知，无未来函数）**。
  共 15 × 4 = **60 组**全跑；复权可用才另加复权口径一组（本批挂起，无）。

============================================================================
沿用未撤回口径（原样，不改写）
============================================================================
  - 28 年全量 1998-09-15~2026-11-13、不裁窗口、不抽样、不分段；全池 5687 只、无降采样。
  - 费率（老板 20:21/20:23）：佣金金额 = max(成交额 × 0.0005, 5 元)，买/卖各自独立按金额计、
    不足 5 元按 5 元这一笔金额收；**这是金额规则、不是费率变更** —— 严禁写「费率被抬到 0.5%」
    「等效 50bp」「成本抬高 10 倍」等换算表述，成本一律以「元」逐项列示。
    印花税 = 0。过户费/滑点老板未指定 → 主口径 0，另出保守对照
    （加回过户 0.001%/边 + 滑点 5bp/边，按金额计）。逐笔申报 5 元最低佣金触发笔数与占比。
  - 不得说「A 股费率低一个数量级」；phase7 加密现货 0.1%/边 / roundtrip 28bp 绝不硬套 A 股。
  - 100 股整手、买入前现金校验（成交额+佣金 ≤ 可用现金）、
    cash + Σ(股数×当日盯市 close) 逐日盯市权益曲线（**严禁逐笔盈亏无条件累加**）、
    持仓中个股达到出场信号才卖、t 收盘出信号 → t+1 open 成交、MIN_HOLD_BARS=1、
    末尾 end_force_close、T+1 逐笔 exit_date>entry_date / exit_bar>entry_bar 违规 0、
    纯多头 / 无做空 / 无配对 / 无对冲。
  - 先不判极端段（17:34 未撤回）：判定 = 4 条硬门槛（回撤≤10% / Calmar≥1.5 /
    成本≤0.50×毛利 / 连亏≤5）+ 笔数≥30；min_sharpe:null 不设夏普硬门槛。
    第 5 条极端段**不判、不记通过、不记 N/A、不用 0 占位**，
    json 写 extreme_gate = "NOT_EVALUATED_BY_BOSS_ORDER"；
    n_pass_decidable_4plus_trades 与 n_passed_final 严格区分，
    n_passed_final 标「非最终达标 / 未含极端段」；config/strategy_params.yaml 一字不改。
  - 冻结门槛不因换规则/换参数而放宽；「因子效果排序」与「达标判定」分开申报；
    cost_gross_ratio 在 gross ≤ 0 时写 None，不用 0 占位。
  - 不与加密 257 组 / I-1 3 组 / I-4 / I-5 结果混算（not_mixed=true）；0 达标就报 0；
    排序 ≠ 达标 ≠ 可上线；I-5 报告与产物全部保留不动。

============================================================================
产出
============================================================================
  backtest/output/a_share_i6_selection_control.json + a_share_i6_selection_control_trades.json
  运行：python backtest/a_share_i6_selection_control.py
"""
from __future__ import annotations

import hashlib
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backtest"))
import a_share_technical_factors as i3          # noqa: E402 （只读复用，逐字不改）
import a_share_portfolio_factors as i5          # noqa: E402 （只读复用，逐字不改）

OUT_DIR = ROOT / "backtest" / "output"
MANIFEST = ROOT / "data" / "cache" / "tdx_a_share_full" / "_manifest.json"

# ---------------------------------------------------------------------------
# ③ 老板指定组合参数（一字不改；脚本常量 ↔ json 双留痕）
# ---------------------------------------------------------------------------
INITIAL_CAPITAL = 100_000.0
MAX_POSITIONS = 2
MAX_EXPOSURE_RATIO = 0.4
EXPOSURE_CAP_YUAN = MAX_EXPOSURE_RATIO * INITIAL_CAPITAL      # 40_000.0
PER_POSITION_BUDGET = 20_000.0                                # 单票等权：0.4×100000/2
EXPOSURE_BASIS = "fixed_initial_capital"
EXPOSURE_BASIS_REASON = (
    "老板『最多只开4成仓』+『10万/2只/等权』直接给出 0.4×100000=40000、单票 20000；"
    "固定初始资金口径为静态上限、一字对应、可复现，不随权益涨跌放大到 >40000 元，"
    "且沿用 I-5『按初始资金固定分 N 份』口径；不得擅自放宽。"
)
LOT = i5.LOT                                                   # 100 股整手
MIN_COMM_YUAN = i5.MIN_COMM_YUAN
COMM_RATE = i5.COMM_RATE
EXTRA_SIDE_RATE = i5.EXTRA_SIDE_RATE                           # 保守对照 0.00051/边
STAMP_DUTY = i5.STAMP_DUTY                                     # 0
YEARS_28 = i5.YEARS_28
MIN_TRADES = i5.MIN_TRADES
EXTREME_GATE = i5.EXTREME_GATE

# ② 四组选股规则
RANDOM_SEED = 20261115
SELECTION_RULES: Dict[str, str] = {
    "A": "signal_day_amount_desc_then_symbol_asc",           # = I-5 主档
    "B": "symbol_asc",
    "C": "signal_day_amount_asc_then_symbol_asc",
    "D": "fixed_seed_random",
}
RULES = list(SELECTION_RULES.keys())

IS_OPTIMIZATION = False
ADJUSTED = False


def _rand_keys(ev_buy: np.ndarray, fid: str) -> np.ndarray:
    """D 规则：splitmix64 风格确定性哈希 over (seed, fid, g, sy, bar)。

    完全由固定 seed + 事件自身的 g/sy/bar（当期已知，无未来函数）决定，
    与数组顺序无关、与运行次数无关 → 可复现。
    """
    fid_seed = int(hashlib.md5(f"{RANDOM_SEED}|{fid}".encode()).hexdigest()[:12], 16)
    g = ev_buy["g"].astype(np.uint64)
    sy = ev_buy["sy"].astype(np.uint64)
    bar = ev_buy["bar"].astype(np.uint64)
    z = (np.uint64(fid_seed)
         + g * np.uint64(0x9E3779B97F4A7C15)
         + sy * np.uint64(0xBF58476D1CE4E5B9)
         + bar * np.uint64(0x94D049BB133111EB))
    z = (z ^ (z >> np.uint64(30))) * np.uint64(0xBF58476D1CE4E5B9)
    z = (z ^ (z >> np.uint64(27))) * np.uint64(0x94D049BB133111EB)
    z = z ^ (z >> np.uint64(31))
    return z.astype(np.int64)                                   # lexsort 数值键


def order_buy(ev_buy: np.ndarray, rule: str, fid: str) -> np.ndarray:
    """按选股规则重排「进场事件」数组（主键恒为 g 升序，保证逐日分组正确）。"""
    b = ev_buy
    if rule == "A":
        return b[np.lexsort((b["sy"], -b["amt"], b["g"]))]
    if rule == "B":
        return b[np.lexsort((b["sy"], b["g"]))]
    if rule == "C":
        return b[np.lexsort((b["sy"], b["amt"], b["g"]))]
    if rule == "D":
        return b[np.lexsort((b["sy"], _rand_keys(b, fid), b["g"]))]
    raise ValueError(f"未知选股规则 {rule}")


def simulate_i6(ctx, ev_buy, ev_sell, fid: str, rule: str):
    """组合引擎（I-6 重写）：10 万 / 2 只 / 单票 2 万 / ≤4 成仓 / 4 规则选股。

    与 I-5 simulate() 同构（复用其资金模型 / 双成本口径 / T+1 校验 / 盯市权益），
    差异仅在：budget=PER_POSITION_BUDGET、4 成仓显式校验、trade 带 selection_rule。
    """
    G = ctx["G"]
    symbols = ctx["symbols"]
    close_ffill = ctx["close_ffill"]
    meta = ctx["meta"]

    cash_p = INITIAL_CAPITAL
    cash_c = INITIAL_CAPITAL
    positions: Dict[int, Dict[str, Any]] = {}
    equity_p = np.empty(G, dtype=np.float64)
    equity_c = np.empty(G, dtype=np.float64)
    pos_count = np.empty(G, dtype=np.int16)
    trades: List[Dict[str, Any]] = []
    t1_violations = 0
    skipped_insufficient_capital = 0
    skipped_parallel_cap = 0
    skipped_exposure_cap = 0
    traded_syms = set()
    cash_util_sum = 0.0

    if len(ev_buy):
        bs = np.searchsorted(ev_buy["g"], np.arange(G), side="left")
        be = np.searchsorted(ev_buy["g"], np.arange(G), side="right")
    else:
        bs = be = np.zeros(G, dtype=np.int64)
    if len(ev_sell):
        ss = np.searchsorted(ev_sell["g"], np.arange(G), side="left")
        se = np.searchsorted(ev_sell["g"], np.arange(G), side="right")
    else:
        ss = se = np.zeros(G, dtype=np.int64)

    def do_sell(sy: int, px: float, bar: int, dt: int, g: int, reason: str):
        nonlocal cash_p, cash_c, t1_violations
        pos = positions.pop(sy, None)
        if pos is None:
            return
        sym = symbols[sy]
        if not (px > 0 and np.isfinite(px)):
            px = pos["entry_px"]
        sell_turnover = pos["shares"] * px
        comm_s, trig_s, prop_s = i5.commission_yuan(sell_turnover)
        other_s_p = 0.0
        other_s_c = sell_turnover * EXTRA_SIDE_RATE
        cash_p += sell_turnover - comm_s - other_s_p
        cash_c += sell_turnover - comm_s - other_s_c
        buy_comm = pos["buy_comm"]
        buy_other_c = pos["buy_other_c"]
        cost_p = buy_comm + comm_s
        cost_c = buy_comm + comm_s + buy_other_c + other_s_c
        gross = sell_turnover - pos["buy_turnover"]
        net = gross - cost_p
        net_c = gross - cost_c
        t1ok = bool(bar > pos["entry_bar"] and dt > pos["entry_date"])
        if not t1ok:
            t1_violations += 1
        trades.append({
            "selection_rule": rule, "factor_id": fid, "symbol": sym,
            "entry_date": int(pos["entry_date"]), "exit_date": int(dt),
            "entry_bar": int(pos["entry_bar"]), "exit_bar": int(bar),
            "entry_g": int(pos["entry_g"]), "exit_g": int(g),
            "hold_trading_days": int(g - pos["entry_g"]),
            "entry_px": round(pos["entry_px"], 4), "exit_px": round(float(px), 4),
            "shares": int(pos["shares"]),
            "buy_turnover_yuan": round(pos["buy_turnover"], 4),
            "sell_turnover_yuan": round(sell_turnover, 4),
            "gross_yuan": round(gross, 4),
            "cost_items_yuan": {
                "buy_commission_yuan": round(buy_comm, 6),
                "sell_commission_yuan": round(comm_s, 6),
                "buy_commission_proportional_yuan": round(pos["buy_prop"], 6),
                "sell_commission_proportional_yuan": round(prop_s, 6),
                "buy_min_commission_triggered": bool(pos["buy_trig"]),
                "sell_min_commission_triggered": bool(trig_s),
                "transfer_fee_buy_yuan": 0.0, "transfer_fee_sell_yuan": 0.0,
                "slippage_buy_yuan": 0.0, "slippage_sell_yuan": 0.0,
                "stamp_duty_sell_yuan": STAMP_DUTY,
                "cost_roundtrip_yuan_primary": round(cost_p, 6),
                "cost_roundtrip_yuan_conservative": round(cost_c, 6),
            },
            "cost_yuan_primary": round(cost_p, 6),
            "cost_yuan_conservative": round(cost_c, 6),
            "net_yuan_primary": round(net, 6),
            "net_yuan_conservative": round(net_c, 6),
            "exit_reason": reason,
            "t1_ok": t1ok,
        })

    for g in range(G):
        # 1) 当日卖出执行（t 收盘出信号 → t+1 open 成交；先卖释放现金）
        for q in range(int(ss[g]), int(se[g])):
            e = ev_sell[q]
            do_sell(int(e["sy"]), float(e["px"]), int(e["bar"]), int(e["date"]), g, "signal_exit")

        # 2) 当日买入候选 → 选股排序（ev_buy 已按本规则预排）→ 分仓
        slots = MAX_POSITIONS - len(positions)
        if slots > 0:
            for q in range(int(bs[g]), int(be[g])):
                if slots <= 0:
                    skipped_parallel_cap += 1
                    break
                e = ev_buy[q]
                sy = int(e["sy"])
                if sy in positions:
                    continue
                px = float(e["px"])
                if not (px > 0 and np.isfinite(px)):
                    continue
                shares = int(PER_POSITION_BUDGET / px / LOT) * LOT
                if shares < LOT:
                    skipped_insufficient_capital += 1
                    continue
                turnover = shares * px
                comm_b, trig_b, prop_b = i5.commission_yuan(turnover)
                need_p = turnover + comm_b
                # 买入前现金校验（成交额 + 买入佣金 ≤ 可用现金）
                if need_p > cash_p + 1e-9:
                    afford = int((cash_p - MIN_COMM_YUAN) / (px * (1.0 + COMM_RATE)) / LOT) * LOT
                    shares = min(shares, max(afford, 0))
                    if shares < LOT:
                        skipped_insufficient_capital += 1
                        continue
                    turnover = shares * px
                    comm_b, trig_b, prop_b = i5.commission_yuan(turnover)
                    need_p = turnover + comm_b
                    if need_p > cash_p + 1e-9:
                        skipped_insufficient_capital += 1
                        continue
                # 4 成仓显式校验（固定初始资金口径，开仓时按买入成交额口径）
                exposure_now = sum(p["buy_turnover"] for p in positions.values())
                if exposure_now + turnover > EXPOSURE_CAP_YUAN + 1e-9:
                    skipped_exposure_cap += 1
                    continue
                other_b_c = turnover * EXTRA_SIDE_RATE
                need_c = turnover + comm_b + other_b_c
                cash_p -= need_p
                cash_c -= need_c
                positions[sy] = {
                    "entry_date": int(e["date"]), "entry_bar": int(e["bar"]), "entry_g": g,
                    "entry_px": px, "shares": shares, "buy_turnover": turnover,
                    "buy_comm": comm_b, "buy_trig": bool(trig_b), "buy_prop": prop_b,
                    "buy_other_c": other_b_c,
                }
                traded_syms.add(sy)
                slots -= 1

        # 3) 末尾未平仓强平（最后交易日，按各股最后一根 close；如实计入 end_force_close）
        if g == G - 1 and positions:
            for sy in list(positions.keys()):
                mt = meta[symbols[sy]]
                do_sell(sy, mt["last_close"], mt["last_bar"], mt["last_date"], g, "end_force_close")

        # 4) 逐日盯市权益曲线 = 可用现金 + 持仓市值（严禁逐笔盈亏无条件累加）
        mv = 0.0
        for sy, pos in positions.items():
            p = close_ffill[sy, g]
            if np.isfinite(p):
                mv += pos["shares"] * float(p)
            else:
                mv += pos["shares"] * pos["entry_px"]
        equity_p[g] = cash_p + mv
        equity_c[g] = cash_c + mv
        pos_count[g] = len(positions)
        if equity_p[g] > 0:
            cash_util_sum += 1.0 - cash_p / equity_p[g] if cash_p > 0 else 1.0

    return {
        "equity_p": equity_p, "equity_c": equity_c, "pos_count": pos_count,
        "trades": trades, "t1_violations": t1_violations,
        "skipped_insufficient_capital": skipped_insufficient_capital,
        "skipped_parallel_cap": skipped_parallel_cap,
        "skipped_exposure_cap": skipped_exposure_cap,
        "n_traded_syms": len(traded_syms), "cash_util_sum": cash_util_sum,
    }


def main() -> int:
    t0 = time.perf_counter()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    gate = i3.load_gate_from_yaml()
    cost_gate = i3.load_cost_gate_from_yaml()
    pfp = i3.param_fingerprint()
    print(f"[gate] {gate} | cost_gate={cost_gate}", flush=True)
    print(f"[param] config sha256-16={pfp['sha256_16']}（应为 31fbb054cddb55af，一字未改）", flush=True)
    print(f"[params] INITIAL_CAPITAL={INITIAL_CAPITAL} MAX_POSITIONS={MAX_POSITIONS} "
          f"MAX_EXPOSURE_RATIO={MAX_EXPOSURE_RATIO} EXPOSURE_CAP_YUAN={EXPOSURE_CAP_YUAN} "
          f"PER_POSITION_BUDGET={PER_POSITION_BUDGET} basis={EXPOSURE_BASIS}", flush=True)

    # 数据层 / 信号层：只读复用 I-5（逐字不改）；信号只算一次，60 组共享
    ctx = i5.load_context()
    events, n_skipped_tail, n_short, sig_seconds = i5.build_all_factor_events(ctx)
    t_signals = time.perf_counter()

    manifest = ctx["manifest"]
    fids = [f["id"] for f in i3.FACTORS]
    fac_by_id = {f["id"]: f for f in i3.FACTORS}

    # ④ 名录排除：本机不可可靠读出 → 不落盘名录、不执行排除（如实留痕）
    roster_exclusion = {
        "current_st": "NOT_APPLIED_UNRELIABLE_SOURCE",
        "current_st_evidence": "ds_stk.dat 的 192 个『代码+ST名称』命中全部为新三板/北交(43x/83x/87x/92x)、沪深主板 A 股 0 命中；profile.dat 为历史名称变更序列(000007 深达声→ST达声 / 000008 深锦兴A→亿安科技)；两表 ST 代码交集 0 → 不构成可靠『当前 ST 名录』",
        "historical_st": "MISSING",
        "shareholder_reduction_announcements": "MISSING",
        "delisting_full_roster": "MISSING",
        "excluded_count": 0,
        "roster_files_written": 0,
        "survivorship_bias_note": "vipdoc 是当前在市 .day 快照，退市股日线文件根本不在池里 →『排除退市名录』只能确认池内当前无退市股，无法消除回测期内『后来退市的股票当时应在池里』造成的偏差；要真正消偏差需历史全名录 + 退市股历史日线",
        "forbidden_confirmed": "未编造名录、未把当前 ST 冒充历史 ST 全过滤、未把『确认存在』写成『已落盘/已排除』",
    }

    runs: List[Dict[str, Any]] = []
    trade_dump: Dict[str, Dict[str, List[Dict[str, Any]]]] = {r: {} for r in RULES}
    by_key: Dict[tuple, Dict[str, Any]] = {}

    for rule in RULES:
        for fid in fids:
            ev = events[fid]
            ev_buy = order_buy(ev["buy"], rule, fid)          # 只重排进场候选
            sim = simulate_i6(ctx, ev_buy, ev["sell"], fid, rule)
            # 复用 I-5 逐因子申报/门槛计算（逐字不改的只读函数）
            row = i5.make_factor_result(fid, fac_by_id[fid], sim, INITIAL_CAPITAL,
                                        MAX_POSITIONS, gate, cost_gate, ctx["bench_A"])
            row["selection_rule"] = rule
            row["selection_rule_def"] = SELECTION_RULES[rule]
            row["batch"] = "I-6"
            row["is_optimization"] = IS_OPTIMIZATION
            row["adjusted"] = ADJUSTED
            row["exposure_cap_yuan"] = EXPOSURE_CAP_YUAN
            row["per_position_budget_yuan"] = PER_POSITION_BUDGET
            row["skipped_exposure_cap"] = int(sim["skipped_exposure_cap"])
            runs.append(row)
            by_key[(rule, fid)] = row
            trade_dump[rule][fid] = sim["trades"]
            print(f"[run] {rule}/{fid} 成交 {row['n_trades']} 笔 / t1违规 {row['t1_violations']} / "
                  f"年化 {row['annualized_return_28y']} / 回撤 {row['primary']['max_drawdown']} / "
                  f"可判定通过 {row['decidable_4plus_trades_pass']} / exposure跳过 {sim['skipped_exposure_cap']}",
                  flush=True)
        print(f"[rule] {rule} 完成（15 因子）", flush=True)
    t_sim = time.perf_counter()

    # ---- 规则对照表：逐因子 × 4 规则（净收益 / 年化 / 回撤 / 达标数）----
    rule_comparison: List[Dict[str, Any]] = []
    for fid in fids:
        rc: Dict[str, Any] = {"factor_id": fid, "name": fac_by_id[fid]["name"], "by_rule": {}}
        for rule in RULES:
            r = by_key[(rule, fid)]
            rc["by_rule"][rule] = {
                "net_yuan_primary": r["net_total_yuan_primary"],
                "annualized_return": r["annualized_return_28y"],
                "max_drawdown": r["primary"]["max_drawdown"],
                "calmar": r["primary"]["calmar"],
                "max_consec_losses": r["max_consec_losses"],
                "n_trades": r["n_trades"],
                "decidable_4plus_trades_pass": r["decidable_4plus_trades_pass"],
            }
        rule_comparison.append(rc)

    # ---- 门槛计数 + 两个达标数区分 ----
    gate_counts_by_rule: Dict[str, Dict[str, int]] = {}
    for rule in RULES:
        rr = [by_key[(rule, f)] for f in fids]
        gate_counts_by_rule[rule] = {
            "1_max_drawdown_le_10pct": sum(1 for r in rr if r["gates"]["1_max_drawdown_le_10pct"]),
            "2_calmar_ge_1p5": sum(1 for r in rr if r["gates"]["2_calmar_ge_1p5"]),
            "3_cost_le_50pct_of_gross": sum(1 for r in rr if r["gates"]["3_cost_le_50pct_of_gross"]),
            "4_max_consec_losses_le_5": sum(1 for r in rr if r["gates"]["4_max_consec_losses_le_5"]),
            "min_trades_ge_30": sum(1 for r in rr if r["gates"]["min_trades_ge_30"]),
            "n_pass_decidable_4plus_trades": sum(1 for r in rr if r["decidable_4plus_trades_pass"]),
        }
    n_pass_decidable = sum(1 for r in runs if r["decidable_4plus_trades_pass"])
    total_t1 = sum(r["t1_violations"] for r in runs)
    total_trades = sum(r["n_trades"] for r in runs)
    trig_total = sum(r["min_commission_5yuan"]["triggered_sides"] for r in runs)
    sides_total = sum(r["min_commission_5yuan"]["total_sides"] for r in runs)
    total_exposure_skips = sum(r["skipped_exposure_cap"] for r in runs)

    # ★ summary.n_stocks_actually_traded：按 _trades.json 逐笔去重正确统计（修正 I-5 的 15 瑕疵）
    all_syms = set()
    per_rule_syms: Dict[str, set] = {r: set() for r in RULES}
    for rule in RULES:
        for fid in fids:
            for t in trade_dump[rule][fid]:
                all_syms.add(t["symbol"])
                per_rule_syms[rule].add(t["symbol"])
    n_stocks_actually_traded = len(all_syms)

    result: Dict[str, Any] = {
        "batch": "I-6",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "role": "backend_architect",
        "phase": "阶段二 · 选股规则对照分离（复权重跑挂起）",
        "meta": {
            "boss_directive_verbatim": "① 补复权因子后重跑；② 换选股规则做对照分离；③ 组合参数（10 万 / 2 只 / 等权）；最多只开4成仓；④ 排除ST及减持、·退市名录；",
            "adjustment_status": "SUSPENDED",
            "adjustment_status_note": "复权三路径均未达到『拿到且验证通过』→ 正式复权重跑挂起；本批 ②③ 使用未复权数据，不把未复权冒充复权；报告标题与结论须显式写『复权重跑挂起、本批仅规则对照、非最终结论、不作上线依据』",
            "is_optimization": IS_OPTIMIZATION,
            "optimization_label": "参数/规则敏感性对照，非寻优、不挑最优",
            "attribution_boundary": "4 规则共享同一未复权数据 → 规则间差异可归因于规则；共同的部分不能据此归因于策略优劣；不得把『换规则后数字变好』写成策略达标/可上线",
            "portfolio_params": {
                "INITIAL_CAPITAL": INITIAL_CAPITAL,
                "MAX_POSITIONS": MAX_POSITIONS,
                "MAX_EXPOSURE_RATIO": MAX_EXPOSURE_RATIO,
                "EXPOSURE_CAP_YUAN": EXPOSURE_CAP_YUAN,
                "PER_POSITION_BUDGET": PER_POSITION_BUDGET,
                "exposure_basis": EXPOSURE_BASIS,
                "exposure_basis_reason": EXPOSURE_BASIS_REASON,
                "equal_weight": True,
                "LOT": LOT,
            },
            "selection_rules": SELECTION_RULES,
            "n_rules": len(RULES),
            "n_factors": len(fids),
            "n_runs": len(runs),
            "random_rule": {
                "seed": RANDOM_SEED,
                "method": "splitmix64-style deterministic hash over (seed, fid, g, sy, bar)",
                "reproducible": True,
                "no_lookahead": True,
                "note": "D 规则随机序完全由固定 seed + 事件自身的 g/sy/bar（当期已知）决定，与数组顺序/运行次数无关；不含未来函数",
            },
            "signal_layer": "只读复用 I-3 a_share_technical_factors.py 的 FACTORS / compute_target() 逐字不改（15 因子、WARM=60、MIN_HOLD_BARS=1）；不铺网格/不寻优/不改 src/",
            "engine_reuse": "只读复用 I-5 a_share_portfolio_factors.py 的 load_context() / build_all_factor_events() / curve_metrics() / commission_yuan() / make_factor_result() 与费率常量；成交层 simulate_i6() 因『10万/2只/≤4成仓 + 4 规则』在本脚本重写，I-5 脚本一字未动",
            "cash_model": "逐日盯市权益曲线 = 可用现金 + 持仓市值；买入前现金校验；严禁逐笔盈亏无条件累加",
            "execution": "t 收盘出信号 → t+1 open 真实股价成交；100 股整数倍取整；T+1（MIN_HOLD_BARS=1，逐笔 exit_date>entry_date、exit_bar>entry_bar）；末尾未平仓 end_force_close 如实计入；纯多头/无做空/无配对/无对冲",
            "fee_rule": "佣金金额 = max(成交额 × 0.0005, 5 元)，买/卖各自独立按金额计（金额规则、非费率变更）；印花税 0；主口径过户/滑点 0；保守对照加回过户 0.001%/边 + 滑点 5bp/边（按金额计）",
            "fee_forbidden_wording": "严禁写『费率被抬到 0.5%』『等效 50bp』『成本抬高 10 倍』；成本一律以『元』逐项列示；不得说『A 股费率低一个数量级』",
            "extreme_gate": EXTREME_GATE,
            "extreme_gate_note": "老板 17:34「先不判极端行情」，方向改后未撤回 → 沿用；判定 = 4 条硬门槛 + 笔数≥30；n_passed_final 标非最终达标/未含极端段；第 5 条不判/不记通过/不记 N/A/不用 0 占位",
            "config_readonly": True,
            "param_fingerprint": pfp["sha256_16"],
            "adjusted": ADJUSTED,
            "years_28": YEARS_28,
            "roster_exclusion": roster_exclusion,
        },
        "summary": {
            "n_stocks_in_pool": manifest["included"]["total_by_code_prefix"],
            "n_stocks_actually_converted": manifest["coverage"]["n_stocks_actually_converted"],
            "n_stocks_actually_traded": n_stocks_actually_traded,
            "n_stocks_actually_traded_method": "按 a_share_i6_selection_control_trades.json 中 60 组逐笔的 symbol 合并去重实算（修正 I-5 summary=15 的瑕疵；非『有成交的组数』）",
            "n_stocks_actually_traded_per_rule": {r: len(per_rule_syms[r]) for r in RULES},
            "n_bars": ctx["total_bars"],
            "n_trading_days": ctx["G"],
            "n_factors": len(fids),
            "n_rules": len(RULES),
            "n_runs": len(runs),
            "n_main_trades": total_trades,
            "n_pass_decidable_4plus_trades": n_pass_decidable,
            "n_passed_final": 0,
            "n_passed_final_label": "非最终达标 / 未含极端段（第 5 条按老板 17:34 指令不判；且复权重跑挂起、本批仅规则对照、非最终结论、不作上线依据）",
            "n_passed_final_reason": "极端段不判使判定未闭合，任何组不能判『最终达标』；严禁把 n_pass_decidable_4plus_trades 冒充 n_passed_final、严禁把 n_passed_final 写成 >0。且本批未复权、结果非可上线依据。",
            "gate_counts_by_rule": gate_counts_by_rule,
            "t1_violations_total": int(total_t1),
            "skipped_exposure_cap_total": int(total_exposure_skips),
            "skipped_exposure_cap_note": "4 成仓显式校验被触发而跳过的买入次数（0 表示 4 成仓约束从未被突破）",
            "min_commission_5yuan": {
                "triggered_sides": trig_total, "total_sides": sides_total,
                "trigger_ratio": round(trig_total / sides_total, 6) if sides_total else None,
            },
            "extreme_gate": EXTREME_GATE,
            "data_fingerprint": manifest["data_fingerprint_sha256_16"],
            "param_fingerprint": pfp["sha256_16"],
            "adjusted": ADJUSTED,
            "is_optimization": IS_OPTIMIZATION,
            "not_mixed": True,
            "not_mixed_note": "不与加密 257 组 / I-1 3 组 / I-4 / I-5 结果混算；0 达标就报 0；排序 ≠ 达标 ≠ 可上线",
            "actual_coverage": {
                "n_stocks_scanned": manifest["coverage"]["n_stocks_in_pool_by_code_prefix"],
                "n_stocks_converted": manifest["coverage"]["n_stocks_actually_converted"],
                "n_stocks_excluded_data_quality": manifest["coverage"]["n_stocks_excluded_data_quality"],
                "n_stocks_insufficient_history": manifest["coverage"]["n_stocks_insufficient_history"],
                "total_bars": ctx["total_bars"],
                "n_trading_days_union": ctx["G"],
                "library_earliest": manifest["coverage"]["library_earliest"],
                "library_latest": manifest["coverage"]["library_latest"],
                "mean_gap_rate": manifest["coverage"]["mean_gap_rate_since_listing"],
                "downsample_or_batching": manifest["coverage"]["downsample_or_batching"],
                "signals_computed": "15 因子 × 全部实际转换个股（只读复用 compute_target，无降采样）；信号只计算一次，4 规则共享",
                "tail_entry_signals_skipped": n_skipped_tail,
                "insufficient_history_stocks_in_signal_pass": n_short,
                "note": "28 年全量 1998-09-15~2026-11-13、不裁窗口、不抽样、不分段；全池 5687 只、无降采样；算力诚实，绝不把降采样写成全市场全量",
            },
            "runtime": {
                "load_seconds": ctx["load_seconds"],
                "signal_seconds": sig_seconds,
                "simulation_seconds": round(t_sim - t_signals, 3),
                "total_seconds": round(time.perf_counter() - t0, 3),
            },
        },
        "rule_comparison": rule_comparison,
        "runs": runs,
        "gates": {
            "frozen": gate,
            "cost_gate": cost_gate,
            "min_sharpe": None,
            "sharpe_hard_gate": "不设硬门槛（min_sharpe=null；夏普仅作参考值）",
            "counts_by_rule": gate_counts_by_rule,
            "n_pass_decidable_4plus_trades": n_pass_decidable,
            "n_passed_final": 0,
            "extreme_gate": EXTREME_GATE,
            "forbidden_confirmed": {
                "no_pair": True, "no_short": True, "no_hedge": True, "no_spread": True,
                "no_zscore_spread": True, "no_hedge_ratio_h": True, "no_etf_short_leg": True,
                "no_index_as_trade_price": True, "no_unconditional_pnl_accumulation": True,
                "no_gate_relax_on_rule_or_param_change": True,
            },
        },
        "compliance": {
            "local_readonly": True, "no_client_launch": True, "no_network_quotes": True,
            "no_registry_change": True, "src_unchanged": True,
            "config_unchanged": True, "config_sha256_16": pfp["sha256_16"],
            "no_bat_gitignore_env_change": True, "no_git_commit": True,
            "no_gate_relax": True, "no_live_trading": True, "no_token_key": True,
            "no_paid_source": True, "no_api_key": True,
            "old_scripts_preserved": True, "old_parquet_preserved": True,
            "no_placeholder_adjusted_parquet": True,
            "no_fabricated_roster": True, "no_unadjusted_as_adjusted": True,
            "no_g6_3_g5_2_rush": True, "no_beautify": True,
            "single_execution_body": True,
            "single_execution_note": "本环境为单一执行体、无独立 subagent 会话，两阶段按 data_engineer / backend_architect 两份 soul 纪律分别落地，非两角色各自独立会话",
            "no_429": True,
            "no_429_note": "本批无联网 / 无 API 调用（WebSearch 仅阶段一资料核实且均成功），未撞 429/503 限流，无需重试，如实登记",
        },
        "limits": [
            "🔴 复权重跑挂起：三路径（gbbq 3DES 需客户端内存 key / base.dbf+gpcw 无复权字段 / 免费公开源仅资料核实）均未拿到且验证通过；全部结果 adjusted=false、非可上线依据",
            "🔴 幸存者偏差：vipdoc 为当前在市 .day 快照，退市股日线文件根本不在池里；排除退市名录只能确认池内当前无退市股，无法消除回测期内偏差；需历史全名录 + 退市股历史日线",
            "🔴 当前 ST 源不可靠、历史 ST / 股东减持 / 退市全名录本机缺 → 本批未落盘名录、未执行排除",
            "🔴 涨跌停 / 停牌 / ST / LOCKED 未建模（.day 无标记，可能成交在不可成交日）",
            "🔴 1990-12-19~1998-09-14 约 8 年日线本机缺失",
            "🟡 过户费/滑点主口径 0（老板未指定）；保守对照 0.001%/边 + 5bp/边为待老板确认的假设",
            "🟡 基准 A 沿用 I-5 的 100 万等权买入持有年化（+6.09%），仅作年化参照，不与本批 10 万口径混算、不作达标判定",
            "🟡 规则对照为『参数/规则敏感性对照，非寻优、不挑最优』；规则间差异可归因于规则，共同部分不能归因于策略优劣",
        ],
    }

    jp = OUT_DIR / "a_share_i6_selection_control.json"
    jp.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    tp = OUT_DIR / "a_share_i6_selection_control_trades.json"
    tp.write_text(json.dumps({
        "batch": "I-6",
        "note": "60 组（4 规则 × 15 因子）primary 全量逐笔；conservative 同成交时序仅成本金额不同（见主 json cost_items_yuan 双口径）；"
                "summary.n_stocks_actually_traded 按本文件全部逐笔 symbol 合并去重实算。",
        "rules": trade_dump,
    }, ensure_ascii=False, default=str), encoding="utf-8")

    print(f"\n[OUT] {jp}", flush=True)
    print(f"[OUT] {tp}", flush=True)
    print(f"[SUMMARY] n_runs={len(runs)} n_main_trades={total_trades} "
          f"n_stocks_actually_traded={n_stocks_actually_traded} "
          f"n_pass_decidable_4plus_trades={n_pass_decidable} n_passed_final=0（非最终达标）", flush=True)
    print(f"[T1] violations_total={total_t1} | [EXPOSURE] skipped={total_exposure_skips} | "
          f"[MIN5] {trig_total}/{sides_total} 边触发", flush=True)
    print(f"[RUNTIME] {result['summary']['runtime']}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
