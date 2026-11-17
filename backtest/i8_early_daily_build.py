# -*- coding: utf-8 -*-
"""I-8 阶段一 · 八年日线（1990-12-19~1998-09-14）落盘（数据工程师纪律）

老板第4条：可下载则下载，不能则维持 1998 后（降级路径已给，不卡死）。
实测：baostock 对 1998 前上市个股有该段数据（如 000001 从 1991-04-03、600656 从 1990-12-19），
但绝大多数 5687 只在该段尚未上市 → 按"实际有 bar 的股票"落盘，逐只标实际起始日，不建空表冒充。

产出：data/cache/tdx_a_share_early/{symbol}_1d_early.parquet + _manifest.json
红线：免费免key、串行保守间隔、撞429/503/502隔180s重试一次、不脑补、不与本机拼接写全量。
"""
from __future__ import annotations
import sys, time, json, argparse, hashlib
from pathlib import Path
from datetime import datetime, timezone
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backtest"))
import baostock as bs
import pandas as pd

OUT = ROOT / "data" / "cache" / "tdx_a_share_early"
PROGRESS = OUT / "_progress.json"
MANIFEST = OUT / "_manifest.json"
ROSTER = ROOT / "data" / "cache" / "tdx_a_share_roster" / "delisting_roster.json"
FULL_MANIFEST = ROOT / "data" / "cache" / "tdx_a_share_full" / "_manifest.json"
FIELDS = "date,open,high,low,close,preclose,volume,amount,adjustflag,turn,tradestatus"
START, END = "1990-12-19", "1998-09-14"
INTERVAL = 0.15
RETRY_SLEEP = 180


def sha16(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""): h.update(c)
    return h.hexdigest()[:16]


def load_prog():
    if PROGRESS.exists(): return json.load(open(PROGRESS, encoding="utf-8"))
    return {"done": {}, "empty": [], "failed": {}, "retry_log": [], "requests": 0,
            "started_at": datetime.now(timezone.utc).isoformat()}


def save_prog(p):
    t = PROGRESS.with_suffix(".tmp"); t.write_text(json.dumps(p, ensure_ascii=False, indent=1), encoding="utf-8"); t.replace(PROGRESS)


def fetch(sym):
    bsc = f"{sym[:2]}.{sym[2:]}"
    rs = bs.query_history_k_data_plus(bsc, FIELDS, start_date=START, end_date=END, frequency="d", adjustflag="1")
    rows = []
    while rs.error_code == "0" and rs.next(): rows.append(rs.get_row_data())
    return rs.error_code, rs.error_msg, rows


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    prog = load_prog()
    # 目标：退市股（多为老股）+ 全池中 listing_date <= 1998-09-14 的个股
    targets = set()
    if ROSTER.exists():
        r = json.load(open(ROSTER, encoding="utf-8"))
        for rec in r["records"]:
            c = rec["code"]
            if c[:2] in ("sh", "sz", "bj"): targets.add(c)
    fm = json.load(open(FULL_MANIFEST, encoding="utf-8"))
    for f in fm["files"]:
        ld = f.get("listing_date", "")
        if ld and ld <= "19980914": targets.add(f["symbol"])
    targets = sorted(targets)
    print(f"[plan] early-window targets={len(targets)}（退市股+1998前上市）", flush=True)

    lg = bs.login(); prog["requests"] += 1
    if lg.error_code != "0": print("FATAL login", flush=True); return 2
    t0 = time.perf_counter()
    processed = 0
    for s in targets:
        if s in prog["done"] or s in prog["empty"]: continue
        if args.limit and processed >= args.limit: break
        processed += 1
        retry = 0
        while True:
            ec, em, rows = fetch(s); prog["requests"] += 1
            if ("10001001" in str(ec) or "未登录" in str(em)) and retry < 2:
                bs.login(); prog["requests"] += 1; retry += 1; continue
            if ec in ("429","503","502") and retry < 1:
                retry += 1
                prog["retry_log"].append({"symbol": s, "ts": datetime.now(timezone.utc).isoformat(),
                    "reason": f"{ec}:{em[:80]}", "action": f"sleep {RETRY_SLEEP}s retry#1"})
                save_prog(prog); time.sleep(RETRY_SLEEP); continue
            if ec != "0":
                prog["failed"][s] = {"error": f"{ec}:{em[:100]}"}; break
            if not rows:
                prog["empty"].append(s)   # 该段无 bar（未上市/无数据），如实登记，不建空表
                break
            df = pd.DataFrame(rows, columns=FIELDS.split(","))
            for c in ["open","high","low","close","preclose","amount","turn"]:
                df[c] = pd.to_numeric(df[c], errors="coerce")
            df["volume"] = pd.to_numeric(df["volume"], errors="coerce").astype("Int64")
            df["date"] = df["date"].str.replace("-", "", regex=False)
            p = OUT / f"{s}_1d_early.parquet"
            df.to_parquet(p, index=False)
            prog["done"][s] = {"parquet": p.name, "sha16": sha16(p), "bars": len(df),
                                "start": df["date"].iloc[0], "end": df["date"].iloc[-1]}
            break
        save_prog(prog)
        time.sleep(INTERVAL)
        if processed % 50 == 0 or processed == 1:
            print(f"[progress] {processed}/{len(targets)} ok={len(prog['done'])} empty={len(prog['empty'])} "
                  f"elapsed={time.perf_counter()-t0:.0f}s", flush=True)
    bs.logout()
    write_manifest(prog, len(targets))
    print(f"[DONE] ok={len(prog['done'])} empty={len(prog['empty'])} fail={len(prog['failed'])}", flush=True)


def write_manifest(prog, n_targets):
    bars = sum(v["bars"] for v in prog["done"].values())
    fp = hashlib.sha256("|".join(sorted(v["sha16"] for v in prog["done"].values())).encode()).hexdigest()[:16]
    out = {"batch":"I-8","phase":"阶段一 · 八年日线(1990-1998)","role":"data_engineer",
        "generated_at":datetime.now(timezone.utc).isoformat(),
        "authorization":"老板 2026-11-16 第4条：可下载则下载，不能则维持1998后",
        "source":"baostock query_history_k_data_plus adjustflag=1（免费免key）",
        "start":START,"end":END,"frequency":"d",
        "n_targets":n_targets,"n_with_bars":len(prog["done"]),"n_empty_no_bars":len(prog["empty"]),
        "n_failed":len(prog["failed"]),"total_bars":bars,"data_fingerprint_sha256_16":fp,
        "requests":prog["requests"],"retry_log":prog["retry_log"],
        "failed_symbols":prog["failed"],
        "start_date_note":"回测起点维持1998-09-15；本目录仅少数1998前上市个股有早期bar，不构成全市场起点前移，不与本机拼接写全量",
        "redlines":"免费免key、未注册、未写key、未启客户端、未内存dump、未建空表冒充、未把降级写成全量",
        "execution_entity":"单一执行体，按 data_engineer.md soul 纪律落地"}
    MANIFEST.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__": sys.exit(main())
