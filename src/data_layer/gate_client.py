"""Gate.io 现货 API 客户端 —— 只读、签名、分页拉取深历史 K 线。

安全：仅调用 GET /spot/candlesticks（行情只读），不做任何下单/资金操作。
KEY/SECRET 从 .env 读取，绝不硬编码、绝不进 git。
频率限制：Gate.io spot 约 200 req/30s，分页窗口间 sleep 规避。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import urllib.request
from pathlib import Path

from src.core.timeframe import resolve_timeframe

GATE_BASE = "https://api.gateio.ws/api/v4"
UA = {"User-Agent": "Mozilla/5.0 (quant-system)"}
ROOT = Path(__file__).resolve().parents[2]


def _load_env() -> dict:
    env = {}
    p = ROOT / ".env"
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env


class GateClient:
    def __init__(self):
        env = _load_env()
        self.key = env.get("GATE_API_KEY", "")
        self.secret = env.get("GATE_API_SECRET", "")
        if not self.key or not self.secret:
            raise RuntimeError(".env 缺少 GATE_API_KEY / GATE_API_SECRET")

    def _sign(self, method: str, path: str, query: str, t: str) -> dict:
        hashed_payload = hashlib.sha512(b"").hexdigest()
        s = f"{method}\n{path}\n{query}\n{hashed_payload}\n{t}".encode()
        sig = base64.b64encode(
            hmac.new(self.secret.encode(), s, hashlib.sha512).digest()
        ).decode()
        return {"KEY": self.key, "Timestamp": t, "Signature": sig}

    def _get(self, path: str, query: str, timeout: int = 30, retries: int = 3) -> list:
        last = None
        for attempt in range(retries + 1):
            t = str(time.time())
            headers = {**UA, **self._sign("GET", path, query, t)}
            url = f"{GATE_BASE}{path}?{query}"
            req = urllib.request.Request(url, headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    return json.loads(r.read().decode()) or []
            except urllib.error.HTTPError as e:
                body = e.read().decode()[:200]
                if e.code == 429:          # 限流，退避
                    time.sleep(1.5 * (attempt + 1))
                    last = e
                    continue
                raise RuntimeError(f"HTTP {e.code}: {body}") from e
            except Exception as e:         # noqa
                time.sleep(0.5 * (attempt + 1))
                last = e
                continue
        raise RuntimeError(f"重试失败: {last}")

    def fetch_candles(self, pair: str, interval: str, from_ts: int, to_ts: int,
                      window_days: int = 7, sleep_s: float = 0.3) -> list:
        """分页拉取 [from_ts, to_ts] 的全量 K 线（每次 ≤1000 根，规避频率限制）。"""
        # 周期秒数由单一事实源派生；未知周期直接报错，绝不 .get(..., 3600)
        # 静默回退 1h（旧实现会按错误步长分页，拉出与声明周期不符的数据）。
        interval_s = resolve_timeframe(interval).seconds
        window = window_days * 86400
        step = max(window // 2, interval_s)
        cur = from_ts
        out: list = []
        while cur < to_ts:
            w_end = min(cur + window, to_ts)
            q = (f"currency_pair={pair}&interval={interval}"
                 f"&from={cur}&to={w_end}")
            batch = self._get("/spot/candlesticks", q)
            out.extend(batch)
            time.sleep(sleep_s)           # 规避频率限制
            if w_end >= to_ts:
                break
            cur = cur + step
        # 去重
        seen = set()
        uniq = []
        for r in out:
            ts = int(r[0])
            if ts in seen:
                continue
            seen.add(ts)
            uniq.append(r)
        uniq.sort(key=lambda r: int(r[0]))
        return uniq

    def account_summary(self) -> dict:
        """只读：账户 USDT 余额概览（验证 KEY 有 account 读权限，可选）。"""
        t = str(time.time())
        headers = {**UA, **self._sign("GET", "/spot/accounts", "", t)}
        url = f"{GATE_BASE}/spot/accounts?currency=USDT"
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode())


if __name__ == "__main__":
    import datetime
    import sys
    from src.core.timeframe import max_lookback_days

    c = GateClient()
    now = int(time.time())
    # 冒烟周期由命令行声明（默认 1h），窗口与深度都由周期派生，不写死 1h/365 天/7 天
    iv = sys.argv[1] if len(sys.argv) > 1 else "1h"
    tf = resolve_timeframe(iv)
    days = min(365, max_lookback_days(iv))
    window_days = max(1, min(7, 900 // max(1, int(tf.bars_per_day))))
    print(f"测试 {iv} {days} 天分页拉取（只读，窗口 {window_days} 天）...")
    raw = c.fetch_candles("BTC_USDT", iv, now - days * 86400, now,
                          window_days=window_days)
    if raw:
        t0 = int(raw[0][0])
        t1 = int(raw[-1][0])
        print(f"实拉 {len(raw)} 根  [{datetime.datetime.fromtimestamp(t0, datetime.timezone.utc):%Y-%m-%d}"
              f" → {datetime.datetime.fromtimestamp(t1, datetime.timezone.utc):%Y-%m-%d}]")
        print(f"最早时间戳 {t0}，距今 {(now-t0)//86400} 天")
