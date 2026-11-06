"""
M1 · 数据层 —— Gate.io 现货 K 线接入、清洗、两腿对齐、Parquet 落地

设计依据：
  - agents/phase2-architecture.md §5 数据方案（数据工程师）
  - agents/phase3-project-plan.md M1（验收标准）

职责边界：只负责"拿到可信行情 + 清洗 + 对齐 + 落地"，不含任何策略逻辑。
公开接口 build_kline_fetcher() 返回带 cache_dir / fetch / align 的方法对象，
下游（信号层 M3）只依赖它，不依赖实现。

实测分页语义（2026-11-07）：
  - GET /spot/candlesticks 字段顺序（单根数组）：
    [t, v, close, high, low, open, amount, is_closed]
    其中 v = 成交额（USDT 计价），amount = 成交量（基础币计价）
  - from/to 可同时传（不混 limit 即可），按时间区间返回
  - limit 上限 < 3000；翻页用 from/to 滑动窗口
"""

from __future__ import annotations

import time
import urllib.request
from pathlib import Path
from typing import Optional

import pandas as pd
import numpy as np

GATE_BASE = "https://api.gateio.ws/api/v4"
UA = {"User-Agent": "Mozilla/5.0 (quant-system M1)"}

# 各粒度默认拉取深度（天）
DEFAULT_LOOKBACK_DAYS = {
    "1h": 730,   # 主尺度（协整/半衰期）
    "5m": 90,    # 默认回测
    "1m": 30,    # 滑点/微观结构
}
INTERVAL_SECONDS = {"1m": 60, "5m": 300, "1h": 3600}

# 清洗阈值（数据工程师口径，可被 strategy_params 覆盖）
PRICE_JUMP_PCT = 0.05      # |close_t/close_{t-1} - 1| > 5% 且量偏低 → 坏价
THIN_VOL_QUANTILE = 0.2    # 成交量 < 中位数的 20% 视为低流动性


def _http_get_json(url: str, timeout: int = 20, retries: int = 3, delay: float = 200 / 1000):
    """带重试的 GET，规避 Gate.io 限流（~200 req/30s）。"""
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
            thin(bool), bad_px(bool)
        """
        if interval not in INTERVAL_SECONDS:
            raise ValueError(f"不支持的 interval: {interval}")
        days = lookback_days or DEFAULT_LOOKBACK_DAYS.get(interval, 30)
        now = int(time.time())
        end = end_ts if end_ts is not None else now
        start = start_ts if start_ts is not None else end - days * 86400

        raw = self._fetch_all(pair, interval, start, end)
        df = self._to_frame(raw, pair)
        df = self._clean(df, interval)
        return df

    def _fetch_all(self, pair: str, interval: str, start: int, end: int) -> list:
        """用 from/to 滑动窗口翻页拉取全区间（规避 limit 上限）。

        Gate.io 实测：深历史接口对过老的 from 会返回 400。
        因此单窗口 400 时不再让整个拉取崩溃，而是把该窗口跳过并告警，
        自动收缩到"可用深度"，保证能拿到的尽量拿到。
        """
        span = end - start
        window = min(7 * 86400, max(span, 1))   # 每次窗口 7 天（1h 约 168 根）
        step = window // 2                        # 50% 重叠防边界漏
        cur = start
        out: list = []
        skipped = 0
        while cur < end:
            w_end = min(cur + window, end)
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
            warnings.warn(
                f"{pair} {interval}: {skipped} 个窗口 400（超出公开接口可用深度），"
                f"实际拉取深度有限，详见返回数据最早时间戳。", stacklevel=2)
        return out

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
        expected = np.arange(ts[0], ts[-1] + INTERVAL_SECONDS[interval], INTERVAL_SECONDS[interval])
        present = set(ts)
        gap = np.zeros(len(df), dtype=bool)
        # 把"该 bar 之前是否有缺口"累积标记（缺口数 >0 的 bar 置 gap=True）
        running = 0
        prev = ts[0]
        for i, t in enumerate(ts):
            missing = int(t - prev) // INTERVAL_SECONDS[interval] - 1
            running += max(0, missing)
            gap[i] = running > 0
            prev = t
        df["gap"] = gap
        return df

    # ---------- 对齐（两腿 inner join）----------
    def align_pair(self, pair_a: str, pair_b: str, interval: str = "5m",
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
