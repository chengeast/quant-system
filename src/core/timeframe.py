"""
core · 周期（timeframe）参数单一事实源

用户 2026-11-08 裁决：「1h 周期怎么能写死呢？这是一个周期参数。不能写死」。

问题背景：
  旧实现把 1h 写死在多处 —— BacktestConfig.bars_per_hour=1、
  annualize_factor=√(365×24)、get_sig 的 60bar 注释、报告/看板的「1h 年化」、
  数据层的 24 根/日、以及 gate_client 对未知 interval 静默回退 3600 秒。
  换周期时必须人工逐处改，漏一处夏普/Calmar/超时/分页就全错。

原则（本模块是唯一事实源，下游禁止再写尺度常数）：
  1. 「周期」是尺度参数，只在调用方声明一次：interval 字符串。
  2. 所有派生量（bar 秒数 / bars_per_hour / bars_per_day / bars_per_year /
     annualize_factor / 最大可拉天数）一律由 interval + 市场交易日历计算。
  3. 市场日历（每天交易小时数、每年交易日数）是市场相关维度：
     加密 7×24 = (24, 365)；A 股 = (4, 252)；美股期货按需填。
     换市场只换这两个数，6 层骨架不动（phase4 跨市场架构）。
  4. 未知 interval 必须报错 —— 绝不静默回退 1h。
     旧 gate_client 的 `{"1h": 3600}.get(interval, 3600)` 会让未知周期
     按 1h 分页拉错数据，是最危险的一处写死。

口径核对（加密 7×24）：
  1h  → bars_per_year = 365×24 = 8760   → annualize_factor = √8760  ≈ 93.59
  30m → 365×48 = 17520                 → √17520 ≈ 132.36
  15m → 365×96 = 35040                 → √35040 ≈ 187.19
  4h  → 365×6  = 2190                  → √2190  ≈ 46.80
  1d  → 365×1  = 365                   → √365   ≈ 19.10
  与第四轮试跑手写的 √(365×24/scale) 完全一致，故参数化不改变任何已归档结果。

日线的特殊处理（timeframe_acceptance 验收 6 抓到并修复）：
  日内周期 bars_per_day = hours_per_day × bars_per_hour；但 1d K 线一天
  只能有 1 根，不能再用「每天交易小时数」缩放。故 bars_per_day 下限为 1，
  A 股 1d（4h/日 × 252 日）正确得到 252 根/年（旧公式误算为 42）。
  1h 加密下 max(1, 24×1)=24，与旧版完全一致 → golden value 不受影响。
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Optional


SECONDS_PER_HOUR = 3_600
SECONDS_PER_DAY = 86_400
MS_PER_SECOND = 1_000

# ---------------------------------------------------------------------------
# interval → 单根 bar 的秒数（全项目唯一一处映射）
# ---------------------------------------------------------------------------
# 实测可用（本项目 Gate.io 现货实拉）：1m / 5m / 15m / 30m / 1h
# 由 1h 聚合得到（第三轮 B 层扫描）：4h / 1d
# 其余为常见标准粒度，供未来市场/数据源接入，未在 MVP 实拉验证。
INTERVAL_SECONDS: dict[str, int] = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "30m": 1_800,
    "1h": 3_600,
    "2h": 7_200,
    "4h": 14_400,
    "8h": 28_800,
    "1d": 86_400,
    "1w": 604_800,
}

# 市场日历默认值（加密 7×24）。换市场由调用方注入，不改本模块。
DEFAULT_HOURS_PER_DAY = 24.0
DEFAULT_TRADING_DAYS_PER_YEAR = 365.0

# 默认周期：代码侧唯一声明点。所有 `interval=...` 的函数默认值都必须引用它，
# 不允许再散落 "1h" 字面量。
# 与 config/strategy_params.yaml 的 timeframe.default_interval 是同一事实的两处
# 表述，由 backtest/timeframe_acceptance.py 强制校验一致（改一处漏一处即验收失败）。
# 注意：interval 是「回测调用参数」，不是全局常量 —— 第四轮 15m/30m 对照实验
# 就是在同一进程里对每个实验传不同 BacktestConfig(interval=...)。
DEFAULT_INTERVAL = "1h"

# Gate.io 只保留最近 10000 根 K 线（实测 2026-11-08，请求更早区间返回 400）
MAX_CANDLES = 10_000
# 默认拉取天数相对理论上限留 5% 余量（避免边界抖动 / 时区截断）
DEFAULT_LOOKBACK_SAFETY = 0.95


@dataclass(frozen=True)
class Timeframe:
    """一个周期的全部派生量（不可变，避免运行中尺度漂移）。"""
    interval: str
    seconds: int
    hours_per_day: float
    trading_days_per_year: float
    bars_per_hour: float
    bars_per_day: float
    bars_per_year: float
    annualize_factor: float

    @property
    def bar_ms(self) -> int:
        """单根 bar 的毫秒数（超时/持仓时长换算用）。"""
        return self.seconds * MS_PER_SECOND

    @property
    def scale_vs_1h(self) -> float:
        """相对 1h 的 bar 长度倍数（1h=1.0、15m=0.25、4h=4.0）。"""
        return self.seconds / INTERVAL_SECONDS["1h"]

    @property
    def annualize_label(self) -> str:
        """报告/看板用的动态口径文案，不再写死「1h 年化 √(365×24)」。"""
        return (f"{self.interval} 年化 √({self.bars_per_year:.0f}) "
                f"≈ {self.annualize_factor:.2f}")

    def describe(self) -> str:
        return (f"Timeframe({self.interval}: {self.seconds}s/bar, "
                f"bars/hour={self.bars_per_hour:g}, "
                f"bars/day={self.bars_per_day:g}, "
                f"bars/year={self.bars_per_year:g}, "
                f"annualize=√{self.bars_per_year:g}"
                f"={self.annualize_factor:.4f}, "
                f"calendar={self.hours_per_day:g}h×"
                f"{self.trading_days_per_year:g}d)")


def resolve_timeframe(interval: str,
                      hours_per_day: float = DEFAULT_HOURS_PER_DAY,
                      trading_days_per_year: float = DEFAULT_TRADING_DAYS_PER_YEAR
                      ) -> Timeframe:
    """由 interval + 市场日历派生全部尺度参数。未知周期直接报错。"""
    if interval not in INTERVAL_SECONDS:
        raise ValueError(
            f"不支持的 interval={interval!r}；可用：{sorted(INTERVAL_SECONDS)}。"
            "周期是参数，不得静默回退到 1h。"
        )
    if hours_per_day <= 0 or trading_days_per_year <= 0:
        raise ValueError(
            f"市场日历非法：hours_per_day={hours_per_day}, "
            f"trading_days_per_year={trading_days_per_year}"
        )
    sec = INTERVAL_SECONDS[interval]
    bars_per_hour = SECONDS_PER_HOUR / sec
    if sec <= SECONDS_PER_DAY:
        # 日内周期：每天 bar 数 = 交易小时数 × 每小时 bar 数。
        # 但日线一天只能有 1 根 —— 交易小时数不该再缩放它。
        # 反例（本验收抓到的真 bug）：A 股 4h/日、252 交易日、1d K 线，
        # 旧公式 4 × (1/24) = 1/6 根/日 → 42 根/年（错）；正确是 252 根/年。
        bars_per_day = max(1.0, hours_per_day * bars_per_hour)
        bars_per_year = trading_days_per_year * bars_per_day
    else:
        # 周线及以上：「每天交易小时数」不适用，按自然日折算
        # （加密 7×24 下 1w ≈ 52 根/年）。A 股周线需另加「每周交易日数」
        # 字段，当前不在 MVP 派生口径内，故只支持自然日折算。
        bars_per_day = SECONDS_PER_DAY / sec
        bars_per_year = trading_days_per_year * bars_per_day
    return Timeframe(
        interval=interval,
        seconds=sec,
        hours_per_day=float(hours_per_day),
        trading_days_per_year=float(trading_days_per_year),
        bars_per_hour=bars_per_hour,
        bars_per_day=bars_per_day,
        bars_per_year=bars_per_year,
        annualize_factor=math.sqrt(bars_per_year),
    )


# 数据文件名形如 ETH_USDT_1h_410d.parquet / BTC_USDT_15m_95d.parquet
_FILE_INTERVAL_RE = re.compile(r"_(\d+[smhdw])_")


def interval_from_filename(filename: str) -> Optional[str]:
    """从标准缓存文件名解析 period；解析不到返回 None（不猜）。"""
    name = str(filename).replace("\\", "/").rsplit("/", 1)[-1]
    m = _FILE_INTERVAL_RE.search(name)
    if m and m.group(1) in INTERVAL_SECONDS:
        return m.group(1)
    return None


def bars_for_hours(hours: float, interval: str,
                   hours_per_day: float = DEFAULT_HOURS_PER_DAY,
                   trading_days_per_year: float = DEFAULT_TRADING_DAYS_PER_YEAR
                   ) -> float:
    """把「物理小时」换算成该周期的 bar 数（供按时间定义的窗口使用）。"""
    if hours <= 0:
        raise ValueError(f"hours 必须为正，实际 {hours}")
    return hours * resolve_timeframe(
        interval, hours_per_day, trading_days_per_year).bars_per_hour


def hours_for_bars(bars: float, interval: str,
                   hours_per_day: float = DEFAULT_HOURS_PER_DAY,
                   trading_days_per_year: float = DEFAULT_TRADING_DAYS_PER_YEAR
                   ) -> float:
    """把该周期的 bar 数换算成物理小时（报告/注释用，避免「60 bar ≈ 60h」写死）。"""
    return bars / resolve_timeframe(
        interval, hours_per_day, trading_days_per_year).bars_per_hour


def max_lookback_days(interval: str,
                      max_candles: int = MAX_CANDLES,
                      hours_per_day: float = DEFAULT_HOURS_PER_DAY,
                      safety: float = DEFAULT_LOOKBACK_SAFETY) -> int:
    """在「最近 N 根 K 线」硬上限下，该周期最多能拉多少自然日。

    由 bars_per_day 推导，不再写死 24/48/96/288/1440 根每日常量。
    """
    if not 0 < safety <= 1:
        raise ValueError(f"safety 必须在 (0,1]，实际 {safety}")
    tf = resolve_timeframe(interval, hours_per_day=hours_per_day)
    return max(1, int(max_candles / tf.bars_per_day * safety))


__all__ = [
    "INTERVAL_SECONDS", "Timeframe", "resolve_timeframe",
    "interval_from_filename", "max_lookback_days",
    "bars_for_hours", "hours_for_bars",
    "DEFAULT_HOURS_PER_DAY", "DEFAULT_TRADING_DAYS_PER_YEAR",
    "DEFAULT_INTERVAL", "MAX_CANDLES", "DEFAULT_LOOKBACK_SAFETY",
    "SECONDS_PER_HOUR", "SECONDS_PER_DAY", "MS_PER_SECOND",
]
