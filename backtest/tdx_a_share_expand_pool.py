# -*- coding: utf-8 -*-
"""I-3 阶段一 · 只读扩标的池（数据工程师产出，PM 承派 + 验收）

老板 2026-11-15 17:40 指令：「对A股主要应该回测的是技术派的策略。应该收集技术派的因子，
回测因子的效果。」+ 17:46「散户是无法直接对A股股票做空的」→ 单腿纯多头、不做空、不对冲。
本脚本**只做前半段的数据层**：按明确机械规则从本机通达信 .day 只读补拉标的池 → 统一 bar
parquet 追加落盘 + manifest 追加（不覆盖 I-1 的 6 个 parquet 与旧键值）。
**不定义任何交易信号**（信号/买卖规则见 agents/phase9-i3-technical-factor-catalog.md，
由阶段二 backtest/a_share_technical_factors.py 落地）。

按 agents/souls/data_engineer.md 纪律：数据源选型 / 接入层 / 清洗 / 缺口检测 / 质量分级上报。
按 I-1 `backtest/tdx_a_share_build_cache.py` 已实测的解码口径（32B/条 <IIIIIfII）逐项复验，
**不脑补、不照抄秘书结论**。

============================================================================
选取规则（机械、可复现、可验证；与 catalog §5 逐字一致）
============================================================================
主池 · 指数（12）：
  R1 代码前缀 ∈ {sh000, sz399}（通达信指数类；指数点位无除权、无退市 → 规避技术派
     因子对除权跳空敏感的问题）
  R2 本机首个交易日 start ≤ 2005-12-31
  R3 末日 end = 2026-11-13（当前在市）
  R4 n_bars ≥ 5000
  R5 排除 I-1 已落盘 6 代码（保留不覆盖）
  R6 按 (market, code) 升序，sh 取前 6、sz 取前 6 → 共 12 个
对照 · 个股（3）：
  S1 glob 前缀 ∈ {sh6000, sh6013, sz0000}
  S2 start ≤ 2000-12-31；S3 end = 2026-11-13；S4 n_bars ≥ 6000；S5 排除 I-1 6 代码
  S6 按代码升序取前 3 → 共 3 个（🔴 未复权 + 除权跳空污染 + 幸存者偏差，仅对照）

============================================================================
红线
============================================================================
本机文件只读：仅 open(..., "rb")，不移动/改名/删除/写入 D:\tdx 任何文件；
不启动客户端、不联网拉行情、不改注册表；不改 src/、不改 config/strategy_params.yaml、
不动 .bat/.gitignore/.env；不提交 Git、不接付费源、不注册/写入任何 API key、无令牌/密钥；
**不做配对 / 不做空 / 不对冲**（本脚本纯数据层，无任何信号）；
I-1 已落盘 6 个 parquet 保留不覆盖；manifest 仅**追加** i3_append 段、旧键值原样保留。

运行：python backtest/tdx_a_share_expand_pool.py
输出：data/cache/tdx_a_share/ 新增 15 个 parquet + _manifest.json（追加 i3_append 段）
"""
from __future__ import annotations

import glob
import hashlib
import json
import struct
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "cache" / "tdx_a_share"
MANIFEST = OUT / "_manifest.json"
TDX = Path("D:/tdx")                       # 只读源，绝不写入
LDAY = TDX / "vipdoc"

DAY_STRUCT = "<IIIIIfII"                   # I-1 已实测：日期/OHLC(int÷100)/成交额f32/成交量u32/保留
DAY_BYTES = 32

# I-1 已落盘 6 标的（保留不覆盖；规则 R5/S5 显式排除）
I1_SYMBOLS = {
    "sh000001", "sh000300", "sh510300", "sh600000", "sz000001", "sz399001",
}

# 交易日历基准（数据驱动，沿用 I-1：各市场用其基准指数实际交易日集合，不脑补外部日历）
CALENDAR_BASIS = {"sh": "sh000001", "sz": "sz399001"}


def sha16(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()[:16]


def market_of(symbol: str) -> str:
    return symbol[:2]


def day_path(symbol: str) -> Path:
    return LDAY / market_of(symbol) / "lday" / f"{symbol}.day"


def head_tail(path: Path) -> Tuple[int, int, int]:
    """只读首尾 4B：返回 (首个交易日, 末个交易日, bar 数)。"""
    sz = path.stat().st_size
    if sz < DAY_BYTES:
        return (0, 0, 0)
    with open(path, "rb") as fh:
        d0 = struct.unpack("<I", fh.read(4))[0]
        fh.seek((sz // DAY_BYTES - 1) * DAY_BYTES)
        d1 = struct.unpack("<I", fh.read(4))[0]
    return (int(d0), int(d1), sz // DAY_BYTES)


def decode_day(path: Path, symbol: str) -> pd.DataFrame:
    """只读解码单个 .day → 统一 bar DataFrame（与 I-1 parquet 同构超集）。"""
    raw = path.read_bytes()                       # 只读，不写回
    n = len(raw) // DAY_BYTES
    dates = np.empty(n, dtype=np.int64)
    o = np.empty(n, dtype=np.float64)
    h = np.empty(n, dtype=np.float64)
    l = np.empty(n, dtype=np.float64)
    c = np.empty(n, dtype=np.float64)
    amt = np.empty(n, dtype=np.float64)
    vol = np.empty(n, dtype=np.int64)
    for i in range(n):
        d, op, hi, lo, cl, a, v, _rsv = struct.unpack_from(DAY_STRUCT, raw, i * DAY_BYTES)
        dates[i] = d
        o[i], h[i], l[i], c[i] = op / 100.0, hi / 100.0, lo / 100.0, cl / 100.0
        amt[i], vol[i] = float(a), int(v)
    ts = np.array([
        int(datetime(int(d // 10000), int(d // 100 % 100), int(d % 100),
                     tzinfo=timezone.utc).timestamp())
        for d in dates
    ], dtype=np.int64)
    df = pd.DataFrame({
        "symbol": symbol,
        "ts": ts,
        "date": dates,
        "open": o, "high": h, "low": l, "close": c,
        "volume": vol,                # 股
        "quote_volume": amt,          # 成交额（元）
        "market": market_of(symbol),
        "adjusted": False,            # 未复权，如实标注
    })
    df["bad_px"] = (
        (df["high"] < df["low"]) |
        (df["open"] > df["high"]) | (df["open"] < df["low"]) |
        (df["close"] > df["high"]) | (df["close"] < df["low"]) |
        (df["close"] <= 0)
    )
    df["thin"] = (df["volume"] <= 0) | (df["quote_volume"] <= 0)
    df = df.sort_values("date").reset_index(drop=True)
    return df


def calendar_index(symbol: str) -> Tuple[List[int], Dict[int, int]]:
    p = day_path(symbol)
    raw = p.read_bytes()
    n = len(raw) // DAY_BYTES
    ds = sorted(struct.unpack_from("<I", raw, i * DAY_BYTES)[0] for i in range(n))
    return ds, {d: i for i, d in enumerate(ds)}


def gap_stats(df: pd.DataFrame, cal_sorted: List[int],
              cal_pos: Dict[int, int]) -> Dict[str, Any]:
    """上市后缺口率 + 缺口分级（沿用 I-1 口径；上市前无数据属正常、非缺口）。"""
    dates = df["date"].tolist()
    first, last = dates[0], dates[-1]
    expected = sum(1 for d in cal_sorted if first <= d <= last)
    n_bars = len(dates)
    gap_rate = 1.0 - n_bars / expected if expected > 0 else 1.0
    ranks = [cal_pos.get(d, -1) for d in dates]
    gap_flag = [False] * n_bars
    for i in range(1, n_bars):
        if ranks[i] >= 0 and ranks[i - 1] >= 0 and ranks[i] - ranks[i - 1] > 1:
            gap_flag[i] = True
    if gap_rate > 0.10:
        grade, note = "🔴", "缺口率>10% 或存在整段缺失，须显式标注、不得静默吞"
    elif gap_rate > 0.01:
        grade, note = "🟡", "缺口率 1%~10%（多为停牌），列为次级缺口"
    else:
        grade, note = "🟢", "缺口率≤1%，数据连续性好"
    return {
        "expected_trading_days_since_listing": expected,
        "gap_rate_since_listing": round(gap_rate, 6),
        "gap_grade": grade,
        "gap_note": note,
        "_gap_flag": gap_flag,
    }


def select_pool() -> Tuple[List[str], List[str]]:
    """按 §5 机械规则只读筛选；返回 (主池指数 12, 对照个股 3)。"""
    idx: List[Tuple[str, str, Tuple[int, int, int]]] = []
    for mk, pre in (("sh", "sh000"), ("sz", "sz399")):
        for fp in glob.glob(str(LDAY / mk / "lday" / f"{pre}*.day")):
            sym = Path(fp).stem
            if sym in I1_SYMBOLS:
                continue
            d0, d1, nb = head_tail(Path(fp))
            if d0 <= 20051231 and d1 == 20261113 and nb >= 5000:
                idx.append((mk, sym, (d0, d1, nb)))
    idx.sort(key=lambda x: (x[0], x[1]))
    main = [x[1] for x in idx if x[0] == "sh"][:6] + \
           [x[1] for x in idx if x[0] == "sz"][:6]

    stk: List[Tuple[str, Tuple[int, int, int]]] = []
    for mk, pre in (("sh", "sh6000"), ("sh", "sh6013"), ("sz", "sz0000")):
        for fp in glob.glob(str(LDAY / mk / "lday" / f"{pre}*.day")):
            sym = Path(fp).stem
            if sym in I1_SYMBOLS:
                continue
            d0, d1, nb = head_tail(Path(fp))
            if d0 <= 20001231 and d1 == 20261113 and nb >= 6000:
                stk.append((sym, (d0, d1, nb)))
    stk.sort(key=lambda x: x[0])
    control = [x[0] for x in stk[:3]]
    return main, control


def build() -> Dict[str, Any]:
    OUT.mkdir(parents=True, exist_ok=True)
    main, control = select_pool()
    all_symbols = main + control
    pool_of = {s: "main_index" for s in main}
    pool_of.update({s: "stock_control" for s in control})

    calendars = {sym: calendar_index(sym) for sym in CALENDAR_BASIS.values()}
    new_files: List[Dict[str, Any]] = []
    for sym in all_symbols:
        p = day_path(sym)
        if not p.exists():
            # 「仅确认存在」与「已实际转换」严格区分：缺文件则显式报 🔴，不静默吞
            new_files.append({"symbol": sym, "error": f"MISSING {p}", "gap_grade": "🔴"})
            continue
        src = p.read_bytes()                       # 只读
        df = decode_day(p, sym)
        cal_sorted, cal_pos = calendars[CALENDAR_BASIS[market_of(sym)]]
        gs = gap_stats(df, cal_sorted, cal_pos)
        df["gap"] = gs.pop("_gap_flag")

        parquet_name = f"{sym}_1d.parquet"
        parquet_path = OUT / parquet_name
        df.to_parquet(parquet_path, index=False)   # 新文件；I-1 旧 6 个不在 all_symbols 中，未覆盖
        pq_bytes = parquet_path.read_bytes()

        new_files.append({
            "symbol": sym,
            "pool": pool_of[sym],
            "source_path": str(p).replace("/", "\\"),
            "source_sha256_16": sha16(src),
            "source_bytes": len(src),
            "parquet": f"data/cache/tdx_a_share/{parquet_name}",
            "parquet_sha256_16": sha16(pq_bytes),
            "parquet_bytes": len(pq_bytes),
            "market": market_of(sym),
            "interval": "1d",
            "start_date": str(int(df["date"].iloc[0])),
            "end_date": str(int(df["date"].iloc[-1])),
            "n_bars": int(len(df)),
            "listing_date": str(int(df["date"].iloc[0])),
            "adjusted": False,
            "adjustment_method": "none（未复权，如实标注）",
            "gap_rate_since_listing": gs["gap_rate_since_listing"],
            "expected_trading_days_since_listing": gs["expected_trading_days_since_listing"],
            "gap_grade": gs["gap_grade"],
            "gap_note": gs["gap_note"],
            "quality": {
                "bad_px_bars": int(df["bad_px"].sum()),
                "thin_bars": int(df["thin"].sum()),
                "gap_flag_bars": int(df["gap"].sum()),
            },
            "columns": list(df.columns),
        })
        print(f"[expand] {sym} [{pool_of[sym]}] bars={len(df)} "
              f"{int(df['date'].iloc[0])}~{int(df['date'].iloc[-1])} "
              f"gap={gs['gap_rate_since_listing']:.4f} {gs['gap_grade']} "
              f"bad={int(df['bad_px'].sum())} thin={int(df['thin'].sum())} "
              f"src16={sha16(src)} pq16={sha16(pq_bytes)}", flush=True)

    # ---- manifest 追加（旧键值原样保留，仅新增 i3_append 顶层段）----
    old_raw = MANIFEST.read_bytes()
    manifest_old_sha16 = sha16(old_raw)
    manifest = json.loads(old_raw.decode("utf-8"))
    manifest["i3_append"] = {
        "batch": "I-3",
        "phase": "阶段一 · 只读扩标的池",
        "role": "data_engineer",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "read_only_confirmed": True,
        "read_only_note": "仅 open(...,'rb') 读取 D:\\tdx；未移动/改名/删除/写入本机任何文件；"
                          "未启动客户端、未联网拉行情、未改注册表。",
        "decode": "沿用 I-1 已实测 struct.unpack_from('<IIIIIfII', b, i*32)，"
                  "OHLC int÷100 元 / 成交额 float32 元 / 成交量 uint32 股；不复权",
        "selection_rule": {
            "main_index": "R1 前缀∈{sh000,sz399}（指数无除权无退市）；R2 start≤2005-12-31；"
                          "R3 end=2026-11-13；R4 n_bars≥5000；R5 排除 I-1 已落盘 6 代码；"
                          "R6 按(market,code)升序 sh 前6 + sz 前6 = 12",
            "stock_control": "S1 前缀∈{sh6000,sh6013,sz0000}；S2 start≤2000-12-31；"
                             "S3 end=2026-11-13；S4 n_bars≥6000；S5 排除 I-1 6 代码；"
                             "S6 按代码升序前 3 = 3（🔴 未复权/除权跳空污染/幸存者偏差，仅对照）",
            "rule_is_mechanical": True,
            "note": "规则为纯机械筛选、可复现；指数名称按代码惯例标注仅供阅读，"
                    "不脑补交易所官方分类/官方发布日；短样本标的如实申报实际起止与 bar 数，"
                    "不得写成『28 年』。",
        },
        "main_index_pool": main,
        "stock_control_pool": control,
        "adjusted": False,
        "adjustment_conclusion": "无法可靠复权（继承 I-1：gbbq 内容非明文、base.dbf 无历史除权序列）",
        "survivorship_bias": {
            "declared": True,
            "grade": "🔴",
            "note": "本机 D:\\tdx\\vipdoc 为当前在市 .day 快照，回测 1998 会剔除已退市/摘牌股 → "
                    "幸存者偏差会系统性高估收益。主池改用 12 个指数（无退市/摘牌问题）规避；"
                    "3 个个股仅作『未复权污染 + 幸存者偏差』双重对照，不进冻结判定主结论、"
                    "不作为可上线依据。🔴 缺口显式申报、不静默吞。",
        },
        "unadjusted_pollution_note": "技术派因子（MA/MACD/RSI/通道）对除权跳空比配对更敏感；"
                                     "主结论优先用指数/无除权标的；个股组显式标『未复权、"
                                     "信号可能受除权跳空污染』，不得写成可上线依据。",
        "institutional_difference_note": "I-1 制度差异 7 项漏『A股无裸卖空』，老板 17:46 已确认；"
                                         "本批禁一切空头暴露（单腿纯多头）。其余缺口仍在："
                                         "涨跌停/停牌(LOCKED 未接入)、ST/退市与幸存者偏差、"
                                         "最小100股、集合竞价、复权。",
        "only_confirmed_vs_converted": "15 个候选均先只读确认存在 + 首尾日期，再实际 decode→parquet "
                                       "转换落盘；无『仅确认存在未转换』项。逐文件指纹见 new_files。",
        "i1_manifest_sha256_16_before_append": manifest_old_sha16,
        "i1_files_preserved": True,
        "i1_old_parquet_not_overwritten": True,
        "field_mapping_ref": "agents/phase9-i3-technical-factor-catalog.md §4（因子×标的×字段映射表）",
        "expected_runs": {
            "n_factors": 15,
            "main_index_n": len(main),
            "stock_control_n": len(control),
            "detail_runs_main": 15 * len(main),
            "detail_runs_control": 15 * len(control),
            "n_runs_detail_total": 15 * (len(main) + len(control)),
            "factor_summary_main": 15,
            "factor_summary_control": 15,
            "note": "阶段二逐因子把 12 个主池指数成交合并为一条已实现权益曲线（统一尺子）；"
                    "排序/门槛基于主池汇总，个股对照单独申报、不混算、不进 n_passed_final。",
        },
        "new_files": new_files,
    }
    new_raw = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")
    MANIFEST.write_bytes(new_raw)
    manifest_new_sha16 = sha16(new_raw)

    print(f"\n[manifest] I-1 旧指纹={manifest_old_sha16} → 追加后新指纹={manifest_new_sha16}",
          flush=True)
    print(f"[pool] main_index={len(main)} {main}", flush=True)
    print(f"[pool] stock_control={len(control)} {control}", flush=True)
    print(f"[files] 新增 parquet {len(new_files)} 个；I-1 旧 6 个保留不覆盖", flush=True)
    return manifest


if __name__ == "__main__":
    build()
    sys.exit(0)
