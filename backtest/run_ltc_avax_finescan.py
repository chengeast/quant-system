"""任务 B · LTC/AVAX 1h 参数细扫（秘书派活单批次 B，PM 分派后端架构师落实）

【范围】只做任务 B，不抢跑 C；只做 1h（4h/1d 样本太薄，不在本批范围）。
  币对锁定 leg_a=LTC, leg_b=AVAX, benchmark=BTC，只读已落盘 1h_410d parquet。

【9 组】围绕粗扫锚点 e=3.5/tp=2.5/sl=2.0 的邻域，采用单变量扰动（OAT）：
  中心 1 组 + entry 3 组 + tp 3 组 + sl 2 组 = 9 组，避免 48 组全扫。
  全部取自 entry∈{2.5,3.0,3.5,4.0} / tp∈{1.5,2.0,2.5,3.0} / sl∈{1.0,1.5,2.0}。

【同口径】完全复用 backtest/run_strategy_with_rules.py 的 run_one_timeframe，
  与 75 组脚本 backtest/run_pair_grid.py 调用签名、GATE、StrategyRules 固定项一致：
  timeout_hours=3.0 / timeout_half_life_factor=1.2 / position_cap=0.05，不擅改。
  不重造回测、不改 src/、不改 config/strategy_params.yaml 冻结门槛、不拉新数据。

【产出】
  backtest/output/ltc_avax_finescan.json  9 组全量（含 check_gates 全项、失败 ERR）
  backtest/output/ltc_avax_finescan.md    可读报告（参数表 / Top1 / 对照 / 判定 / 结论）

【D-2 提交前清理（2026-11-09）】
  派活单给定的「LTC/AVAX 1h 夏普 0.473」经秘书核实无任何盘上产物，已认错作废；
  原「0.473 对照常量」及以其为基准的对照文案已删除并改为「作废声明」，
  对照基准统一改用盘上可回读实测值（0.2376 中心点 / 0.499 75 组冠军 / 0.5550 细扫 Top1）。
  本次只清理作废基准文案：未改回测逻辑、未改 9 组参数网格、未改产物结构。

运行：
  python backtest/run_ltc_avax_finescan.py
"""
from __future__ import annotations

import json
import sys
import traceback
import warnings
from pathlib import Path
from typing import Any, Dict, List

warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from backtest.run_strategy_with_rules import (  # noqa: E402
    run_one_timeframe, GATE, _rules_dict,
)
from src.decision_layer.mean_reversion_strategy import StrategyRules  # noqa: E402

CACHE = "data/cache"
LEG_A, LEG_B, BENCH = "LTC", "AVAX", "BTC"
TF = "1h"
OUT_DIR = Path("backtest/output")

GATE_TEXT = "回撤≤10% / 夏普≥2.0 / Calmar≥1.5 / 成本≤毛利35% / 极端段不亏 / 连亏≤5"

# ---- 对照基准（2026-11-09 D-2 提交前清理）----
# ⚠️ 作废声明：派活单曾给定的「LTC/AVAX 1h e=3.5/tp=2.5/sl=2.0 夏普 0.473」
#    经秘书核实无任何盘上产物，已向老板认错作废；禁止再作为结果或对照基准引用。
#    下列常量全部取自盘上可回读的实测产物，不含任何作废值：
#      0.2376 = 本脚本 9 组细扫中心点（3.5/2.5/2.0）同口径复跑夏普
#               （backtest/output/ltc_avax_finescan.json 第 1 组）
#      0.499  = 75 组粗扫冠军 ETH/SOL 1h 3.5/2.5/2.0 夏普 0.49883
#               （backtest/output/pair_param_sweep.json 全局 Top1，达标 0）
#      0.5550 = 本脚本 9 组细扫 Top1（4.0/2.5/2.0）夏普
#               （backtest/output/ltc_avax_finescan.json 第 4 组）
#      1.2093 = C-2b 125 组扩池网格全局最高夏普（ETH/AVAX 1d 3.5/2.5/2.0，
#               backtest/output/pair_param_sweep_avax_ada.json，达标仍 0）；仅登记，不作本脚本基准
VOID_0_473_NOTE = "0.473 已作废：无盘上产物，禁止作结果/对照基准（2026-11-09 秘书认错作废）"
COARSE_TOP1_LABEL = "ETH/SOL 1h (3.5/2.5/2.0)"
COARSE_TOP1_SHARPE = 0.499
CENTER_SHARPE = 0.2376
FINE_TOP1_SHARPE = 0.5550
C2B_TOP1_SHARPE = 1.2093

# check_gates 返回的全项（engine.py:708-719），逐项如实列出，不增删
GATE_KEYS = [
    ("net_positive", "净收益为正"),
    ("beat_benchmark", "跑赢BTC"),
    ("max_dd", "回撤≤10%"),
    ("sharpe", "夏普≥2.0"),
    ("calmar", "Calmar≥1.5"),
    ("extreme_survived", "极端段不亏"),
]

# 9 组 OAT 邻域（中心 + 每次只动一个维度）
FINE_PARAMS: List[Dict[str, Any]] = [
    dict(entry=3.5, tp=2.5, sl=2.0, tag="中心·粗扫锚点"),
    dict(entry=2.5, tp=2.5, sl=2.0, tag="entry↓"),
    dict(entry=3.0, tp=2.5, sl=2.0, tag="entry↓"),
    dict(entry=4.0, tp=2.5, sl=2.0, tag="entry↑"),
    dict(entry=3.5, tp=1.5, sl=2.0, tag="tp↓"),
    dict(entry=3.5, tp=2.0, sl=2.0, tag="tp↓"),
    dict(entry=3.5, tp=3.0, sl=2.0, tag="tp↑"),
    dict(entry=3.5, tp=2.5, sl=1.0, tag="sl↓"),
    dict(entry=3.5, tp=2.5, sl=1.5, tag="sl↓"),
]


def _fmt(v: Any, nd: int = 3) -> str:
    try:
        return f"{float(v):.{nd}f}"
    except Exception:
        return "N/A"


def _fmt_pct(v: Any) -> str:
    try:
        return f"{float(v) * 100:+.2f}%"
    except Exception:
        return "N/A"


def _fmt_dd(v: Any) -> str:
    """回撤是绝对幅度，不带正负号（避免 '+1.41%' 被误读为正收益）。"""
    try:
        return f"{float(v) * 100:.2f}%"
    except Exception:
        return "N/A"


def _flag(v: Any) -> str:
    if isinstance(v, str):  # 兼容 JSON 读回后的 "True"/"False" 字符串
        return "✅" if v.strip().lower() == "true" else "❌"
    return "✅" if bool(v) else "❌"


def _jsonable(o: Any) -> Any:
    """把 numpy 标量（np.bool_/np.float64 等）转成 Python 原生类型再落 JSON，
    避免 check_gates 的布尔项被 default=str 写成字符串 'True'/'False'
    （下游 all(gp.values()) 会把 'False' 当真）。不改引擎，仅规范化产物。"""
    if isinstance(o, dict):
        return {k: _jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_jsonable(v) for v in o]
    if isinstance(o, bool) or o is None:
        return o
    if hasattr(o, "item"):
        try:
            return _jsonable(o.item())
        except Exception:
            pass
    if isinstance(o, (int, float, str)):
        return o
    return str(o)


def run_all() -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    total = len(FINE_PARAMS)
    for i, p in enumerate(FINE_PARAMS, 1):
        print(f"[{i}/{total}] {LEG_A}/{LEG_B} {TF} "
              f"e={p['entry']} tp={p['tp']} sl={p['sl']} ({p['tag']})", flush=True)
        rules = StrategyRules(
            entry_sigma=p["entry"],
            take_profit_sigma_from_entry=p["tp"],
            stop_loss_sigma_from_entry=p["sl"],
            timeout_hours=3.0,
            timeout_half_life_factor=1.2,
            position_cap=0.05,
        )
        base = dict(idx=i, pair=f"{LEG_A}/{LEG_B}", tf=TF,
                    entry=p["entry"], tp=p["tp"], sl=p["sl"], tag=p["tag"])
        try:
            # 与 75 组脚本 run_pair_grid.py 完全同口径（含两个 real_*_days 参数）
            r = run_one_timeframe(
                TF, rules, CACHE, LEG_A, LEG_B, BENCH, GATE,
                real_4h_days=400, real_1d_days=900,
            )
            m = r.metrics
            gp = m.get("gate_pass", {}) or {}
            passed = bool(gp) and all(gp.values())
            rec = dict(
                **base,
                trades=r.n_trades,
                net_peak=m.get("peak_total_return"),
                net_steady=m.get("steady_total_return"),
                maxdd=m.get("max_drawdown"),
                sharpe=m.get("sharpe"),
                calmar=m.get("calmar"),
                beat_benchmark=m.get("beat_benchmark"),
                extreme_pnl=m.get("extreme_pnl"),
                extreme_survived=m.get("extreme_survived"),
                cost_ok=m.get("cost_executable"),
                cost_paid=m.get("cost_paid"),
                max_consec=m.get("max_consecutive_losses", 0),
                gate_pass=gp,
                passed=passed,
                bars=r.data_bars,
                source=r.source.get(TF, "?"),
                rules=_rules_dict(rules),
                errors=r.errors,
            )
            if r.errors:
                rec["engine_error"] = "; ".join(str(e) for e in r.errors)[:200]
            results.append(rec)
            print(f"    bars={r.data_bars} trades={r.n_trades} "
                  f"sharpe={_fmt(rec['sharpe'])} calmar={_fmt(rec['calmar'])} "
                  f"maxdd={_fmt_pct(rec['maxdd'])} cost_ok={rec['cost_ok']} "
                  f"passed={passed}", flush=True)
        except Exception as e:
            # 失败标 ERR，不吞掉：落完整异常类型 + 消息 + 末尾堆栈
            rec = dict(
                **base,
                error=f"{type(e).__name__}: {e}"[:200],
                trace=traceback.format_exc()[-600:],
                bars=0,
            )
            results.append(rec)
            print(f"    ERR {rec['error']}", flush=True)
    return results


def build_report(results: List[Dict[str, Any]]) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / "ltc_avax_finescan.md"
    ok = [x for x in results if "error" not in x]
    err = [x for x in results if "error" in x]
    by_sharpe = sorted(ok, key=lambda x: (x.get("sharpe") if x.get("sharpe") is not None else -1e9),
                       reverse=True)
    top1 = by_sharpe[0] if by_sharpe else None

    L: List[str] = []
    L.append("# 任务 B · LTC/AVAX 1h 参数细扫报告")
    L.append("")
    L.append("日期：2026-11-09  ")
    L.append(f"币对：leg_a={LEG_A} / leg_b={LEG_B} / benchmark={BENCH}  ")
    L.append("周期：**仅 1h**（4h/1d 样本太薄，不在本批范围）  ")
    L.append("数据：`data/cache/*_USDT_1h_410d.parquet`（LTC/AVAX/BTC，只读已落盘，不拉新数据）  ")
    L.append("参数：9 组 OAT 邻域（中心 3.5/2.5/2.0 + 单变量扰动）；"
             "entry∈{2.5,3.0,3.5,4.0} / tp∈{1.5,2.0,2.5,3.0} / sl∈{1.0,1.5,2.0}  ")
    L.append("固定项（与 75 组同口径，不擅改）：timeout_hours=3.0 / "
             "timeout_half_life_factor=1.2 / position_cap=0.05  ")
    L.append(f"冻结门槛（不放宽）：{GATE_TEXT}  ")
    L.append("接口：复用 `run_strategy_with_rules.run_one_timeframe` + `GATE`；"
             "门槛判定走 `check_gates`，未改 `src/`、未改冻结门槛、未重造回测。")
    L.append("")

    # ---- §1 运行概览 ----
    L.append("## 1. 运行概览")
    L.append("")
    L.append(f"- 应跑 **9** 组；跑通 **{len(ok)}** 组；失败 ERR **{len(err)}** 组。")
    bars = [x.get("bars") for x in ok]
    if bars:
        L.append(f"- 跑通组 bars：min={min(bars)} / max={max(bars)}；"
                 f"全部≈9841 → {'✅ 一致' if all(b == 9841 for b in bars) else '⚠️ 存在偏差（见下表）'}。")
    L.append(f"- 数据来源：{'、'.join(sorted({str(x.get('source')) for x in ok})) or 'N/A'}"
             f"（LTC/AVAX/BTC 各 9841 bars，只读已落盘 1h_410d）。")
    if err:
        L.append("- 失败组（不吞掉，异常已落 JSON `error` / `trace` 字段）：")
        for x in err:
            L.append(f"  - 第 {x['idx']} 组 e={x['entry']}/tp={x['tp']}/sl={x['sl']}："
                     f"**ERR** {x['error']}")
    else:
        L.append("- 失败组：无。")
    L.append("")

    # ---- §2 9 组参数全量表 ----
    L.append("## 2. 9 组参数全量表")
    L.append("")
    L.append("| # | entry/tp/sl | 扰动 | 净峰值 | 夏普 | Calmar | 回撤 | 成本可执 | 成本($) | 成交笔 | bars | 门槛判定 |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for x in results:
        param = f"{x['entry']}/{x['tp']}/{x['sl']}"
        if "error" in x:
            L.append(f"| {x['idx']} | {param} | {x.get('tag','')} | "
                     f"ERR {x['error'][:28]} | | | | | | | 0 | **ERR** |")
            continue
        gate_txt = "✅ 通过" if x.get("passed") else "❌ 未达"
        L.append(
            f"| {x['idx']} | {param} | {x.get('tag','')} | "
            f"{_fmt_pct(x.get('net_peak'))} | {_fmt(x.get('sharpe'), 4)} | "
            f"{_fmt(x.get('calmar'), 4)} | {_fmt_dd(x.get('maxdd'))} | "
            f"{_flag(x.get('cost_ok'))} | {_fmt(x.get('cost_paid'), 2)} | "
            f"{x.get('trades')} | {x.get('bars')} | {gate_txt} |"
        )
    L.append("")
    L.append("> 「门槛判定」列 = `check_gates` 全项通过（与 75 组脚本 "
             "`passed = bool(gp) and all(gp.values())` 同口径）。"
             "「成本可执」= 引擎 `cost_executable`（口径 `cost_paid ≤ 0.35 × 毛利总额`，"
             "`engine.py:681`）；`metrics` 未暴露毛利总额，故不反算精确成本占比，避免脑补。")
    L.append("")

    # ---- §3 Top1 ----
    L.append("## 3. Top1")
    L.append("")
    if top1 is None:
        L.append("- 无可用的非 ERR 组，无法确定 Top1。")
    else:
        L.append(f"- **Top1 = 第 {top1['idx']} 组 e={top1['entry']}/tp={top1['tp']}/sl={top1['sl']}"
                 f"（{top1.get('tag','')}）**，夏普 **{_fmt(top1.get('sharpe'), 4)}**。")
        L.append(f"- 该组：净峰值 {_fmt_pct(top1.get('net_peak'))} / Calmar {_fmt(top1.get('calmar'), 4)} / "
                 f"回撤 {_fmt_dd(top1.get('maxdd'))} / 成本可执 {_flag(top1.get('cost_ok'))} / "
                 f"成交 {top1.get('trades')} 笔 / 门槛判定 "
                 f"{'✅ 通过' if top1.get('passed') else '❌ 未达'}。")
    L.append("")

    # ---- §4 门槛判定如实（check_gates 全项） ----
    L.append("## 4. 门槛判定如实（check_gates 全项）")
    L.append("")
    L.append("`check_gates`（`engine.py:708-719`）返回 6 项，逐组逐项列出，不增删、不脑补：")
    L.append("")
    L.append("| # | entry/tp/sl | " + " | ".join(name for _, name in GATE_KEYS) + " | 全项通过 |")
    L.append("|---|---|" + "---|" * (len(GATE_KEYS) + 1))
    for x in results:
        param = f"{x['entry']}/{x['tp']}/{x['sl']}"
        if "error" in x:
            L.append(f"| {x['idx']} | {param} | " + " | ".join(["—"] * len(GATE_KEYS)) + " | **ERR** |")
            continue
        gp = x.get("gate_pass", {}) or {}
        cells = [_flag(gp.get(k)) for k, _ in GATE_KEYS]
        L.append(f"| {x['idx']} | {param} | " + " | ".join(cells)
                 + f" | {_flag(x.get('passed'))} |")
    L.append("")
    L.append("### 冻结门槛 6 条逐条（含 check_gates 未覆盖项）")
    L.append("")
    L.append("> **如实标注**：`check_gates` 覆盖「回撤≤10% / 夏普≥2.0 / Calmar≥1.5 / 极端段不亏」"
             "（另含净收益为正、跑赢BTC）；**「成本≤毛利35%」与「连亏≤5」两条不在 "
             "`check_gates` 返回项内**，下表单列如实判定，不把「未检查」当作「通过」，不放宽。")
    L.append("")
    L.append("| # | entry/tp/sl | 回撤≤10% | 夏普≥2.0 | Calmar≥1.5 | 成本≤毛利35% | 极端段不亏 | 连亏≤5 | 6 条全过 |")
    L.append("|---|---|---|---|---|---|---|---|---|")
    for x in results:
        param = f"{x['entry']}/{x['tp']}/{x['sl']}"
        if "error" in x:
            L.append(f"| {x['idx']} | {param} | " + " | ".join(["—"] * 7) + " | **ERR** |")
            continue
        dd_ok = (x.get("maxdd") is not None) and (x["maxdd"] <= 0.10)
        sh_ok = (x.get("sharpe") is not None) and (x["sharpe"] >= 2.0)
        cal_ok = (x.get("calmar") is not None) and (x["calmar"] >= 1.5)
        cost_ok = bool(x.get("cost_ok"))
        ext_ok = bool(x.get("extreme_survived"))
        consec_ok = (x.get("max_consec") is not None) and (x["max_consec"] <= 5)
        all6 = dd_ok and sh_ok and cal_ok and cost_ok and ext_ok and consec_ok
        L.append(
            f"| {x['idx']} | {param} | {_flag(dd_ok)} | {_flag(sh_ok)} | {_flag(cal_ok)} | "
            f"{_flag(cost_ok)} | {_flag(ext_ok)} | {_flag(consec_ok)}({x.get('max_consec')}) | "
            f"{_flag(all6)} |"
        )
    L.append("")
    passed_all = [x for x in ok if x.get("passed")]
    L.append(f"- `check_gates` 全项通过组数：**{len(passed_all)} / {len(ok)}**（同 75 组口径）。")
    L.append("")

    # ---- §5 明确判定 ----
    L.append("## 5. 明确判定：夏普≥1.0 / 成本占比≤35%")
    L.append("")
    ge1 = [x for x in ok if (x.get("sharpe") is not None) and (x["sharpe"] >= 1.0)]
    cost_pass = [x for x in ok if bool(x.get("cost_ok"))]
    if ge1:
        L.append(f"- **夏普≥1.0：有 {len(ge1)} 组** → "
                 + "、".join(f"#{x['idx']}({_fmt(x['sharpe'],4)})" for x in ge1))
    else:
        mx = max((x.get("sharpe") for x in ok if x.get("sharpe") is not None), default=None)
        L.append(f"- **夏普≥1.0：无任何一组**（9 组最高夏普 {_fmt(mx, 4) if mx is not None else 'N/A'}，"
                 f"门槛 1.0 未触及）。**明确判定：无。**")
    if cost_pass:
        L.append(f"- **成本占比≤35%（cost_executable=True）：有 {len(cost_pass)} 组** → "
                 + "、".join(f"#{x['idx']}" for x in cost_pass))
    else:
        L.append("- **成本占比≤35%（cost_executable=True）：无任何一组**（9 组 `cost_executable` 全为 ❌）。"
                 "**明确判定：无。**")
    L.append("")
    L.append("> 口径说明：「成本占比≤35%」即引擎冻结门槛 `max_cost_as_pct_of_gross=0.35` 的判定"
             "（`cost_paid ≤ 0.35 × 毛利总额`），以 `cost_executable` 字段为准。")
    L.append("")

    # ---- §6 对照 75 组（D-2 清理：0.473 作废，基准改盘上实测值） ----
    L.append("## 6. 对照 75 组粗扫")
    L.append("")
    L.append(f"> ⚠️ **作废声明（2026-11-09 D-2 提交前清理）**：{VOID_0_473_NOTE}。"
             "本节及全文不再引用 0.473 作结果或对照基准；下列对照一律只用盘上可回读实测值。")
    L.append("")
    L.append(f"- 75 组粗扫 Top1：{COARSE_TOP1_LABEL} 夏普 **{COARSE_TOP1_SHARPE}**"
             f"（盘上 `pair_param_sweep.json` 实测 0.49883，达标 0）。")
    L.append(f"- 细扫中心点（e=3.5/tp=2.5/sl=2.0）同口径复跑夏普 **{CENTER_SHARPE}**"
             f"（盘上 `ltc_avax_finescan.json` 第 1 组）。")
    L.append(f"- 细扫 Top1 盘上登记值 **{FINE_TOP1_SHARPE}**"
             f"（`ltc_avax_finescan.json` 第 4 组；本脚本重跑 Top1 见 §3）。")
    L.append("")
    if top1 is None:
        L.append("- Top1 无法确定，以下对照不成立。")
    else:
        t1s = top1.get("sharpe")
        diff = t1s - COARSE_TOP1_SHARPE
        L.append(f"- **LTC/AVAX 细扫 Top1 夏普 {_fmt(t1s, 4)} vs ETH/SOL 1h 0.499："
                 f"{'超过' if diff > 0 else '未超过'}**（差值 {diff:+.4f}）。")
        # 中心点复跑（同口径）与盘上登记值核对
        center = next((x for x in ok if x.get("tag", "").startswith("中心")), None)
        if center is not None and center.get("sharpe") is not None:
            cs = center["sharpe"]
            d_c = cs - CENTER_SHARPE
            L.append(f"- 中心点 e=3.5/tp=2.5/sl=2.0 同口径复跑夏普 **{_fmt(cs, 4)}** "
                     f"vs 盘上登记值 {CENTER_SHARPE}（差值 {d_c:+.4f}）→ "
                     f"{'复现一致' if abs(d_c) < 0.02 else '与登记值有偏差（如实记录，不脑补）'}。")
        else:
            L.append("- 中心点未跑通，无法与盘上登记值 0.2376 对照。")
        d_t1 = t1s - CENTER_SHARPE
        L.append(f"- **细扫 Top1 相对盘上中心点 {CENTER_SHARPE}：{'有提升' if d_t1 > 0 else '无提升'}**"
                 f"（{_fmt(t1s,4)} vs {CENTER_SHARPE}，差值 {d_t1:+.4f}）。")
        if center is not None and center.get("sharpe") is not None:
            cs = center["sharpe"]
            L.append(f"- **补充**：细扫 Top1 相对「本次盘上同口径复跑中心点 "
                     f"{_fmt(cs,4)}」→ {'有提升' if t1s > cs else '无提升'}"
                     f"（差值 {t1s - cs:+.4f}）；该口径完全由本次 `run_one_timeframe` 实测，"
                     f"不含任何作废值。")
        L.append("")
        L.append("> 口径说明：0.2376（本脚本中心点）/ 0.499（75 组冠军）/ "
                 "0.5550（本脚本细扫 Top1）均为盘上 JSON 可回读实测值；"
                 "作废值 0.473 仅出现在「作废声明」上下文，不参与任何差值计算。"
                 f"C-2b 125 组扩池网格全局最高夏普 {C2B_TOP1_SHARPE} 另见 "
                 "`pair_param_sweep_avax_ada.md`，不作本脚本对照基准。")
    L.append("")

    # ---- §7 结论 ----
    L.append("## 7. 结论")
    L.append("")
    if top1 is None:
        L.append("- 无可用结果组，无法下结论。")
    else:
        t1s = top1.get("sharpe")
        L.append(f"- **扩池/细扫效果**：9 组邻域细扫最高夏普 {_fmt(t1s, 4)}"
                 f"（e={top1['entry']}/tp={top1['tp']}/sl={top1['sl']}），"
                 f"相对盘上中心点 {CENTER_SHARPE} "
                 f"{'有提升' if t1s > CENTER_SHARPE else '未提升'}"
                 f"（{t1s - CENTER_SHARPE:+.4f}）；"
                 f"相对 75 组全局 Top1 0.499 "
                 f"{'超过' if t1s > COARSE_TOP1_SHARPE else '未超过'}"
                 f"（{t1s - COARSE_TOP1_SHARPE:+.4f}）。")
        c2 = next((x for x in ok if x.get("tag", "").startswith("中心")), None)
        if c2 is not None and c2.get("sharpe") is not None:
            L.append(f"  - 按盘上同口径复跑中心点 {_fmt(c2['sharpe'],4)} 计，细扫把 entry "
                     f"3.5→4.0，夏普 {_fmt(c2['sharpe'],4)}→{_fmt(t1s,4)}"
                     f"（{t1s - c2['sharpe']:+.4f}），**细扫本身确有拉高**；"
                     f"作废值 0.473 不参与任何对照（见 §6 作废声明）。")
        L.append(f"- **是否接近/达到冻结门槛**："
                 f"{'接近' if t1s >= 1.0 else '远未接近'}（夏普门槛 2.0，Top1 仅 {_fmt(t1s,4)}，"
                 f"差 {2.0 - t1s:.4f}）；"
                 f"9 组中 `check_gates` 全项通过 {len(passed_all)} 组、成本可执 {len(cost_pass)} 组、"
                 f"夏普≥1.0 {len(ge1)} 组 → **无任何一组达标冻结门槛**。")
        L.append("- 门槛不放宽、不脑补：逐项未达标事实见 §2 / §4，按盘上 `check_gates` 全项如实申报。")
    L.append("")

    # ---- §8 产物 / 口径 / 限制 ----
    L.append("## 8. 产物与口径说明")
    L.append("")
    L.append("- JSON：`backtest/output/ltc_avax_finescan.json`（9 组全量，含 `gate_pass` 全项、"
             "ERR 组 `error`/`trace`）。")
    L.append("- 报告：`backtest/output/ltc_avax_finescan.md`（本文件）。")
    L.append("- 脚本：`backtest/run_ltc_avax_finescan.py`（`python backtest/run_ltc_avax_finescan.py` 可复跑）。")
    L.append("- 口径：`passed` 同 75 组脚本（`check_gates` 全项 and）；"
             "成本与连亏两条额外单列（§4），不并入 `passed`、不吞。")
    L.append("- 限制：只做 1h；4h/1d 因样本太薄不在本批范围；未拉新数据、未改 `src/`、"
             "未改冻结门槛；作废值 0.473 无盘上产物、已作废，全文仅见于「作废声明」，不作对照基准。")
    L.append("- JSON 类型：落盘前把 numpy 标量统一转 Python 原生类型，`gate_pass` 全项为布尔值"
             "（非字符串），供下游 `all(gp.values())` 正确消费；md 与 json 同批生成、口径一致。")
    L.append("- 已闭环：作废值 0.473 经秘书核实无盘上产物、已认错作废（见 §6 作废声明）；"
             "对照基准统一改用盘上实测 0.2376 / 0.499 / 0.5550，不再引用 0.473。")
    L.append("")
    out.write_text("\n".join(L), encoding="utf-8")
    return out


def main() -> int:
    print("== 任务 B · LTC/AVAX 1h 参数细扫（9 组，同 75 组口径）==", flush=True)
    results = run_all()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    json_path = OUT_DIR / "ltc_avax_finescan.json"
    json_path.write_text(json.dumps(_jsonable(results), ensure_ascii=False, indent=2),
                         encoding="utf-8")
    md_path = build_report(results)
    ok = [x for x in results if "error" not in x]
    err = [x for x in results if "error" in x]
    print(f"\n应跑 {len(results)} / 跑通 {len(ok)} / 失败 {len(err)}", flush=True)
    for x in sorted(ok, key=lambda y: (y.get("sharpe") or -1e9), reverse=True)[:3]:
        print(f"  #{x['idx']} e={x['entry']}/tp={x['tp']}/sl={x['sl']} "
              f"sharpe={_fmt(x.get('sharpe'),4)} cost_ok={x.get('cost_ok')} "
              f"passed={x.get('passed')}", flush=True)
    print(f"JSON: {json_path}", flush=True)
    print(f"报告: {md_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
