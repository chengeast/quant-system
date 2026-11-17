# -*- coding: utf-8 -*-
"""I-5 阶段一 · 全市场 A 股个股池 + 本机通达信 .day 只读解码 → parquet（数据工程师产出）

PM 承派 + 验收；本脚本按 agents/souls/data_engineer.md 纪律执行：
  - 数据源接入 / 清洗 / 缺口检测 / 数据质量分级上报
  - **不定义任何交易信号**（信号层交后端架构师阶段二 a_share_portfolio_factors.py）

老板 2026-11-15 21:15 指令（逐字）：
「…整个A股市场有五千多支个股…选出符合条件的个股，再分仓持有…」
→ 本阶段构建**全市场个股池**（不再是 I-1/I-3/I-4 的 12 指数 + 3 个股），
  严格按代码前缀纳入个股、逐类剔除指数/ETF/基金/债券/板块指数等非个股。

红线（逐条遵守）：
  - **本机文件只读**：仅 pathlib.Path.read_bytes() 读 D:\\tdx；绝不移动/改名/删除/写入；
    不启动通达信/同花顺客户端、不联网拉行情、不改注册表；
  - **I-1/I-3/I-4 旧 parquet 与旧脚本一律保留不动**（本脚本只写新目录
    data/cache/tdx_a_share_full/，不触碰 data/cache/tdx_a_share/ 与 backtest/ 下任何旧 .py）；
  - 不改 src/、不改 config/strategy_params.yaml、不动 .bat/.gitignore/.env；
  - 不提交 Git、不放宽冻结门槛、不启用实盘、无令牌/密钥、不接付费源、不注册 API key；
  - **严禁把未复权冒充复权、严禁脑补除权因子**（gbbq 非明文，见 adjustment 结论）。

「仅确认存在」与「已实际转换」严格区分：本脚本对每只纳入个股都**实际 decode → 写 parquet**，
逐文件登记 parquet_sha256_16；无「仅确认存在未转换」项。

运行：python backtest/tdx_a_share_full_build_cache.py
输出：data/cache/tdx_a_share_full/{symbol}_1d.parquet + _manifest.json
"""
from __future__ import annotations

import hashlib
import json
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "cache" / "tdx_a_share_full"
TDX = Path("D:/tdx")                        # 只读源，绝不写入
LDAY = TDX / "vipdoc"

# 32 字节/条：日期 uint32(YYYYMMDD) / 开高低收 int÷100 元 / 成交额 float32 元
#              / 成交量 uint32 股 / 保留位 uint32  —— 与 I-1 实测 struct '<IIIIIfII' 完全一致
DAY_STRUCT_NAME = "<IIIIIfII"
DAY_BYTES = 32
DAY_DTYPE = np.dtype([
    ("date", "<u4"), ("open", "<u4"), ("high", "<u4"), ("low", "<u4"),
    ("close", "<u4"), ("amount", "<f4"), ("volume", "<u4"), ("rsv", "<u4"),
])
assert DAY_DTYPE.itemsize == DAY_BYTES

# ---------------------------------------------------------------------------
# 股票池规则（严格按 I-5 派活单 704 行；逐类可复现，不脑补交易所官方分类）
# ---------------------------------------------------------------------------
# 纳入：沪 600/601/603/605/688；深 000/001/002/003/30；北交 43/83/87/92
# 剔除：指数 sh000/sz399/bj899；ETF/基金 sh50/51/56/58、sz15/16/18；
#       债券 sh/sz 11/12/13；板块指数 sh880/881；B 股 sh900/sz200；
#       以及所有其它非普通股代码段（国债逆回购 sh204、CDR sh689、自定义指数 sh999、
#       新基金段 sh520/526/530/551、sz201、bj810 等）—— 一律保守剔除并逐类列数量。
INCLUDE_PREFIX: Dict[str, List[Tuple[str, str]]] = {
    "sh": [("600", "沪主板"), ("601", "沪主板"), ("603", "沪主板"),
           ("605", "沪主板"), ("688", "科创板")],
    "sz": [("000", "深主板/中小板"), ("001", "深主板/中小板"),
           ("002", "深主板/中小板"), ("003", "深主板/中小板"),
           ("30", "创业板")],
    "bj": [("43", "北交所"), ("83", "北交所"), ("87", "北交所"), ("92", "北交所")],
}
# 代码段 → 类别（按最长前缀匹配；p3 优先于 p2）
PREFIX_CATEGORY: List[Tuple[str, str, str, bool]] = [
    # (市场, 前缀, 类别, 是否纳入个股)
    ("sh", "600", "个股·沪主板", True), ("sh", "601", "个股·沪主板", True),
    ("sh", "603", "个股·沪主板", True), ("sh", "605", "个股·沪主板", True),
    ("sh", "688", "个股·科创板", True),
    ("sz", "000", "个股·深主板/中小板", True), ("sz", "001", "个股·深主板/中小板", True),
    ("sz", "002", "个股·深主板/中小板", True), ("sz", "003", "个股·深主板/中小板", True),
    ("sz", "30", "个股·创业板", True),
    ("bj", "43", "个股·北交所", True), ("bj", "83", "个股·北交所", True),
    ("bj", "87", "个股·北交所", True), ("bj", "92", "个股·北交所", True),
    ("sh", "000", "指数·sh000xxx（指数点位，非个股）", False),
    ("sz", "399", "指数·sz399xxx（指数点位，非个股）", False),
    ("bj", "899", "指数·bj899xxx（指数点位，非个股）", False),
    ("sh", "880", "板块指数·sh880xxx", False), ("sh", "881", "板块指数·sh881xxx", False),
    ("sh", "999", "自定义/其它指数·sh999xxx", False),
    ("sh", "204", "国债逆回购·sh204xxx（非个股）", False),
    ("sh", "520", "基金/ETF·sh520xxx（非个股）", False),
    ("sh", "526", "基金/ETF·sh526xxx（非个股）", False),
    ("sh", "530", "基金/ETF·sh530xxx（非个股）", False),
    ("sh", "551", "基金/ETF·sh551xxx（非个股）", False),
    ("sh", "689", "CDR/存托凭证·sh689xxx（非 688 科创，保守剔除）", False),
    ("sh", "900", "B 股·sh900xxx（非 A 股普通股）", False),
    ("sz", "200", "B 股·sz200xxx（非 A 股普通股）", False),
    ("sz", "201", "其它·sz201xxx（非个股）", False),
    ("bj", "810", "其它·bj810xxx（非个股，价格≈10000 点/极短）", False),
    ("sh", "50", "ETF/基金·sh50xxxx", False), ("sh", "51", "ETF/基金·sh51xxxx", False),
    ("sh", "56", "ETF/基金·sh56xxxx", False), ("sh", "58", "ETF/基金·sh58xxxx", False),
    ("sz", "15", "ETF/基金·sz15xxxx", False), ("sz", "16", "ETF/基金·sz16xxxx", False),
    ("sz", "18", "ETF/基金·sz18xxxx", False),
    ("sh", "11", "债券·sh11xxxx", False), ("sh", "12", "债券·sh12xxxx", False),
    ("sh", "13", "债券·sh13xxxx", False),
    ("sz", "11", "债券·sz11xxxx", False), ("sz", "12", "债券·sz12xxxx", False),
    ("sz", "13", "债券·sz13xxxx", False),
]


def sha16(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()[:16]


def classify(mk: str, code: str) -> Tuple[str, bool]:
    """返回 (类别, 是否纳入个股)。按最长前缀优先匹配，未命中列「其它·<前缀>」并剔除。"""
    for m, pre, cat, inc in PREFIX_CATEGORY:
        if m == mk and code.startswith(pre):
            return cat, inc
    return f"其它·{mk}{code[:3]}xxx（未命中个股前缀，保守剔除）", False


def decode_day(path: Path) -> Tuple[Optional[pd.DataFrame], Optional[str], Dict[str, Any]]:
    """只读解码单个 .day → 统一 bar DataFrame。异常返回 (None, 剔除理由, 统计)。"""
    raw = path.read_bytes()                       # 只读，不写回
    stat: Dict[str, Any] = {"source_bytes": len(raw)}
    if len(raw) == 0:
        return None, "空文件（0 字节）", stat
    if len(raw) % DAY_BYTES != 0:
        return None, f"文件大小非 32B 整数倍（{len(raw)} B）", stat
    arr = np.frombuffer(raw, dtype=DAY_DTYPE)     # 向量化解码，与 struct.unpack_from 逐条等价
    n = len(arr)
    stat["n_bars_raw"] = n
    if n == 0:
        return None, "无 bar（0 条记录）", stat
    date = arr["date"].astype(np.int64)
    o = arr["open"].astype(np.float64) / 100.0
    h = arr["high"].astype(np.float64) / 100.0
    l = arr["low"].astype(np.float64) / 100.0
    c = arr["close"].astype(np.float64) / 100.0
    amt = arr["amount"].astype(np.float64)
    vol = arr["volume"].astype(np.int64)
    # 全 0 价（或全非正）→ 剔除
    if not np.any(c > 0):
        return None, "全 0 / 非正收盘价（无有效行情）", stat
    bad_px = ((h < l) | (o > h) | (o < l) | (c > h) | (c < l) | (c <= 0))
    thin = (vol == 0)
    # 日期非严格递增（重复/倒序）→ 标记，去重保留首条
    dup = np.concatenate([[False], np.diff(date) <= 0])
    if dup.any():
        keep = ~dup
        date, o, h, l, c, amt, vol, bad_px, thin = (
            date[keep], o[keep], h[keep], l[keep], c[keep], amt[keep], vol[keep],
            bad_px[keep], thin[keep])
    stat["n_bars"] = int(len(date))
    stat["bad_px_bars"] = int(bad_px.sum())
    stat["thin_bars"] = int(thin.sum())
    stat["duplicate_or_unsorted_bars_removed"] = int(dup.sum())
    # ts：UTC 00:00 epoch 秒（与 I-1 parquet 同构；向量化生成）
    ts = pd.to_datetime(pd.Series(date).astype(str), format="%Y%m%d", utc=True).astype("int64").to_numpy() // 10**9
    sym = path.stem
    df = pd.DataFrame({
        "symbol": sym,
        "ts": ts,
        "date": date,
        "open": o.astype(np.float32), "high": h.astype(np.float32),
        "low": l.astype(np.float32), "close": c.astype(np.float32),
        "volume": vol.astype(np.int64),             # 成交量（股）—— compute_target F12/F13 读此列
        "quote_volume": amt.astype(np.float64),    # 成交额（元）—— 选股排序用
        "amount": amt.astype(np.float64),          # 成交额别名（显式）
        "market": mk_of(sym),
        "adjusted": False,                          # 未复权，如实标注
        "bad_px": bad_px, "thin": thin, "gap": False,
    })
    return df, None, stat


def mk_of(symbol: str) -> str:
    return symbol[:2]


def main() -> int:
    t0 = time.perf_counter()
    OUT.mkdir(parents=True, exist_ok=True)
    generated_at = datetime.now(timezone.utc).isoformat()

    # ---- 扫描 + 分类（只读目录，不读文件内容即可得代码段统计） ----
    per_market: Dict[str, Dict[str, Any]] = {}
    all_included: List[str] = []
    category_counts: Counter = Counter()
    include_by_plate: Counter = Counter()
    for mk in ("sh", "sz", "bj"):
        files = sorted((LDAY / mk / "lday").glob("*.day"))
        c: Counter = Counter()
        inc_syms: List[str] = []
        for f in files:
            code = f.stem[2:]
            cat, inc = classify(mk, code)
            c[cat] += 1
            category_counts[cat] += 1
            if inc:
                inc_syms.append(f.stem)
                # 按 classify 的类别名（含板块标签）直接归板
                include_by_plate[cat] += 1
        all_included.extend(inc_syms)
        per_market[mk] = {
            "n_day_files": len(files),
            "n_included_stocks": len(inc_syms),
            "classification": dict(sorted(c.items())),
        }
        print(f"[scan] {mk}: {len(files)} .day 文件, 纳入个股 {len(inc_syms)}", flush=True)
        for k, v in sorted(c.items()):
            print(f"        {k}: {v}", flush=True)

    n_code_pool = len(all_included)
    print(f"[scan] 代码前缀统计个股总数 = {n_code_pool}（老板/秘书实测 5687 对齐校验）", flush=True)
    assert n_code_pool == 5687, f"股票池总数 {n_code_pool} != 5687，须核实分类规则"

    # ---- 实际解码 → parquet（逐文件；异常逐项列、不静默吞） ----
    file_records: List[Dict[str, Any]] = []
    excluded_data_quality: List[Dict[str, str]] = []
    insufficient_history: List[str] = []
    all_dates: List[np.ndarray] = []
    total_bars = 0
    t_decode = time.perf_counter()
    for idx, sym in enumerate(all_included):
        src = LDAY / mk_of(sym) / "lday" / f"{sym}.day"
        raw_bytes = src.read_bytes()
        s_sha = sha16(raw_bytes)
        df, reason, stat = decode_day(src)
        if df is None:
            excluded_data_quality.append({"symbol": sym, "reason": reason or "未知",
                                          "source_bytes": stat.get("source_bytes")})
            continue
        n = int(stat["n_bars"])
        total_bars += n
        all_dates.append(df["date"].to_numpy(np.int64))
        if n < 61:                       # WARM=60 + 1，不足以产生任何交易（如实标记、不静默吞）
            insufficient_history.append(sym)
        pq = OUT / f"{sym}_1d.parquet"
        df.to_parquet(pq, index=False, engine="pyarrow", compression="snappy")
        pq_sha = sha16(pq.read_bytes())
        file_records.append({
            "symbol": sym,
            "source_path": str(src).replace("/", "\\"),
            "source_sha256_16": s_sha,
            "source_bytes": int(stat["source_bytes"]),
            "parquet": f"data/cache/tdx_a_share_full/{sym}_1d.parquet",
            "parquet_sha256_16": pq_sha,
            "parquet_bytes": int(pq.stat().st_size),
            "market": mk_of(sym),
            "interval": "1d",
            "start_date": str(int(df["date"].iloc[0])),
            "end_date": str(int(df["date"].iloc[-1])),
            "n_bars": n,
            "listing_date": str(int(df["date"].iloc[0])),
            "adjusted": False,
            "adjustment_method": "none（未复权，如实标注）",
            "insufficient_history_for_signals": bool(n < 61),
            "quality": {
                "bad_px_bars": int(stat["bad_px_bars"]),
                "thin_bars": int(stat["thin_bars"]),
                "duplicate_or_unsorted_bars_removed": int(stat["duplicate_or_unsorted_bars_removed"]),
            },
        })
        if (idx + 1) % 1000 == 0:
            print(f"[decode] {idx+1}/{n_code_pool} 已转换, 累计 {total_bars:,} bars", flush=True)

    # ---- 全局交易日历（数据驱动：所有纳入个股日期并集） ----
    union_dates = np.unique(np.concatenate(all_dates)) if all_dates else np.array([], dtype=np.int64)
    n_trading_days = int(len(union_dates))
    # 逐股缺口率：1 - n_bars /（全局日历中该股上市日之后的交易日数）
    gap_sum = 0.0
    for rec in file_records:
        listing = int(rec["start_date"])
        expected = int((union_dates >= listing).sum())
        exp = max(expected, 1)
        gap = max(0.0, 1.0 - rec["n_bars"] / exp)
        rec["gap_rate_since_listing"] = round(gap, 6)
        rec["expected_trading_days_since_listing"] = expected
        rec["gap_grade"] = "🟢" if gap <= 0.01 else ("🟡" if gap <= 0.10 else "🔴")
        gap_sum += gap
    mean_gap = round(gap_sum / len(file_records), 6) if file_records else None

    t_decode_end = time.perf_counter()

    # ---- manifest ----
    excluded_by_category = {k: v for k, v in sorted(category_counts.items()) if not k.startswith("个股·")}
    included_by_category = {k: v for k, v in sorted(category_counts.items()) if k.startswith("个股·")}
    manifest: Dict[str, Any] = {
        "batch": "I-5",
        "phase": "阶段一 · 全市场个股池 + 只读解码",
        "role": "data_engineer",
        "generated_at": generated_at,
        "source_root": "D:\\tdx",
        "read_only_confirmed": True,
        "read_only_note": "仅 Path.read_bytes() 读取 D:\\tdx；未移动/改名/删除/写入本机任何文件；"
                          "未启动客户端、未联网拉行情、未改注册表。I-1/I-3/I-4 旧 parquet 与旧脚本保留不动。",
        "decode": {
            "format": "struct.unpack_from('<IIIIIfII', b, i*32) 等价的 numpy.frombuffer 向量化解码",
            "bytes_per_bar": 32,
            "fields": ["date:uint32 YYYYMMDD", "open:int/100 元", "high:int/100 元",
                       "low:int/100 元", "close:int/100 元", "amount:float32 元",
                       "volume:uint32 股", "reserved:uint32"],
            "price_scale": "int÷100 = 元",
            "raw_unadjusted_quote": True,
            "verified_against": "I-1 manifest decode段 + 本批只读抽样（sh000010 首收 2941.84 指数点位 / sh600000 首收 27.75 股价）",
        },
        "stock_pool_rule": {
            "include_prefix": {"sh": ["600", "601", "603", "605", "688"],
                               "sz": ["000", "001", "002", "003", "30"],
                               "bj": ["43", "83", "87", "92"]},
            "exclude": ["指数 sh000/sz399/bj899", "ETF/基金 sh50/51/56/58、sz15/16/18",
                        "债券 sh/sz 11/12/13", "板块指数 sh880/881", "B股 sh900/sz200",
                        "其它非普通股代码段（sh204 国债逆回购 / sh520,526,530,551 基金 / "
                        "sh689 CDR / sh999 自定义指数 / sz201 / bj810）"],
            "rule_is_mechanical": True,
            "note": "严格按代码前缀筛选，非交易所官方分类；未命中个股前缀一律保守剔除并逐类列数量，不静默吞。"
                    "sh000xxx 为上交所指数代码段、sz399xxx 为深证指数段，不是深市个股被错标成 sh；"
                    "I-4 的错误是拿指数点位序列当个股成交标的（方向错误），本批不再用指数当成交标的。",
        },
        "per_market": per_market,
        "included": {
            "by_category": included_by_category,
            "total_by_code_prefix": n_code_pool,
            "total_actually_converted": len(file_records),
            "align_with_secretary_5687": bool(n_code_pool == 5687),
        },
        "excluded_non_stock": {
            "by_category": excluded_by_category,
            "total": int(sum(excluded_by_category.values())),
        },
        "excluded_data_quality": excluded_data_quality,
        "insufficient_history_stocks": {
            "n": len(insufficient_history),
            "rule": "n_bars < 61（WARM=60 + 1）：指标预热不足，回测中不产生任何交易；"
                    "仍入库（不静默剔除），在阶段二 actual coverage 中如实计数",
            "symbols": insufficient_history[:50],
            "note": "完整清单见 files[*].insufficient_history_for_signals=True",
        },
        "coverage": {
            "library_earliest": str(int(union_dates[0])) if n_trading_days else None,
            "library_latest": str(int(union_dates[-1])) if n_trading_days else None,
            "n_stocks_in_pool_by_code_prefix": n_code_pool,
            "n_stocks_actually_converted": len(file_records),
            "n_stocks_excluded_data_quality": len(excluded_data_quality),
            "n_stocks_insufficient_history": len(insufficient_history),
            "total_bars": total_bars,
            "n_trading_days_union": n_trading_days,
            "mean_gap_rate_since_listing": mean_gap,
            "gap_rate_method": "1 - n_bars /（全局交易日历中该股上市日之后的交易日数）；"
                               "全局日历 = 所有纳入个股 date 并集（数据驱动，不脑补外部日历）",
            "adjusted": False,
            "downsample_or_batching": "无降采样、无抽样：代码段 5687 只个股全部实际解码转 parquet；"
                                      "逐文件循环写入（I/O 分批，非数据降采样）",
        },
        "limits_red": [
            {"item": "无法可靠复权", "grade": "🔴",
             "detail": "gbbq 5,931,171 B 非明文/加密（I-1 已核：4B 头 204523 + 204523×29B 记录，"
                       "整除成立但内容非明文）；base.dbf 仅当前基础信息、无历史除权序列。"
                       "个股 MA/MACD/RSI/通道等技术信号受除权跳空污染，**比指数严重得多**。"
                       "不得脑补复权因子、不得把未复权冒充复权；全部结果标 adjusted=false、非可上线依据。"},
            {"item": "幸存者偏差", "grade": "🔴",
             "detail": "D:\\tdx\\vipdoc 为当前在市 .day 快照，回测 1998 会剔除已退市/摘牌股 → 系统性高估收益。"
                       "全市场个股池无法像 I-3 用指数规避，此为本批固有 🔴 缺口，须显式申报。"},
            {"item": "涨跌停 / 停牌 / ST / LOCKED 未建模", "grade": "🔴",
             "detail": ".day 无涨跌停/停牌/ST 标记；src/core/market_ifaces.py RegimeStatus.LOCKED"
                       "（market_ifaces.py:154）已预留但未接入。可能成交在不可成交日；"
                       "thin(volume=0) 仅标零成交、不等同 LOCKED，不脑补涨跌停。"},
            {"item": "1990-12-19~1998-09-14 约 8 年缺失", "grade": "🔴",
             "detail": "全库最早 1998-09-15、最晚 2026-11-13；老板『开市以来』本机不含此段，列为需老板补数据。"},
            {"item": "新股上市初期 / 历史不足", "grade": "🟡",
             "detail": "n_bars < 61 的个股（多为 2025~2026 新上市）指标预热不足，回测中不产生交易；"
                       "逐只标记 insufficient_history_for_signals，不静默剔除，实际覆盖如实申报。"},
            {"item": "最小 100 股 / 集合竞价 / 交易日历", "grade": "🟡",
             "detail": "100 股整手由阶段二组合引擎显式建模（按 100 股整数倍取整）；"
                       "集合竞价微观结构未建模（t+1 open 已含跳空）；A 股 252 日由 src/core/timeframe.py 只读复用。"},
        ],
        "need_boss_data": [
            {"item": "可信复权因子 / gbbq 解密表 / 明文除权数据", "grade": "🔴"},
            {"item": "涨跌停·停牌·ST·退市全历史名录（接入 LOCKED + 消幸存者偏差）", "grade": "🔴"},
            {"item": "1990-12-19~1998-09-14 约 8 年日线", "grade": "🔴"},
            {"item": "同花顺 / 交易所多源交叉校验", "grade": "🟡"},
        ],
        "i1_i3_i4_preserved": {
            "old_parquet_dir": "data/cache/tdx_a_share/（21 parquet，未覆盖、未删除）",
            "old_scripts": "backtest/tdx_a_share_build_cache.py / tdx_a_share_expand_pool.py / "
                           "a_share_m8_rerun.py / a_share_technical_factors.py / "
                           "a_share_technical_factors_real.py 全部保留不动",
            "this_batch_writes_only": "data/cache/tdx_a_share_full/ + backtest/tdx_a_share_full_build_cache.py（新增可复现脚本）",
        },
        "only_confirmed_vs_converted": "每只纳入个股均先只读确认存在，再实际 decode → parquet 落盘并登记 parquet_sha256_16；"
                                       "无「仅确认存在未转换」项。数据质量剔除项逐条列于 excluded_data_quality。",
        "runtime": {
            "scan_seconds": round(t_decode - t0, 3),
            "decode_and_write_seconds": round(t_decode_end - t_decode, 3),
            "total_seconds": round(t_decode_end - t0, 3),
        },
        "files": file_records,
    }
    # 整体数据指纹（不含自身字段）
    fingerprint = sha16(json.dumps(manifest, ensure_ascii=False, sort_keys=True,
                                   default=str).encode("utf-8"))
    manifest["data_fingerprint_sha256_16"] = fingerprint
    mf = OUT / "_manifest.json"
    mf.write_text(json.dumps(manifest, ensure_ascii=False, indent=1, default=str), encoding="utf-8")

    print(f"\n[DONE] 代码段个股 {n_code_pool}（对齐 5687 ✅）| 实际转换 {len(file_records)} | "
          f"数据质量剔除 {len(excluded_data_quality)} | 历史不足 {len(insufficient_history)}", flush=True)
    print(f"[DONE] 总 bars {total_bars:,} | 全局交易日 {n_trading_days} | "
          f"起 {manifest['coverage']['library_earliest']} 止 {manifest['coverage']['library_latest']}", flush=True)
    print(f"[DONE] 平均缺口率 {mean_gap} | manifest 指纹 {fingerprint}", flush=True)
    print(f"[DONE] 耗时 scan={manifest['runtime']['scan_seconds']}s "
          f"decode+write={manifest['runtime']['decode_and_write_seconds']}s "
          f"total={manifest['runtime']['total_seconds']}s", flush=True)
    print(f"[OUT] {mf}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
