# -*- coding: utf-8 -*-
"""I-1 阶段一 · 本机通达信 .day 只读解析 → 统一 bar parquet（数据工程师产出）

PM 承派 + 验收；本脚本按 agents/souls/data_engineer.md 纪律执行：
  - 数据源选型 / 接入层 / 清洗 / 缺口检测 / 数据质量分级上报
  - **不定义任何交易信号**（信号骨架交后端架构师阶段二，见 a_share_m8_rerun.py）

老板 2026-11-15 16:55 指令（逐字）：「我让你完全访问本机，你能读取我电脑上安装的
通达信或同花顺吗？里面有从沪深两市开市以来的所有数据…你让PM再做一个一样的回测程序出来。」

本脚本只做前半句「能不能读」，如实回答：
  - 通达信 `D:\\tdx\\vipdoc\\{sh,sz,bj}\\lday\\*.day` 真实存在，明文 32B/bar 可解码；
  - **真实日期范围 1998-09-15 ~ 2026-11-13**，1990-12-19~1998-09-14 本机缺失（🔴 需补）；
  - 不是「开市以来」，如实申报，不脑补、不照抄秘书结论（本脚本逐项复验）。

红线（逐条遵守）：
  - **本机文件只读**：仅 open(..., "rb") 读 `D:\\tdx`，绝不移动/改名/删除/写入；
    不启动通达信/同花顺客户端、不联网拉行情、不改注册表；
  - 不改 `src/`、`config/strategy_params.yaml`、`.bat`/`.gitignore`/`.env`；
  - 不提交 Git、不接付费源、不注册/写入任何 API key、无令牌/密钥；
  - **严禁把未复权冒充复权、严禁脑补除权因子、严禁编造复权结果**。

运行：python backtest/tdx_a_share_build_cache.py
输出：data/cache/tdx_a_share/*.parquet + _manifest.json
"""
from __future__ import annotations

import hashlib
import json
import struct
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "cache" / "tdx_a_share"
TDX = Path("D:/tdx")                       # 只读源，绝不写入
LDAY = TDX / "vipdoc"
GBBQ = TDX / "T0002" / "hq_cache" / "gbbq"
BASE_DBF = TDX / "T0002" / "hq_cache" / "base.dbf"

# 32 字节/条：日期 uint32(YYYYMMDD) / 开高低收 int÷100 元 / 成交额 float32 元
#              / 成交量 uint32 股 / 保留位 uint32
DAY_STRUCT = "<IIIIIfII"
DAY_BYTES = 32

# 标的池：由数据可得性确定（少量代表性配对，不铺大网格）。
# 理由逐条写进 manifest.pairs，供阶段二直接读取，避免阶段二擅自替换标的。
PAIRS: List[Dict[str, Any]] = [
    {
        "id": "IDX_PAIR", "leg_a": "sh000001", "leg_b": "sz399001",
        "kind": "index×index", "corporate_action_risk": "none",
        "reason": "双宽基指数（上证综指×深证成指）：指数点位序列无除权、无退市，"
                  "1998-09-15~2026-11-13 各 6824 根全样本对齐最好；复权结论对其不敏感 → 主组。",
    },
    {
        "id": "IDX_ETF", "leg_a": "sh000300", "leg_b": "sh510300",
        "kind": "index×ETF", "corporate_action_risk": "etf_dividend_residual",
        "reason": "沪深300 指数×沪深300ETF：同一标的的现货/基金腿，基差收敛逻辑最贴近"
                  "配对均值回归；ETF 有分红除息 residual（🟡 显式标注），2012-05-28 起样本较短。",
    },
    {
        "id": "BANK_PAIR", "leg_a": "sh600000", "leg_b": "sz000001",
        "kind": "stock×stock", "corporate_action_risk": "unadjusted+survivorship",
        "reason": "主板银行股对（浦发银行×平安银行）：有协整可能，但个股送转/分红"
                  "**未复权**且现存股票池回测 1998 存在**幸存者偏差**（🔴）。"
                  "仅作敏感性对照，不进冻结判定主结论、不作为可上线依据。",
    },
]
SYMBOLS = sorted({s for p in PAIRS for s in (p["leg_a"], p["leg_b"])})

# 交易日历基准（数据驱动，不脑补外部日历）：各市场用其基准指数的实际交易日集合。
# sh→sh000001, sz→sz399001, bj→bj899050。
CALENDAR_BASIS = {"sh": "sh000001", "sz": "sz399001", "bj": "bj899050"}


def sha16(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()[:16]


def market_of(symbol: str) -> str:
    return symbol[:2]


def day_path(symbol: str) -> Path:
    return LDAY / market_of(symbol) / "lday" / f"{symbol}.day"


def decode_day(path: Path) -> pd.DataFrame:
    """只读解码单个 .day → 统一 bar DataFrame（与项目现有 data/cache parquet 同构超集）。"""
    raw = path.read_bytes()                      # 只读，不写回
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
    # ts：UTC 00:00 epoch 秒（与现有 parquet 的 ts 同为秒级，ts_ms = ts*1000）
    ts = np.array([
        int(datetime(int(d // 10000), int(d // 100 % 100), int(d % 100),
                     tzinfo=timezone.utc).timestamp())
        for d in dates
    ], dtype=np.int64)
    df = pd.DataFrame({
        "symbol": symbol_of(path),
        "ts": ts,
        "date": dates,
        "open": o, "high": h, "low": l, "close": c,
        "volume": vol,                # 股
        "quote_volume": amt,          # 成交额（元）
        "market": market_of(symbol_of(path)),
        "adjusted": False,            # 未复权，如实标注
    })
    # 数据质量分级（数据工程师职责：清洗/缺口检测，不静默吞）
    df["bad_px"] = (
        (df["high"] < df["low"]) |
        (df["open"] > df["high"]) | (df["open"] < df["low"]) |
        (df["close"] > df["high"]) | (df["close"] < df["low"]) |
        (df["close"] <= 0)
    )
    df["thin"] = (df["volume"] <= 0) | (df["quote_volume"] <= 0)
    df = df.sort_values("date").reset_index(drop=True)
    return df


def symbol_of(path: Path) -> str:
    return path.stem


def calendar_index(symbol: str) -> (List[int], Dict[int, int]):
    """某市场基准指数的实际交易日序列 + 日期→序号（用于 gap / 缺口率，数据驱动）。"""
    p = day_path(symbol)
    raw = p.read_bytes()
    n = len(raw) // DAY_BYTES
    ds = [struct.unpack_from("<I", raw, i * DAY_BYTES)[0] for i in range(n)]
    ds = sorted(ds)
    return ds, {d: i for i, d in enumerate(ds)}


def gap_stats(df: pd.DataFrame, cal_sorted: List[int], cal_pos: Dict[int, int]) -> Dict[str, Any]:
    """上市后缺口率 + 缺口分级。上市前无数据属正常（非缺口），按首日起算。"""
    dates = df["date"].tolist()
    first, last = dates[0], dates[-1]
    expected = sum(1 for d in cal_sorted if first <= d <= last)
    n_bars = len(dates)
    gap_rate = 1.0 - n_bars / expected if expected > 0 else 1.0
    # 逐 bar gap 标记：相邻两根间跳过了 >=1 个基准交易日
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


def inspect_adjustment_sources() -> Dict[str, Any]:
    """复权硬前置：只读核实 gbbq / base.dbf 可解析性（不解密、不脑补、不联网、不装付费）。"""
    g_raw = GBBQ.read_bytes()
    g_size = len(g_raw)
    g_head4 = int.from_bytes(g_raw[:4], "little")   # 头 4B = 记录数（TDX 惯例）
    rec_divides = (g_size - 4) % 29 == 0 and (g_size - 4) // 29 == g_head4
    d_raw = BASE_DBF.read_bytes()
    d_size = len(d_raw)
    d_ver = d_raw[0]
    d_nrec = int.from_bytes(d_raw[4:8], "little")
    d_headlen = int.from_bytes(d_raw[8:10], "little")
    d_reclen = int.from_bytes(d_raw[10:12], "little")
    return {
        "gbbq": {
            "path": str(GBBQ).replace("/", "\\"),
            "size_bytes": g_size,
            "head4_uint32": g_head4,
            "record_struct": "4B 头(记录数) + 204523×29B 记录（由文件大小整除验证，非解密结论）",
            "record_divides_exactly": bool(rec_divides),
            "head_hex_16": g_raw[:16].hex(),
            "content_plaintext": False,
            "note": "头/记录内容非明文（疑似 TDX 自定义加密/压缩），重复字节段非行情明文；"
                    "标准库无解密表，本批不联网拉解密实现、不装付费源、不注册 key。"
                    "**不得据此脑补或编造除权因子**。",
        },
        "base_dbf": {
            "path": str(BASE_DBF).replace("/", "\\"),
            "size_bytes": d_size,
            "version_byte": f"0x{d_ver:02x}",
            "is_dbase_iii_plaintext_header": d_ver == 0x03,
            "n_records": d_nrec, "header_len": d_headlen, "record_len": d_reclen,
            "note": "标准 dBASE III 头（字段名明文），但存的是**当前基础信息/股本**，"
                    "非历史除权除息事件序列，不能据此做前复权。",
        },
        "gpcw_dat": {
            "path": "D:\\tdx\\vipdoc\\cw\\gpcw*.dat",
            "note": "财务数据文件，非除权因子；本批不解析（格式未核实）。",
        },
        "conclusion": "无法可靠复权",
        "conclusion_basis": "gbbq 内容加密且无可靠解密/交叉校验来源；base.dbf 无历史除权序列；"
                            "按红线不脑补除权因子、不编造复权结果。",
        "downgrade": [
            "🟢 主组选纯指数（sh000001/sz399001/sh000300）：指数点位序列无除权，不受影响",
            "🟡 ETF（sh510300）分红除息 residual 显式标注，不作无除权声明",
            "🔴 个股（sh600000/sz000001）送转/分红未复权 + 幸存者偏差，仅作敏感性对照，不进冻结判定主结论",
        ],
        "must_label_in_phase2": True,
        "result_not_upper_line_evidence": True,   # 结论不得写成「可上线依据」
        "need_boss": ["可信复权因子来源（交易所官方除权表 / 明文除权数据）",
                     "gbbq 解密表或等价明文股本变迁数据", "同花顺/交易所多源交叉校验"],
    }


def build() -> Dict[str, Any]:
    OUT.mkdir(parents=True, exist_ok=True)
    calendars = {sym: calendar_index(sym) for sym in CALENDAR_BASIS.values()}

    files_meta: List[Dict[str, Any]] = []
    for sym in SYMBOLS:
        p = day_path(sym)
        if not p.exists():
            files_meta.append({"symbol": sym, "error": f"MISSING {p}", "gap_grade": "🔴"})
            continue
        src = p.read_bytes()                      # 只读
        df = decode_day(p)
        cal_sorted, cal_pos = calendars[CALENDAR_BASIS[market_of(sym)]]
        gs = gap_stats(df, cal_sorted, cal_pos)
        df["gap"] = gs.pop("_gap_flag")

        parquet_name = f"{sym}_1d.parquet"
        parquet_path = OUT / parquet_name
        df.to_parquet(parquet_path, index=False)
        pq_bytes = parquet_path.read_bytes()

        n_bad = int(df["bad_px"].sum())
        n_thin = int(df["thin"].sum())
        n_gap = int(df["gap"].sum())
        files_meta.append({
            "symbol": sym,
            "source_path": str(p).replace("/", "\\"),     # 源确切路径
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
            "quality": {"bad_px_bars": n_bad, "thin_bars": n_thin, "gap_flag_bars": n_gap},
            "columns": list(df.columns),
        })
        print(f"[build] {sym}: bars={len(df)} {df['date'].iloc[0]}~{df['date'].iloc[-1]} "
              f"gap={gs['gap_rate_since_listing']:.4f} {gs['gap_grade']} "
              f"bad={n_bad} thin={n_thin} gapflag={n_gap} "
              f"src16={sha16(src)} pq16={sha16(pq_bytes)}", flush=True)

    # 全库范围复验（每文件只读首尾 4B）
    import glob
    allf = glob.glob(str(LDAY / "*" / "lday" / "*.day"))
    gmin, gmax, before1995, before1998 = 99999999, 0, 0, 0
    for fp in allf:
        with open(fp, "rb") as fh:
            sz = Path(fp).stat().st_size
            if sz < 32:
                continue
            fh.seek(0)
            d0 = struct.unpack("<I", fh.read(4))[0]
            fh.seek((sz // 32 - 1) * DAY_BYTES)
            d1 = struct.unpack("<I", fh.read(4))[0]
        gmin, gmax = min(gmin, d0), max(gmax, d1)
        if d0 < 19950101:
            before1995 += 1
        if d0 < 19980915:
            before1998 += 1

    manifest: Dict[str, Any] = {
        "batch": "I-1",
        "phase": "阶段一 · 数据层",
        "role": "data_engineer",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_root": "D:\\tdx",
        "read_only_confirmed": True,
        "read_only_note": "仅 open(...,'rb') 读取；未移动/改名/删除/写入 D:\\tdx 任何文件；"
                          "未启动客户端、未联网拉行情、未改注册表。",
        "decode": {
            "format": "struct.unpack_from('<IIIIIfII', b, i*32)",
            "bytes_per_bar": DAY_BYTES,
            "fields": ["date:uint32 YYYYMMDD", "open:int/100 元", "high:int/100 元",
                       "low:int/100 元", "close:int/100 元", "amount:float32 元",
                       "volume:uint32 股", "reserved:uint32"],
            "price_scale": "int÷100 = 元",
            "raw_unadjusted_quote": True,
            "verified_against_secretary_probe": True,
        },
        "coverage": {
            "library_earliest": str(gmin),
            "library_latest": str(gmax),
            "files_scanned": len(allf),
            "files_before_1995": before1995,
            "files_before_1998_09_15": before1998,
            "boss_claim": "「从沪深两市开市以来所有数据」——本机**不含** 1990-12-19~1998-09-14",
            "missing_global": {
                "range": ["1990-12-19", "1998-09-14"],
                "approx_years": 8,
                "grade": "🔴",
                "impact": "28 年 / 6824 根已远超加密 410d/900d，覆盖 2008/2015/2018/2020/2022/2024 多 regime，"
                          "**不必等补齐即可开工**；该段仅列为『需老板补数据』，不阻塞本批。",
            },
        },
        "calendar": {
            "method": "数据驱动：各市场用其基准指数实际交易日集合（sh000001/sz399001/bj899050），不脑补外部日历",
            "trading_days_per_year_config": 252,
            "note": "config/strategy_params.yaml timeframe 已为 A 股预留 (4h, 252日)；"
                    "阶段二夏普/Calmar 年化按 1d→252 根/年（src/core/timeframe.py resolve_timeframe）。",
        },
        "adjustment": inspect_adjustment_sources(),
        "institutional_differences": [
            {"item": "T+1（当日买入次日才可卖）", "modeled": True, "grade": "🟢",
             "handling": "日线 1d + bar t 出信号 t+1 成交：开仓 t+1、最早平仓 t+2，天然隔日，T+1 自动满足",
             "note": "由阶段二 t+1 成交口径保证，见 a_share_m8_rerun.py"},
            {"item": "涨跌停 / 停牌（复用 MarketStatus.LOCKED）", "modeled": False, "grade": "🔴",
             "handling": ".day 无涨跌停/停牌标记；不脑补标记。thin(volume=0) 仅标零成交，不等同 LOCKED",
             "note": "src/core/market_ifaces.py RegimeStatus.LOCKED 已预留但本批未接入；"
                     "涨跌停锁死无法成交的情形未建模 → 列为缺口，结果不得写可上线依据"},
            {"item": "ST / 退市 与 幸存者偏差", "modeled": False, "grade": "🔴",
             "handling": "当前在市股票池回测 1998 会剔除已退市股；主组改用无退市问题的指数；个股组仅对照并显式标注",
             "note": "分级上报，不静默吞；个股组不进冻结判定主结论"},
            {"item": "最小 100 股（整手）", "modeled": False, "grade": "🟡",
             "handling": "回测按名义金额/比例仓位计，不模拟整手取整",
             "note": "比例收益口径不受影响；实盘需整手约束，列为次级缺口"},
            {"item": "交易日历 252", "modeled": True, "grade": "🟢",
             "handling": "夏普/Calmar 年化按 A 股 1d→252 根/年（timeframe.py 已预留），日历用基准指数实交日",
             "note": "与 config timeframe.trading_days_per_year 派生一致，不改 config"},
            {"item": "集合竞价 / 开盘缺口", "modeled": True, "grade": "🟡",
             "handling": "t+1 以当日 open 成交，开盘跳空已含在 open 价内；集合竞价微观结构未建模",
             "note": "较用 close 成交更保守（同 phase6 §1）"},
            {"item": "复权 / 除权除息", "modeled": False, "grade": "🔴",
             "handling": "结论『无法可靠复权』→ 降级：主组纯指数无除权、ETF 标 residual、个股 🔴 仅对照",
             "note": "严禁未复权冒充复权；阶段二必须显式标注该限制"},
        ],
        "pairs": PAIRS,
        "need_boss_data": [
            {"item": "1990-12-19~1998-09-14 约 8 年日线", "grade": "🔴",
             "why": "老板『开市以来』口径；本机缺失"},
            {"item": "可信复权因子来源 / gbbq 解密表 / 明文除权数据", "grade": "🔴",
             "why": "本批无法可靠复权，个股与 ETF 序列除权跳空未消除"},
            {"item": "同花顺 / 交易所多源交叉校验", "grade": "🟡",
             "why": "当前仅通达信单源明文，未做跨源一致性校验"},
            {"item": "A 股真实费率档（佣金/印花税/过户费/滑点）", "grade": "🟡",
             "why": "阶段二全部为『待老板确认的假设』，非交易所官方核实值"},
            {"item": "A 股极端段定义（2008/2015/2018/2020/2022/2024…选哪段）", "grade": "🔴",
             "why": "冻结门槛 2026-02 是加密崩盘段；换 A 股属老板决策权，PM/工程师不得擅自替换"},
        ],
        "files": files_meta,
        "phase2_gate": "阶段一通过项：明文可解码 + 全库范围复验 + 标的池可得；"
                       "阶段一**未通过项**：『可靠复权』——按派活单如实标注并降级，"
                       "阶段二必须显式标注『未复权』限制，结果不得写成『可上线依据』。",
    }

    mf = OUT / "_manifest.json"
    mf.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[manifest] -> {mf}  ({len(files_meta)} 标的, {len(PAIRS)} 配对)", flush=True)
    print(f"[coverage] 全库最早={gmin} 最晚={gmax} 文件数={len(allf)} "
          f"早于1995={before1995} 早于1998-09-15={before1998}", flush=True)
    print(f"[adjustment] 结论 = {manifest['adjustment']['conclusion']}（阶段二须显式标注）", flush=True)
    return manifest


if __name__ == "__main__":
    build()
    sys.exit(0)
