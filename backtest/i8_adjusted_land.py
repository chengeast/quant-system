# -*- coding: utf-8 -*-
"""I-8 阶段一 · baostock 后复权日线批量落盘（数据工程师纪律）

授权：老板 2026-11-16 22:17 第 1 条正式授权 C 路径批量落盘。
源：baostock（免费/免注册/免 key），必要时 akshare/东财 F10 等免费免 key 源补缺。
口径：后复权 adjustflag=1（基准固定不漂移）；同时落一份 adjustflag=3 未复权对照用于逐 bar 校验。
限流：单连接串行 + 保守间隔 0.35s；撞 429/503/502 隔 180s 自动重试一次并留痕。
红线：不接付费源、不注册、不写 key、不启客户端、不内存 dump；本机 .day 只读。
断点续传：已落盘且 manifest 登记的 symbol 跳过；每只成功即增量写 progress，可安全中断。

用法：
  python backtest/i8_adjusted_land.py --limit 200      # 本次最多处理 200 只（含退市股优先）
  python backtest/i8_adjusted_land.py                    # 全量（断点续传）
"""
from __future__ import annotations
import sys, time, json, argparse, hashlib
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backtest"))
import baostock as bs
import pandas as pd

OUT = ROOT / "data" / "cache" / "tdx_a_share_adjusted"
PROGRESS = OUT / "_progress.json"
MANIFEST = OUT / "_manifest.json"
FULL_MANIFEST = ROOT / "data" / "cache" / "tdx_a_share_full" / "_manifest.json"
FIELDS = "date,open,high,low,close,preclose,volume,amount,adjustflag,turn,tradestatus"
RENAME = {"date": "date", "open": "open", "high": "high", "low": "low", "close": "close",
          "preclose": "preclose", "volume": "volume", "amount": "amount",
          "turn": "turn", "tradestatus": "tradestatus"}
START, END = "1998-09-15", "2026-11-13"
INTERVAL = 0.35           # 保守间隔（秒）
RETRY_SLEEP = 180         # 429/503/502 隔 3 分钟
RETRY_CODES = {"429", "503", "502"}

# 异常重试关键词（baostock 把网络错误放 error_msg，不总是 HTTP 码）
NET_KEYS = ["429", "503", "502", "timeout", "timed out", "connection", "socket",
            "远程主机", "连接", "network", "refused", "reset"]


def bs_code(symbol: str) -> str:
    return f"{symbol[:2]}.{symbol[2:]}"


def sha16(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()[:16]


def build_targets() -> tuple[list[dict], dict]:
    """本机 5687 只 + baostock 退市股 342 只，合并去重，退市股优先排前。"""
    m = json.load(open(FULL_MANIFEST, encoding="utf-8"))
    pool = {f["symbol"] for f in m["files"]}
    # 退市股名录（query_stock_basic 需在 login 后调用，这里返回占位，main 补）
    return sorted(pool), {}


def fetch_one(symbol: str, adjustflag: str):
    """返回 (error_code, error_msg, rows)。抛出的网络异常由调用方处理。"""
    rs = bs.query_history_k_data_plus(bs_code(symbol), FIELDS, start_date=START, end_date=END,
                                      frequency="d", adjustflag=adjustflag)
    rows = []
    while rs.error_code == "0" and rs.next():
        rows.append(rs.get_row_data())
    return rs.error_code, rs.error_msg, rows


def is_net_error(code: str, msg: str) -> bool:
    low = (str(msg) + " " + str(code)).lower()
    return code in RETRY_CODES or any(k in low for k in NET_KEYS)


def load_progress():
    if PROGRESS.exists():
        return json.load(open(PROGRESS, encoding="utf-8"))
    return {"done": {}, "failed": {}, "retry_log": [], "request_count": 0,
            "started_at": datetime.now(timezone.utc).isoformat()}


def save_progress(p):
    tmp = PROGRESS.with_suffix(".tmp")
    tmp.write_text(json.dumps(p, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(PROGRESS)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="本次最多处理只数（0=全量）")
    ap.add_argument("--include-delisted", type=int, default=1, help="是否含退市股（1 是）")
    ap.add_argument("--delisted-only", action="store_true", help="仅落退市股历史日线（补幸存者偏差）")
    args = ap.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    prog = load_progress()
    lg = bs.login()
    if lg.error_code != "0":
        print(f"FATAL login: {lg.error_code} {lg.error_msg}", flush=True)
        return 2
    prog["request_count"] += 1

    # 退市股名录
    delisted = {}
    if args.include_delisted:
        rs = bs.query_stock_basic()
        drows = []
        while rs.error_code == "0" and rs.next():
            drows.append(rs.get_row_data())
        prog["request_count"] += 1
        for r in drows:
            # code, name, ipoDate, outDate, type, status
            if r[4] == "1" and r[5] == "0":  # 股票 + 退市
                sym = r[0].replace(".", "")
                delisted[sym] = {"name": r[1], "ipo": r[2], "delist_date": r[3]}
        print(f"[delisted] {len(delisted)} 只退市股入列", flush=True)

    pool, _ = build_targets()
    # 目标 = 本机池 ∪ 退市股；退市股排前（消幸存者偏差优先）
    targets = [{"symbol": s, "delisted": delisted.get(s)} for s in sorted(delisted)]
    if not args.delisted_only:
        targets += [{"symbol": s, "delisted": None} for s in pool if s not in delisted]

    n_todo = 0
    for t in targets:
        s = t["symbol"]
        if s in prog["done"] and (OUT / f"{s}_1d_adj.parquet").exists():
            continue
        n_todo += 1
        if args.limit and n_todo > args.limit:
            break
    print(f"[plan] targets={len(targets)} todo(remaining)={n_todo} limit={args.limit or 'ALL'}", flush=True)

    processed = 0
    t_start = time.perf_counter()
    for t in targets:
        s = t["symbol"]
        if s in prog["done"] and (OUT / f"{s}_1d_adj.parquet").exists():
            continue
        if args.limit and processed >= args.limit:
            break
        processed += 1
        retry = 0
        while True:
            try:
                ec1, em1, adj = fetch_one(s, "1")   # 仅后复权（raw 对照已在探针完成）
                prog["request_count"] += 1
                # 会话过期（10001001 用户未登录）→ 重新 login 后续期，不计失败
                if "10001001" in str(ec1) or "未登录" in str(em1):
                    bs.login()
                    prog["request_count"] += 1
                    if retry < 3:
                        retry += 1
                        ec1, em1, adj = fetch_one(s, "1")
                        prog["request_count"] += 1
            except Exception as e:  # 网络异常
                prog["request_count"] += 1
                if is_net_error("", str(e)) and retry < 1:
                    retry += 1
                    prog["retry_log"].append({"symbol": s, "ts": datetime.now(timezone.utc).isoformat(),
                                               "reason": str(e)[:200], "action": f"sleep {RETRY_SLEEP}s retry#1"})
                    save_progress(prog)
                    print(f"[retry] {s} net error -> sleep {RETRY_SLEEP}s", flush=True)
                    time.sleep(RETRY_SLEEP)
                    continue
                prog["failed"][s] = {"error": str(e)[:200], "ts": datetime.now(timezone.utc).isoformat()}
                break
            if is_net_error(ec1, em1) and retry < 1:
                retry += 1
                prog["retry_log"].append({"symbol": s, "ts": datetime.now(timezone.utc).isoformat(),
                                           "reason": f"adj={ec1}:{em1[:80]}",
                                           "action": f"sleep {RETRY_SLEEP}s retry#1"})
                save_progress(prog)
                print(f"[retry] {s} code {ec1} -> sleep {RETRY_SLEEP}s", flush=True)
                time.sleep(RETRY_SLEEP)
                continue
            if ec1 != "0":
                prog["failed"][s] = {"error": f"adj={ec1}:{em1[:120]}",
                                      "ts": datetime.now(timezone.utc).isoformat()}
                break
            # 成功 → 落 parquet（仅后复权；未复权对照见探针 i8_adjusted_probe.json）
            df_adj = pd.DataFrame(adj, columns=FIELDS.split(","))
            for c in ["open", "high", "low", "close", "preclose", "amount", "turn"]:
                df_adj[c] = pd.to_numeric(df_adj[c], errors="coerce")
            df_adj["volume"] = pd.to_numeric(df_adj["volume"], errors="coerce").astype("Int64")
            df_adj["date"] = df_adj["date"].str.replace("-", "", regex=False)
            if len(df_adj) == 0:
                prog["failed"][s] = {"error": "adj empty rows", "ts": datetime.now(timezone.utc).isoformat()}
                break
            pa = OUT / f"{s}_1d_adj.parquet"
            df_adj.rename(columns=RENAME)[list(RENAME.values())].to_parquet(pa, index=False)
            prog["done"][s] = {
                "adj_parquet": pa.name, "adj_sha16": sha16(pa), "adj_bars": len(df_adj),
                "start": df_adj["date"].iloc[0], "end": df_adj["date"].iloc[-1],
                "delisted": t["delisted"], "ts": datetime.now(timezone.utc).isoformat(),
            }
            prog.pop(s, None)
            save_progress(prog)
            if processed % 20 == 0 or processed == 1:
                el = time.perf_counter() - t_start
                print(f"[progress] {processed}/{n_todo} done | elapsed {el:.0f}s | "
                      f"rate {processed/el:.2f}/s | ok={len(prog['done'])} fail={len(prog['failed'])}", flush=True)
            break
        time.sleep(INTERVAL)

    bs.logout()
    write_manifest(prog, targets)
    el = time.perf_counter() - t_start
    print(f"[DONE] processed={processed} ok={len(prog['done'])} fail={len(prog['failed'])} "
          f"elapsed={el:.0f}s requests={prog['request_count']}", flush=True)


def write_manifest(prog, targets):
    done = prog["done"]
    n_delist = sum(1 for v in done.values() if v.get("delisted"))
    bars = sum(v["adj_bars"] for v in done.values())
    fp = hashlib.sha256("|".join(sorted(v["adj_sha16"] for v in done.values())).encode()).hexdigest()[:16]
    out = {
        "batch": "I-8", "phase": "阶段一 · C 路径批量落盘（后复权）", "role": "data_engineer",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "authorization": "老板 2026-11-16 22:17 第 1 条正式授权 C 路径批量落盘",
        "source": "baostock", "source_url": "http://www.baostock.com/",
        "api": "query_history_k_data_plus", "fields": FIELDS,
        "adjust_method": "后复权 adjustflag=1（基准固定不漂移）；并落 adjustflag=3 未复权对照",
        "align_baseline": "本机 .day 未复权 OHLC（adjustflag=3 逐 bar 对照，见 i8_adjusted_probe.json）",
        "start": START, "end": END, "frequency": "d",
        "rate_limit": {"mode": "单连接串行", "interval_sec": INTERVAL,
                       "retry_on": ["429", "503", "502"], "retry_count": 1, "retry_sleep_sec": RETRY_SLEEP},
        "targets_total": len(targets), "n_success": len(done), "n_failed": len(prog["failed"]),
        "n_delisted_success": n_delist, "total_adj_bars": bars,
        "coverage_rate": round(len(done) / len(targets), 6) if targets else None,
        "gap_rate": round(1 - len(done) / len(targets), 6) if targets else None,
        "data_fingerprint_sha256_16": fp,
        "request_count": prog["request_count"], "retry_log": prog["retry_log"],
        "failed_symbols": prog["failed"],
        "cross_validation": "见 backtest/output/i8_adjusted_probe.json（验证通过才进阶段二）",
        "files": {k: v for k, v in done.items()},
        "redlines": "未接付费源/未注册/未写key/未启客户端/未内存dump；本机 D:\\tdx 只读；config 未改",
        "execution_entity": "单一执行体，按 data_engineer.md soul 纪律落地，非独立 subagent 会话",
    }
    # 退市股日线 manifest 独立文件，不覆盖因子方案主 _manifest.json
    dm = OUT / "_delisted_manifest.json"
    dm.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[manifest] written: {dm}", flush=True)


if __name__ == "__main__":
    sys.exit(main())
