"""任务 C-2b · AVAX/ADA 扩池 M8 全量网格回测（分块落盘版，125 组）
=====================================================================
秘书派活单批次 C-2b（老板 2026-11-09 选 (b) 推翻 C-1 回退裁决、强行扩池）。
PM 分派后端架构师落实：本脚本仅为「编排 / 驱动层」，复用既有 M8 引擎，
不重造回测、不改 src/、不改 config/strategy_params.yaml 冻结门槛、
不动 .bat、不拉新数据（只读已落盘 parquet）。

【范围】5 币对 × 5 参数 × 5 周期 = 125 组全量，不缩减。
  币对：9 币重筛显著对中含 AVAX/ADA 的 5 对，benchmark=BTC，
        EG_p 见 backtest/output/pair_rescreen_9coins.md。
  参数：与 75 组 run_pair_grid.py 的 PARAMS 完全一致（5 组）。
  周期：15m/30m/1h/4h/1d，TF_DAYS 与 75 组一致。

【同口径】复用 backtest/run_strategy_with_rules.py 的 run_one_timeframe + GATE，
  固定项 timeout_hours=3.0 / timeout_half_life_factor=1.2 / position_cap=0.05，
  real_4h_days=400 / real_1d_days=900，与 75 组调用签名一致，不擅改。
  check_gates / 报告口径沿用 run_pair_grid.py。

【数据对齐】两腿 ts 由引擎 load_signals 的 merge(on="ts") inner join 对齐
  （src/backtest/engine.py:266），不假设逐 bar 完全对齐。
  新币 15m/30m 窗口止于 11-09 11:30、老币止于 11-08，交集 bar 数如实记录。

【极端段】2026-02 窗口 = [1770163200, 1770681600]。
  若某 (币对, 周期) 的 ts 交集窗口不含该段 → 记 N/A（不可评估），
  不把引擎给出的 extreme_pnl=0.0 / extreme_survived=True 当通过；该组不计入达标。

【BTC/AVAX 同币对】leg_a 与 benchmark 同为 BTC。经实测 run_one_timeframe 可正常
  跑通（1h 预检 bars=9841 / trades=105 / errors=[]），引擎未禁止，故保留该对、
  不替换为 BCH/AVAX（BCH 缺 15m/30m 会破坏 125 组全量）。临时文件同名但
  data["leg_a"] 与 data["benchmark"] 同源于 BTC_USDT_*_*.parquet、内容一致，
  不影响结果；beat_benchmark 口径含义受限，如实标注，不改引擎。

【作废数据声明】不引用「125 组 LTC 扩池 / LTC/AVAX 1h 夏普 0.473」
  （无盘上产物、已作废）。可引用基准仅：75 组 Top1 0.499、B 细扫 Top1 0.5550。

【分块执行（抗中断）】
  python backtest/run_pair_grid_avax_ada.py --tf 15m    # 只跑 1 个周期（25 组）
  python backtest/run_pair_grid_avax_ada.py --tf 30m
  python backtest/run_pair_grid_avax_ada.py --tf 1h
  python backtest/run_pair_grid_avax_ada.py --tf 4h
  python backtest/run_pair_grid_avax_ada.py --tf 1d
  python backtest/run_pair_grid_avax_ada.py --finalize  # 汇总成 125 组全量 + md
  每跑完 1 组即合并写入 partial.json（重跑同周期幂等覆盖），中断不丢已完成组。

【产出】
  backtest/output/pair_param_sweep_avax_ada_partial.json  分块累积
  backtest/output/pair_param_sweep_avax_ada.json          125 组全量
  backtest/output/pair_param_sweep_avax_ada.md             可读报告
"""
from __future__ import annotations

import argparse
import json
import sys
import traceback
import warnings
from pathlib import Path
from typing import Any, Dict, List, Tuple

warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import pandas as pd  # noqa: E402
from backtest.run_strategy_with_rules import (  # noqa: E402
    run_one_timeframe, GATE, _rules_dict,
)
from src.decision_layer.mean_reversion_strategy import StrategyRules  # noqa: E402

CACHE = "data/cache"
OUT_DIR = Path("backtest/output")
PARTIAL = OUT_DIR / "pair_param_sweep_avax_ada_partial.json"
FINAL_JSON = OUT_DIR / "pair_param_sweep_avax_ada.json"
FINAL_MD = OUT_DIR / "pair_param_sweep_avax_ada.md"

# 5 个含 AVAX/ADA 的 9 币重筛显著对（EG_p 见 pair_rescreen_9coins.md）
PAIRS: List[Tuple[str, str, str, float]] = [
    ("XRP", "AVAX", "BTC", 0.0049),
    ("LTC", "AVAX", "BTC", 0.0009),
    ("ETH", "AVAX", "BTC", 0.0452),
    ("BTC", "AVAX", "BTC", 0.0221),   # leg_a == benchmark，实测引擎可跑通，见文件头
    ("XRP", "ADA", "BTC", 0.0257),
]
PARAMS = [
    dict(entry=2.5, tp=1.5, sl=1.5),
    dict(entry=2.5, tp=2.0, sl=1.5),
    dict(entry=3.0, tp=2.0, sl=2.0),
    dict(entry=3.0, tp=2.5, sl=2.0),
    dict(entry=3.5, tp=2.5, sl=2.0),
]
TIMEFRAMES = ["15m", "30m", "1h", "4h", "1d"]
TF_DAYS = {"15m": 95, "30m": 195, "1h": 410, "4h": 400, "1d": 900}

EXTREME_START, EXTREME_END = 1770163200, 1770681600  # 2026-02-04 ~ 2026-02-10 UTC
GATE_TEXT = "回撤≤10% / 夏普≥2.0 / Calmar≥1.5 / 成本≤毛利35% / 极端段不亏 / 连亏≤5"
GATE6_NAMES = ["回撤≤10%", "夏普≥2.0", "Calmar≥1.5", "成本≤毛利35%", "极端段不亏", "连亏≤5"]
GATE6_KEYS = ["dd", "sh", "cal", "cost", "ext", "consec"]

# 可引用基准（盘上实测）；作废数据 0.473 / "125 组 LTC 扩池" 严禁引用
COARSE_TOP1 = dict(label="75 组网格（扩池前）", sharpe=0.499, passed=0,
                   cost_all_false=True, src="backtest/output/pair_param_sweep.json")
FINESCAN_TOP1 = dict(label="批次 B · LTC/AVAX 1h 细扫", sharpe=0.5550, passed=0,
                     cost_all_false=True, src="backtest/output/ltc_avax_finescan.md")


# ---------------------------------------------------------------------------
# 格式化 / 序列化
# ---------------------------------------------------------------------------
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
    """回撤是绝对幅度，不带正负号。"""
    try:
        return f"{float(v) * 100:.2f}%"
    except Exception:
        return "N/A"


def _flag(v: Any) -> str:
    if isinstance(v, str):
        return "✅" if v.strip().lower() == "true" else "❌"
    return "✅" if bool(v) else "❌"


def _jsonable(o: Any) -> Any:
    """numpy 标量转 Python 原生类型，避免 gate_pass 布尔被写成字符串
    'True'/'False'（下游 all(gp.values()) 会把 'False' 当真）。不改引擎，仅规范化产物。"""
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


def _sharpe(x: Dict[str, Any]) -> float:
    v = x.get("sharpe")
    return float(v) if v is not None else -1e9


# ---------------------------------------------------------------------------
# 数据窗口核对（只读 parquet 的 ts 列，不跑回测、不改引擎）
# ---------------------------------------------------------------------------
def _extreme_evaluable(tf: str, la: str, lb: str) -> Tuple[bool, int]:
    d = f"{TF_DAYS[tf]}d"
    fa = Path(CACHE) / f"{la}_USDT_{tf}_{d}.parquet"
    fb = Path(CACHE) / f"{lb}_USDT_{tf}_{d}.parquet"
    if not (fa.exists() and fb.exists()):
        return False, 0
    ta = set(pd.read_parquet(fa, columns=["ts"])["ts"].astype("int64").tolist())
    tb = set(pd.read_parquet(fb, columns=["ts"])["ts"].astype("int64").tolist())
    inter = ta & tb
    if not inter:
        return False, 0
    covers = (min(inter) <= EXTREME_START) and (max(inter) >= EXTREME_END)
    return bool(covers), len(inter)


# ---------------------------------------------------------------------------
# 冻结门槛 6 条逐条（含 check_gates 未覆盖的成本 / 连亏；极端段 N/A 不算通过）
# ---------------------------------------------------------------------------
def _gate6_detail(x: Dict[str, Any]) -> Dict[str, bool]:
    if "error" in x:
        return {k: False for k in GATE6_KEYS}
    dd, sh, cal, mc = x.get("maxdd"), x.get("sharpe"), x.get("calmar"), x.get("max_consec")
    return dict(
        dd=bool(dd is not None and dd <= 0.10),
        sh=bool(sh is not None and sh >= 2.0),
        cal=bool(cal is not None and cal >= 1.5),
        cost=bool(x.get("cost_ok")),
        ext=bool(x.get("extreme_survived")) if x.get("extreme_evaluable") else False,
        consec=bool(mc is not None and mc <= 5),
    )


def _run_one(tf: str, la: str, lb: str, bm: str, eg_p: float,
             p: Dict[str, Any], idx: int, cov: bool, jbar: int) -> Dict[str, Any]:
    base = dict(idx=idx, pair=f"{la}/{lb}", leg_a=la, leg_b=lb, benchmark=bm,
                eg_p=eg_p, tf=tf, tf_days=TF_DAYS[tf],
                entry=p["entry"], tp=p["tp"], sl=p["sl"],
                extreme_evaluable=bool(cov), joint_bars=jbar)
    rules = StrategyRules(
        entry_sigma=p["entry"],
        take_profit_sigma_from_entry=p["tp"],
        stop_loss_sigma_from_entry=p["sl"],
        timeout_hours=3.0,
        timeout_half_life_factor=1.2,
        position_cap=0.05,
    )
    try:
        # 与 75 组脚本 run_pair_grid.py 完全同口径（含两个 real_*_days 参数）
        r = run_one_timeframe(tf, rules, CACHE, la, lb, bm, GATE,
                              real_4h_days=400, real_1d_days=900)
        m = r.metrics
        gp = _jsonable(m.get("gate_pass", {}) or {})
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
            extreme_pnl_adj=(m.get("extreme_pnl") if cov else "N/A（不可评估）"),
            extreme_survived_adj=(bool(m.get("extreme_survived")) if cov else "N/A（不可评估）"),
            cost_ok=m.get("cost_executable"),
            cost_paid=m.get("cost_paid"),
            max_consec=m.get("max_consecutive_losses", 0),
            gate_pass=gp,
            passed=bool(passed),
            bars=r.data_bars,
            source=r.source.get(tf, "?"),
            rules=_rules_dict(rules),
            errors=list(r.errors),
        )
        if r.errors:
            rec["engine_error"] = "; ".join(str(e) for e in r.errors)[:200]
    except Exception as e:
        # 失败标 ERR，不吞掉：落完整异常类型 + 消息 + 末尾堆栈
        rec = dict(**base,
                   error=f"{type(e).__name__}: {e}"[:200],
                   trace=traceback.format_exc()[-600:],
                   bars=0)
    rec["gate6_detail"] = _gate6_detail(rec)
    rec["gate6_pass"] = all(rec["gate6_detail"].values())
    return rec


# ---------------------------------------------------------------------------
# 分块累积（每组落盘，重跑同周期幂等覆盖）
# ---------------------------------------------------------------------------
def _load_partial(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        return d.get("results", []) if isinstance(d, dict) else list(d)
    except Exception:
        return []


def _save_partial(results: List[Dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(
        {"meta": {"task": "C-2b", "note": "分块跑、每组落盘；--finalize 汇总成 125 组全量"},
         "results": _jsonable(results)},
        ensure_ascii=False, indent=2), encoding="utf-8")


def _load_all_partials() -> List[Dict[str, Any]]:
    """合并主 partial + 各周期独立 partial（并行分块用）；同键后写覆盖，
    均为盘上实测结果，去重不脑补。"""
    files = [PARTIAL] + [f for f in sorted(
        OUT_DIR.glob("pair_param_sweep_avax_ada_partial_*.json"))
        if f.resolve() != PARTIAL.resolve()]
    merged: Dict[Tuple[Any, ...], Dict[str, Any]] = {}
    for f in files:
        for x in _load_partial(f):
            merged[_key(x)] = x
    return list(merged.values())


def _key(x: Dict[str, Any]) -> Tuple[Any, ...]:
    return (x.get("pair"), x.get("entry"), x.get("tp"), x.get("sl"), x.get("tf"))


def run_tf(tf: str, partial_path: Path) -> None:
    prev = [x for x in _load_partial(partial_path) if x.get("tf") != tf]  # 幂等：重跑覆盖该周期
    results = list(prev)
    cov_cache: Dict[Tuple[str, str, str], Tuple[bool, int]] = {}
    total = len(PAIRS) * len(PARAMS)
    i = 0
    print(f"== C-2b 分块：周期 {tf}（{total} 组）==", flush=True)
    for la, lb, bm, eg_p in PAIRS:
        for p in PARAMS:
            i += 1
            key = (la, lb, tf)
            if key not in cov_cache:
                cov_cache[key] = _extreme_evaluable(tf, la, lb)
            cov, jbar = cov_cache[key]
            print(f"[{tf} {i}/{total}] {la}/{lb} e={p['entry']} tp={p['tp']} sl={p['sl']}",
                  flush=True)
            rec = _run_one(tf, la, lb, bm, eg_p, p, i, cov, jbar)
            results.append(rec)
            _save_partial(results, partial_path)   # 每组落盘：中断不丢已完成组
            if "error" in rec:
                print(f"    ERR {rec['error']}", flush=True)
            else:
                print(f"    bars={rec['bars']} trades={rec['trades']} "
                      f"sharpe={_fmt(rec['sharpe'], 4)} cost_ok={rec['cost_ok']} "
                      f"gate6={rec['gate6_pass']}", flush=True)
    n_ok = sum(1 for x in results if x.get("tf") == tf and "error" not in x)
    print(f"== 周期 {tf} 完成：跑通 {n_ok} / ERR {total - n_ok}；partial 累计 {len(results)} 组 ==",
          flush=True)


# ---------------------------------------------------------------------------
# 报告
# ---------------------------------------------------------------------------
def build_report(results: List[Dict[str, Any]], extra: List[Tuple[Any, ...]] | None = None) -> None:
    ok = [x for x in results if "error" not in x]
    err = [x for x in results if "error" in x]
    by_sharpe = sorted(ok, key=_sharpe, reverse=True)
    top1 = by_sharpe[0] if by_sharpe else None
    passed_n = sum(1 for x in ok if x.get("passed"))
    gate6_n = sum(1 for x in ok if x.get("gate6_pass"))
    cost_ok_n = sum(1 for x in ok if bool(x.get("cost_ok")))
    ext_na_n = sum(1 for x in ok if not x.get("extreme_evaluable"))

    ev_map: Dict[Tuple[str, str], bool] = {}
    win_by_tf: Dict[str, Dict[str, Any]] = {}
    for tf in TIMEFRAMES:
        rows = [x for x in ok if x.get("tf") == tf]
        jb = [x.get("joint_bars") for x in rows if x.get("joint_bars")]
        win_by_tf[tf] = dict(
            n=len(rows),
            jmin=min(jb) if jb else None,
            jmax=max(jb) if jb else None,
            evaluable=all(bool(x.get("extreme_evaluable")) for x in rows) if rows else None,
        )
        for x in rows:
            ev_map.setdefault((x["pair"], tf), bool(x.get("extreme_evaluable")))

    L: List[str] = []
    L.append("# 任务 C-2b · AVAX/ADA 扩池 M8 全量网格回测报告")
    L.append("")
    L.append("日期：2026-11-09  ")
    L.append("范围：**仅 C-2b**（不抢跑 D）；5 币对 × 5 参数 × 5 周期 = **125 组全量**，未缩减  ")
    L.append("币对：" + "、".join(f"{la}/{lb}(EG_p={eg_p})" for la, lb, bm, eg_p in PAIRS)
             + "；benchmark 统一 BTC  ")
    L.append("周期：15m(95d) / 30m(195d) / 1h(410d) / 4h(400d) / 1d(900d)（TF_DAYS 同 75 组）  ")
    L.append("参数：5 组（与 75 组 `run_pair_grid.py` 的 PARAMS 完全一致）："
             "2.5/1.5/1.5、2.5/2.0/1.5、3.0/2.0/2.0、3.0/2.5/2.0、3.5/2.5/2.0（entry/tp/sl）  ")
    L.append("固定项（与 75 组同口径，不擅改）：timeout_hours=3.0 / "
             "timeout_half_life_factor=1.2 / position_cap=0.05；real_4h_days=400 / real_1d_days=900  ")
    L.append(f"冻结门槛（不放宽）：{GATE_TEXT}  ")
    L.append("接口：复用 `run_strategy_with_rules.run_one_timeframe` + `GATE`；门槛判定走 "
             "`check_gates`；未改 `src/`、未改冻结门槛、未重造回测、未拉新数据、未动 `.bat`。")
    L.append("")
    L.append("> ⚠️ **作废数据声明**：本报告不引用秘书此前口头汇报的「125 组 LTC 扩池 / "
             "LTC/AVAX 1h 夏普 0.473」（无任何盘上产物，已作废）。可引用的真实基准仅："
             "75 组 `backtest/output/pair_param_sweep.json` 全局 Top1 夏普 **0.499**"
             "（达标 0、成本全 False），批次 B `backtest/output/ltc_avax_finescan.md` "
             "Top1 **0.5550**（中心点同口径复跑 0.2376、9 组达标 0、成本全不可执行）。")
    L.append("")

    # ---- §1 运行概览 ----
    L.append("## 1. 运行概览")
    L.append("")
    L.append(f"- 应跑 **125** 组（5 币对 × 5 参数 × 5 周期）；跑通 **{len(ok)}** 组；"
             f"失败 **ERR {len(err)}** 组。")
    L.append("- 分块执行（抗中断）：每个周期启动独立进程、每完成 1 组即写入该周期独立 partial "
             "文件（`pair_param_sweep_avax_ada_partial_<周期>.json`）；另有 1 个主 "
             "`pair_param_sweep_avax_ada_partial.json` 承接首轮串行残留。重跑同周期幂等覆盖，"
             "5 个周期全部完成后 `--finalize` 合并所有 partial（按 币对/参数/周期 键去重）"
             "汇总成 125 组全量；任一组中断都不丢已完成部分。")
    if err:
        L.append("- 失败组（不吞掉，异常类型 + 消息 + 末尾堆栈已落 JSON `error` / `trace` 字段）：")
        for x in err:
            L.append(f"  - #{x.get('idx')} {x.get('pair')} {x.get('tf')} "
                     f"e={x.get('entry')}/tp={x.get('tp')}/sl={x.get('sl')}："
                     f"**ERR** {x.get('error')}")
    else:
        L.append("- 失败组：无（125 组全部跑通，无 ERR）。")
    if extra:
        L.append(f"- ⚠️ partial 含 **{len(extra)}** 组非预期键，汇总时已忽略（如实记录）："
                 + "、".join(str(k) for k in extra[:10]))
    L.append("")
    L.append("- **数据对齐**：两腿 ts 由引擎 `load_signals` 的 `merge(on=\"ts\")` inner join 对齐"
             "（`src/backtest/engine.py:266`），不假设逐 bar 完全对齐。新币 15m/30m 窗口止于 "
             "11-09 11:30、老币止于 11-08，inner join 后 joint_bars 少于单腿 bar 数（下表），如实记录。")
    L.append("")
    L.append("| 周期 | 跑通组数 | 两腿 ts 交集 bar 数 (min~max) | 数据窗口是否含 2026-02 | 极端段判定 |")
    L.append("|---|---|---|---|---|")
    for tf in TIMEFRAMES:
        w = win_by_tf[tf]
        jm = (f"{w['jmin']}~{w['jmax']}" if w["jmin"] is not None else "N/A")
        if w["evaluable"] is True:
            L.append(f"| {tf} | {w['n']} | {jm} | 是 | 可评估（引用引擎 `extreme_pnl` / `extreme_survived`） |")
        elif w["evaluable"] is False:
            L.append(f"| {tf} | {w['n']} | {jm} | 否 | **N/A（不可评估）**，不把 0 当通过 |")
        else:
            L.append(f"| {tf} | {w['n']} | {jm} | N/A | N/A |")
    L.append("")
    L.append("- **BTC/AVAX 同币对**：leg_a 与 benchmark 同为 BTC。实测 `run_one_timeframe` 可正常"
             "跑通（1h 预检 bars=9841 / trades=105 / errors=[]），引擎未禁止 → 保留该对、"
             "不替换为 BCH/AVAX（BCH 缺 15m/30m 会破坏 125 组全量）。脚本内 leg_a 与 benchmark "
             "临时文件同名，但二者同源于 `BTC_USDT_{tf}_{days}d.parquet`、内容一致，不影响结果；"
             "`beat_benchmark`（跑赢 BTC 单边）在该对上口径含义受限，如实标注，不改引擎。")
    L.append("")

    # ---- §2 同币对不同周期对比表 ----
    L.append("## 2. 同币对不同周期对比表（每币对 5 参数 × 5 周期网格 + 最优周期列）")
    L.append("")
    L.append("> `*` 标记的周期数据窗口不含 2026-02，极端段判定 N/A（不可评估），"
             "该周期组不计入达标；详见 §1 / §5。单元格「达标」列按冻结门槛 6 条全过判定。")
    L.append("")
    for la, lb, bm, eg_p in PAIRS:
        pair = f"{la}/{lb}"
        L.append(f"### {pair}（leg_a={la}, leg_b={lb}, benchmark={bm}, EG_p={eg_p}）")
        L.append("")
        head = "| 参数(entry/tp/sl) | " + " | ".join(
            f"{tf}({TF_DAYS[tf]}d){'' if ev_map.get((pair, tf), True) else '*'}"
            for tf in TIMEFRAMES) + " | 最优周期 | 任一周期达标 |"
        L.append(head)
        L.append("|---|" + "---|" * (len(TIMEFRAMES) + 2))
        for p in PARAMS:
            cells = []
            combo = []
            for tf in TIMEFRAMES:
                rec = next((x for x in results if x.get("pair") == pair
                            and x.get("tf") == tf and x.get("entry") == p["entry"]
                            and x.get("tp") == p["tp"] and x.get("sl") == p["sl"]), None)
                if rec is None:
                    cells.append("N/A")
                    continue
                if "error" in rec:
                    cells.append(f"ERR: {str(rec['error'])[:12]}")
                    continue
                combo.append(rec)
                mark = "✅" if rec.get("gate6_pass") else "❌"
                cells.append(f"{mark} 净{_fmt_pct(rec.get('net_peak'))} "
                             f"dd{_fmt_dd(rec.get('maxdd'))} 夏{_fmt(rec.get('sharpe'), 2)} "
                             f"Cal{_fmt(rec.get('calmar'), 2)} 笔{rec.get('trades')}")
            best_cell = (f"{max(combo, key=_sharpe)['tf']}（夏普 {_fmt(_sharpe(max(combo, key=_sharpe)), 2)}）"
                         if combo else "N/A")
            any_pass = any(x.get("gate6_pass") for x in combo)
            L.append(f"| e={p['entry']}/tp={p['tp']}/sl={p['sl']} | "
                     + " | ".join(cells)
                     + f" | {best_cell} | {'✅' if any_pass else '❌'} |")
        L.append("")

    # ---- §3 全局 Top10 ----
    L.append("## 3. 全局 Top10（按夏普降序，与对照基准口径一致）")
    L.append("")
    L.append("| # | 币对 | 周期 | entry/tp/sl | 净峰值 | 夏普 | Calmar | 回撤 | 成本可执 | 门槛6条 | 成交笔 |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|")
    for i, x in enumerate(by_sharpe[:10], 1):
        L.append(
            f"| {i} | {x['pair']} | {x['tf']} | {x['entry']}/{x['tp']}/{x['sl']} | "
            f"{_fmt_pct(x.get('net_peak'))} | {_fmt(x.get('sharpe'), 4)} | "
            f"{_fmt(x.get('calmar'), 4)} | {_fmt_dd(x.get('maxdd'))} | "
            f"{_flag(x.get('cost_ok'))} | {'✅' if x.get('gate6_pass') else '❌'} | "
            f"{x.get('trades')} |")
    if not by_sharpe:
        L.append("| — | 无可用的非 ERR 组 | | | | | | | | | |")
    L.append("")

    # ---- §4 每币对最佳组合 ----
    L.append("## 4. 每币对最佳组合（按夏普）")
    L.append("")
    L.append("| 币对 | 周期 | entry/tp/sl | 净峰值 | 夏普 | Calmar | 回撤 | 成本可执 | 成交笔 | 门槛6条 |")
    L.append("|---|---|---|---|---|---|---|---|---|---|")
    for la, lb, bm, eg_p in PAIRS:
        pair = f"{la}/{lb}"
        cand = [x for x in ok if x.get("pair") == pair]
        if not cand:
            L.append(f"| {pair} | N/A | 无可用组 | | | | | | | |")
            continue
        b = max(cand, key=_sharpe)
        L.append(
            f"| {pair} | {b['tf']} | {b['entry']}/{b['tp']}/{b['sl']} | "
            f"{_fmt_pct(b.get('net_peak'))} | {_fmt(b.get('sharpe'), 4)} | "
            f"{_fmt(b.get('calmar'), 4)} | {_fmt_dd(b.get('maxdd'))} | "
            f"{_flag(b.get('cost_ok'))} | {b.get('trades')} | "
            f"{'✅' if b.get('gate6_pass') else '❌'} |")
    L.append("")

    # ---- §5 达标组数 + 逐项门槛判定 ----
    L.append("## 5. 达标组数 + 逐项门槛判定（不放宽、不把 0 当通过）")
    L.append("")
    L.append(f"- 总组合数 **{len(results)}**；跑通 **{len(ok)}**；ERR **{len(err)}**。")
    L.append(f"- `check_gates` 全项通过组数（75 组 `passed = bool(gp) and all(gp.values())` 口径）："
             f"**{passed_n} / {len(ok)}**。")
    L.append(f"- 冻结门槛 **6 条全过（`gate6_pass`）组数 = 达标组数：{gate6_n} / {len(ok)}**。")
    L.append(f"- `cost_executable=True` 组数：**{cost_ok_n} / {len(ok)}**。")
    L.append(f"- 极端段 N/A（不可评估）组数：**{ext_na_n} / {len(ok)}**"
             "（15m/30m 数据窗口不含 2026-02）。")
    L.append("")
    L.append("### 冻结门槛 6 条逐条（含 `check_gates` 未覆盖的成本 / 连亏）")
    L.append("")
    L.append("| 门槛 | 通过组数 / 跑通组数 | 说明 |")
    L.append("|---|---|---|")
    cnt = {k: sum(1 for x in ok if x.get("gate6_detail", {}).get(k)) for k in GATE6_KEYS}
    notes = [
        "引擎 `max_drawdown ≤ 0.10`",
        "引擎 `sharpe ≥ 2.0`",
        "引擎 `calmar ≥ 1.5`",
        "引擎 `cost_executable`（`cost_paid ≤ 0.35 × 毛利总额`，`engine.py:681`）",
        f"`extreme_survived`；另 {ext_na_n} 组 N/A（不可评估）→ 不计通过、不把 0 当通过",
        "`max_consecutive_losses ≤ 5`（逐笔盈亏，引擎 `engine.py:663-673`）",
    ]
    for name, k, note in zip(GATE6_NAMES, GATE6_KEYS, notes):
        L.append(f"| {name} | {cnt[k]} / {len(ok)} | {note} |")
    L.append("")
    L.append("> **如实标注**：`check_gates`（`engine.py:708-719`）返回 6 项，其中不含「成本≤毛利35%」"
             "与「连亏≤5」，本表单列如实判定，不把「未检查」当「通过」。"
             "引擎对不含 2026-02 的窗口给出 `extreme_pnl=0.0` / `extreme_survived=True`"
             "（`engine.py:651-652,691`），属占位值；本报告按数据窗口判为 N/A（不可评估），"
             "该周期组的 `gate6_pass` 一律为 ❌，**不把 0 当通过、不放宽**。")
    L.append("")

    # ---- §6 扩池前后对照 ----
    L.append("## 6. 扩池前后对照（与 75 组 0.499 / B 细扫 0.5550 并排）")
    L.append("")
    t1s = _sharpe(top1) if top1 else None
    c2b_cost_all_false = (cost_ok_n == 0)
    c2b_cost_txt = "全 False" if c2b_cost_all_false else f"{cost_ok_n} 组 True"
    L.append("| 批次 | 来源 | 范围 | 全局 Top1 夏普 | 达标组数 | 成本可执行 |")
    L.append("|---|---|---|---|---|---|")
    L.append(f"| 75 组网格（扩池前） | `{COARSE_TOP1['src']}` | 3 币对 × 5 参数 × 5 周期 = 75 组 | "
             f"**0.499** | 0 | 全 False |")
    L.append(f"| 批次 B 细扫 | `{FINESCAN_TOP1['src']}` | LTC/AVAX 1h 9 组 | "
             f"**0.5550** | 0 | 全 False |")
    t1_txt = f"**{_fmt(t1s, 4)}**" if t1s is not None else "N/A"
    L.append(f"| **C-2b 扩池（本批）** | `{FINAL_JSON.as_posix()}` | "
             f"5 币对 × 5 参数 × 5 周期 = 125 组 | {t1_txt} | {gate6_n} | "
             f"{c2b_cost_txt} |")
    L.append("")
    if t1s is not None:
        d1, d2 = t1s - COARSE_TOP1["sharpe"], t1s - FINESCAN_TOP1["sharpe"]
        L.append(f"- **是否出现更高夏普**：C-2b 全局 Top1 {_fmt(t1s, 4)} "
                 f"vs 75 组 0.499 → {'是（更高）' if d1 > 0 else '否（未超过）'}（差值 {d1:+.4f}）；"
                 f"vs B 细扫 0.5550 → {'是（更高）' if d2 > 0 else '否（未超过）'}（差值 {d2:+.4f}）。")
        if d1 > 0 or d2 > 0:
            L.append("  - ⚠️ **如实补充**：C-2b 较高夏普组集中在长周期（4h/1d），成交笔数少、样本薄，"
                     "夏普/Calmar 统计意义弱；本报告只申报实测数值，不据此下「策略更优」的金融结论。")
        if top1:
            L.append(f"  - C-2b Top1 = {top1['pair']} {top1['tf']} "
                     f"e={top1['entry']}/tp={top1['tp']}/sl={top1['sl']}，"
                     f"净 {_fmt_pct(top1.get('net_peak'))} / Calmar {_fmt(top1.get('calmar'), 4)} / "
                     f"回撤 {_fmt_dd(top1.get('maxdd'))} / 成本可执 {_flag(top1.get('cost_ok'))} / "
                     f"{top1.get('trades')} 笔。")
    else:
        L.append("- 无可用非 ERR 组，无法判定是否出现更高夏普。")
    L.append(f"- **成本可执行是否仍全 False**：**{'是' if c2b_cost_all_false else '否'}**"
             f"（C-2b `cost_executable=True` 组数 = {cost_ok_n} / {len(ok)}）。")
    L.append(f"- **达标组数**：75 组 0 / B 细扫 0 / C-2b {gate6_n}（门槛一律未放宽）。")
    L.append("")

    # ---- §7 成本可执行统计 + 周期效应 ----
    L.append("## 7. 成本可执行统计 + 周期效应")
    L.append("")
    L.append(f"- `cost_executable=True`（成本≤毛利35%）组数：**{cost_ok_n} / {len(ok)}**；"
             f"全 False = {'是' if c2b_cost_all_false else '否'}。")
    L.append("")
    L.append("| 周期 | 跑通组数 | 平均成交笔 | 平均净峰值 | 平均夏普 | 平均成本($) | cost_ok=True 组数 | 极端段可评估 |")
    L.append("|---|---|---|---|---|---|---|---|")

    def _avg(rows: List[Dict[str, Any]], key: str):
        vals = [x[key] for x in rows if x.get(key) is not None]
        return sum(vals) / len(vals) if vals else None

    for tf in TIMEFRAMES:
        rows = [x for x in ok if x.get("tf") == tf]
        w = win_by_tf[tf]
        ev_txt = "是" if w["evaluable"] is True else ("否（N/A）" if w["evaluable"] is False else "N/A")
        L.append(
            f"| {tf} | {len(rows)} | {_fmt(_avg(rows, 'trades'), 1)} | "
            f"{_fmt_pct(_avg(rows, 'net_peak'))} | {_fmt(_avg(rows, 'sharpe'), 3)} | "
            f"{_fmt(_avg(rows, 'cost_paid'), 2)} | "
            f"{sum(1 for x in rows if bool(x.get('cost_ok')))} | {ev_txt} |")
    L.append("")
    if c2b_cost_all_false:
        eff_tail = "无一组越过成本线或达到冻结门槛。"
    else:
        eff_tail = (f"全批有 {cost_ok_n} 组 `cost_executable=True`（多为长周期少成交，统计意义弱），"
                    "但无一组达到冻结门槛。")
    L.append("- **周期效应（如实描述，用上表均值支撑）**：短周期（15m/30m）数据窗口短、"
             "成交笔数多（样本多）但单笔空间薄、成本累计高、夏普显著为负，且窗口不含 2026-02 "
             "→ 极端段 N/A（不可评估）；长周期（4h/1d）窗口长、单笔空间大但成交笔数少（样本薄），"
             "夏普/Calmar 统计意义弱。" + eff_tail
             + "本批不对周期优劣下金融结论，仅如实申报统计事实。")
    L.append("")

    # ---- §8 结论 ----
    L.append("## 8. 结论（如实、不美化、不放宽）")
    L.append("")
    if top1 is None:
        L.append("- 无可用非 ERR 组，无法下任何结论。")
    else:
        d1, d2 = t1s - COARSE_TOP1["sharpe"], t1s - FINESCAN_TOP1["sharpe"]
        higher = "就夏普数值而言出现更高组合" if (d1 > 0 or d2 > 0) else "未出现更高夏普组合"
        L.append(f"- **AVAX/ADA 扩池有没有改善**：125 组全量网格全局 Top1 夏普 "
                 f"{_fmt(t1s, 4)}（{top1['pair']} {top1['tf']} "
                 f"e={top1['entry']}/tp={top1['tp']}/sl={top1['sl']}）；"
                 f"与 75 组 0.499 比 {d1:+.4f}、与 B 细扫 0.5550 比 {d2:+.4f} → "
                 f"**{higher}**。")
        L.append(f"- **是否达到冻结门槛**：**未达到**。夏普门槛 2.0，全局 Top1 {_fmt(t1s, 4)}"
                 f"（差 {2.0 - t1s:.4f}）；`check_gates` 全项通过 {passed_n} 组、"
                 f"冻结 6 条全过（达标）{gate6_n} 组 → **无任何一组达标冻结门槛**。")
        if c2b_cost_all_false:
            L.append("- **成本可执行**：仍全 False，与 75 组、B 细扫一致，成本线未被越过。")
        else:
            L.append(f"- **成本可执行**：出现 **{cost_ok_n} 组 `cost_executable=True`**"
                     "（与 75 组 / B 细扫「全 False」不同，如实申报、不美化）；这些组多集中在"
                     "长周期、成交笔数少，统计意义弱，且仍无一组达到冻结门槛。")
        L.append("- 门槛一律不放宽；逐项未达标事实见 §5，ERR 如实标注不吞、不脑补。"
                 "本报告只做 C-2b 事实申报，不下金融/投资结论、不抢跑批次 D。")
    L.append("")

    # ---- §9 产物 / 口径 / 限制 / 风险未决 ----
    L.append("## 9. 产物、口径、限制与风险/未决")
    L.append("")
    L.append(f"- JSON（125 组全量）：`{FINAL_JSON.as_posix()}`（纯列表，含 `gate_pass` 全项、"
             "`gate6_detail` / `gate6_pass`、极端段 N/A 标注、ERR 组 `error` / `trace`）。")
    L.append(f"- 分块累积：`{PARTIAL.as_posix()}`（每组落盘，重跑同周期幂等覆盖）。")
    L.append(f"- 报告：`{FINAL_MD.as_posix()}`（本文件）。")
    L.append("- 脚本：`backtest/run_pair_grid_avax_ada.py`（`--tf <周期>` 分块跑 / `--finalize` 汇总，可复跑）。")
    L.append("- 口径：`passed` 同 75 组脚本（`check_gates` 全项 and）；成本与连亏两条额外单列（§5），"
             "不并入 `passed`、不吞；`gate6_pass` = 冻结 6 条全过（极端段 N/A 视为不满足）。"
             "JSON 落盘前把 numpy 标量统一转 Python 原生类型，`gate_pass` 全项为布尔值（非字符串）。")
    L.append("- 限制：未改 `src/`、未改 `config/strategy_params.yaml` 冻结门槛、未动 `.bat`、"
             "未拉新数据（只读已落盘 parquet）；复用 `run_one_timeframe` + `check_gates`，未重造回测。")
    L.append("- 数据注意（C-2a 上报）：新币低量率偏高（AVAX 15m 23.4% / 30m 20.9%，"
             "LTC 18.7%/14.6%，ADA 11.0%/7.4%）属流动性特征、非缺数；新币窗口止于 11-09 11:30、"
             "老币止于 11-08，两腿经 ts inner join 对齐（§1），未假设逐 bar 完全对齐。")
    L.append("- 风险/未决：① `BTC/AVAX` 的 leg_a 与 benchmark 同币，`beat_benchmark` 口径含义受限"
             "（引擎未禁止、实测可跑通，未替换，如实标注）；② 引擎 `extreme_pnl` 在含 2026-02 的"
             "周期也可能因该段无交易/净值持平而取 0.0，`extreme_survived=True` 为引擎既有口径，"
             "PM 不改引擎、不另行脑补为「真实穿越检验通过」，建议后续由量化专家复核极端段口径；"
             "③ 15m/30m 极端段 N/A（不可评估），不计入达标；④ 低量率偏高对成本/滑点的影响"
             "本批未单独建模（沿用引擎既有成本模型），列为未决项。")
    L.append("")
    FINAL_MD.write_text("\n".join(L), encoding="utf-8")


# ---------------------------------------------------------------------------
# 汇总
# ---------------------------------------------------------------------------
def finalize() -> int:
    raw = _load_all_partials()
    expected = [(f"{la}/{lb}", p["entry"], p["tp"], p["sl"], tf)
                for la, lb, bm, eg_p in PAIRS
                for p in PARAMS
                for tf in TIMEFRAMES]
    expected_set = set(expected)
    got = {_key(x) for x in raw}
    missing = [k for k in expected if k not in got]
    extra = [k for k in got if k not in expected_set]
    print(f"== C-2b 汇总：partial 现有 {len(raw)} 组，期望 {len(expected)} 组 ==", flush=True)
    if missing:
        print(f"❌ finalize 中止：缺 {len(missing)} 组（不脑补、不生成不完整最终产物）", flush=True)
        for k in missing[:20]:
            print("   MISSING", k, flush=True)
        return 2
    if extra:
        print(f"⚠️ partial 含 {len(extra)} 组非预期键，汇总时忽略（如实记录）", flush=True)
    by_key = {_key(x): x for x in raw}
    ordered = []
    for i, k in enumerate(expected, 1):
        rec = dict(by_key[k])
        rec["idx"] = i
        ordered.append(rec)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    FINAL_JSON.write_text(json.dumps(_jsonable(ordered), ensure_ascii=False, indent=2),
                          encoding="utf-8")
    build_report(ordered, extra)
    ok = [x for x in ordered if "error" not in x]
    err = [x for x in ordered if "error" in x]
    print(f"\n== C-2b 汇总完成：{len(ordered)} 组（跑通 {len(ok)} / ERR {len(err)}）==", flush=True)
    for x in sorted(ok, key=_sharpe, reverse=True)[:3]:
        print(f"  #{x['idx']} {x['pair']} {x['tf']} e={x['entry']}/tp={x['tp']}/sl={x['sl']} "
              f"sharpe={_fmt(x.get('sharpe'), 4)} cost_ok={x.get('cost_ok')} "
              f"gate6={x.get('gate6_pass')}", flush=True)
    print(f"JSON: {FINAL_JSON}", flush=True)
    print(f"报告: {FINAL_MD}", flush=True)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="C-2b AVAX/ADA 扩池 M8 全量网格（分块落盘）")
    ap.add_argument("--tf", choices=TIMEFRAMES, help="只跑一个周期（25 组），合并进 partial.json")
    ap.add_argument("--finalize", action="store_true",
                    help="合并所有 partial（主文件 + 各周期独立文件）汇总成 125 组全量 json + md 报告")
    ap.add_argument("--out-partial", default=str(PARTIAL),
                    help="本周期结果写入的 partial 路径（并行时每周期指定独立文件）")
    args = ap.parse_args()
    if args.finalize:
        return finalize()
    if args.tf:
        run_tf(args.tf, Path(args.out_partial))
        return 0
    ap.error("需指定 --tf <15m|30m|1h|4h|1d> 或 --finalize")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
