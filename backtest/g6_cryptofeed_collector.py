"""G-6-③ cryptofeed 未来实盘采集器（4 币 × Binance + Bybit 双源；Gate.io 本机 502 不可达、G-1 已证 → 如实标 N/A、不硬连）。

数据流（公共 WS 端点、无鉴权、无 key）：
  binance spot        → L2 top5 盘口 + 逐笔 trades
  bybit spot          → L2 top5 盘口 + 逐笔 trades + funding（8h 结算推送）
  binance futures     → OI（open interest，源支持则采；Bybit WS 无 OI 推送 → N/A）
落盘：
  SQLite   data/cache/g6_cryptofeed_live/{COIN}/{COIN}.db（表 l2/trades/funding/oi，source 列分源）
  Parquet  data/cache/g6_cryptofeed_live/{COIN}/l2_daily_YYYYMMDD.parquet、trades_daily_YYYYMMDD.parquet（每日 00:00 UTC 转前一日）
保留策略：trades 7d 滚动 / funding 30d / l2、oi 永存（SQLite 内）；磁盘水位 >20GB 告警日志（不自动删、如实报）。
重连：WS 断线由 cryptofeed 内建指数退避（RetryPolicy base=1s、封顶 120s）自动重连（长跑 retries=-1）；
      遇 429/502/503/超时隔 3 分钟再试一次仍失败 → 该源如实记 N/A 分级、不静默吞（heartbeat/manifest 留痕；冒烟 retries=2）。
网络：本机直连交易所端点不可达时，可用 G6COLLECT_PROXY 环境变量指定代理
      （如 socks5://127.0.0.1:10808，沿用 D-2/F-2 既有本地代理链），脚本本身不写死任何代理地址。
心跳：每 30s 写 data/cache/g6_cryptofeed_live/heartbeat.json（{coin:{source:{last_ts,uptime,reconnects,status}}}）。
manifest：data/cache/g6_cryptofeed_live/_manifest.json（启动时间、币/源清单、表结构、落盘规则、SHA256-16 数据指纹、磁盘水位、合规声明）。

启动：python backtest/g6_cryptofeed_collector.py                      # 前台 7×24
冒烟：python backtest/g6_cryptofeed_collector.py --once --seconds 300 # 跑满 300s 落一批验证链路后自停（本批验收用冒烟模式）

合规：只读公共端点；不挂单不下单；不接付费源；不改 src/、不动 config/、不动 .bat/.env/.gitignore；不提交 Git。
"""
import argparse
import asyncio
import datetime as dt
import hashlib
import json
import logging
import os
import sqlite3
import time

import pandas as pd
from cryptofeed import FeedHandler
from cryptofeed.callback import Callback
from cryptofeed.symbols import Symbol, PERPETUAL
from cryptofeed.exchanges import Binance, Bybit, BinanceFutures

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = os.path.join(ROOT, "data", "cache", "g6_cryptofeed_live")
COINS = ["BTC", "ETH", "DOGE", "LTC"]          # 与 G-2 选定 G1 carry 多头池一致
BIN, BYB, BINF = "binance", "bybit", "binance-futures"
SOURCES = [BIN, BYB, BINF]                     # Gate.io N/A（本机 502，G-1 已证，不硬连）
KEEP_TRADES_DAYS, KEEP_FUNDING_DAYS = 7, 30
GB_WATERMARK = 20.0
HEARTBEAT_SEC = 30
L2_TOP = 5                                      # L2 按源支持取可用档数（top5）
RETRY_WAIT_SEC = 180                            # 429/502/503/超时：隔 3 分钟再试一次
PROXY = os.environ.get("G6COLLECT_PROXY")        # 可选：本机直连不可达时走代理（不写死地址）

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("g6collector")


def coin_dir(coin: str) -> str:
    d = os.path.join(BASE, coin)
    os.makedirs(d, exist_ok=True)
    return d


def open_db(coin: str) -> sqlite3.Connection:
    conn = sqlite3.connect(os.path.join(coin_dir(coin), f"{coin}.db"), timeout=30)
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS l2(ts_ms INTEGER, source TEXT, coin TEXT,
      b0p REAL, b0q REAL, b1p REAL, b1q REAL, b2p REAL, b2q REAL, b3p REAL, b3q REAL,
      b4p REAL, b4q REAL, a0p REAL, a0q REAL, a1p REAL, a1q REAL, a2p REAL, a2q REAL,
      a3p REAL, a3q REAL, a4p REAL, a4q REAL);
    CREATE TABLE IF NOT EXISTS trades(ts_ms INTEGER, source TEXT, coin TEXT, price REAL,
      qty REAL, side TEXT, trade_id TEXT);
    CREATE TABLE IF NOT EXISTS funding(ts_ms INTEGER, source TEXT, coin TEXT, rate REAL,
      next_ts INTEGER, index_price REAL);
    CREATE TABLE IF NOT EXISTS oi(ts_ms INTEGER, source TEXT, coin TEXT, oi REAL, price REAL);
    """)
    return conn


class Store:
    """每币一个 SQLite + 内存批量缓冲（5s 批 flush）。"""

    def __init__(self, coin: str):
        self.coin = coin
        self.conn = open_db(coin)
        self.buf = {"l2": [], "trades": [], "funding": [], "oi": []}

    def add_l2(self, ts_ms, source, book):
        r = [ts_ms, source, self.coin]
        bids, asks = list(book.bids)[:L2_TOP], list(book.asks)[:L2_TOP]
        for i in range(L2_TOP):
            r += list(bids[i]) if i < len(bids) else [None, None]
        for i in range(L2_TOP):
            r += list(asks[i]) if i < len(asks) else [None, None]
        self.buf["l2"].append(tuple(r))

    def add_trade(self, ts_ms, source, t):
        self.buf["trades"].append((ts_ms, source, self.coin, t.price, t.qty,
                                   t.side.value if hasattr(t.side, "value") else str(t.side),
                                   str(t.id)))

    def add_funding(self, ts_ms, source, f):
        self.buf["funding"].append((ts_ms, source, self.coin, f.rate,
                                    int(f.next_funding_ts) if f.next_funding_ts else None,
                                    f.mark_price))

    def add_oi(self, ts_ms, source, o):
        self.buf["oi"].append((ts_ms, source, self.coin, o.oi, o.price))

    def flush(self):
        for table, rows in self.buf.items():
            if rows:
                nq = ",".join("?" * len(rows[0]))
                self.conn.executemany(f"INSERT INTO {table} VALUES {nq}", rows)
                rows.clear()
        self.conn.commit()

    def count(self, table: str) -> int:
        return self.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


class G6Collector:
    def __init__(self):
        os.makedirs(BASE, exist_ok=True)
        self.stores = {c: Store(c) for c in COINS}
        self.reconnects = {s: 0 for s in SOURCES}
        self.last_ts = {c: {s: None for s in SOURCES} for c in COINS}
        self.source_status = {BIN: "ok", BYB: "ok", BINF: "ok", "gate": "N/A（本机 502 不可达、G-1 已证、不硬连）"}
        self.source_retried = {s: 0 for s in SOURCES}          # 每源已重试次数（≥2 → N/A 分级）
        self.start_ts = time.time()

    # ---------- 数据回调（async 包装，符合 cryptofeed 3.0 Callback 契约）----------
    def _mk_cb(self, source: str, kind: str):
        async def cb(data, receipt_ts, *, exchange, symbol):
            coin = symbol.base
            st = self.stores.get(coin)
            if st is None:
                return
            ts_ms = int(receipt_ts * 1000)
            self.last_ts[coin][source] = ts_ms
            if kind == "l2":
                st.add_l2(ts_ms, source, data)
            elif kind == "trades":
                st.add_trade(ts_ms, source, data)
            elif kind == "funding":
                st.add_funding(ts_ms, source, data)
            elif kind == "oi":
                st.add_oi(ts_ms, source, data)
        return cb

    def _err_cb(self, source: str):
        """源级错误回调：429/502/503/超时隔 3 分钟再试一次（冒烟 retries=2 留痕），仍失败记 N/A。"""
        async def on_error(err, receipt_ts, *, exchange, symbol=None, **kwargs):
            msg = str(err)
            self.reconnects[source] = self.reconnects.get(source, 0) + 1
            if any(k in msg for k in ("429", "502", "503", "timed out", "timeout", "Timeout")):
                self.source_retried[source] = self.source_retried.get(source, 0) + 1
                if self.source_retried[source] == 1:
                    log.warning("%s 遇 429/502/503/超时：隔 3 分钟（%ds）重试一次，留痕 %s",
                                source, RETRY_WAIT_SEC, msg[:120])
                elif self.source_retried[source] == 2:
                    log.error("%s 3 分钟后重试仍失败 → 记 N/A（分级：不可达、不静默吞）", source)
                    self.source_status[source] = "N/A"
            else:
                log.warning("%s 连接异常（非 429/502/503 类，走 cryptofeed 内建指数退避重连）：%s", source, msg[:120])
        return on_error


    # ---------- 心跳 / manifest / 水位 / 保留策略 ----------
    def heartbeat(self):
        hb = {}
        for c in COINS:
            hb[c] = {}
            for s in SOURCES:
                hb[c][s] = {
                    "last_ts": self.last_ts[c][s],
                    "uptime_s": round(time.time() - self.start_ts, 1),
                    "reconnects": self.reconnects[s],
                    "status": self.source_status[s],
                }
        tmp = os.path.join(BASE, "heartbeat.json.tmp")
        with open(tmp, "w") as f:
            json.dump(hb, f, indent=1)
        os.replace(tmp, os.path.join(BASE, "heartbeat.json"))

    def disk_gb(self) -> float:
        total = 0
        for root, _dirs, files in os.walk(BASE):
            for fn in files:
                try:
                    total += os.path.getsize(os.path.join(root, fn))
                except OSError:
                    pass
        return total / 1e9

    def retention(self):
        now_ms = time.time() * 1000
        for st in self.stores.values():
            st.conn.execute("DELETE FROM trades WHERE ts_ms < ?", (now_ms - KEEP_TRADES_DAYS * 86400e3,))
            st.conn.execute("DELETE FROM funding WHERE ts_ms < ?", (now_ms - KEEP_FUNDING_DAYS * 86400e3,))
            st.conn.commit()
        self.disk_check()

    def disk_check(self):
        gb = self.disk_gb()
        log.info("磁盘水位 %.3fGB / 告警线 %.0fGB", gb, GB_WATERMARK)
        if gb > GB_WATERMARK:
            log.warning("磁盘水位 %.3fGB 超 %dGB：仅告警、不自动删（如实上报）", gb, int(GB_WATERMARK))

    @staticmethod
    def _day_start_ms(day: str) -> int:
        return int(dt.datetime.strptime(day, "%Y%m%d").replace(tzinfo=dt.timezone.utc).timestamp() * 1000)

    def flush_daily_parquet(self, day: str):
        for st in self.stores.values():
            d = coin_dir(st.coin)
            for table, name in (("l2", "l2_daily"), ("trades", "trades_daily")):
                out = os.path.join(d, f"{name}_{day}.parquet")
                if not os.path.exists(out):
                    df = pd.read_sql_query(f"SELECT * FROM {table} WHERE ts_ms >= ?",
                                           st.conn, params=(self._day_start_ms(day),))
                    df.to_parquet(out, index=False)
                    log.info("转 parquet %s：%d 行", os.path.relpath(out, ROOT), len(df))

    @staticmethod
    def _sha16(path: str) -> str:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()[:16]

    def manifest(self, mode: str, seconds: int | None):
        rows = {c: {t: self.stores[c].count(t) for t in ("l2", "trades", "funding", "oi")} for c in COINS}
        files = []
        for c in COINS:
            for fn in sorted(os.listdir(coin_dir(c))):
                p = os.path.join(coin_dir(c), fn)
                if os.path.isfile(p):
                    files.append({"path": f"g6_cryptofeed_live/{c}/{fn}", "sha16": self._sha16(p)})
        m = {
            "collector": "backtest/g6_cryptofeed_collector.py",
            "collector_started_utc": dt.datetime.fromtimestamp(self.start_ts, dt.timezone.utc).isoformat(),
            "mode": mode, "smoke_seconds": seconds,
            "coins": COINS,
            "sources": {BIN: "binance spot WS（L2 top5 + trades）",
                        BYB: "bybit spot WS（L2 top5 + trades + funding 8h）",
                        BINF: "binance futures WS（OI，源支持则采）",
                        "gate": "N/A（Gate.io 本机 502 不可达，G-1 已证，三源中如实标 N/A、不硬连）"},
            "source_status": self.source_status,
            "reconnects_total": self.reconnects,
            "proxy": PROXY if PROXY else "direct（未设 G6COLLECT_PROXY）",
            "tables": {
                "l2": "ts_ms,source,coin,b0p..b4q,a0p..a4q（top5，L2 按源支持取可用档数）",
                "trades": "ts_ms,source,coin,price,qty,side,trade_id（逐笔）",
                "funding": "ts_ms,source,coin,rate,next_ts,index_price（8h 结算推送）",
                "oi": "ts_ms,source,coin,oi,price（仅 binance-futures 源；Bybit WS 无 OI 推送 → 该源 N/A）",
            },
            "retention": "trades 7d 滚动 / funding 30d / l2+oi 永存（SQLite 内）",
            "parquet_rule": "每日 00:00 UTC 将前一日 l2/trades 转 {COIN}/l2_daily|trades_daily_YYYYMMDD.parquet",
            "disk_gb": round(self.disk_gb(), 4), "watermark_gb": GB_WATERMARK,
            "row_counts": rows,
            "file_sha16": files,
            "compliance": "只读公共 WS 端点、无鉴权、无 key 写入；不挂单不下单；不接付费源；"
                          "429/502/503/超时隔 3 分钟重试一次留痕、仍失败记 N/A；磁盘超水位仅告警不自动删；"
                          "未改 src/、未改 config/、未动 .bat/.env/.gitignore、未提交 Git",
        }
        with open(os.path.join(BASE, "_manifest.json"), "w") as f:
            json.dump(m, f, indent=1, ensure_ascii=False)
        log.info("manifest 已写 data/cache/g6_cryptofeed_live/_manifest.json")

    # ---------- 构建 FeedHandler（3 个 feed：binance spot / bybit spot / binance futures OI）----------
    def build_handler(self, retry_seconds: int = 0) -> FeedHandler:
        handler = FeedHandler()
        proxy_kw = dict(http_proxy=PROXY) if PROXY else {}
        # source -> (exchange cls, 订阅 channels, 回调 key 映射)
        # 渠道名按源支持取可用：binance spot 无 OI/funding 推送渠道 → OI 走 binance-futures；
        # bybit spot 支持 l2_book/trades/funding（OI 渠道恒不订阅 → N/A）
        for source, exch, channels, kind_map in (
            (BIN, Binance, ["l2_book", "trades"], {"l2_book": "l2", "trades": "trades"}),
            (BYB, Bybit, ["l2_book", "trades", "funding"], {"l2_book": "l2", "trades": "trades", "funding": "funding"}),
            (BINF, BinanceFutures, ["open_interest"], {"open_interest": "oi"}),
        ):
            cbs = {ch: Callback(self._mk_cb(source, kind_map[ch])) for ch in channels}
            if retry_seconds > 0:
                # 冒烟：429/502/503 类失败重试 1 次（3min 间隔留痕），仍失败 → feed 退出、标 N/A
                cbs["error"] = Callback(self._err_cb(source))
                retries = 2
            else:
                # 长跑：retries=-1 → 内建指数退避（base=1s、封顶 120s）无限重连，断线自动恢复
                retries = -1
            kwargs = dict(callbacks=cbs, retries=retries,
                          symbols=[Symbol(coin, "USDT", type=PERPETUAL if source == BINF else "spot") for coin in COINS],
                          channels=channels, **proxy_kw)
            if source == BIN:
                feed = exch(depth_interval="100ms", **kwargs)
            elif source == BYB:
                feed = exch(**kwargs)
            else:
                feed = exch(open_interest_interval=1.0, **kwargs)
            handler.add_feed(feed)
        return handler

    # ---------- 主循环 ----------
    def run(self, once: bool, seconds: int):
        stop = {"flag": False}

        import signal
        def stopper(*_):
            stop["flag"] = True
        try:
            signal.signal(signal.SIGINT, stopper)
            signal.signal(signal.SIGTERM, stopper)
        except ValueError:
            pass

        async def drive_one_attempt(handler):
            t0 = time.time()
            handler_task = asyncio.create_task(handler.run_async(install_signal_handlers=False), name="g6_handler")
            sidecar = asyncio.create_task(self._sidecar(stop, once, seconds), name="g6_sidecar")
            stop_task = asyncio.create_task(asyncio.sleep(seconds) if once else asyncio.Event().wait())
            while not handler_task.done():
                if stop_task.done() or stop["flag"]:
                    stop["flag"] = True
                    break
                await asyncio.sleep(0.2)
            if stop["flag"] or once:
                handler.request_stop()
            try:
                await handler_task
            except BaseException as e:
                log.warning("handler 退出（可能全部 feed 失败/连接不可达）：%s", e)
            sidecar.cancel()
            try:
                await sidecar
            except asyncio.CancelledError:
                pass
            # 启动期不可达的源（连接/符号加载即失败）：冒烟 3 分钟再试一次、仍失败记 N/A；长跑由 drive() 循环承担
            unreachable = [s for s in SOURCES
                           if self.source_status.get(s, "ok") == "ok"
                           and all(v is None for v in (self.last_ts[c][s] for c in COINS))]
            if unreachable and once:
                log.info("不可达源 %s：隔 3 分钟（%ds）自动重试一次（留痕）", unreachable, RETRY_WAIT_SEC)
                await asyncio.sleep(RETRY_WAIT_SEC)
                handler2 = self.build_handler(retry_seconds=RETRY_WAIT_SEC)
                handler_task2 = asyncio.create_task(handler2.run_async(install_signal_handlers=False), name="g6_handler_retry")
                deadline = time.time() + min(RETRY_WAIT_SEC, max(30, seconds // 3))
                while not handler_task2.done() and time.time() < deadline and not stop["flag"]:
                    await asyncio.sleep(1)
                handler2.request_stop()
                try:
                    await handler_task2
                except BaseException as e:
                    log.warning("重试 handler 退出：%s", e)
            for s in unreachable:
                if all(v is None for v in (self.last_ts[c][s] for c in COINS)):
                    log.error("源 %s 3 分钟后重试仍无数据 → 记 N/A（分级：不可达、不静默吞）", s)
                    self.source_status[s] = "N/A"
            return t0

        async def drive():
            t0 = 0.0
            # 长跑：全部源不可达且 3 分钟重试仍失败 → 不退出、持续重试（7×24 韧性）；冒烟：一次重试后即 N/A 收尾
            while True:
                handler = self.build_handler(retry_seconds=RETRY_WAIT_SEC if once else 0)
                t0 = await drive_one_attempt(handler)
                if once:
                    break
                if stop["flag"]:
                    break
                if any(s != "N/A" for s in (self.source_status[s] for s in SOURCES)):
                    # 至少一源仍在工作 → 已进正常长跑，无需再重建
                    break
                log.info("所有源 N/A：3 分钟（%ds）后自动再试（长跑韧性）", RETRY_WAIT_SEC)
                await asyncio.sleep(RETRY_WAIT_SEC)

        try:
            asyncio.run(drive())
        except BaseException as e:
            log.warning("run 退出：%s", e)
        finally:
            for st in self.stores.values():
                st.flush()
            if once:
                self.flush_daily_parquet(dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d"))
            self.heartbeat()
            self.manifest("once" if once else "7x24", seconds if once else None)
            log.info("采集器已停（%s），产物齐全：heartbeat + manifest + SQLite/Parquet", "冒烟" if once else "手动/信号")

    async def _sidecar(self, stop, once: bool, seconds: int):
        """副驾：每 30s 心跳、5s 批量 flush、每小时保留策略、跨日转 parquet。"""
        last_hb = last_flush = last_ret = 0.0
        last_parquet = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d")
        t0 = time.time()
        while not stop["flag"]:
            now = time.time()
            if now - last_hb >= HEARTBEAT_SEC:
                self.heartbeat(); last_hb = now
            if now - last_flush >= 5:
                for st in self.stores.values():
                    st.flush()
                last_flush = now
            if now - last_ret >= 3600:
                self.retention(); last_ret = now
            today = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d")
            if today != last_parquet:
                self.flush_daily_parquet(last_parquet)
                last_parquet = today
            if once and now - t0 >= seconds:
                stop["flag"] = True
                break
            await asyncio.sleep(0.5)


def main():
    ap = argparse.ArgumentParser(description="G-6-③ cryptofeed 未来实盘采集器")
    ap.add_argument("--once", action="store_true", help="冒烟模式：跑满 --seconds 后自停")
    ap.add_argument("--seconds", type=int, default=300, help="冒烟时长秒数（默认 300）")
    a = ap.parse_args()
    G6Collector().run(a.once, a.seconds)


if __name__ == "__main__":
    main()
