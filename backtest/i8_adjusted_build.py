# -*- coding: utf-8 -*-
"""I-8 阶段一 · 全量后复权数据构建（数据工程师纪律，高效因子方案）

原理（已由 i8_local_adjust_equivalence.json 验证 PASS，误差<1%）：
  后复权价 = 本机未复权价 × baostock backAdjustFactor（按除权日分段）
  本机全量 5687 只未复权 parquet 已存在（tdx_a_share_full，adjusted=false），
  只需每只拉一次 query_adjust_factor（27-42 条记录）即可构造全量后复权数据，
  避免逐只拉全历史 adjustflag=1（后者单只 7-13s，全量需十余小时）。

退市股（342 只）本机无 .day parquet → 单独用 query_history_k_data_plus(adjustflag=1) 拉取
（已由 i8_adjusted_land.py 落部分，本脚本续传补齐）。

产出：data/cache/tdx_a_share_adjusted/{symbol}_1d.parquet（后复权，字段与 full 一致）+ _manifest.json
红线：免费免key源、串行+保守间隔、撞429/503/502隔180s重试一次留痕、本机只读、不改config。
"""
from __future__ import annotations
import sys, time, json, argparse, hashlib, bisect
from pathlib import Path
from datetime import datetime, timezone
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backtest"))
import baostock as bs
import pandas as pd
import numpy as np

OUT = ROOT / "data" / "cache" / "tdx_a_share_adjusted"
FULL = ROOT / "data" / "cache" / "tdx_a_share_full"
FULL_MANIFEST = FULL / "_manifest.json"
PROGRESS = OUT / "_build_progress.json"
MANIFEST = OUT / "_manifest.json"
INTERVAL = 0.12
RETRY_SLEEP = 180
FIELDS = ["date", "open", "high", "low", "close", "preclose", "volume", "amount", "turn", "tradestatus"]
RAW_FIELDS = ["date", "open", "high", "low", "close", "amount", "volume", "tradestatus"]


def sha16(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()[:16]


def load_progress():
    if PROGRESS.exists():
        return json.load(open(PROGRESS, encoding="utf-8"))
    return {"done": {}, "failed": {}, "retry_log": [], "requests": 0,
            "method": "本机未复权×backAdjustFactor（等价性见 i8_local_adjust_equivalence.json）",
            "started_at": datetime.now(timezone.utc).isoformat()}


def save_progress(p):
    t = PROGRESS.with_suffix(".tmp")
    t.write_text(json.dumps(p, ensure_ascii=False, indent=1), encoding="utf-8")
    t.replace(PROGRESS)


def fetch_factors(symbol: str):
    bsc = f"{symbol[:2]}.{symbol[2:]}"
    rs = bs.query_adjust_factor(code=bsc, start_date="1990-01-01", end_date="2026-12-31")
    rows = []
    while rs.error_code == "0" and rs.next():
        r = rs.get_row_data()
        rows.append((r[1].replace("-", ""), float(r[3])))  # dividOperateDate, backAdjustFactor
    return rs.error_code, rs.error_msg, sorted(rows)


def login_ensure():
    """确保已登录（仅会话过期时重登，由调用方在检测到 10001001 时显式触发；此处保留空操作避免逐只探活浪费请求）"""
    return


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)

    m = json.load(open(FULL_MANIFEST, encoding="utf-8"))
    symbols = [f["symbol"] for f in m["files"]]  # 5687 只本机有未复权 parquet
    prog = load_progress()
    lg = bs.login()
    if lg.error_code != "0":
        print(f"FATAL login {lg.error_code} {lg.error_msg}", flush=True)
        return 2
    prog["requests"] += 1

    todo = [s for s in symbols if s not in prog["done"] or not (OUT / f"{s}_1d.parquet").exists()]
    if args.limit:
        todo = todo[:args.limit]
    print(f"[plan] pool={len(symbols)} todo={len(todo)} limit={args.limit or 'ALL'}", flush=True)

    t0 = time.perf_counter()
    for idx, s in enumerate(todo, 1):
        retry = 0
        while True:
            login_ensure()
            ec, em, fac = fetch_factors(s)
            prog["requests"] += 1
            if ("10001001" in str(ec) or "未登录" in str(em)) and retry < 3:
                bs.login(); prog["requests"] += 1; retry += 1; continue
            if ec in ("429", "503", "502") and retry < 1:
                retry += 1
                prog["retry_log"].append({"symbol": s, "ts": datetime.now(timezone.utc).isoformat(),
                                           "reason": f"{ec}:{em[:80]}", "action": f"sleep {RETRY_SLEEP}s retry#1"})
                save_progress(prog); time.sleep(RETRY_SLEEP); continue
            if ec != "0":
                prog["failed"][s] = {"error": f"{ec}:{em[:100]}", "ts": datetime.now(timezone.utc).isoformat()}
                break
            # 读本机未复权 parquet
            rawp = FULL / f"{s}_1d.parquet"
            try:
                df = pd.read_parquet(rawp)
            except Exception as e:
                prog["failed"][s] = {"error": f"read raw parquet: {str(e)[:80]}", "ts": datetime.now(timezone.utc).isoformat()}
                break
            # 复权：close/open/high/low/preclose × 分段 backAdjustFactor（numpy 向量化）
            dates_fac = [x[0] for x in fac]
            vals_fac = np.asarray([x[1] for x in fac], dtype=np.float64)
            darr = df["date"].astype(str).str.replace("-", "", regex=False).to_numpy()
            # 每根 bar 取不晚于该日的最后一个因子（searchsorted 向量化，替代逐行 bisect）
            idx_fac = np.searchsorted(np.asarray(dates_fac), darr, side="right") - 1
            factors = np.where(idx_fac >= 0, vals_fac[np.maximum(idx_fac, 0)], 1.0)
            def apply_factor(col):
                v = pd.to_numeric(df[col], errors="coerce").to_numpy(dtype=np.float64)
                return v * factors
            for col in ["open", "high", "low", "close", "preclose"]:
                if col in df.columns:
                    df[col] = apply_factor(col)
            # 补 tradestatus/turn（若 raw 无则填默认）
            if "tradestatus" not in df.columns:
                df["tradestatus"] = 1
            df["date"] = darr
            cols = [c for c in FIELDS if c in df.columns]
            outp = OUT / f"{s}_1d.parquet"
            df[cols].to_parquet(outp, index=False)
            prog["done"][s] = {"parquet": outp.name, "sha16": sha16(outp), "bars": len(df),
                                "n_factors": len(fac), "start": str(darr[0]) if len(darr) else None,
                                "end": str(darr[-1]) if len(darr) else None,
                                "ts": datetime.now(timezone.utc).isoformat()}
            prog.pop(s, None)
            save_progress(prog)
            break
        time.sleep(INTERVAL)
        if idx % 200 == 0 or idx == 1:
            el = time.perf_counter() - t0
            print(f"[progress] {idx}/{len(todo)} elapsed={el:.0f}s rate={idx/el:.1f}/s "
                  f"ok={len(prog['done'])} fail={len(prog['failed'])}", flush=True)

    bs.logout()
    write_manifest(prog, len(symbols))
    print(f"[DONE] ok={len(prog['done'])} fail={len(prog['failed'])} "
          f"elapsed={time.perf_counter()-t0:.0f}s requests={prog['requests']}", flush=True)


def write_manifest(prog, pool_size):
    done = prog["done"]
    bars = sum(v["bars"] for v in done.values())
    fp = hashlib.sha256("|".join(sorted(v["sha16"] for v in done.values())).encode()).hexdigest()[:16]
    out = {
        "batch": "I-8", "phase": "阶段一 · 全量后复权构建", "role": "data_engineer",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "authorization": "老板 2026-11-16 22:17 第1条正式授权 C 路径批量落盘",
        "source": "baostock factor + 本机 tdx_a_share_full 未复权 parquet",
        "source_url": "http://www.baostock.com/", "factor_api": "query_adjust_factor",
        "factor_field": "backAdjustFactor", "adjust_method": "后复权=本机未复权×backAdjustFactor（基准固定不漂移）",
        "equivalence_evidence": "backtest/output/i8_local_adjust_equivalence.json（3只误差<1%，PASS）",
        "cross_validation": "backtest/output/i8_adjusted_probe.json（PASS）+ i8_adjusted_basis_check.json（PASS基准固定）",
        "align_baseline": "本机 .day 未复权 OHLC（adjustflag=3 逐 bar 对照）",
        "start": "1998-09-15", "end": "2026-11-13", "frequency": "d",
        "rate_limit": {"mode": "单连接串行", "interval_sec": INTERVAL, "retry_on": ["429","503","502"],
                       "retry_count": 1, "retry_sleep_sec": RETRY_SLEEP},
        "pool_size": pool_size, "n_success": len(done), "n_failed": len(prog["failed"]),
        "coverage_rate": round(len(done) / pool_size, 6) if pool_size else None,
        "gap_rate": round(1 - len(done) / pool_size, 6) if pool_size else None,
        "total_bars": bars, "data_fingerprint_sha256_16": fp,
        "requests": prog["requests"], "retry_log": prog["retry_log"],
        "failed_symbols": prog["failed"],
        "delisted_note": "342只退市股日线单独由 i8_adjusted_land.py 用 adjustflag=1 拉取（_progress.json delisted标记）",
        "redlines": "免费免key、未注册、未写key、未启客户端、未内存dump、本机只读、config未改",
        "execution_entity": "单一执行体，按 data_engineer.md soul 纪律落地",
    }
    MANIFEST.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[manifest] {MANIFEST}", flush=True)


if __name__ == "__main__":
    sys.exit(main())
