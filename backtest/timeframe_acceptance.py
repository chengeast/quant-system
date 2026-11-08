"""周期参数化专项验收（用户 2026-11-08 裁决「1h 周期不能写死」）

本脚本把「周期是参数」从注释变成可执行断言。覆盖：
  1. 派生量正确性（1h/30m/15m/4h/1d + annualize_factor² == bars_per_year）
  2. 未知 interval 必须报错，绝不静默回退 1h
  3. 代码侧 DEFAULT_INTERVAL / 市场日历 与 config/strategy_params.yaml 一致
  4. 防呆：手写 bars_per_hour / annualize_factor 与派生值不符 → ValueError
  5. 防呆：数据文件名声明周期与 interval 不符 → ValueError；聚合场景放行
  6. 换市场日历（A 股 4h×252d）只改两个数，骨架不动
  7. 物理小时 ↔ bar 数互转、互逆
  8. 最大可拉天数由 10000 根上限派生，不写死
  9. M4 超时：3h 物理下界在 1h/30m/15m 下保持一致（业务语义不随周期漂移）
 10. 产物动态口径：golden value / 报告 / 看板 不再写死 1h

以计算结果为准、不瞎猜：每条都真实执行，失败如实报 ❌ 并 exit 1。
"""

from __future__ import annotations

import json
import math
import re
import sys
import warnings
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
warnings.filterwarnings("ignore")

import pandas as pd

from src.core.timeframe import (
    DEFAULT_HOURS_PER_DAY, DEFAULT_INTERVAL, DEFAULT_TRADING_DAYS_PER_YEAR,
    INTERVAL_SECONDS, MAX_CANDLES, bars_for_hours, hours_for_bars,
    interval_from_filename, max_lookback_days, resolve_timeframe,
)
from src.backtest.engine import BacktestConfig
from src.decision_layer.mean_reversion_strategy import (
    MeanReversionStrategy, PositionSnapshot,
)
from src.monitoring_layer.monitoring import CryptoMonitorPolicy

PASS_CNT = 0
FAIL_CNT = 0


def _mark(name: str, ok: bool, detail: str = "") -> None:
    global PASS_CNT, FAIL_CNT
    mark = "✅" if ok else "❌"
    if ok:
        PASS_CNT += 1
    else:
        FAIL_CNT += 1
    print(f"  {mark} {name}" + (f"  {detail}" if detail else ""))


def _expect_value_error(fn, keyword: str) -> tuple[bool, str]:
    """断言 fn() 抛 ValueError 且错误信息含 keyword。"""
    try:
        fn()
    except ValueError as e:
        msg = str(e)
        return (keyword in msg), msg
    except Exception as e:                                # noqa: BLE001
        return False, f"抛出了非 ValueError: {type(e).__name__}: {e}"
    return False, "未抛异常"


def _cfg_scalar(section: str, key: str) -> str | None:
    """不依赖 PyYAML 的极简标量读取（验收只用得到顶层段内的标量）。"""
    text = (ROOT / "config" / "strategy_params.yaml").read_text(encoding="utf-8")
    in_section = False
    for ln in text.splitlines():
        if re.match(rf"^{re.escape(section)}\s*:", ln):
            in_section = True
            continue
        if in_section:
            if ln and not ln[0].isspace() and ":" in ln:
                break                                     # 下一个顶层段
            m = re.match(rf"^\s+{re.escape(key)}\s*:\s*(.+?)\s*(?:#.*)?$", ln)
            if m:
                return m.group(1).strip().strip('"').strip("'")
    return None


# ---------------------------------------------------------------- 1
def check_derivation() -> None:
    print("\n[验收 1] 周期派生量正确性（单一事实源）")
    expect = {"1h": (1.0, 8760), "30m": (2.0, 17520), "15m": (4.0, 35040),
              "4h": (0.25, 2190), "1d": (1 / 24, 365)}
    for iv, (bph, bpy) in expect.items():
        tf = resolve_timeframe(iv)
        ok = (abs(tf.bars_per_hour - bph) < 1e-9
              and abs(tf.bars_per_year - bpy) < 1e-6
              and abs(tf.annualize_factor ** 2 - tf.bars_per_year) < 1e-6
              and tf.bar_ms == INTERVAL_SECONDS[iv] * 1000
              and abs(tf.scale_vs_1h - INTERVAL_SECONDS[iv] / 3600) < 1e-9)
        _mark(f"{iv:>3s}: bars/hour={tf.bars_per_hour:g}, "
              f"bars/year={tf.bars_per_year:g}, √={tf.annualize_factor:.2f}",
              ok, f"bar_ms={tf.bar_ms}")
    # 换周期后派生量必须真的不同（防止任何一处偷偷回退到 1h）
    a, b = resolve_timeframe("1h"), resolve_timeframe("15m")
    _mark("1h vs 15m 派生量确实不同",
          a.annualize_factor != b.annualize_factor and a.bar_ms == 4 * b.bar_ms)


# ---------------------------------------------------------------- 2
def check_unknown_interval() -> None:
    print("\n[验收 2] 未知周期必须报错（绝不静默回退 1h）")
    ok, msg = _expect_value_error(lambda: resolve_timeframe("7m"), "不支持的 interval")
    _mark("resolve_timeframe('7m') 抛 ValueError", ok, msg[:60])
    ok2, msg2 = _expect_value_error(
        lambda: BacktestConfig(interval="7m"), "不支持的 interval")
    _mark("BacktestConfig(interval='7m') 抛 ValueError", ok2, msg2[:60])
    ok3, msg3 = _expect_value_error(
        lambda: MeanReversionStrategy(interval="7m"), "不支持的 interval")
    _mark("MeanReversionStrategy(interval='7m') 抛 ValueError", ok3, msg3[:60])
    # 数据层也不能再 .get(interval, 3600) 静默回退 1h
    gc = (ROOT / "src/data_layer/gate_client.py").read_text(encoding="utf-8")
    _mark("gate_client 未知周期由 resolve_timeframe 拒绝（无 3600 回退）",
          "resolve_timeframe(interval).seconds" in gc
          and ".get(interval, 3600)" not in gc)


# ---------------------------------------------------------------- 3
def check_config_alignment() -> None:
    print("\n[验收 3] 代码默认周期 ↔ 冻结配置声明一致（防两张皮）")
    cfg_iv = _cfg_scalar("timeframe", "default_interval")
    _mark(f"default_interval 一致（代码={DEFAULT_INTERVAL}, 配置={cfg_iv}）",
          cfg_iv == DEFAULT_INTERVAL)
    for key, code_val in (("hours_per_day", DEFAULT_HOURS_PER_DAY),
                          ("trading_days_per_year", DEFAULT_TRADING_DAYS_PER_YEAR)):
        raw = _cfg_scalar("timeframe", key)
        ok = raw is not None and abs(float(raw) - code_val) < 1e-9
        _mark(f"{key} 一致（代码={code_val:g}, 配置={raw}）", ok)
    raw = _cfg_scalar("timeframe", "max_candles_per_request")
    _mark(f"10000 根上限一致（代码={MAX_CANDLES}, 配置={raw}）",
          raw is not None and int(float(raw)) == MAX_CANDLES)
    # 旧 1min 口径文档债必须已清除（注释里保留旧值作为变更说明是允许的，
    # 因此只断言配置项的生效值，而非全文字符串）
    _mark("annualization 生效值改为按 interval 派生",
          _cfg_scalar("annualization", "sharpe_factor") == "sqrt(bars_per_year)"
          and _cfg_scalar("annualization", "derived_from") == "timeframe")


# ---------------------------------------------------------------- 4
def _cfg_15m(**kw) -> BacktestConfig:
    return BacktestConfig(
        interval="15m",
        leg_a_file="ETH_USDT_15m_95d.parquet",
        leg_b_file="SOL_USDT_15m_95d.parquet",
        benchmark_file="BTC_USDT_15m_95d.parquet",
        **kw,
    )


def check_scale_constant_guard() -> None:
    print("\n[验收 4] 防呆：手写尺度常数与声明周期不符即拦下")
    ok, msg = _expect_value_error(
        lambda: _cfg_15m(bars_per_hour=1.0), "bars_per_hour")
    _mark("bars_per_hour=1.0（旧 1h 残留）+ interval=15m → 拒绝", ok, msg[:70])
    ok2, msg2 = _expect_value_error(
        lambda: _cfg_15m(annualize_factor=math.sqrt(8760)), "annualize_factor")
    _mark("annualize_factor=√8760（旧 1h 残留）+ interval=15m → 拒绝", ok2, msg2[:70])
    tf = resolve_timeframe("15m")
    c = _cfg_15m(bars_per_hour=tf.bars_per_hour,
                 annualize_factor=tf.annualize_factor)
    _mark("显式传入与派生一致的尺度常数 → 放行（兼容旧调用）",
          c.bars_per_hour == tf.bars_per_hour
          and c.annualize_factor == tf.annualize_factor)
    c2 = _cfg_15m()
    _mark("不传尺度常数时自动派生",
          c2.bars_per_hour == 4.0 and abs(c2.annualize_factor - math.sqrt(35040)) < 1e-9)


# ---------------------------------------------------------------- 5
def check_filename_guard() -> None:
    print("\n[验收 5] 防呆：数据文件名声明周期须与回测周期一致")
    ok, msg = _expect_value_error(
        lambda: BacktestConfig(interval="15m"), "周期不一致")
    _mark("默认 1h 文件名 + interval=15m → 拒绝", ok, msg[:70])
    ok2, msg2 = _expect_value_error(
        lambda: BacktestConfig(interval="4h", leg_a_file="ETH_USDT_15m_95d.parquet"),
        "周期不一致")
    _mark("文件名 15m + interval=4h → 拒绝", ok2, msg2[:70])
    # 第三轮聚合扫描：读 1h parquet 后聚合成 4h/1d，须显式声明源数据周期
    c = BacktestConfig(interval="4h", data_interval="1h")
    _mark("聚合场景 data_interval='1h' + interval='4h' → 放行",
          c.timeframe.interval == "4h" and c.data_interval == "1h")
    _mark("interval_from_filename 解析标准缓存名",
          interval_from_filename("BTC_USDT_30m_195d.parquet") == "30m"
          and interval_from_filename("no_period.parquet") is None)


# ---------------------------------------------------------------- 6
def check_market_calendar() -> None:
    print("\n[验收 6] 换市场只换交易日历（phase4 跨市场接口）")
    cn = resolve_timeframe("1d", hours_per_day=4, trading_days_per_year=252)
    crypto = resolve_timeframe("1d")
    _mark(f"A 股 1d: bars/year={cn.bars_per_year:.0f}, √={cn.annualize_factor:.2f}",
          abs(cn.bars_per_year - 252) < 1e-9
          and abs(cn.annualize_factor - math.sqrt(252)) < 1e-9)
    _mark("同一 interval 在不同市场日历下年化因子不同",
          cn.annualize_factor != crypto.annualize_factor)
    c = BacktestConfig(interval="1d", hours_per_day=4,
                       trading_days_per_year=252,
                       leg_a_file="A_1d.parquet", leg_b_file="B_1d.parquet",
                       benchmark_file="BM_1d.parquet")
    _mark("BacktestConfig 注入 A 股日历后派生一致",
          abs(c.annualize_factor - math.sqrt(252)) < 1e-9)


# ---------------------------------------------------------------- 7
def check_time_conversion() -> None:
    print("\n[验收 7] 物理时间 ↔ bar 数互转（业务窗口按时间定义）")
    cases = {"1h": 3.0, "30m": 6.0, "15m": 12.0, "4h": 0.75, "1d": 0.125}
    for iv, bars in cases.items():
        got = bars_for_hours(3.0, iv)
        _mark(f"3 物理小时 = {got:g} 根 {iv}", abs(got - bars) < 1e-9)
    for iv in ("1h", "15m", "4h"):
        b = bars_for_hours(7.5, iv)
        back = hours_for_bars(b, iv)
        _mark(f"{iv}: 7.5h → {b:g} bar → {back:g}h 互逆", abs(back - 7.5) < 1e-9)


# ---------------------------------------------------------------- 8
def check_lookback() -> None:
    print("\n[验收 8] 最大可拉天数由 10000 根上限派生，不写死")
    vals = {iv: max_lookback_days(iv) for iv in ("1m", "5m", "15m", "30m", "1h")}
    _mark(f"随周期单调收缩 {vals}",
          vals["1h"] > vals["30m"] > vals["15m"] > vals["5m"] > vals["1m"])
    ok = True
    for iv, d in vals.items():
        tf = resolve_timeframe(iv)
        if d * tf.bars_per_day > MAX_CANDLES:
            ok = False
    _mark("各周期默认窗口均不超过 10000 根硬上限", ok)
    # 派生窗口与第四轮实测值同量级（395/197/98 vs 落盘 410/195/95 天）
    _mark("30m/15m 派生窗口与实测落盘天数同量级（±5 天）",
          abs(vals["30m"] - 195) <= 5 and abs(vals["15m"] - 95) <= 5)


# ---------------------------------------------------------------- 9
def check_timeout_physical_invariance() -> None:
    print("\n[验收 9] M4 超时 3h 物理下界跨周期不变（业务语义不漂移）")
    # half_life 全设 1，使 timeout_bars = max(3h × bars_per_hour, 1.2)
    # 由 3h 下界主导；z 固定在开仓点，既不回归也不止损，故只有超时能离场。
    sig = pd.DataFrame({"half_life": [1.0] * 5})
    # entry_ts_ms 必须 > 0：M4 把 0 视为「无有效开仓时刻」，bars_held 恒 0，
    # 超时永远不会触发。真实回测时间戳为 Unix 毫秒，天然满足。
    base_ts = 1_700_000_000_000
    for iv in ("1h", "30m", "15m"):
        tf = resolve_timeframe(iv)
        strat = MeanReversionStrategy(interval=iv)
        pos = PositionSnapshot(state="LONG_SPREAD", entry_ts_ms=base_ts,
                               entry_zscore=-3.2, position_qty=1.0)
        trig_bar = math.ceil(3.0 * tf.bars_per_hour)
        at = strat._state_machine(pos, -3.2, base_ts + trig_bar * tf.bar_ms, 1.0,
                                  100.0, 50.0, sig=sig)
        before = strat._state_machine(pos, -3.2,
                                      base_ts + (trig_bar - 1) * tf.bar_ms, 1.0,
                                      100.0, 50.0, sig=sig)
        phys_h = trig_bar * tf.bar_ms / 3_600_000
        ok = (at is not None and at.reason == "timeout"
              and before is None and abs(phys_h - 3.0) < 1e-9)
        _mark(f"{iv:>3s}: 第 {trig_bar:>2d} 根 bar 触发 timeout，"
              f"物理时长 {phys_h:g}h（前一根仍持仓）", ok)
    # 订单 TTL 随周期派生，且 M7 24h 去重窗口是物理时间、与周期无关
    t1 = MeanReversionStrategy(interval="1h")._make_intent(
        0, "buy", -3.2, 100.0, 50.0, "LONG_SPREAD", "entry")
    t15 = MeanReversionStrategy(interval="15m")._make_intent(
        0, "buy", -3.2, 100.0, 50.0, "LONG_SPREAD", "entry")
    _mark(f"订单 TTL 随周期派生（1h={t1.ttl_ms}, 15m={t15.ttl_ms}）",
          t1.ttl_ms == 3_600_000 and t15.ttl_ms == 900_000)
    d1 = CryptoMonitorPolicy(interval="1h").dedupe_window_ms
    d15 = CryptoMonitorPolicy(interval="15m").dedupe_window_ms
    _mark("M7 24h 物理去重窗口与周期无关", d1 == d15 == 86_400_000)


# ---------------------------------------------------------------- 10
def check_artifacts() -> None:
    print("\n[验收 10] 产物动态口径：报告 / golden value / 看板 不写死 1h")
    gv_path = ROOT / "backtest/output/golden_value.json"
    rep_path = ROOT / "backtest/output/m8_backtest_report.md"
    dash_path = ROOT / "dashboard/index.html"
    if not gv_path.exists():
        _mark("golden_value.json 存在", False, "请先运行 python backtest/m8_report.py")
        return
    gv = json.loads(gv_path.read_text(encoding="utf-8"))
    need = ("interval", "annualize_label", "bars_per_year", "annualize_factor",
            "hours_per_day", "trading_days_per_year")
    _mark(f"golden value 含周期元数据 {[k for k in need if k in gv]}",
          all(k in gv for k in need))
    _mark("golden value 年化口径与派生值一致",
          abs(float(gv.get("annualize_factor", -1))
              - math.sqrt(float(gv.get("bars_per_year", 0)))) < 1e-6)
    if rep_path.exists():
        rep = rep_path.read_text(encoding="utf-8")
        _mark("M8 报告声明「由周期参数派生，非写死 1h」",
              "由周期参数派生" in rep and str(gv.get("interval")) in rep)
    else:
        _mark("M8 报告存在", False)
    if dash_path.exists():
        dash = dash_path.read_text(encoding="utf-8")
        renderer = (ROOT / "dashboard/render_dashboard.py").read_text(encoding="utf-8")
        # 注意：动态拼接后 h1 恰好也是「410 天 1h」，所以不能断言产物里没有
        # 这个字符串 —— 应断言渲染器源码里没有写死该字面量。
        _mark("看板标题动态渲染周期（渲染器无写死 410 天 1h）",
              "410 天 1h" not in renderer and str(gv.get("interval")) in dash)
        _mark("看板展示动态年化口径",
              str(gv.get("annualize_label", "___")) in dash)
    else:
        _mark("dashboard/index.html 存在", False)


def main() -> int:
    print("=" * 70)
    print("周期参数化专项验收 · src/core/timeframe.py 单一事实源")
    print("=" * 70)
    check_derivation()
    check_unknown_interval()
    check_config_alignment()
    check_scale_constant_guard()
    check_filename_guard()
    check_market_calendar()
    check_time_conversion()
    check_lookback()
    check_timeout_physical_invariance()
    check_artifacts()
    print("\n" + "=" * 70)
    verdict = "✅ 通过" if FAIL_CNT == 0 else "❌ 未通过"
    print(f"周期参数化验收结论: {verdict}  （{PASS_CNT} 通过 / {FAIL_CNT} 失败）")
    print("=" * 70)
    return 0 if FAIL_CNT == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
