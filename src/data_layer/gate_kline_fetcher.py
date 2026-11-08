"""
M1 · 数据层 —— Gate.io 现货 K 线接入、清洗、两腿对齐、Parquet 落地

设计依据：
  - agents/phase2-architecture.md §5 数据方案（数据工程师）
  - agents/phase3-project-plan.md M1（验收标准）

职责边界：只负责"拿到可信行情 + 清洗 + 对齐 + 落地"，不含任何策略逻辑。

历史深度（实测 2026-11-07 / 2026-11-08，用只读 KEY 解锁后）：
  - Gate.io K 线只保留最近 10000 根（硬上限），请求更早区间会 400
  - 1h → ~416 天；30m → ~208 天；15m → ~104 天；5m → ~34.7 天；1m → ~7 天
  - 实测可达窗口：15m 95 天 / 9121 根，30m 195 天 / 9361 根（2026-11-08 拉取）
  - 单次请求 ≤1000 根，深历史需 from/to 分页 + 频率退避
  - 有 GATE_API_KEY 时走 GateClient（签名只读）拉深历史；无则回退公开接口（~30 天）

周期参数化（用户 2026-11-08 裁决「周期不能写死」）：
  interval→秒数、每日 bar 数、最大可拉天数一律来自 src/core/timeframe.py，
  本文件不再自带映射表，也不再写 24/48/96/288 根每日常量；
  未知 interval 直接报错，绝不静默按 1h 处理。
"""

from __future__ import annotations

import time
import urllib.request
from pathlib import Path
from typing import Optional

import pandas as pd
import numpy as np

from src.core.timeframe import (
    INTERVAL_SECONDS, MAX_CANDLES, max_lookback_days, resolve_timeframe,
)

GATE_BASE = "https://api.gateio.ws/api/v4"
UA = {"User-Agent": "Mozilla/5.0 (quant-system)"}

# 各粒度默认拉取深度（天）—— 由周期 + 10000 根上限推导，不写死天数。
# 实测 2026-11-08：请求早于「最近 10000 根」时 Gate.io 直接返回 400
#   {"label":"INVALID_PARAM_VALUE","message":"Candlestick too long ago.
#    Maximum 10000 points ago are allowed"}，故降尺度只能缩短日历窗口。
# 仅列本项目实拉验证过的 Gate.io 现货粒度；4h/1d 等聚合尺度不由此表约束。
GATE_NATIVE_INTERVALS = ("1m", "5m", "15m", "30m", "1h")
DEFAULT_LOOKBACK_DAYS = {
    iv: max_lookback_days(iv, max_candles=MAX_CANDLES)
    for iv in GATE_NATIVE_INTERVALS
}

# 清洗阈值（数据工程师口径，可被 strategy_params 覆盖）
PRICE_JUMP_PCT = 0.05      # |close_t/close_{t-1} - 1| > 5% 且量偏低 → 坏价
THIN_VOL_QUANTILE = 0.2    # 成交量 < 中位数的 20% 视为低流动性


def _get_auth_client():
    """有 KEY 时返回签名只读客户端，无则 None（回退公开接口）。"""
    try:
        from .gate_client import GateClient
        c = GateClient()
        return c
    except Exception:
        return None


def _http_get_json(url: str, timeout: int = 20, retries: int = 3, delay: float = 200 / 1000):
    """带重试的 GET（公开接口，无签名）。"""
    last_exc = None
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, headers=UA)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                import json
                return json.loads(r.read().decode())
        except Exception as e:  # noqa: BLEND01
            last_exc = e
            if attempt < retries:
                time.sleep(delay * (attempt + 1))
                continue
    raise RuntimeError(f"GET 失败（{retries} 次重试后）: {url} -> {last_exc}")


class GateKLineFetcher:
    """拉取 + 清洗 + 对齐 + 落地的数据层入口。"""

    def __init__(self, cache_dir: str = "data/cache", use_cache: bool = True):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.use_cache = use_cache

    # ---------- 拉取 ----------
    def fetch_kline(self, pair: str, interval: str, lookback_days: Optional[int] = None,
                    start_ts: Optional[int] = None, end_ts: Optional[int] = None) -> pd.DataFrame:
        """拉取单一 currency_pair 的 K 线，返回清洗后的 DataFrame。

        列：ts(int,UTC秒), open, high, low, close, volume(基础币), quote_volume(USDT),
            thin(bool), bad_px(bool), gap(bool)

        有 GATE_API_KEY 时走 GateClient（签名只读）拉深历史（~410 天）；
        无 key 时回退公开接口（~30 天）。
        """
        # 未知周期在这里就报错，绝不静默按 1h 分页拉错数据
        resolve_timeframe(interval)
        days = lookback_days or DEFAULT_LOOKBACK_DAYS.get(
            interval, max_lookback_days(interval))
        now = int(time.time())
        end = end_ts if end_ts is not None else now
        start = start_ts if start_ts is not None else end - days * 86400

        raw = self._fetch_all(pair, interval, start, end)
        df = self._to_frame(raw, pair)
        df = self._clean(df, interval)
        return df

    def _fetch_all(self, pair: str, interval: str, start: int, end: int) -> list:
        """深历史拉取：优先用只读签名客户端（GateClient，分页+退避），
        无 KEY 时回退公开接口的 from/to 滑动窗口。"""
        auth = _get_auth_client()
        if auth is not None:
            try:
                return auth.fetch_candles(pair, interval, start, end,
                                           window_days=self._window_days(interval))
            except Exception as e:  # 签名/网络失败则回退公开接口
                import warnings
                warnings.warn(f"签名深历史拉取失败，回退公开接口: {e}", stacklevel=2)

        # 公开接口回退：from/to 滑动窗口，对 400 自动收缩
        step = self._window_days(interval) * 86400 // 2
        cur = start
        out: list = []
        skipped = 0
        while cur < end:
            w_end = min(cur + self._window_days(interval) * 86400, end)
            url = (f"{GATE_BASE}/spot/candlesticks?currency_pair={pair}"
                   f"&interval={interval}&from={cur}&to={w_end}")
            batch = self._safe_get(url)
            if batch is None:
                skipped += 1
                if w_end >= end:
                    break
                cur = cur + step
                continue
            out.extend(batch)
            if w_end >= end:
                break
            cur = cur + step
        if skipped:
            import warnings
            warnings.warn(f"{pair} {interval}: {skipped} 窗口超出公开接口深度", stacklevel=2)
        return out

    @staticmethod
    def _window_days(interval: str) -> int:
        """分页窗口大小（天），使每窗口 K 线数 < 1000。由周期派生每日 bar 数。"""
        bars_per_day = resolve_timeframe(interval).bars_per_day
        days = max(1, 900 // max(1, int(bars_per_day)))  # 约 900 根/窗口
        return min(days, 7)

    def _safe_get(self, url: str):
        """单窗口拉取：成功返回 list，400 返回 None（用于自动收缩），其它异常重试后抛出。"""
        import urllib.error
        req = urllib.request.Request(url, headers=UA)
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=20) as r:
                    import json
                    return json.loads(r.read().decode())
            except urllib.error.HTTPError as e:
                if e.code == 400:
                    return None          # 深度不足，收缩而非崩溃
                time.sleep(0.5 * (attempt + 1))
                if attempt == 2:
                    raise
                continue
            except Exception as e:       # noqa: BLEND01
                time.sleep(0.5 * (attempt + 1))
                if attempt == 2:
                    raise RuntimeError(f"GET 失败: {url} -> {e}")
        return None

    def _to_frame(self, raw: list, pair: str) -> pd.DataFrame:
        if not raw:
            return pd.DataFrame(columns=["ts", "open", "high", "low", "close",
                                         "volume", "quote_volume"])
        recs = [
            {
                "pair": pair,
                "ts": int(r[0]),
                "close": float(r[2]),
                "high": float(r[3]),
                "low": float(r[4]),
                "open": float(r[5]),
                "volume": float(r[6]) if r[6] is not None else 0.0,
                "quote_volume": float(r[1]) if r[1] is not None else 0.0,
            }
            for r in raw
        ]
        df = pd.DataFrame(recs)
        # 去重：翻页边界易重复，(pair,ts) 为主键保留最后一根
        df = df.drop_duplicates(subset=["ts"], keep="last")
        df = df.sort_values("ts").reset_index(drop=True)
        return df

    def _clean(self, df: pd.DataFrame, interval: str) -> pd.DataFrame:
        """去重(已在 _to_frame)、异常值标记、缺口检测。"""
        if df.empty:
            return df
        ts = df["ts"].to_numpy()
        close = df["close"].to_numpy()
        vol = df["volume"].to_numpy()

        thin = np.zeros(len(df), dtype=bool)
        bad_px = np.zeros(len(df), dtype=bool)

        # 零成交 / 低流动性 → thin
        vol_med = np.median(vol) if len(vol) else 0.0
        thin = (vol == 0) | (vol_med > 0 and vol < THIN_VOL_QUANTILE * vol_med)

        # 价格跳变 + 低量 → 坏价
        pct = np.abs(np.diff(close, prepend=close[0]) / np.where(close[0] == 0, 1, close[0]))
        bad_px = (pct > PRICE_JUMP_PCT) & (vol_med > 0) & (vol < THIN_VOL_QUANTILE * vol_med)

        df["thin"] = thin
        df["bad_px"] = bad_px

        # 缺口检测：按预期 bar 数生成完整时间轴，缺失 ≥1 bar 标记
        bar_s = resolve_timeframe(interval).seconds
        expected = np.arange(ts[0], ts[-1] + bar_s, bar_s)
        present = set(ts)
        gap = np.zeros(len(df), dtype=bool)
        # 把"该 bar 之前是否有缺口"累积标记（缺口数 >0 的 bar 置 gap=True）
        running = 0
        prev = ts[0]
        for i, t in enumerate(ts):
            missing = int(t - prev) // bar_s - 1
            running += max(0, missing)
            gap[i] = running > 0
            prev = t
        df["gap"] = gap
        return df

    # ---------- 对齐（两腿 inner join）----------
    # interval 无默认值：周期必须由调用方显式声明，不允许默认 5m/1h（用户 2026-11-08）
    def align_pair(self, pair_a: str, pair_b: str, interval: str,
                   lookback_days: Optional[int] = None) -> pd.DataFrame:
        """拉取两腿并对齐：resample 到统一 grid 后 inner join。
        缺腿整 bar 丢弃（禁 ffill，避免价差被伪平）。"""
        da = self.fetch_kline(pair_a, interval, lookback_days)
        db = self.fetch_kline(pair_b, interval, lookback_days)
        if da.empty or db.empty:
            return pd.DataFrame()
        merged = da.merge(db, on="ts", how="inner", suffixes=("_a", "_b"))
        # 成交量列重命名 volume_* -> vol_*（语义统一）
        merged = merged.rename(columns={"volume_a": "vol_a", "volume_b": "vol_b"})
        out = merged[["ts", "close_a", "close_b", "vol_a", "vol_b",
                       "thin_a", "thin_b", "bad_px_a", "bad_px_b", "gap_a", "gap_b"]].copy()
        return out

    # ---------- 落地 ----------
    def to_parquet(self, df: pd.DataFrame, path: str) -> Path:
        p = self.cache_dir / path
        p.parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(p, index=False)
        return p


def build_kline_fetcher(cache_dir: str = "data/cache") -> GateKLineFetcher:
    """M1 对外稳定读接口：下游只拿到这个，不依赖实现细节。"""
    return GateKLineFetcher(cache_dir=cache_dir)
