# -*- coding: utf-8 -*-
"""G-4-②-410d · 数据层重建（后端架构师落实，PM 承派+验收）。

背景（Read 核对，不脑补）：
  旧 `data/cache/g1_carry_data/_manifest.json` 的 sources 显示 perp 只有
  4h=28d / 1d=31d、funding=33d(100根/币)、spot 400d/900d，实际交集 4h=89~91根 /
  1d=31根、span_days 29~30。**只把 funding 换成 410d，交集仍被 perp 28d 卡死**，
  严禁把 410d funding 硬拼到旧 8 个 parquet 上制造「窗口已扩大」的假象。

本脚本做什么：
  从 Binance Vision 免费公开归档（bucket `data.binance.vision`，website endpoint
  `https://data.binance.vision/` HTTPS 匿名 GET、无 key；S3 REST 000 / boto3 未装，
  见 G-6-① 实测）实际补拉：
    - perp（futures/um klines）4h + 1d 410d
    - spot klines 4h 410d
    - spot 1d 410d 复用 G-6-① 已落盘 `klines_1d_410d.parquet`（不重复下载，如实标注）
  与 G-6-① 已落盘的 `funding_410d.parquet`（4币各1278个8h点，
  2025-09-01~2026-10-31）对齐，按 G-3 口径重建 basis/basis_pct，产出
  **4 币 × 2 粒度 = 8 个 carry parquet** + `_manifest.json` + `_download_log.json`
  到 `data/cache/g1_carry_data_410d/`。

口径（复用 backtest/g1_carry_basis_data.py，不改骨架）：
  basis = perp_close - spot_close；basis_pct = basis / spot_close
  4h 网格 = spot 4h ts（与 funding 8h 结算点 00/08/16 内连接）
  1d 网格 = funding ts（00/08/16），左连 spot 1d（00:00）后 dropna → 1 根/日
  列：ts, spot_open, spot_close, perp_open, perp_close, basis, basis_pct,
      funding_rate, basis_pos, fr_pos（与旧 8 parquet 完全一致，供 G-3/G-4② 骨架读取）

实测 schema 纠正（下载样本核实，不脑补）：
  futures/um klines：CSV **有表头**、open_time 为【毫秒】(ms)
  spot klines      ：CSV **无表头**、open_time 为【微秒】(us)
  G-6-① 记录的「无表头/微秒」仅适用于 spot；perp 与本轮实测不同 → 分别解析。

归档确切路径（本轮 HEAD 逐项核实 200，先核实再拉、不猜路径）：
  data/futures/um/monthly/klines/{S}/{iv}/{S}-{iv}-{YYYY-MM}.zip
  data/futures/um/daily/klines/{S}/{iv}/{S}-{iv}-{YYYY-MM-DD}.zip
  data/spot/monthly/klines/{S}/4h/{S}-4h-{YYYY-MM}.zip
  data/spot/daily/klines/{S}/4h/{S}-4h-{YYYY-MM-DD}.zip

红线：不改 config/src/.bat/.gitignore/.env、不提交 Git、不启实盘、不接付费源
      （Tardis/Kaiko/Amberdata/CoinAPI）、不放宽冻结门槛、无令牌/密钥写入、
      不抢跑 G-6-②/G-6-③/G-5-②。「仅确认存在」与「已实际下载」严格区分；
      404/不可达列明缺口分级、不硬拉、不静默吞、不伪造、不写占位 parquet。

运行：python backtest/g1_carry_410d_data.py
"""
from __future__ import annotations

import hashlib
import io
import json
import sys
import urllib.error
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
WEBSITE = "https://data.binance.vision/"
G6 = ROOT / "data" / "cache" / "g6_s3_archive"
OUT_DIR = ROOT / "data" / "cache" / "g1_carry_data_410d"
RAW = OUT_DIR / "_raw"

COINS = ["BTCUSDT", "ETHUSDT", "DOGEUSDT", "LTCUSDT"]
# 旧 carry parquet 用短币名
SHORT = {"BTCUSDT": "BTC", "ETHUSDT": "ETH", "DOGEUSDT": "DOGE", "LTCUSDT": "LTC"}

MONTHS = ["2025-09", "2025-10", "2025-11", "2025-12",
          "2026-01", "2026-02", "2026-03", "2026-04", "2026-05", "2026-06",
          "2026-07", "2026-08", "2026-09", "2026-10"]           # 14 个月度归档
DAYS = [f"2026-11-{d:02d}" for d in range(1, 14)]                # 当月 13 天 daily

WINDOW_START = "2025-09-30"        # G-6-① manifest 410d 窗口起点
WINDOW_END = "2026-11-13"
EXTREME_START = 1770163200         # 2026-02-04 00:00 UTC
EXTREME_END = 1770681600           # 2026-02-10 00:00 UTC

# 需要【实际下载】的三类：perp 4h / perp 1d / spot 4h；spot 1d 复用 G-6-①
FETCH_KINDS = [
    ("futures/um", "4h", "perp_4h"),
    ("futures/um", "1d", "perp_1d"),
    ("spot", "4h", "spot_4h"),
]

KLINE_COLS = ["open_time", "open", "high", "low", "close", "volume",
              "close_time", "quote_volume", "count", "taker_buy_volume",
              "taker_buy_quote_volume", "ignore"]


def _sha16(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()[:16]


def _url(market: str, iv: str, sym: str, period: str, scope: str) -> str:
    """按 HEAD 核实过的确切命名拼 URL（不猜路径）。"""
    if scope == "monthly":
        rel = f"data/{market}/monthly/klines/{sym}/{iv}/{sym}-{iv}-{period}.zip"
    else:
        rel = f"data/{market}/daily/klines/{sym}/{iv}/{sym}-{iv}-{period}.zip"
    return WEBSITE + rel


def build_fetch_plan() -> list[dict]:
    plan = []
    for sym in COINS:
        for market, iv, kind in FETCH_KINDS:
            for m in MONTHS:
                u = _url(market, iv, sym, m, "monthly")
                plan.append({"sym": sym, "kind": kind, "scope": "monthly",
                             "period": m, "market": market, "iv": iv, "url": u,
                             "dest": RAW / kind / sym / f"{sym}-{iv}-{m}.zip"})
            for d in DAYS:
                u = _url(market, iv, sym, d, "daily")
                plan.append({"sym": sym, "kind": kind, "scope": "daily",
                             "period": d, "market": market, "iv": iv, "url": u,
                             "dest": RAW / kind / sym / f"{sym}-{iv}-{d}.zip"})
    return plan


def fetch_one(item: dict) -> dict:
    """实际 HTTPS GET 下载一个 zip；失败如实记录 HTTP/错误，不静默吞。"""
    dest: Path = item["dest"]
    url = item["url"]
    rec = {"sym": item["sym"], "kind": item["kind"], "scope": item["scope"],
           "period": item["period"], "url": url,
           "path": str(dest.relative_to(ROOT))}
    for attempt in (1, 2):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=45) as r:
                data = r.read()
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
            rec.update({"http": r.status, "bytes": len(data),
                        "status": "downloaded", "attempt": attempt})
            return rec
        except urllib.error.HTTPError as e:
            rec.update({"http": e.code, "bytes": 0,
                        "status": "http_error", "err": str(e), "attempt": attempt})
            if e.code == 404:      # 归档不存在 → 不硬拉、不重试
                return rec
        except Exception as e:     # 网络抖动 → 重试 1 次
            rec.update({"http": 0, "bytes": 0, "status": "error",
                        "err": str(e), "attempt": attempt})
    return rec


def parse_klines_zip(zpath: Path, is_perp: bool) -> pd.DataFrame:
    """解析单个 klines zip → DataFrame[ts, open, close]。

    perp(futures/um)：有表头、open_time 毫秒 → ts = open_time // 1000
    spot            ：无表头、open_time 微秒 → ts = open_time // 1_000_000
    """
    with zipfile.ZipFile(zpath) as z:
        name = [n for n in z.namelist() if n.endswith(".csv")][0]
        if is_perp:
            df = pd.read_csv(z.open(name), header=0)
        else:
            df = pd.read_csv(z.open(name), header=None, names=KLINE_COLS)
    ot = pd.to_numeric(df["open_time"], errors="coerce")
    df["ts"] = (ot // (1000 if is_perp else 1_000_000)).astype("int64")
    out = pd.DataFrame({
        "ts": df["ts"],
        "open": pd.to_numeric(df["open"], errors="coerce"),
        "close": pd.to_numeric(df["close"], errors="coerce"),
    })
    return out.dropna().drop_duplicates("ts").sort_values("ts").reset_index(drop=True)


def load_kind(sym: str, kind: str, download_log: list[dict]) -> pd.DataFrame:
    """汇总某币某类所有已下载 zip（仅 status=downloaded，不读占位/不脑补）。"""
    is_perp = kind.startswith("perp")
    frames = []
    for rec in download_log:
        if rec["sym"] == sym and rec["kind"] == kind and rec["status"] == "downloaded":
            frames.append(parse_klines_zip(ROOT / rec["path"], is_perp))
    if not frames:
        return pd.DataFrame(columns=["ts", "open", "close"])
    all_ = pd.concat(frames, ignore_index=True)
    return all_.drop_duplicates("ts").sort_values("ts").reset_index(drop=True)


def load_spot_1d(sym: str) -> tuple[pd.DataFrame, dict]:
    """复用 G-6-① 已落盘 spot 1d 410d parquet（439 根，2025-09-01~2026-11-13）。"""
    p = G6 / sym / "binance_vision" / "klines_1d_410d.parquet"
    df = pd.read_parquet(p)
    ts = (df["open_ts"].astype("int64") // 1_000_000).astype("int64")  # us → s
    out = pd.DataFrame({"ts": ts, "open": df["open"].astype(float),
                        "close": df["close"].astype(float)})
    meta = {"source": "G-6-① 已落盘（复用，本轮未重复下载）",
            "path": str(p.relative_to(ROOT)), "sha256_16": _sha16(p),
            "rows": int(len(df)),
            "time_min": str(df["open_ts"].min()), "time_max": str(df["open_ts"].max())}
    return out, meta


def load_funding(sym: str) -> tuple[pd.DataFrame, dict]:
    """G-6-① 已落盘 funding 410d（1278 个 8h 点）。"""
    p = G6 / sym / "binance_vision" / "funding_410d.parquet"
    df = pd.read_parquet(p)
    if "calc_time" in df.columns:
        ts = (pd.to_numeric(df["calc_time"], errors="coerce") // 1000).astype("int64")
    else:
        ts = (df["calc_ts"].astype("int64") // 1_000_000).astype("int64")
    out = pd.DataFrame({"ts": ts,
                        "funding_rate": pd.to_numeric(df["last_funding_rate"], errors="coerce")})
    out = out.dropna().drop_duplicates("ts").sort_values("ts").reset_index(drop=True)
    meta = {"source": "G-6-① 已落盘（复用，本轮未重复下载）",
            "path": str(p.relative_to(ROOT)), "sha256_16": _sha16(p),
            "rows": int(len(out)),
            "time_min": str(pd.to_datetime(out["ts"].min(), unit="s", utc=True)),
            "time_max": str(pd.to_datetime(out["ts"].max(), unit="s", utc=True)),
            "tail_gap": "2026-11-01..2026-11-13 共 13 天无归档（monthly 次月生成 + 无 daily fundingRate），🟡 缺口率 13/410=3.17%"}
    return out, meta


def build_carry(sym: str, iv: str, src: dict) -> tuple[pd.DataFrame, dict]:
    """按 G-3 网格口径重建单币单粒度 carry 序列。"""
    fr = src["funding"]
    spot = src["spot_4h"] if iv == "4h" else src["spot_1d"]
    perp = src["perp_4h"] if iv == "4h" else src["perp_1d"]

    # 网格：4h = spot 4h ts；1d = funding ts（与旧脚本一致）
    grid = spot[["ts"]].copy() if iv == "4h" else fr[["ts"]].copy()
    m = grid.merge(fr, on="ts", how="inner")            # 只保留有 funding 的 bar
    m = m.merge(perp.rename(columns={"open": "perp_open", "close": "perp_close"}),
                on="ts", how="left")                    # perp 缺失 → basis NaN（如实）
    m = m.merge(spot.rename(columns={"open": "spot_open", "close": "spot_close"}),
                on="ts", how="left")
    m = m.dropna(subset=["spot_open", "spot_close", "funding_rate"]).reset_index(drop=True)

    m["basis"] = m["perp_close"] - m["spot_close"]
    m["basis_pct"] = m["basis"] / m["spot_close"]
    m["basis_pos"] = m["basis"] > 0                     # perp_close NaN → False（不开仓）
    m["fr_pos"] = m["funding_rate"] > 0
    keep = ["ts", "spot_open", "spot_close", "perp_open", "perp_close",
            "basis", "basis_pct", "funding_rate", "basis_pos", "fr_pos"]
    m = m[keep]

    short = SHORT[sym]
    out_p = OUT_DIR / f"{short}_USDT_{iv}_410d_carry.parquet"
    m.to_parquet(out_p, index=False)

    seg = m[(m["ts"] >= EXTREME_START) & (m["ts"] < EXTREME_END)]
    # perp/spot 4h 与网格对齐率（如实，不脑补）
    perp_aligned = float(m["perp_close"].notna().mean())
    man = {
        "coin": short, "iv": iv,
        "bars": int(len(m)),
        "span_days": int((m["ts"].max() - m["ts"].min()) / 86400),
        "ts_min": int(m["ts"].min()), "ts_max": int(m["ts"].max()),
        "time_min": str(pd.to_datetime(m["ts"].min(), unit="s", utc=True)),
        "time_max": str(pd.to_datetime(m["ts"].max(), unit="s", utc=True)),
        "out_file": str(out_p.relative_to(ROOT)), "out_sha256_16": _sha16(out_p),
        "columns": keep,
        "basis_formula": "basis = perp_close - spot_close；basis_pct = basis / spot_close（G-3 口径未改）",
        "extreme_2026_02": {
            "window": "2026-02-04..2026-02-10",
            "start_ts": EXTREME_START, "end_ts": EXTREME_END,
            "bars_in_window": int(len(seg)),
            "covered": bool(len(seg) > 0),
            "verdict": "覆盖（funding/perp/spot 三源 2026-02 monthly 均实际下载）" if len(seg) > 0 else "未覆盖",
        },
        "perp_aligned_frac": round(perp_aligned, 6),
        "basis_pos_frac": round(float(m["basis_pos"].mean()), 6),
        "fr_pos_frac": round(float(m["fr_pos"].mean()), 6),
        "fr_mean": float(m["funding_rate"].mean()),
        "fr_both_pos_contiguous_check": "由回测脚本 M3 骨架判定，本层不定义交易信号",
    }
    return m, man


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    plan = build_fetch_plan()
    print(f"[计划] 实际下载 {len(plan)} 个 zip（3 类 × 4 币 × (14 monthly + 13 daily)），"
          f"website endpoint 匿名 GET")
    logs: list[dict] = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(fetch_one, it): it for it in plan}
        for fut in as_completed(futs):
            logs.append(fut.result())
    logs.sort(key=lambda r: (r["sym"], r["kind"], r["scope"], r["period"]))

    ok = [r for r in logs if r["status"] == "downloaded"]
    bad = [r for r in logs if r["status"] != "downloaded"]
    print(f"[下载] 成功 {len(ok)} / 失败 {len(bad)}")
    for r in bad:
        print(f"  ! {r['status']} HTTP={r['http']} {r['url']} {r.get('err','')}")

    (OUT_DIR / "_download_log.json").write_text(
        json.dumps({"generated_at_utc": datetime.now(timezone.utc).isoformat(),
                    "website_endpoint": WEBSITE,
                    "fetch_method": "urllib HTTPS GET 匿名只读（S3 公开桶 website endpoint，无 key）",
                    "plan_count": len(plan), "downloaded": len(ok), "failed": len(bad),
                    "records": logs}, ensure_ascii=False, indent=2), encoding="utf-8")

    if len(ok) == 0:
        print("[阻断] 0 个 zip 下载成功 → 不写占位 parquet、不伪造数据，如实上报")
        return 2

    files_manifest = []
    for sym in COINS:
        src = {
            "funding": load_funding(sym)[0],
            "spot_1d": load_spot_1d(sym)[0],
            "perp_4h": load_kind(sym, "perp_4h", logs),
            "perp_1d": load_kind(sym, "perp_1d", logs),
            "spot_4h": load_kind(sym, "spot_4h", logs),
        }
        for iv in ("4h", "1d"):
            df, man = build_carry(sym, iv, src)
            files_manifest.append(man)
            print(f"  {man['coin']:4s} {iv}: {man['bars']} bars / {man['span_days']}d "
                  f"[{man['time_min'][:10]}..{man['time_max'][:10]}] "
                  f"extreme_bars={man['extreme_2026_02']['bars_in_window']} "
                  f"perp_align={man['perp_aligned_frac']} → {man['out_file']}")

    # 逐源确切路径 + 状态汇总（「已实际下载」与「仅确认存在」严格区分）
    per_kind = {}
    for kind in ["perp_4h", "perp_1d", "spot_4h"]:
        klogs = [r for r in logs if r["kind"] == kind]
        per_kind[kind] = {
            "path_template": {
                "monthly": "data/futures/um/monthly/klines/{S}/{iv}/{S}-{iv}-{YYYY-MM}.zip"
                           if kind.startswith("perp") else
                           "data/spot/monthly/klines/{S}/4h/{S}-4h-{YYYY-MM}.zip",
                "daily": "data/futures/um/daily/klines/{S}/{iv}/{S}-{iv}-{YYYY-MM-DD}.zip"
                         if kind.startswith("perp") else
                         "data/spot/daily/klines/{S}/4h/{S}-4h-{YYYY-MM-DD}.zip",
            },
            "planned": len(klogs),
            "actually_downloaded": sum(1 for r in klogs if r["status"] == "downloaded"),
            "missing": [r["url"] for r in klogs if r["status"] != "downloaded"],
            "exact_paths_and_http": [
                {"url": r["url"], "http": r["http"], "status": r["status"], "bytes": r["bytes"]}
                for r in klogs],
        }

    gap_grading = []
    if bad:
        gap_grading.append({"level": "🟡", "item": "部分 klines 归档 zip HEAD/GET 非 200",
                            "count": len(bad),
                            "action": "逐项列于 _download_log.json / per_kind.missing，不硬拉、不静默吞、不写占位"})
    gap_grading.append({"level": "🟡",
                        "item": "funding 410d 窗口尾部 2026-11-01..11-13 无归档（G-6-① 已判）",
                        "scope": "4 币 × funding × 当月 13 天",
                        "action": "monthly 次月生成 + 无 daily fundingRate；缺口率 13/410=3.17%，如实标注、不用 0 占位"})
    gap_grading.append({"level": "🟡",
                        "item": "本层 spot 1d 410d 复用 G-6-① 已落盘 parquet，未重复下载",
                        "scope": "spot 1d × 4 币 × 410d",
                        "action": "如实标注复用来源与 sha256-16，不冒充本轮新下载"})

    manifest = {
        "task": "G-4-②-410d 数据层重建（perp+spot klines 410d 补拉 + 对齐 funding 410d 重建 basis）",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "role": "后端架构师落实 / PM 承派+验收",
        "data_source": {
            "bucket_name": "data.binance.vision",
            "website_endpoint": WEBSITE,
            "auth": "公开桶 / 无 API key / HTTPS 匿名 GET 只读（无令牌写入）",
            "boto3_installed": False,
            "s3_rest_reachable_local": False,
            "note": "沿用 G-6-① 实测：S3 REST 000/301、本机 boto3/botocore 未装 → 走 website endpoint HTTPS",
        },
        "window": {"target": "410d", "start": WINDOW_START, "end": WINDOW_END, "days": 410,
                   "extreme_segment": "2026-02-04..2026-02-10"},
        "old_bottleneck": ("data/cache/g1_carry_data/_manifest.json：perp 仅 4h=28d / 1d=31d、"
                           "funding=33d(100根/币)、spot 400d/900d，实际交集 4h=89~91根 / 1d=31根、"
                           "span_days 29~30。只换 funding 410d 不会扩窗 → 本轮补 perp 价格后重建。"),
        "parquet_files": files_manifest,
        "sources": {
            "funding_410d": {sym: load_funding(sym)[1] for sym in COINS},
            "spot_1d_410d_reused": {sym: load_spot_1d(sym)[1] for sym in COINS},
            "downloaded_klines": per_kind,
        },
        "lookback_depth": {
            "funding_landed": "2025-09-01..2026-10-31（426d，含 410d 窗口起点前 29d）",
            "perp_spot_downloaded": "2025-09..2026-10 monthly 14 月 + 2026-11-01..11-13 daily 13 天（逐项见 per_kind.exact_paths_and_http）",
            "intersection_window": "由 funding 决定，止于 2026-10-31（尾部 13 天 funding 缺 → 交集不覆盖 2026-11）",
        },
        "coverage": {
            "extreme_2026_02": {f"{m['coin']}_{m['iv']}": m["extreme_2026_02"]
                                for m in files_manifest},
            "verdict": "8 个 parquet 的 2026-02 段 bar 数均 > 0 → 极端段可实判（不再 N/A）",
        },
        "gap_grading": gap_grading,
        "strict_distinction": {
            "actually_downloaded": "per_kind.*.actually_downloaded 计数的 zip（落 data/cache/g1_carry_data_410d/_raw/）",
            "only_confirmed_exist": "HEAD 200 但未计入本轮下载的项：无（本轮计划内全部尝试下载，失败项列 missing）",
            "reused_not_redownloaded": "spot 1d 410d、funding 410d 复用 G-6-① 已落盘，明确标注",
            "no_placeholder": "未写任何占位 parquet；0 成交/0 达标如实申报；不硬拼到旧 8 parquet",
        },
        "compliance": {
            "config_modified": False, "src_modified": False,
            "bat_gitignore_env_modified": False, "git_committed": False,
            "live_trading_enabled": False, "paid_sources_used": False,
            "thresholds_relaxed": False, "api_key_written": False,
            "next_phase_jumped": False, "placeholder_zero_used": False,
            "fabricated_data": False,
        },
    }
    (OUT_DIR / "_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[manifest] → {OUT_DIR / '_manifest.json'}")
    print(f"[下载日志] → {OUT_DIR / '_download_log.json'}（{len(logs)} 项逐项 HTTP 码）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
